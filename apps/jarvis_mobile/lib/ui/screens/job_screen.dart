import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/api.dart';
import '../../core/theme.dart';
import '../../state/activity.dart';
import '../../state/chat.dart';
import '../../state/providers.dart';
import '../widgets/hud.dart';
import '../widgets/markdown_view.dart';

const _jobStatus = {
  'queued': 'Wartet',
  'running': 'Läuft',
  'paused': 'Pausiert',
  'completed': 'Fertig',
  'failed': 'Fehlgeschlagen',
  'cancelled': 'Abgebrochen',
};

final jobProvider = FutureProvider.autoDispose.family<Map<String, dynamic>, String>((ref, id) async {
  final r = await ref.watch(apiProvider).get('/api/mobile/coding/jobs/$id');
  return (r as Map).cast<String, dynamic>();
});

/// Detail view of a coding job – where a tap on "Coding-Job fertig / fehlgeschlagen" lands:
/// status, result or error message, the last steps, and the actions (create PR, remove).
class JobScreen extends ConsumerStatefulWidget {
  const JobScreen({super.key, required this.jobId});
  final String jobId;
  @override
  ConsumerState<JobScreen> createState() => _JobScreenState();
}

class _JobScreenState extends ConsumerState<JobScreen> {
  String? _busy;
  String? _error;

  Future<void> _remove() async {
    setState(() {
      _busy = 'delete';
      _error = null;
    });
    try {
      await ref.read(apiProvider).post('/api/mobile/coding/jobs/${widget.jobId}/delete');
      ref.read(activityProvider.notifier).reload();
      if (mounted) context.canPop() ? context.pop() : context.go('/');
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = null);
    }
  }

  /// Creating a pull request pushes code to GitHub, so it goes through Jarvis and his approval flow
  /// (the request then shows up under "Wartet auf Sie") instead of a direct call from the phone.
  Future<void> _requestPr(Map<String, dynamic> job) async {
    setState(() => _busy = 'pr');
    final title = job['title'] ?? 'Coding-Job';
    await ref.read(chatProvider.notifier).send(
          'Erstelle für den Coding-Job „$title“ (ID ${widget.jobId}) einen Pull Request. '
          'Hole dir die Freigabe, bevor du pushst.',
        );
    if (!mounted) return;
    setState(() => _busy = null);
    context.push('/chat');
  }

  @override
  Widget build(BuildContext context) {
    final job = ref.watch(jobProvider(widget.jobId));
    return Scaffold(
      backgroundColor: J.void_,
      body: SafeArea(
        child: Column(children: [
          Padding(
            padding: const EdgeInsets.all(J.s2),
            child: Row(children: [
              Pressable(
                onTap: () => context.canPop() ? context.pop() : context.go('/'),
                semantics: 'Zurück',
                child: const SizedBox.square(dimension: J.hit, child: Center(child: Glyph(GlyphKind.back))),
              ),
              Expanded(child: Text('Coding-Job', style: J.title)),
            ]),
          ),
          const Hairline(),
          Expanded(
            child: job.when(
              loading: () => const Center(child: SizedBox.square(dimension: 18, child: CircularProgressIndicator(strokeWidth: 1.4, color: J.cyan))),
              error: (e, _) => _Missing(error: e, onRetry: () => ref.invalidate(jobProvider(widget.jobId))),
              data: _body,
            ),
          ),
        ]),
      ),
    );
  }

  Widget _body(Map<String, dynamic> j) {
    final status = j['status'] as String;
    final result = ((j['result'] as Map?) ?? const {}).cast<String, dynamic>();
    final summary = (result['summary'] ?? '').toString().trim();
    final error = (result['error'] ?? '').toString().trim();
    final tests = result['tests']?.toString();
    final steps = ((j['steps'] as List?) ?? const []).cast<Map>();
    final finished = j['finished_at'] as String?;
    return RefreshIndicator(
      color: J.cyan,
      backgroundColor: J.panel,
      onRefresh: () async => ref.invalidate(jobProvider(widget.jobId)),
      child: ListView(padding: const EdgeInsets.fromLTRB(J.s5, J.s5, J.s5, J.s7), children: [
        Caps(_jobStatus[status] ?? status, color: switch (status) { 'completed' => J.green, 'failed' => J.red, 'running' => J.cyan, _ => J.amber }),
        const SizedBox(height: J.s2),
        Text(j['title'].toString(), style: J.title),
        const SizedBox(height: J.s2),
        Text(
          [
            if (j['branch'] != null) 'Branch ${j['branch']}',
            if (j['model'] != null) j['model'].toString(),
            if (finished != null) relDay(DateTime.parse(finished)),
          ].join(' · '),
          style: J.mono(11),
        ),
        if (status == 'failed' || error.isNotEmpty) ...[
          const SizedBox(height: J.s5),
          const Caps('Fehlermeldung', color: J.red),
          const SizedBox(height: J.s2),
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(J.s3),
            decoration: BoxDecoration(color: J.panel, border: Border.all(color: J.red.withValues(alpha: 0.4)), borderRadius: BorderRadius.circular(J.radius)),
            child: SelectableText(error.isNotEmpty ? error : (summary.isNotEmpty ? summary : 'Keine Fehlermeldung gespeichert.'), style: J.mono(12, color: J.red)),
          ),
        ],
        if (summary.isNotEmpty && status != 'failed') ...[
          const SizedBox(height: J.s5),
          const Caps('Ergebnis'),
          const SizedBox(height: J.s2),
          SelectionArea(child: MarkdownView(summary)),
        ],
        if (tests != null && tests.isNotEmpty && tests != 'null') ...[
          const SizedBox(height: J.s5),
          const Caps('Tests'),
          const SizedBox(height: J.s2),
          SelectableText(tests, style: J.mono(12, color: J.text2)),
        ],
        const SizedBox(height: J.s5),
        const Caps('Schritte'),
        const SizedBox(height: J.s2),
        if (steps.isEmpty) Text('Keine Schritte aufgezeichnet.', style: J.bodyDim),
        for (final s in steps)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: J.s1 + 1),
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Padding(
                padding: const EdgeInsets.only(top: 7),
                child: Container(width: 5, height: 5, decoration: const BoxDecoration(shape: BoxShape.circle, color: J.cyanDeep)),
              ),
              const SizedBox(width: J.s3),
              Expanded(
                child: Text(
                  s['tool'] != null ? '${s['tool']}  ${s['detail'] ?? ''}' : (s['text'] ?? '').toString(),
                  style: s['tool'] != null ? J.mono(11.5, color: J.text2) : J.bodyDim.copyWith(fontSize: 14),
                  maxLines: 4,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ]),
          ),
        const SizedBox(height: J.s5),
        const Hairline(),
        const SizedBox(height: J.s3),
        Wrap(spacing: J.s4, children: [
          if (j['can_pr'] == true) LightAction('PR erstellen', busy: _busy == 'pr', onTap: () => _requestPr(j)),
          if (j['can_delete'] == true) LightAction('Entfernen', color: J.red, busy: _busy == 'delete', onTap: _remove),
          if (j['task_id'] != null) LightAction('Aufgabe', color: J.text2, onTap: () => context.push('/activity?task=${j['task_id']}')),
        ]),
        if (_error != null) Padding(padding: const EdgeInsets.only(top: J.s3), child: Text(_error!, style: J.mono(12, color: J.red))),
      ]),
    );
  }
}

class _Missing extends StatelessWidget {
  const _Missing({required this.error, required this.onRetry});
  final Object error;
  final VoidCallback onRetry;
  @override
  Widget build(BuildContext context) {
    final gone = error is ApiError && (error as ApiError).status == 404;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(J.s6),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Text(
            gone ? 'Dieser Job existiert nicht mehr (vielleicht schon entfernt).' : (error is ApiError && (error as ApiError).offline ? 'Jarvis ist nicht erreichbar.' : '$error'),
            style: J.bodyDim,
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: J.s3),
          if (!gone) LightAction('Erneut versuchen', onTap: onRetry),
          if (gone) LightAction('Zur Aktivität', onTap: () => context.go('/activity')),
        ]),
      ),
    );
  }
}
