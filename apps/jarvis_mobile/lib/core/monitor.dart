// Agent monitor model: the snapshot of `GET /api/coding/jobs/{id}/monitor` plus the small formatters the
// monitor UI needs. Pure Dart – no Flutter imports – so it is unit-testable.

class MonitorStep {
  const MonitorStep({
    required this.id,
    required this.tool,
    required this.detail,
    required this.status,
    this.startedAt,
    this.durationS,
    this.result,
  });

  factory MonitorStep.fromJson(Map<String, dynamic> j) => MonitorStep(
        id: j['id'].toString(),
        tool: j['tool'].toString(),
        detail: (j['detail'] ?? '').toString(),
        status: (j['status'] ?? 'running').toString(),
        startedAt: DateTime.tryParse((j['started_at'] ?? '').toString()),
        durationS: (j['duration_s'] as num?)?.toDouble(),
        result: j['result'] as String?,
      );

  final String id;
  final String tool;
  final String detail;

  /// running | paused | ok | error | aborted
  final String status;
  final DateTime? startedAt;
  final double? durationS;
  final String? result;

  bool get live => status == 'running' || status == 'paused';
}

class MonitorUsage {
  const MonitorUsage({this.input = 0, this.output = 0, this.cacheRead = 0, this.cacheCreation = 0, this.costUsd, this.costFinal = false});

  factory MonitorUsage.fromJson(Map<String, dynamic> j) => MonitorUsage(
        input: (j['input_tokens'] as num?)?.toInt() ?? 0,
        output: (j['output_tokens'] as num?)?.toInt() ?? 0,
        cacheRead: (j['cache_read_tokens'] as num?)?.toInt() ?? 0,
        cacheCreation: (j['cache_creation_tokens'] as num?)?.toInt() ?? 0,
        costUsd: (j['cost_usd'] as num?)?.toDouble(),
        costFinal: j['cost_final'] == true,
      );

  final int input;
  final int output;
  final int cacheRead;
  final int cacheCreation;
  final double? costUsd;
  final bool costFinal;

  int get io => input + output;
}

class JobMonitor {
  const JobMonitor({
    required this.jobId,
    required this.title,
    required this.status,
    required this.elapsedS,
    required this.fetchedAt,
    required this.currentKind,
    required this.currentLabel,
    this.model,
    this.effort,
    this.branch,
    this.steps = const [],
    this.stepCount = 0,
    this.runningTools = 0,
    this.lastText = '',
    this.messages = const [],
    this.pendingMessages = 0,
    this.usage = const MonitorUsage(),
    this.error,
    this.canPause = false,
    this.canResume = false,
    this.canCancel = false,
    this.canMessage = false,
  });

  factory JobMonitor.fromJson(Map<String, dynamic> j, {DateTime? now}) {
    final cur = (j['current'] as Map?)?.cast<String, dynamic>() ?? const {};
    return JobMonitor(
      jobId: j['job_id'].toString(),
      title: (j['title'] ?? '').toString(),
      status: (j['status'] ?? '').toString(),
      model: j['model'] as String?,
      effort: j['effort'] as String?,
      branch: j['branch'] as String?,
      elapsedS: (j['elapsed_s'] as num?)?.toDouble() ?? 0,
      // the phone's clock may differ from the Mac's: runtime ticks from the moment the snapshot arrived
      fetchedAt: now ?? DateTime.now(),
      currentKind: (cur['kind'] ?? 'waiting').toString(),
      currentLabel: (cur['label'] ?? '').toString(),
      steps: ((j['steps'] as List?) ?? const []).map((e) => MonitorStep.fromJson((e as Map).cast<String, dynamic>())).toList(),
      stepCount: (j['step_count'] as num?)?.toInt() ?? 0,
      runningTools: ((j['running_tools'] as List?) ?? const []).length,
      lastText: (j['last_text'] ?? '').toString(),
      messages: ((j['messages'] as List?) ?? const []).map((e) => ((e as Map)['text'] ?? '').toString()).toList(),
      pendingMessages: (j['pending_messages'] as num?)?.toInt() ?? 0,
      usage: MonitorUsage.fromJson((j['usage'] as Map?)?.cast<String, dynamic>() ?? const {}),
      error: j['error'] as String?,
      canPause: j['can_pause'] == true,
      canResume: j['can_resume'] == true,
      canCancel: j['can_cancel'] == true,
      canMessage: j['can_message'] == true,
    );
  }

  final String jobId;
  final String title;
  final String status;
  final String? model;
  final String? effort;
  final String? branch;
  final double elapsedS;
  final DateTime fetchedAt;

  /// tool | thinking | waiting | paused | done
  final String currentKind;
  final String currentLabel;
  final List<MonitorStep> steps;
  final int stepCount;
  final int runningTools;
  final String lastText;
  final List<String> messages;
  final int pendingMessages;
  final MonitorUsage usage;
  final String? error;
  final bool canPause;
  final bool canResume;
  final bool canCancel;
  final bool canMessage;

  bool get active => status == 'running' || status == 'paused' || status == 'queued';

  /// Runtime at [now] (without pauses); only a running job keeps counting.
  double elapsedAt(DateTime now) =>
      status == 'running' ? elapsedS + now.difference(fetchedAt).inMilliseconds.clamp(0, 1 << 40) / 1000 : elapsedS;
}

/// 3725 -> "1 h 2 min", 42 -> "42 s".
String formatDuration(num seconds) {
  if (seconds.isNaN || seconds < 0) return '–';
  final s = seconds.floor();
  final d = s ~/ 86400, h = (s % 86400) ~/ 3600, m = (s % 3600) ~/ 60;
  if (d > 0) return '$d d $h h';
  if (h > 0) return '$h h $m min';
  if (m > 0) return '$m min ${s % 60} s';
  return '$s s';
}

/// 950 -> "950", 12345 -> "12,3 Tsd.", 2400000 -> "2,4 Mio."
String formatTokens(int n) {
  String one(double v) => v.toStringAsFixed(1).replaceAll('.', ',');
  if (n >= 1000000) return '${one(n / 1000000)} Mio.';
  if (n >= 10000) return '${one(n / 1000)} Tsd.';
  return n.toString();
}

String formatUsd(double? v) {
  if (v == null) return '–';
  return v < 1 ? '${v.toStringAsFixed(3).replaceAll('.', ',')} \$' : '${v.toStringAsFixed(2).replaceAll('.', ',')} \$';
}

const statusTextDe = {
  'queued': 'Wartet',
  'running': 'Läuft',
  'paused': 'Pausiert',
  'completed': 'Abgeschlossen',
  'failed': 'Fehlgeschlagen',
  'cancelled': 'Abgebrochen',
  'waiting_approval': 'Wartet auf Freigabe',
  'waiting_input': 'Braucht Angaben',
  'waiting_external': 'Wartet auf Ergebnis',
};
