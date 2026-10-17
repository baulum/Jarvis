import '../core/live.dart';

/// What Jarvis is doing right now – derived only from real core events, never simulated.
enum Mode { idle, listening, thinking, speaking, retrieving, tools, calling, offline }

const modeLabel = {
  Mode.idle: 'Bereit',
  Mode.listening: 'Hört zu',
  Mode.thinking: 'Denkt nach',
  Mode.speaking: 'Spricht',
  Mode.retrieving: 'Durchsucht das Gedächtnis',
  Mode.tools: 'Arbeitet mit Werkzeugen',
  Mode.calling: 'Im Gespräch',
  Mode.offline: 'Nicht erreichbar',
};

enum Tone { info, ok, warn, bad }

class ActivityLine {
  const ActivityLine(this.at, this.text, this.tone, {this.node});
  final DateTime at;
  final String text;
  final Tone tone;
  final String? node;
}

class NodeGlow {
  const NodeGlow(this.kind, this.until);
  final String kind; // recalled | active | ok | error | approval
  final DateTime? until;
}

/// Immutable live state folded from the event stream (unit-tested in test/live_model_test.dart).
class LiveModel {
  const LiveModel({
    this.runningSessions = const {},
    this.runningTools = const {},
    this.codingJobs = const {},
    this.lastRecall,
    this.lastError,
    this.voice,
    this.lines = const [],
    this.glow = const {},
    this.pendingApprovals = 0,
    this.rev = 0,
  });

  final Set<String> runningSessions;
  final Map<String, String> runningTools; // key → tool name
  final Set<String> codingJobs;
  final DateTime? lastRecall;
  final DateTime? lastError;
  final String? voice; // Mac voice daemon state (speaking/listening/thinking)
  final List<ActivityLine> lines;
  final Map<String, NodeGlow> glow;
  final int pendingApprovals;
  final int rev;

  LiveModel copyWith({
    Set<String>? runningSessions,
    Map<String, String>? runningTools,
    Set<String>? codingJobs,
    DateTime? lastRecall,
    DateTime? lastError,
    String? voice,
    List<ActivityLine>? lines,
    Map<String, NodeGlow>? glow,
    int? pendingApprovals,
  }) =>
      LiveModel(
        runningSessions: runningSessions ?? this.runningSessions,
        runningTools: runningTools ?? this.runningTools,
        codingJobs: codingJobs ?? this.codingJobs,
        lastRecall: lastRecall ?? this.lastRecall,
        lastError: lastError ?? this.lastError,
        voice: voice ?? this.voice,
        lines: lines ?? this.lines,
        glow: glow ?? this.glow,
        pendingApprovals: pendingApprovals ?? this.pendingApprovals,
        rev: rev + 1,
      );

  List<ActivityLine> _line(DateTime now, String text, Tone tone, [String? node]) {
    final next = [ActivityLine(now, text, tone, node: node), ...lines];
    return next.length > 40 ? next.sublist(0, 40) : next;
  }

  Map<String, NodeGlow> _glow(Iterable<String> ids, String kind, DateTime? until) =>
      {...glow, for (final id in ids) id: NodeGlow(kind, until)};

  LiveModel apply(CoreEvent ev, DateTime now) {
    final d = ev.data;
    final sid = ev.sessionId ?? d['session_id']?.toString();
    switch (ev.type) {
      case 'chat.user':
        return sid == null ? this : copyWith(runningSessions: {...runningSessions, sid});
      case 'chat.done':
        if (sid == null) return this;
        final err = d['error']?.toString();
        final failed = err != null && err.isNotEmpty && err != 'cancelled';
        return copyWith(
          runningSessions: {...runningSessions}..remove(sid),
          runningTools: {...runningTools}..removeWhere((k, _) => k.startsWith('$sid:')),
          lastError: failed ? now : null,
          lines: failed ? _line(now, 'Fehler: $err', Tone.bad) : null,
        );
      case 'memory.recalled':
        final hits = ev.nodes.where((n) => n.startsWith('memory:')).toList();
        if (hits.isEmpty) return this;
        final extraction = d['purpose'] == 'extraction';
        return copyWith(
          lastRecall: extraction ? null : now,
          glow: _glow(hits, 'recalled', now.add(const Duration(seconds: 6))),
          lines: _line(
              now,
              extraction ? 'Abgleich mit ${hits.length} Erinnerungen' : '${hits.length} Erinnerungen abgerufen',
              Tone.info,
              hits.first),
        );
      case 'tool.started':
      case 'tool.finished':
        final tool = d['tool']?.toString();
        if (tool == null) return this;
        final key = '${sid ?? ev.taskId ?? 'system'}:$tool';
        final node = 'tool:$tool';
        if (ev.type == 'tool.started') {
          return copyWith(
            runningTools: {...runningTools, key: tool},
            glow: _glow([node], 'active', null),
            lines: _line(now, 'Werkzeug: $tool', Tone.info, node),
          );
        }
        final ok = d['status'] == 'ok';
        return copyWith(
          runningTools: {...runningTools}..remove(key),
          lastError: ok ? null : now,
          glow: _glow([node], ok ? 'ok' : 'error', now.add(const Duration(seconds: 3))),
          lines: _line(now, ok ? '$tool erledigt' : '$tool fehlgeschlagen', ok ? Tone.ok : Tone.bad, node),
        );
      case 'coding.updated':
        final job = d['job_id']?.toString();
        if (job == null) return this;
        final status = d['status']?.toString();
        final done = status == 'completed' || status == 'failed' || status == 'cancelled';
        return copyWith(
          codingJobs: done ? ({...codingJobs}..remove(job)) : {...codingJobs, job},
          lines: _line(now, 'Coding-Auftrag ${status ?? ''}: ${d['title'] ?? ''}', status == 'failed' ? Tone.bad : Tone.info),
        );
      case 'approval.requested':
        return copyWith(
          pendingApprovals: pendingApprovals + 1,
          lines: _line(now, 'Wartet auf Freigabe: ${d['summary'] ?? d['tool']}', Tone.warn),
        );
      case 'approval.decided':
        return copyWith(pendingApprovals: pendingApprovals > 0 ? pendingApprovals - 1 : 0);
      case 'voice.status':
        return copyWith(voice: d['state']?.toString() ?? 'idle');
      default:
        if (ev.type.startsWith('task.') && (d['title'] != null) && d['status'] != null) {
          final st = d['status'].toString();
          final tone = st == 'failed' ? Tone.bad : st.startsWith('waiting') ? Tone.warn : st == 'completed' ? Tone.ok : Tone.info;
          return copyWith(lines: _line(now, 'Aufgabe „${d['title']}“: $st', tone, 'task:${ev.taskId ?? d['task_id']}'));
        }
        return this;
    }
  }

  /// Drops expired glows (called ~1×/s).
  LiveModel expire(DateTime now) {
    final g = {...glow}..removeWhere((_, v) => v.until != null && v.until!.isBefore(now));
    return g.length == glow.length ? this : copyWith(glow: g);
  }
}

/// One mode for the whole interface, by priority: what Sir most needs to know right now.
Mode deriveMode({required Link link, required String callStatus, required LiveModel live, required DateTime now}) {
  if (callStatus != 'idle' && callStatus != 'ended') return Mode.calling;
  if (link == Link.offline) return Mode.offline;
  if (live.voice == 'speaking') return Mode.speaking;
  if (live.voice == 'listening' || live.voice == 'recording') return Mode.listening;
  if (live.runningTools.isNotEmpty || live.codingJobs.isNotEmpty) return Mode.tools;
  final agent = live.runningSessions.isNotEmpty;
  if (agent && live.lastRecall != null && now.difference(live.lastRecall!).inMilliseconds < 1500) {
    return Mode.retrieving;
  }
  if (agent || live.voice == 'thinking') return Mode.thinking;
  return Mode.idle;
}
