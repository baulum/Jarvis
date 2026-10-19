import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:local_auth/local_auth.dart';

import '../../core/api.dart';
import '../../core/theme.dart';
import '../../state/activity.dart';
import '../../state/providers.dart';
import '../widgets/hud.dart';

const _taskStatus = {
  'queued': 'Wartet',
  'running': 'Läuft',
  'waiting_approval': 'Wartet auf Freigabe',
  'waiting_input': 'Braucht Angaben',
  'waiting_external': 'Wartet auf Ergebnis',
  'completed': 'Erledigt',
  'failed': 'Fehlgeschlagen',
  'cancelled': 'Abgebrochen',
};

/// One timeline of what Jarvis does and waits for. Decisions first, then work in progress, then history.
class ActivityScreen extends ConsumerStatefulWidget {
  const ActivityScreen({super.key, this.focus = const {}});
  final Map<String, String> focus;
  @override
  ConsumerState<ActivityScreen> createState() => _ActivityScreenState();
}

class _ActivityScreenState extends ConsumerState<ActivityScreen> {
  bool _focused = false;

  void _applyFocus(ActivityData d) {
    if (_focused) return;
    _focused = true;
    final f = widget.focus;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      if (f['approval'] != null) {
        final a = d.approvals.where((x) => x['id'] == f['approval']).firstOrNull;
        if (a != null) openApproval(context, ref, a, decide: f['decide']);
      } else if (f['task'] != null) {
        final t = d.tasks.where((x) => x['id'] == f['task']).firstOrNull;
        if (t != null) _openTask(context, t);
      } else if (f['call'] != null) {
        final c = d.calls.where((x) => x['id'] == f['call']).firstOrNull;
        if (c != null) _openCall(context, c);
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final data = ref.watch(activityProvider);
    return Scaffold(
      backgroundColor: J.void_,
      body: SafeArea(
        child: Column(children: [
          _Header(title: 'Aktivität', trailing: Pressable(
            onTap: () => context.push('/settings'),
            semantics: 'Einstellungen',
            child: const SizedBox.square(dimension: J.hit, child: Center(child: Glyph(GlyphKind.settings, size: 20, color: J.text2))),
          )),
          const Hairline(),
          Expanded(
            child: data.when(
              loading: () => const Center(child: SizedBox.square(dimension: 18, child: CircularProgressIndicator(strokeWidth: 1.4, color: J.cyan))),
              error: (e, _) => _Offline(error: e, onRetry: () => ref.invalidate(activityProvider)),
              data: (d) {
                _applyFocus(d);
                return RefreshIndicator(
                  color: J.cyan,
                  backgroundColor: J.panel,
                  onRefresh: () => ref.read(activityProvider.notifier).reload(),
                  child: ListView(padding: const EdgeInsets.only(bottom: J.s7), children: _sections(context, d)),
                );
              },
            ),
          ),
        ]),
      ),
    );
  }

  List<Widget> _sections(BuildContext context, ActivityData d) {
    final waiting = d.tasks.where((t) => t['status'] == 'waiting_input').toList();
    final running = d.tasks.where((t) => ['queued', 'running', 'waiting_approval', 'waiting_external'].contains(t['status'])).toList();
    final history = <({DateTime at, Widget w})>[
      for (final a in d.alerts) (at: DateTime.parse(a['created_at'] as String), w: _AlertRow(a: a)),
      for (final c in d.calls) (at: DateTime.parse(c['created_at'] as String), w: _CallRow(c: c, onTap: () => _openCall(context, c))),
      for (final t in d.tasks.where((t) => ['completed', 'failed', 'cancelled'].contains(t['status'])).take(15))
        (at: DateTime.parse(t['updated_at'] as String), w: _TaskRow(t: t, onTap: () => _openTask(context, t))),
    ]..sort((a, b) => b.at.compareTo(a.at));

    return [
      if (d.approvals.isNotEmpty || waiting.isNotEmpty) ...[
        const _Section('Wartet auf Sie', color: J.amber),
        for (final a in d.approvals) _ApprovalRow(a: a, onTap: () => openApproval(context, ref, a)),
        for (final t in waiting) _TaskRow(t: t, onTap: () => _openTask(context, t)),
      ],
      if (running.isNotEmpty || d.watches.isNotEmpty) ...[
        const _Section('Läuft'),
        for (final t in running) _TaskRow(t: t, onTap: () => _openTask(context, t)),
        for (final w in d.watches) _WatchRow(w: w),
      ],
      const _Section('Verlauf'),
      if (history.isEmpty)
        Padding(padding: const EdgeInsets.all(J.s5), child: Text('Noch nichts geschehen.', style: J.bodyDim)),
      for (final h in history.take(60)) h.w,
    ];
  }

  void _openTask(BuildContext context, Map<String, dynamic> t) {
    showHudSheet(context, builder: (ctx) => ConstrainedBox(
          constraints: BoxConstraints(maxHeight: MediaQuery.sizeOf(ctx).height * 0.8),
          child: ListView(shrinkWrap: true, padding: const EdgeInsets.fromLTRB(J.s5, J.s5, J.s5, J.s6), children: [
            Caps(_taskStatus[t['status']] ?? t['status'].toString(), color: _statusColor(t['status'] as String)),
            const SizedBox(height: J.s2),
            Text(t['title'] as String, style: J.title),
            if (t['question'] != null) ...[
              const SizedBox(height: J.s4),
              Text(t['question'].toString(), style: J.body.copyWith(color: J.amber)),
              const SizedBox(height: J.s2),
              Text('Antworten Sie Jarvis im Gespräch – er setzt die Aufgabe fort.', style: J.bodyDim),
            ],
            if (t['error'] != null) ...[
              const SizedBox(height: J.s3),
              Text(t['error'].toString(), style: J.mono(12, color: J.red)),
            ],
            const SizedBox(height: J.s5),
            const Caps('Schritte'),
            const SizedBox(height: J.s2),
            for (final s in (t['steps'] as List).cast<Map>())
              Padding(
                padding: const EdgeInsets.symmetric(vertical: J.s2),
                child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Padding(
                    padding: const EdgeInsets.only(top: 7),
                    child: Container(width: 6, height: 6, decoration: BoxDecoration(shape: BoxShape.circle, color: _statusColor(s['status'] as String))),
                  ),
                  const SizedBox(width: J.s3),
                  Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text(s['name'].toString(), style: J.body),
                      Text(s['status'].toString(), style: J.mono(10.5, color: J.text3)),
                      if (s['error'] != null) Text(s['error'].toString(), style: J.mono(11, color: J.red)),
                    ]),
                  ),
                ]),
              ),
          ]),
        ));
  }

  void _openCall(BuildContext context, Map<String, dynamic> c) {
    final transcript = ((c['transcript'] as List?) ?? const []).cast<Map>();
    showHudSheet(context, builder: (ctx) => ConstrainedBox(
          constraints: BoxConstraints(maxHeight: MediaQuery.sizeOf(ctx).height * 0.8),
          child: ListView(shrinkWrap: true, padding: const EdgeInsets.fromLTRB(J.s5, J.s5, J.s5, J.s6), children: [
            Caps(_callTitle(c), color: c['status'] == 'missed' ? J.red : J.text3),
            const SizedBox(height: J.s2),
            Text((c['message'] as String?)?.isNotEmpty == true ? c['message'] as String : 'Gespräch mit J.A.R.V.I.S.', style: J.title),
            const SizedBox(height: J.s2),
            Text(relDay(DateTime.parse(c['created_at'] as String)) + (c['duration_s'] != null ? ' · ${c['duration_s']} s' : ''), style: J.mono(11.5)),
            const SizedBox(height: J.s5),
            if (transcript.isEmpty) Text('Kein Transkript.', style: J.bodyDim),
            for (final l in transcript)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: J.s1),
                child: Text.rich(TextSpan(children: [
                  TextSpan(text: l['role'] == 'user' ? 'SIE  ' : 'JARVIS  ', style: J.mono(10.5, color: l['role'] == 'user' ? J.text3 : J.cyan)),
                  TextSpan(text: l['text'].toString(), style: J.bodyDim),
                ])),
              ),
            if (c['status'] == 'missed') ...[
              const SizedBox(height: J.s4),
              LightAction('Zurückrufen', onTap: () {
                Navigator.pop(ctx);
                ref.read(nativeProvider).startCall();
                context.push('/call');
              }),
            ],
          ]),
        ));
  }
}

String _callTitle(Map<String, dynamic> c) {
  final dir = c['direction'] == 'to_owner' ? 'Jarvis hat angerufen' : 'Sie haben angerufen';
  return switch (c['status']) {
    'missed' => 'Verpasster Anruf',
    'declined' => 'Abgelehnt',
    'failed' => 'Fehlgeschlagen',
    _ => dir,
  };
}

Color _statusColor(String s) => switch (s) {
      'completed' || 'ok' => J.green,
      'failed' || 'error' => J.red,
      'running' => J.cyan,
      'skipped' || 'cancelled' => J.text3,
      _ => J.amber,
    };

/// Approve/reject: biometric confirmation on the phone, then a Keystore-signed request; the core checks
/// signature, nonce and timestamp and keeps critical actions desktop-only.
Future<void> openApproval(BuildContext context, WidgetRef ref, Map<String, dynamic> a, {String? decide}) {
  return showHudSheet(context, builder: (ctx) => _ApprovalSheet(a: a, preselect: decide));
}

class _ApprovalSheet extends ConsumerStatefulWidget {
  const _ApprovalSheet({required this.a, this.preselect});
  final Map<String, dynamic> a;
  final String? preselect;
  @override
  ConsumerState<_ApprovalSheet> createState() => _ApprovalSheetState();
}

class _ApprovalSheetState extends ConsumerState<_ApprovalSheet> {
  String? _busy;
  String? _error;
  String? _done;

  Future<void> _decide(bool approve) async {
    setState(() {
      _busy = approve ? 'approve' : 'reject';
      _error = null;
    });
    try {
      if (approve) {
        final auth = LocalAuthentication();
        final ok = await auth.authenticate(
          localizedReason: 'Freigabe bestätigen: ${widget.a['summary']}',
          biometricOnly: false,
          persistAcrossBackgrounding: true,
        );
        if (!ok) {
          setState(() => _busy = null);
          return;
        }
      }
      final id = widget.a['id'] as String;
      final res = await ref.read(apiProvider).signedPost('/api/mobile/approvals/$id', 'approval:$id', {'approve': approve}) as Map;
      HapticFeedback.mediumImpact();
      ref.read(activityProvider.notifier).reload();
      setState(() {
        _busy = null;
        _done = approve ? (res['status'] == 'executed' ? 'Freigegeben und ausgeführt.' : 'Freigegeben (${res['status']}).') : 'Abgelehnt.';
      });
    } on ApiError catch (e) {
      setState(() {
        _busy = null;
        _error = e.message;
      });
    } catch (e) {
      setState(() {
        _busy = null;
        _error = 'Bestätigung nicht möglich: $e';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final a = widget.a;
    final critical = a['risk'] == 'critical';
    return Padding(
      padding: const EdgeInsets.fromLTRB(J.s5, J.s5, J.s5, J.s6),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
        Caps('Freigabe · Risiko ${a['risk']}', color: J.amber),
        const SizedBox(height: J.s3),
        Text(a['summary'].toString(), style: J.title),
        const SizedBox(height: J.s3),
        Text(a['reason']?.toString() ?? '', style: J.bodyDim),
        const SizedBox(height: J.s2),
        Text('${a['tool']} · angefragt über ${a['channel']} · ${relDay(DateTime.parse(a['created_at'] as String))}', style: J.mono(11)),
        if (_argLines(a['args']).isNotEmpty) ...[
          const SizedBox(height: J.s3),
          // what exactly will run – not only the one-line summary
          Container(
            constraints: const BoxConstraints(maxHeight: 170),
            width: double.infinity,
            padding: const EdgeInsets.all(J.s3),
            decoration: BoxDecoration(color: J.panel, border: Border.all(color: J.line), borderRadius: BorderRadius.circular(J.radius)),
            child: SingleChildScrollView(
              child: Text(_argLines(a['args']), style: J.mono(11.5, color: J.text2)),
            ),
          ),
        ],
        const SizedBox(height: J.s5),
        if (_done != null)
          Text(_done!, style: J.body.copyWith(color: J.green))
        else if (critical)
          Text('Kritische Aktionen (Geld, Löschungen, endgültige Rechnungen) gibt Jarvis nur am gepaarten Mac frei.',
              style: J.bodyDim.copyWith(color: J.amber))
        else
          Row(children: [
            Expanded(child: LightAction('Ablehnen', color: J.red, busy: _busy == 'reject', onTap: () => _decide(false))),
            Expanded(
              child: Align(
                alignment: Alignment.centerRight,
                child: LightAction(widget.preselect == 'approve' ? 'Jetzt genehmigen' : 'Genehmigen', busy: _busy == 'approve', onTap: () => _decide(true)),
              ),
            ),
          ]),
        if (_error != null) Padding(padding: const EdgeInsets.only(top: J.s3), child: Text(_error!, style: J.mono(12, color: J.red))),
      ]),
    );
  }
}

/// `key: value` lines of an approval's arguments, long values shortened.
String _argLines(Object? args) {
  if (args is! Map || args.isEmpty) return '';
  return args.entries.map((e) {
    final v = e.value is String ? e.value as String : jsonEncode(e.value);
    return '${e.key}: ${v.length > 400 ? '${v.substring(0, 400)}…' : v}';
  }).join('\n');
}

// ------------------------------------------------------------------ rows
class _Header extends StatelessWidget {
  const _Header({required this.title, this.trailing});
  final String title;
  final Widget? trailing;
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(J.s2, J.s2, J.s2, J.s2),
        child: Row(children: [
          Pressable(
            onTap: () => context.canPop() ? context.pop() : context.go('/'),
            semantics: 'Zurück',
            child: const SizedBox.square(dimension: J.hit, child: Center(child: Glyph(GlyphKind.back))),
          ),
          Expanded(child: Text(title, style: J.title)),
          ?trailing,
        ]),
      );
}

class _Section extends StatelessWidget {
  const _Section(this.text, {this.color});
  final String text;
  final Color? color;
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(J.s5, J.s6, J.s5, J.s2),
        child: Caps(text, color: color),
      );
}

class _Row extends StatelessWidget {
  const _Row({required this.dot, required this.title, required this.meta, this.sub, this.onTap});
  final Color dot;
  final String title;
  final String meta;
  final String? sub;
  final VoidCallback? onTap;
  @override
  Widget build(BuildContext context) => Pressable(
        onTap: onTap,
        haptic: onTap != null,
        child: Container(
          padding: const EdgeInsets.fromLTRB(J.s5, J.s3, J.s5, J.s3),
          decoration: const BoxDecoration(border: Border(bottom: BorderSide(color: J.line))),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Container(width: 6, height: 6, decoration: BoxDecoration(shape: BoxShape.circle, color: dot, boxShadow: [BoxShadow(color: dot.withValues(alpha: 0.6), blurRadius: 6)])),
            ),
            const SizedBox(width: J.s4),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(title, style: J.body, maxLines: 2, overflow: TextOverflow.ellipsis),
                if (sub != null && sub!.isNotEmpty) Text(sub!, style: J.bodyDim.copyWith(fontSize: 14), maxLines: 2, overflow: TextOverflow.ellipsis),
                const SizedBox(height: 2),
                Text(meta, style: J.mono(10.5, color: J.text3)),
              ]),
            ),
          ]),
        ),
      );
}

class _ApprovalRow extends StatelessWidget {
  const _ApprovalRow({required this.a, required this.onTap});
  final Map<String, dynamic> a;
  final VoidCallback onTap;
  @override
  Widget build(BuildContext context) => _Row(
        dot: J.amber,
        title: a['summary'].toString(),
        sub: a['reason']?.toString(),
        meta: 'FREIGABE · ${a['risk'].toString().toUpperCase()} · ${relDay(DateTime.parse(a['created_at'] as String))}',
        onTap: onTap,
      );
}

class _TaskRow extends StatelessWidget {
  const _TaskRow({required this.t, required this.onTap});
  final Map<String, dynamic> t;
  final VoidCallback onTap;
  @override
  Widget build(BuildContext context) {
    final steps = (t['steps'] as List).cast<Map>();
    final done = steps.where((s) => s['status'] == 'completed' || s['status'] == 'skipped').length;
    return _Row(
      dot: _statusColor(t['status'] as String),
      title: t['title'].toString(),
      sub: t['question']?.toString() ?? t['error']?.toString(),
      meta: 'AUFGABE · ${(_taskStatus[t['status']] ?? t['status']).toString().toUpperCase()} · $done/${steps.length} · ${relDay(DateTime.parse(t['updated_at'] as String))}',
      onTap: onTap,
    );
  }
}

class _AlertRow extends StatelessWidget {
  const _AlertRow({required this.a});
  final Map<String, dynamic> a;
  @override
  Widget build(BuildContext context) {
    final delivered = a['status'] == 'sent' || (a['status'] as String).startsWith('call');
    return _Row(
      dot: a['level'] == 'info' ? J.cyanDeep : J.amber,
      title: a['title'].toString(),
      sub: a['body']?.toString(),
      meta: '${(a['category'] as String).toUpperCase()} · ${switch (a['decision']) {
        'call' => 'ANRUF',
        'silent' => 'STILL',
        'none' => 'UNTERDRÜCKT',
        _ => 'PUSH',
      }}${delivered ? '' : ' · NICHT ZUGESTELLT'} · ${relDay(DateTime.parse(a['created_at'] as String))}',
    );
  }
}

class _CallRow extends StatelessWidget {
  const _CallRow({required this.c, required this.onTap});
  final Map<String, dynamic> c;
  final VoidCallback onTap;
  @override
  Widget build(BuildContext context) => _Row(
        dot: c['status'] == 'missed' || c['status'] == 'failed' ? J.red : J.green,
        title: _callTitle(c),
        sub: c['message']?.toString(),
        meta: 'ANRUF${c['duration_s'] != null ? ' · ${c['duration_s']} S' : ''} · ${relDay(DateTime.parse(c['created_at'] as String))}',
        onTap: onTap,
      );
}

class _WatchRow extends StatelessWidget {
  const _WatchRow({required this.w});
  final Map<String, dynamic> w;
  @override
  Widget build(BuildContext context) => _Row(
        dot: J.cyan,
        title: 'Rückruf vorgemerkt',
        sub: w['message']?.toString(),
        meta: switch (w['type']) {
          'coding_job' => 'SOBALD DER CODING-AUFTRAG FERTIG IST',
          'task' => 'SOBALD DIE AUFGABE FERTIG IST',
          _ => 'UM ${clock(DateTime.parse(w['target'] as String))}',
        },
      );
}

class _Offline extends StatelessWidget {
  const _Offline({required this.error, required this.onRetry});
  final Object error;
  final VoidCallback onRetry;
  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(J.s6),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Text(error is ApiError && (error as ApiError).offline ? 'Jarvis ist nicht erreichbar.' : '$error', style: J.bodyDim, textAlign: TextAlign.center),
            const SizedBox(height: J.s3),
            LightAction('Erneut versuchen', onTap: onRetry),
          ]),
        ),
      );
}
