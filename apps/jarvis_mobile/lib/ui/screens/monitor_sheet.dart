import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api.dart';
import '../../core/monitor.dart';
import '../../core/theme.dart';
import '../../state/activity.dart';
import '../../state/monitor.dart';
import '../../state/providers.dart';
import '../widgets/confirm_sheet.dart';
import '../widgets/hud.dart';

Color statusColor(String s) => switch (s) {
      'completed' || 'ok' => J.green,
      'failed' || 'error' => J.red,
      'running' => J.cyan,
      'skipped' || 'cancelled' || 'aborted' => J.text3,
      _ => J.amber, // queued, paused, waiting_*
    };

/// Agent monitor for one coding job: what it does right now, every tool call with its result, runtime,
/// tokens, cost – and the controls (pause / resume / cancel / message), the same as on the desktop.
Future<void> openJobMonitor(BuildContext context, String jobId) {
  return showHudSheet(
    context,
    builder: (ctx) => ConstrainedBox(
      constraints: BoxConstraints(maxHeight: MediaQuery.sizeOf(ctx).height * 0.9),
      child: JobMonitorSheet(jobId: jobId),
    ),
  );
}

class JobMonitorSheet extends ConsumerStatefulWidget {
  const JobMonitorSheet({super.key, required this.jobId});
  final String jobId;
  @override
  ConsumerState<JobMonitorSheet> createState() => _JobMonitorSheetState();
}

class _JobMonitorSheetState extends ConsumerState<JobMonitorSheet> {
  Timer? _tick;

  @override
  void initState() {
    super.initState();
    _tick = Timer.periodic(const Duration(seconds: 1), (_) {
      // only a running job's clock moves; avoid rebuilding a finished one every second
      if (mounted && ref.read(jobMonitorProvider(widget.jobId)).valueOrNull?.status == 'running') setState(() {});
    });
  }

  @override
  void dispose() {
    _tick?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final snap = ref.watch(jobMonitorProvider(widget.jobId));
    final ctl = ref.read(jobMonitorProvider(widget.jobId).notifier);
    return snap.when(
      loading: () => const Padding(padding: EdgeInsets.all(J.s6), child: Center(child: CircularProgressIndicator(strokeWidth: 1.4, color: J.cyan))),
      error: (e, _) => Padding(
        padding: const EdgeInsets.all(J.s5),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Text(e is ApiError && e.offline ? 'Jarvis ist nicht erreichbar.' : '$e', style: J.bodyDim, textAlign: TextAlign.center),
          const SizedBox(height: J.s3),
          LightAction('Erneut versuchen', onTap: () => ref.invalidate(jobMonitorProvider(widget.jobId))),
        ]),
      ),
      data: (m) {
        final history = m.steps.reversed.toList();
        final elapsed = m.elapsedAt(DateTime.now());
        return ListView(shrinkWrap: true, padding: const EdgeInsets.fromLTRB(J.s5, J.s4, J.s5, J.s6), children: [
          Caps(statusTextDe[m.status] ?? m.status, color: statusColor(m.status)),
          const SizedBox(height: J.s2),
          Text(m.title, style: J.title),
          if (m.model != null || m.branch != null)
            Padding(
              padding: const EdgeInsets.only(top: J.s1),
              child: Text([if (m.model != null) '${m.model}${m.effort != null ? ' · ${m.effort}' : ''}', if (m.branch != null && m.branch!.isNotEmpty) m.branch!].join('  ·  '),
                  style: J.mono(10.5, color: J.text3), maxLines: 2, overflow: TextOverflow.ellipsis),
            ),
          const SizedBox(height: J.s4),
          _CurrentStep(m: m),
          const SizedBox(height: J.s4),
          Row(children: [
            _Stat('Laufzeit', formatDuration(elapsed)),
            _Stat('Schritte', '${m.stepCount}'),
            _Stat('Tokens', formatTokens(m.usage.io)),
            _Stat('Kosten', m.usage.costUsd == null ? '–' : '${m.usage.costFinal ? '' : '≥ '}${formatUsd(m.usage.costUsd)}'),
          ]),
          if (m.canPause || m.canResume || m.canCancel || m.canMessage) ...[
            const SizedBox(height: J.s4),
            AgentControlsBar(
              subject: 'Job',
              canPause: m.canPause,
              canResume: m.canResume,
              canCancel: m.canCancel,
              canMessage: m.canMessage,
              pending: m.pendingMessages,
              onPause: ctl.pause,
              onResume: ctl.resume,
              onCancel: ctl.cancel,
              onMessage: (t, now) => ctl.message(t, immediate: now),
            ),
          ],
          if (m.messages.isNotEmpty) ...[
            const SizedBox(height: J.s5),
            const Caps('Nachrichten an den Agenten'),
            const SizedBox(height: J.s2),
            for (final t in m.messages) Padding(padding: const EdgeInsets.only(bottom: J.s1), child: Text('» $t', style: J.bodyDim.copyWith(fontSize: 14))),
          ],
          const SizedBox(height: J.s5),
          Caps('Schritte · neueste zuerst${m.stepCount > m.steps.length ? ' · letzte ${m.steps.length}' : ''}'),
          const SizedBox(height: J.s1),
          if (history.isEmpty) Padding(padding: const EdgeInsets.symmetric(vertical: J.s3), child: Text('Noch keine Werkzeugaufrufe.', style: J.bodyDim)),
          for (final s in history) _StepTile(key: ValueKey(s.id), step: s),
          if (m.error != null) Padding(padding: const EdgeInsets.only(top: J.s4), child: Text(m.error!, style: J.mono(12, color: J.red))),
        ]);
      },
    );
  }
}

class _CurrentStep extends StatelessWidget {
  const _CurrentStep({required this.m});
  final JobMonitor m;
  @override
  Widget build(BuildContext context) {
    final color = m.currentKind == 'paused' ? J.amber : m.currentKind == 'done' ? statusColor(m.status) : J.cyan;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(J.s3),
      decoration: BoxDecoration(color: J.panel, border: Border.all(color: J.line), borderRadius: BorderRadius.circular(J.radius)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Container(width: 6, height: 6, decoration: BoxDecoration(shape: BoxShape.circle, color: color, boxShadow: [BoxShadow(color: color.withValues(alpha: 0.6), blurRadius: 6)])),
          const SizedBox(width: J.s2),
          Caps(m.currentKind == 'done' ? 'Ergebnis' : 'Aktueller Schritt'),
        ]),
        const SizedBox(height: J.s2),
        Text(m.currentLabel, style: J.mono(12.5, color: J.text, spacing: 0.1), maxLines: 4, overflow: TextOverflow.ellipsis),
        if (m.runningTools > 1)
          Padding(padding: const EdgeInsets.only(top: J.s1), child: Text('${m.runningTools} Werkzeugaufrufe laufen parallel', style: J.mono(10.5, color: J.text3))),
        if (m.lastText.isNotEmpty && m.currentKind != 'done')
          Padding(padding: const EdgeInsets.only(top: J.s2), child: Text(m.lastText, style: J.bodyDim.copyWith(fontSize: 14), maxLines: 3, overflow: TextOverflow.ellipsis)),
      ]),
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat(this.label, this.value);
  final String label;
  final String value;
  @override
  Widget build(BuildContext context) => Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Caps(label),
          const SizedBox(height: 2),
          Text(value, style: J.mono(13, color: J.text, spacing: 0), maxLines: 1, overflow: TextOverflow.ellipsis),
        ]),
      );
}

class _StepTile extends StatefulWidget {
  const _StepTile({super.key, required this.step});
  final MonitorStep step;
  @override
  State<_StepTile> createState() => _StepTileState();
}

class _StepTileState extends State<_StepTile> {
  bool _open = false;
  @override
  Widget build(BuildContext context) {
    final s = widget.step;
    final c = statusColor(s.status);
    return Pressable(
      onTap: () => setState(() => _open = !_open),
      haptic: false,
      semantics: '${s.tool} ${s.detail}',
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.symmetric(vertical: J.s3),
        decoration: const BoxDecoration(border: Border(bottom: BorderSide(color: J.line))),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Container(width: 6, height: 6, decoration: BoxDecoration(shape: BoxShape.circle, color: c, boxShadow: s.live ? [BoxShadow(color: c.withValues(alpha: 0.6), blurRadius: 6)] : null)),
          ),
          const SizedBox(width: J.s3),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Text(s.tool, style: J.mono(12.5, color: J.cyan, spacing: 0.2)),
                const Spacer(),
                if (s.durationS != null) Text(formatDuration(s.durationS!), style: J.mono(10.5, color: J.text3)),
              ]),
              if (s.detail.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 2),
                  child: Text(s.detail, style: J.mono(11.5, color: J.text2, spacing: 0.1), maxLines: _open ? 20 : 2, overflow: TextOverflow.ellipsis),
                ),
              if (s.result != null && s.result!.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 4),
                  child: Text(s.result!, style: J.mono(11, color: s.status == 'error' ? J.red : J.text3, spacing: 0.1), maxLines: _open ? 20 : 2, overflow: TextOverflow.ellipsis),
                ),
            ]),
          ),
        ]),
      ),
    );
  }
}

/// Pause / resume / cancel / message to the agent. Pausing and cancelling ask for confirmation first.
class AgentControlsBar extends StatefulWidget {
  const AgentControlsBar({
    super.key,
    required this.subject,
    required this.canPause,
    required this.canResume,
    required this.canCancel,
    required this.canMessage,
    required this.onPause,
    required this.onResume,
    required this.onCancel,
    required this.onMessage,
    this.pending = 0,
  });

  /// what is controlled, for texts ("Job", "Aufgabe")
  final String subject;
  final bool canPause;
  final bool canResume;
  final bool canCancel;
  final bool canMessage;
  final int pending;
  final Future<void> Function() onPause;
  final Future<void> Function() onResume;
  final Future<void> Function() onCancel;
  final Future<void> Function(String text, bool immediate) onMessage;
  @override
  State<AgentControlsBar> createState() => _AgentControlsBarState();
}

class _AgentControlsBarState extends State<AgentControlsBar> {
  final _text = TextEditingController();
  bool _immediate = false;
  String? _busy;

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  /// true when the action went through
  Future<bool> _run(String key, Future<void> Function() fn, String done) async {
    setState(() => _busy = key);
    try {
      await fn();
      if (mounted) _toast(done);
      return true;
    } on ApiError catch (e) {
      if (mounted) _toast(e.offline ? 'Jarvis ist nicht erreichbar' : e.message);
      return false;
    } finally {
      if (mounted) setState(() => _busy = null);
    }
  }

  void _toast(String msg) {
    final m = ScaffoldMessenger.maybeOf(context);
    m?.hideCurrentSnackBar();
    m?.showSnackBar(SnackBar(content: Text(msg), duration: const Duration(seconds: 3)));
  }

  Future<void> _pause() async {
    final job = widget.subject == 'Job';
    final ok = await confirmAction(
      context,
      title: '${widget.subject} pausieren?',
      body: job
          ? 'Der Agent-Prozess wird eingefroren und läuft mit „Fortsetzen“ genau an dieser Stelle weiter.'
          : 'Der Schritt in Arbeit wird noch beendet, danach hält die Aufgabe an und läuft mit „Fortsetzen“ weiter.',
      confirmLabel: 'Pausieren',
    );
    if (ok) await _run('pause', widget.onPause, '${widget.subject} pausiert');
  }

  Future<void> _cancel() async {
    final ok = await confirmAction(
      context,
      title: '${widget.subject} abbrechen?',
      body: 'Die Arbeit wird beendet und kann nicht fortgesetzt werden. Bereits geschriebene Dateien bleiben im Worktree erhalten.',
      confirmLabel: 'Abbrechen',
      danger: true,
    );
    if (ok) await _run('cancel', widget.onCancel, '${widget.subject} abgebrochen');
  }

  Future<void> _send() async {
    final t = _text.text.trim();
    if (t.isEmpty) return;
    final now = _immediate;
    final ok = await _run('message', () => widget.onMessage(t, now), now ? 'Nachricht gesendet – Schritt wird unterbrochen' : 'Nachricht vorgemerkt');
    if (ok && mounted) _text.clear();
  }

  @override
  Widget build(BuildContext context) {
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Wrap(spacing: J.s4, children: [
        if (widget.canPause) LightAction('Pausieren', color: J.amber, busy: _busy == 'pause', onTap: _pause),
        if (widget.canResume) LightAction('Fortsetzen', busy: _busy == 'resume', onTap: () => _run('resume', widget.onResume, '${widget.subject} läuft weiter')),
        if (widget.canCancel) LightAction('Abbrechen', color: J.red, busy: _busy == 'cancel', onTap: _cancel),
      ]),
      if (widget.canMessage) ...[
        const SizedBox(height: J.s2),
        Container(
          padding: const EdgeInsets.fromLTRB(J.s3, J.s1, J.s1, J.s1),
          decoration: BoxDecoration(color: J.panel, border: Border.all(color: J.line), borderRadius: BorderRadius.circular(J.radius)),
          child: Row(crossAxisAlignment: CrossAxisAlignment.center, children: [
            Expanded(
              child: TextField(
                controller: _text,
                minLines: 1,
                maxLines: 4,
                style: J.body.copyWith(fontSize: 15),
                textInputAction: TextInputAction.newline,
                onChanged: (_) => setState(() {}),
                decoration: InputDecoration(
                  isCollapsed: true,
                  border: InputBorder.none,
                  contentPadding: const EdgeInsets.symmetric(vertical: J.s3),
                  hintText: 'Nachricht an den Agenten',
                  hintStyle: J.bodyDim.copyWith(color: J.text3, fontSize: 15),
                ),
              ),
            ),
            Pressable(
              onTap: _text.text.trim().isEmpty || _busy != null ? null : _send,
              semantics: 'Nachricht senden',
              child: SizedBox.square(
                dimension: J.hit,
                child: _busy == 'message'
                    ? const Center(child: SizedBox.square(dimension: 16, child: CircularProgressIndicator(strokeWidth: 1.4, color: J.cyan)))
                    : const Icon(Icons.arrow_upward_rounded, color: J.cyan, size: 22),
              ),
            ),
          ]),
        ),
        Pressable(
          onTap: () => setState(() => _immediate = !_immediate),
          haptic: true,
          semantics: 'Sofort zustellen',
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: J.s2),
            child: Row(children: [
              Icon(_immediate ? Icons.check_box_rounded : Icons.check_box_outline_blank_rounded, size: 20, color: _immediate ? J.cyan : J.text3),
              const SizedBox(width: J.s2),
              Expanded(
                child: Text(
                  'Sofort zustellen (unterbricht den aktuellen Schritt)${widget.pending > 0 ? ' · ${widget.pending} wartet' : ''}',
                  style: J.mono(10.5, color: J.text3, spacing: 0.1),
                ),
              ),
            ]),
          ),
        ),
      ],
    ]);
  }
}

/// Controls for a task of the task engine, shown in the task sheet. A message goes to the agent working for it.
class TaskControls extends ConsumerWidget {
  const TaskControls({super.key, required this.task, required this.onChanged});
  final Map<String, dynamic> task;
  final VoidCallback onChanged;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final status = task['status'] as String;
    final id = task['id'] as String;
    final api = ref.read(apiProvider);
    Future<void> act(String what) async {
      await api.post('/api/tasks/$id/$what');
      await ref.read(activityProvider.notifier).reload();
      onChanged();
    }

    return AgentControlsBar(
      subject: 'Aufgabe',
      canPause: status == 'queued' || status == 'running',
      canResume: status == 'paused',
      canCancel: !const ['completed', 'failed', 'cancelled'].contains(status),
      canMessage: const ['queued', 'running', 'paused', 'waiting_external'].contains(status),
      onPause: () => act('pause'),
      onResume: () => act('resume'),
      onCancel: () => act('cancel'),
      onMessage: (text, now) => api.post('/api/tasks/$id/message', {'text': text, 'immediate': now}),
    );
  }
}
