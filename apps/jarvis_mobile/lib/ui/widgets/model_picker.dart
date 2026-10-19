import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api.dart';
import '../../core/theme.dart';
import '../../state/agent.dart';
import 'hud.dart';

/// Small status line under the wordmark: active model and the Claude quota. Tap → model sheet.
class ModelChip extends ConsumerWidget {
  const ModelChip({super.key, this.sessionId});
  final String? sessionId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final info = ref.watch(agentInfoProvider).valueOrNull;
    if (info == null) return const SizedBox(height: 18);
    final p = info.pressure;
    final warn = p >= 0.8;
    final label = info.quotaKnown ? '${info.activeLabel.toUpperCase()} · ${(p * 100).round()} %' : info.activeLabel.toUpperCase();
    return Pressable(
      semantics: 'Modell wählen. Aktuell ${info.activeLabel}',
      onTap: () => showModelSheet(context, sessionId: sessionId),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: J.s3, vertical: 3),
        child: Text(label, style: J.caps.copyWith(color: p >= 0.95 ? J.red : warn ? J.amber : J.cyanDeep)),
      ),
    );
  }
}

/// Compact "Sonnet · Hoch ⌄" pill for the composer; opens the same sheet as [ModelChip].
class ModelEffortPill extends ConsumerWidget {
  const ModelEffortPill({super.key, this.sessionId});
  final String? sessionId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final info = ref.watch(agentInfoProvider).valueOrNull;
    return Pressable(
      semantics: 'Modell und Denkaufwand wählen',
      onTap: () => showModelSheet(context, sessionId: sessionId),
      child: Container(
        constraints: const BoxConstraints(minHeight: 32),
        padding: const EdgeInsets.symmetric(horizontal: J.s3),
        decoration: BoxDecoration(borderRadius: BorderRadius.circular(16), border: Border.all(color: J.line)),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          Text(info?.activeLabel ?? 'Modell', style: J.mono(12, color: J.text)),
          Text('  ·  ${info?.effortLabel ?? 'Auto'}', style: J.mono(12, color: J.text2)),
          const SizedBox(width: 2),
          const Icon(Icons.expand_more_rounded, size: 16, color: J.text3),
        ]),
      ),
    );
  }
}

Future<void> showModelSheet(BuildContext context, {String? sessionId}) =>
    showHudSheet(context, builder: (_) => _ModelSheet(sessionId: sessionId));

class _ModelSheet extends ConsumerStatefulWidget {
  const _ModelSheet({this.sessionId});
  final String? sessionId;
  @override
  ConsumerState<_ModelSheet> createState() => _ModelSheetState();
}

class _ModelSheetState extends ConsumerState<_ModelSheet> {
  bool _thisChat = false; // scope: only this conversation
  String? _busy;
  String? _error;

  Future<void> _pick(String? model) async {
    setState(() {
      _busy = model ?? 'auto';
      _error = null;
    });
    try {
      await ref.read(agentInfoProvider.notifier).setModel(
            model,
            scope: _thisChat && widget.sessionId != null ? 'session' : 'default',
            session: _thisChat ? widget.sessionId : null,
          );
    } on ApiError catch (e) {
      _error = e.message;
    }
    if (mounted) setState(() => _busy = null);
  }

  @override
  Widget build(BuildContext context) {
    final info = ref.watch(agentInfoProvider).valueOrNull ?? const AgentInfo();
    final hasSession = widget.sessionId != null;
    return Padding(
      padding: const EdgeInsets.fromLTRB(J.s5, J.s4, J.s5, J.s5),
      child: SingleChildScrollView(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
          const Caps('Claude-Modell'),
          const SizedBox(height: J.s3),
          _row(info, 'auto', 'Automatisch', 'Haiku für Kleinigkeiten, sonst Sonnet', selected: info.auto && info.defaultModel == 'sonnet' && info.sessionOverride == null),
          for (final m in modelAliases)
            _row(info, m, m[0].toUpperCase() + m.substring(1), modelBlurb[m]!,
                selected: info.sessionOverride == m || (info.sessionOverride == null && !(info.auto && info.defaultModel == 'sonnet') && info.defaultModel == m)),
          if (hasSession) ...[
            const SizedBox(height: J.s2),
            Pressable(
              onTap: () => setState(() => _thisChat = !_thisChat),
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: J.s2),
                child: Row(children: [
                  Icon(_thisChat ? Icons.check_box : Icons.check_box_outline_blank, size: 18, color: J.cyan),
                  const SizedBox(width: J.s2),
                  Text('Nur für dieses Gespräch', style: J.label),
                ]),
              ),
            ),
          ],
          const SizedBox(height: J.s3),
          const Hairline(),
          const SizedBox(height: J.s4),
          const Caps('Denkaufwand'),
          const SizedBox(height: J.s3),
          Wrap(spacing: J.s2, runSpacing: J.s2, children: [
            for (final e in efforts) _effortChip(info, e.$1, e.$2),
          ]),
          const SizedBox(height: J.s2),
          Text('Höher = gründlicher, aber langsamer. Gilt ab der nächsten Antwort.', style: J.mono(11, color: J.text3)),
          if (_error != null) Text(_error!, style: J.mono(12, color: J.red)),
          const SizedBox(height: J.s4),
          const Hairline(),
          const SizedBox(height: J.s4),
          const Caps('Kontingent'),
          const SizedBox(height: J.s3),
          if (!info.quotaKnown)
            Text('Noch keine Daten – erscheinen nach der nächsten Antwort.', style: J.mono(12, color: J.text3))
          else ...[
            _bar('5 Stunden', info.fiveHour),
            const SizedBox(height: J.s3),
            _bar('7 Tage', info.sevenDay),
          ],
          const SizedBox(height: J.s2),
          LightAction('Jetzt aktualisieren', busy: _busy == 'quota', onTap: () async {
            setState(() => _busy = 'quota');
            try {
              await ref.read(agentInfoProvider.notifier).refreshQuota();
            } on ApiError catch (e) {
              _error = e.message;
            }
            if (mounted) setState(() => _busy = null);
          }),
        ]),
      ),
    );
  }

  Future<void> _pickEffort(String id) async {
    setState(() {
      _busy = 'effort-$id';
      _error = null;
    });
    try {
      await ref.read(agentInfoProvider.notifier).setEffort(id);
    } on ApiError catch (e) {
      _error = e.message;
    }
    if (mounted) setState(() => _busy = null);
  }

  Widget _effortChip(AgentInfo info, String id, String label) {
    final selected = (info.effort ?? 'auto') == id;
    return Pressable(
      semantics: 'Denkaufwand $label',
      onTap: () => _pickEffort(id),
      child: Container(
        constraints: const BoxConstraints(minHeight: 40),
        padding: const EdgeInsets.symmetric(horizontal: J.s4),
        alignment: Alignment.center,
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(20),
          border: Border.all(color: selected ? J.cyan : J.line),
          color: selected ? J.cyan.withValues(alpha: 0.12) : null,
        ),
        child: Text(label, style: J.mono(12.5, color: selected ? J.cyan : J.text2)),
      ),
    );
  }

  Widget _row(AgentInfo info, String key, String title, String blurb, {required bool selected}) => Pressable(
        onTap: () => _pick(key == 'auto' ? null : key),
        semantics: '$title wählen',
        child: Container(
          constraints: const BoxConstraints(minHeight: J.hit),
          padding: const EdgeInsets.symmetric(vertical: J.s2),
          child: Row(children: [
            Container(
              width: 8,
              height: 8,
              decoration: BoxDecoration(shape: BoxShape.circle, color: selected ? J.cyan : Colors.transparent, border: Border.all(color: selected ? J.cyan : J.lineBright)),
            ),
            const SizedBox(width: J.s3),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(title, style: J.body.copyWith(color: selected ? J.ice : J.text)),
                Text(blurb, style: J.mono(11, color: J.text3)),
              ]),
            ),
            if (_busy == key || (key == 'auto' && _busy == 'auto'))
              const SizedBox.square(dimension: 14, child: CircularProgressIndicator(strokeWidth: 1.5, color: J.cyan)),
          ]),
        ),
      );

  Widget _bar(String label, QuotaWindow? w) {
    final u = (w?.utilization ?? 0).clamp(0.0, 1.0);
    final color = u >= 0.95 ? J.red : u >= 0.8 ? J.amber : J.cyan;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Text(label, style: J.label),
        const Spacer(),
        Text(w == null ? '–' : w.percent, style: J.mono(12, color: color)),
      ]),
      const SizedBox(height: 6),
      ClipRRect(
        borderRadius: BorderRadius.circular(2),
        child: LinearProgressIndicator(value: u, minHeight: 4, backgroundColor: J.line, valueColor: AlwaysStoppedAnimation(color)),
      ),
      if (w != null && w.resetLabel.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 4), child: Text(w.resetLabel, style: J.mono(11, color: J.text3))),
    ]);
  }
}
