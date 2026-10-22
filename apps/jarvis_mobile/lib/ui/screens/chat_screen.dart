import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/theme.dart';
import '../../state/chat.dart';
import '../../state/live_model.dart';
import '../../state/providers.dart';
import '../../state/timeline.dart';
import '../widgets/composer.dart';
import '../widgets/core_orb.dart';
import '../widgets/generated_media.dart';
import '../widgets/hud.dart';
import '../widgets/markdown_view.dart';
import '../widgets/model_picker.dart';
import '../widgets/route_map.dart';

/// Conversation with the same Jarvis as on the Mac, in the order things happened: Sir's words in a tinted bubble
/// on the right; Jarvis' answer as a panel with its text and tool calls interleaved, the "Gedankenprozess" on top;
/// system notes as a centred line.
class ChatScreen extends ConsumerStatefulWidget {
  const ChatScreen({super.key, this.sessionId, this.initial});
  final String? sessionId;
  final String? initial;
  @override
  ConsumerState<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends ConsumerState<ChatScreen> {
  final _scroll = ScrollController();
  bool _atEnd = true; // auto-scroll only while Sir is at the end

  @override
  void initState() {
    super.initState();
    _scroll.addListener(() {
      if (!_scroll.hasClients) return;
      final atEnd = _scroll.position.extentAfter < 48;
      if (atEnd != _atEnd) setState(() => _atEnd = atEnd);
    });
    if (widget.sessionId != null && widget.sessionId != ref.read(chatProvider).sessionId) {
      Future.microtask(() => ref.read(chatProvider.notifier).open(widget.sessionId!));
    }
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  void _toEnd({bool animate = false}) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scroll.hasClients) return;
      final max = _scroll.position.maxScrollExtent;
      if (animate) {
        _scroll.animateTo(max, duration: J.base, curve: J.enter);
      } else {
        _scroll.jumpTo(max);
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final chat = ref.watch(chatProvider);
    final mode = ref.watch(modeProvider);
    ref.listen(chatProvider, (a, b) {
      if (!identical(a?.timeline.items, b.timeline.items) && _atEnd) _toEnd();
    });
    final rows = buildRows(chat.timeline.items);

    return Scaffold(
      backgroundColor: J.void_,
      body: SafeArea(
        child: Column(children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(J.s2, J.s2, J.s3, J.s2),
            child: Row(children: [
              Pressable(
                onTap: () => context.pop(),
                semantics: 'Zurück',
                child: const SizedBox.square(dimension: J.hit, child: Center(child: Glyph(GlyphKind.back))),
              ),
              Hero(tag: 'core', child: CoreOrb(mode: mode, size: 54)),
              const SizedBox(width: J.s2),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text('J.A.R.V.I.S.', style: J.wordmark.copyWith(fontSize: 13, letterSpacing: 4)),
                  const SizedBox(height: 4),
                  Text(modeLabel[mode]!, style: J.mono(11.5, color: J.text2)),
                  Align(alignment: Alignment.centerLeft, child: ModelChip(sessionId: chat.sessionId)),
                ]),
              ),
              Pressable(
                onTap: () => _history(context),
                semantics: 'Verlauf',
                child: const SizedBox.square(dimension: J.hit, child: Center(child: Glyph(GlyphKind.chat, size: 20, color: J.text2))),
              ),
              Pressable(
                onTap: () => ref.read(chatProvider.notifier).fresh(),
                semantics: 'Neues Gespräch',
                child: const SizedBox.square(dimension: J.hit, child: Icon(Icons.add_rounded, color: J.text2)),
              ),
            ]),
          ),
          const Hairline(),
          Expanded(
            child: chat.loading
                ? const Center(child: SizedBox.square(dimension: 18, child: CircularProgressIndicator(strokeWidth: 1.4, color: J.cyan)))
                : rows.isEmpty
                    ? Center(
                        child: Padding(
                          padding: const EdgeInsets.all(J.s6),
                          child: Text('Was kann ich für Sie tun, Sir?', style: J.display.copyWith(fontSize: 24, color: J.text2), textAlign: TextAlign.center),
                        ),
                      )
                    : Stack(children: [
                        ListView.builder(
                          controller: _scroll,
                          padding: const EdgeInsets.fromLTRB(J.s4, J.s5, J.s4, J.s5),
                          itemCount: rows.length + (chat.timeline.hasMore ? 1 : 0),
                          itemBuilder: (_, i) {
                            if (chat.timeline.hasMore) {
                              if (i == 0) return _OlderButton(busy: chat.loadingOlder, onTap: () => ref.read(chatProvider.notifier).loadOlder());
                              i -= 1;
                            }
                            final row = rows[i];
                            return KeyedSubtree(
                              key: ValueKey(row.key),
                              child: switch (row) {
                                UserRow r => _UserBubble(item: r.item),
                                SystemRow r => _SystemNote(item: r.item),
                                AnswerRow r => _Answer(row: r, onLink: (url) => _openLink(context, url)),
                              },
                            );
                          },
                        ),
                        if (!_atEnd)
                          Positioned(
                            right: J.s4,
                            bottom: J.s3,
                            child: Pressable(
                              onTap: () => _toEnd(animate: true),
                              semantics: 'Zum Ende',
                              child: Container(
                                padding: const EdgeInsets.symmetric(horizontal: J.s3, vertical: J.s2),
                                decoration: BoxDecoration(color: J.panel, border: Border.all(color: J.lineBright), borderRadius: BorderRadius.circular(20)),
                                child: Row(mainAxisSize: MainAxisSize.min, children: [
                                  const Icon(Icons.arrow_downward_rounded, size: 15, color: J.text2),
                                  const SizedBox(width: 6),
                                  Text('Zum Ende', style: J.mono(11, color: J.text2)),
                                ]),
                              ),
                            ),
                          ),
                      ]),
          ),
          if (chat.error != null)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: J.s4, vertical: J.s2),
              child: Text(chat.error!, style: J.mono(12, color: J.amber)),
            ),
          Padding(
            padding: const EdgeInsets.fromLTRB(J.s4, J.s2, J.s4, J.s3),
            child: Composer(
              sessionId: chat.sessionId,
              onSend: (t) {
                setState(() => _atEnd = true);
                _toEnd();
                ref.read(chatProvider.notifier).send(t);
              },
              busy: chat.sending || chat.running,
              onStop: () => ref.read(chatProvider.notifier).interrupt(),
            ),
          ),
        ]),
      ),
    );
  }

  Future<void> _openLink(BuildContext context, String url) async {
    final opened = await ref.read(nativeProvider).openUrl(url).catchError((Object _) => false);
    if (!opened && context.mounted) {
      await Clipboard.setData(ClipboardData(text: url));
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Link kopiert')));
      }
    }
  }

  void _history(BuildContext context) {
    showHudSheet(context, builder: (ctx) => Consumer(builder: (ctx, ref, _) {
      final sessions = ref.watch(sessionsProvider);
      return ConstrainedBox(
        constraints: BoxConstraints(maxHeight: MediaQuery.sizeOf(ctx).height * 0.7),
        child: sessions.when(
          loading: () => const Padding(padding: EdgeInsets.all(J.s6), child: Center(child: CircularProgressIndicator(strokeWidth: 1.4, color: J.cyan))),
          error: (e, _) => Padding(padding: const EdgeInsets.all(J.s5), child: Text('$e', style: J.bodyDim)),
          data: (rows) => ListView(shrinkWrap: true, padding: const EdgeInsets.symmetric(vertical: J.s4), children: [
            const Padding(padding: EdgeInsets.fromLTRB(J.s5, 0, J.s5, J.s3), child: Caps('Gespräche – alle Geräte')),
            for (final s in rows)
              Pressable(
                onTap: () {
                  Navigator.pop(ctx);
                  ref.read(chatProvider.notifier).open(s['id'] as String);
                },
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: J.s5, vertical: J.s3),
                  child: Row(children: [
                    SizedBox(width: 64, child: Text(_channel(s['channel'] as String?), style: J.mono(10.5, color: J.text3))),
                    Expanded(child: Text((s['title'] as String?)?.trim().isNotEmpty == true ? s['title'] as String : 'Ohne Titel', maxLines: 1, overflow: TextOverflow.ellipsis, style: J.body)),
                    Text(relDay(DateTime.parse(s['last_activity_at'] as String)), style: J.mono(10.5, color: J.text3)),
                  ]),
                ),
              ),
          ]),
        ),
      );
    }));
  }

  String _channel(String? c) => switch (c) {
        'voice' => 'STIMME',
        'phone' => 'ANRUF',
        'mobile' => 'HANDY',
        'telegram' => 'TELEGRAM',
        _ => 'MAC',
      };
}

class _OlderButton extends StatelessWidget {
  const _OlderButton({required this.busy, required this.onTap});
  final bool busy;
  final VoidCallback onTap;
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: J.s4),
        child: Center(
          child: Pressable(
            onTap: busy ? null : onTap,
            semantics: 'Ältere Nachrichten laden',
            child: Container(
              constraints: const BoxConstraints(minHeight: 40),
              padding: const EdgeInsets.symmetric(horizontal: J.s4, vertical: J.s2),
              decoration: BoxDecoration(border: Border.all(color: J.line), borderRadius: BorderRadius.circular(20)),
              child: Text(busy ? 'Lädt …' : 'Ältere Nachrichten laden', style: J.mono(11.5, color: J.text2)),
            ),
          ),
        ),
      );
}

// ------------------------------------------------------------------ message types
class _UserBubble extends StatelessWidget {
  const _UserBubble({required this.item});
  final TItem item;
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: J.s4, left: J.s6),
        child: Align(
          alignment: Alignment.centerRight,
          child: Opacity(
            opacity: item.local ? 0.7 : 1,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: J.s4, vertical: J.s3),
              decoration: BoxDecoration(
                color: J.cyan.withValues(alpha: 0.10),
                border: Border.all(color: J.cyan.withValues(alpha: 0.32)),
                borderRadius: const BorderRadius.only(
                  topLeft: Radius.circular(16),
                  topRight: Radius.circular(16),
                  bottomLeft: Radius.circular(16),
                  bottomRight: Radius.circular(4),
                ),
              ),
              child: SelectableText(item.text, style: J.body.copyWith(color: J.ice)),
            ),
          ),
        ),
      );
}

class _SystemNote extends StatelessWidget {
  const _SystemNote({required this.item});
  final TItem item;
  @override
  Widget build(BuildContext context) {
    final error = item.data['level'] == 'error';
    final color = error ? J.red : J.amber;
    return Padding(
      padding: const EdgeInsets.only(bottom: J.s4),
      child: Semantics(
        liveRegion: true,
        child: Row(children: [
          const Expanded(child: Divider(height: 1, color: J.line)),
          const SizedBox(width: J.s2),
          Icon(error ? Icons.error_outline_rounded : Icons.do_not_disturb_on_outlined, size: 14, color: color),
          const SizedBox(width: 6),
          Flexible(flex: 4, child: Text(item.text, textAlign: TextAlign.center, style: J.mono(11.5, color: color, spacing: 0))),
          const SizedBox(width: J.s2),
          const Expanded(child: Divider(height: 1, color: J.line)),
        ]),
      ),
    );
  }
}

// ------------------------------------------------------------------ Jarvis' answer
class _Answer extends ConsumerWidget {
  const _Answer({required this.row, required this.onLink});
  final AnswerRow row;
  final void Function(String url) onLink;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final running = row.running;
    final hasText = row.items.any((i) => i.isMessage && i.text.isNotEmpty);
    final spans = row.tools.length > 1 ? toolSpans(row.tools, DateTime.now().millisecondsSinceEpoch) : const <String, ToolSpan>{};
    final children = <Widget>[
      if (row.process.isNotEmpty || running) _ThoughtProcess(process: row.process, running: running),
      for (final item in row.items)
        if (item.isTool)
          _ToolCall(key: ValueKey(item.key), item: item, span: spans[item.id], onLink: onLink)
        else if (item.text.isNotEmpty)
          SelectionArea(
            key: ValueKey(item.key),
            child: MarkdownView(
              item.text,
              onLink: onLink,
              checks: item.checks,
              onToggleTask: item.local ? null : (index, checked) => ref.read(chatProvider.notifier).toggleCheck(item.id, index, checked),
            ),
          ),
      if (running && !hasText && row.tools.every((t) => t.status != 'running')) const _Thinking(),
    ];
    return Padding(
      padding: const EdgeInsets.only(bottom: J.s4, right: J.s2),
      child: Container(
        padding: const EdgeInsets.fromLTRB(J.s4, J.s3, J.s4, J.s4),
        decoration: const BoxDecoration(
          color: J.panel,
          border: Border.fromBorderSide(BorderSide(color: J.line)),
          borderRadius: BorderRadius.only(
            topLeft: Radius.circular(4),
            topRight: Radius.circular(16),
            bottomLeft: Radius.circular(16),
            bottomRight: Radius.circular(16),
          ),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          for (var i = 0; i < children.length; i++) Padding(padding: EdgeInsets.only(top: i == 0 ? 0 : J.s3), child: children[i]),
        ]),
      ),
    );
  }
}

String _ms(int ms) => ms >= 1000 ? '${(ms / 1000).toStringAsFixed(1).replaceAll('.', ',')} s' : '$ms ms';

String _clock(int? ms) {
  if (ms == null) return '–';
  final t = DateTime.fromMillisecondsSinceEpoch(ms);
  String two(int v) => v.toString().padLeft(2, '0');
  return '${two(t.hour)}:${two(t.minute)}:${two(t.second)},${(ms % 1000).toString().padLeft(3, '0')}';
}

// ------------------------------------------------------------------ tool call
class _ToolCall extends StatefulWidget {
  const _ToolCall({super.key, required this.item, required this.span, required this.onLink});
  final TItem item;
  final ToolSpan? span;
  final void Function(String url) onLink;
  @override
  State<_ToolCall> createState() => _ToolCallState();
}

class _ToolCallState extends State<_ToolCall> {
  bool _open = false;
  Timer? _tick;

  @override
  void initState() {
    super.initState();
    _syncTick();
  }

  @override
  void didUpdateWidget(_ToolCall old) {
    super.didUpdateWidget(old);
    _syncTick();
  }

  void _syncTick() {
    // live duration while the call runs
    if (widget.item.status == 'running') {
      _tick ??= Timer.periodic(const Duration(milliseconds: 250), (_) => setState(() {}));
    } else {
      _tick?.cancel();
      _tick = null;
    }
  }

  @override
  void dispose() {
    _tick?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final d = widget.item.data;
    final status = widget.item.status;
    final started = widget.item.intField('started_ts');
    final duration = widget.item.intField('duration_ms') ??
        (status == 'running' && started != null ? DateTime.now().millisecondsSinceEpoch - started : null);
    final detail = d['legacy'] != true && (d['input'] != null || d['result'] != null || d['error'] != null || started != null);
    final color = status == 'failed' ? J.red : (status == 'running' ? J.cyan : J.green);
    final label = switch (status) { 'running' => 'läuft', 'failed' => 'fehlgeschlagen', _ => 'erfolgreich' };
    final display = d['display'];
    final route = display is Map && display['kind'] == 'route' && status == 'ok' ? display.cast<String, dynamic>() : null;
    final media = status == 'ok' ? mediaDisplay(display) : null;
    final span = widget.span;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Container(
        decoration: BoxDecoration(
          border: Border.all(color: status == 'failed' ? J.red.withValues(alpha: 0.35) : (status == 'running' ? J.cyanDeep : J.line)),
          borderRadius: BorderRadius.circular(J.radius),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Semantics(
            button: detail,
            label: '${widget.item.name}, $label',
            child: GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: detail ? () => setState(() => _open = !_open) : null,
              child: ConstrainedBox(
                constraints: const BoxConstraints(minHeight: 44),
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: J.s3),
                  child: Row(children: [
                    Container(width: 6, height: 6, decoration: BoxDecoration(shape: BoxShape.circle, color: color)),
                    const SizedBox(width: J.s2),
                    Flexible(child: Text(widget.item.name, maxLines: 1, overflow: TextOverflow.ellipsis, style: J.mono(12, color: J.text, spacing: 0))),
                    if (span != null && span.parallel) ...[
                      const SizedBox(width: J.s2),
                      Text('parallel', style: J.mono(10, color: J.text3, spacing: 0)),
                    ],
                    const Spacer(),
                    if (span != null) _Track(span: span, color: color),
                    if (duration != null) ...[
                      const SizedBox(width: J.s2),
                      Text(_ms(duration), style: J.mono(11, color: J.text3, spacing: 0)),
                    ],
                    if (detail) ...[
                      const SizedBox(width: 4),
                      AnimatedRotation(
                        turns: _open ? 0.25 : 0,
                        duration: J.quick,
                        child: const Icon(Icons.chevron_right_rounded, size: 18, color: J.text3),
                      ),
                    ],
                  ]),
                ),
              ),
            ),
          ),
          if (status == 'failed' && d['error'] != null && !_open)
            Padding(
              padding: const EdgeInsets.fromLTRB(J.s3, 0, J.s3, J.s2),
              child: Text(d['error'].toString(), style: J.mono(11.5, color: J.red, spacing: 0)),
            ),
          AnimatedSize(
            duration: J.quick,
            alignment: Alignment.topCenter,
            child: _open
                ? Padding(
                    padding: const EdgeInsets.fromLTRB(J.s3, 0, J.s3, J.s3),
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      const Hairline(),
                      const SizedBox(height: J.s2),
                      _kv('Start', _clock(started)),
                      _kv('Ende', d['ended_ts'] == null ? 'läuft' : _clock(widget.item.intField('ended_ts'))),
                      if (d['tool_use_id'] != null) _kv('Aufruf-ID', d['tool_use_id'].toString()),
                      if (d['input'] != null) _payload('Eingabe', d['input'].toString()),
                      if (d['error'] != null) _payload('Fehler', d['error'].toString(), color: J.red),
                      if (d['result'] != null) _payload('Ergebnis', d['result'].toString()),
                      if (d['redacted'] == true)
                        Padding(padding: const EdgeInsets.only(top: J.s2), child: Text('Ergebnis auf diesem Gerät ausgeblendet.', style: J.mono(11, color: J.text3, spacing: 0))),
                    ]),
                  )
                : const SizedBox(width: double.infinity),
          ),
        ]),
      ),
      if (route != null) ...[
        const SizedBox(height: J.s2),
        RouteMap(display: route, onOpen: widget.onLink),
      ],
      if (media != null) ...[
        const SizedBox(height: J.s2),
        GeneratedMedia(display: media),
      ],
    ]);
  }

  Widget _kv(String k, String v) => Padding(
        padding: const EdgeInsets.only(bottom: 2),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          SizedBox(width: 76, child: Text(k, style: J.mono(11, color: J.text3, spacing: 0))),
          Expanded(child: Text(v, style: J.mono(11, color: J.text2, spacing: 0))),
        ]),
      );

  Widget _payload(String label, String text, {Color color = J.text2}) => Padding(
        padding: const EdgeInsets.only(top: J.s2),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(label, style: J.mono(11, color: J.text3, spacing: 0)),
          const SizedBox(height: 4),
          Container(
            width: double.infinity,
            constraints: const BoxConstraints(maxHeight: 220),
            padding: const EdgeInsets.all(J.s2),
            decoration: BoxDecoration(color: J.void_, border: Border.all(color: J.line), borderRadius: BorderRadius.circular(J.radius)),
            child: SingleChildScrollView(child: SelectableText(text, style: J.mono(11.5, color: color, spacing: 0).copyWith(height: 1.45))),
          ),
        ]),
      );
}

/// Position of a call on the shared time axis of the answer: overlapping bars = parallel calls.
class _Track extends StatelessWidget {
  const _Track({required this.span, required this.color});
  final ToolSpan span;
  final Color color;
  @override
  Widget build(BuildContext context) => SizedBox(
        width: 64,
        height: 3,
        child: LayoutBuilder(
          builder: (_, c) => Stack(children: [
            Positioned.fill(child: DecoratedBox(decoration: BoxDecoration(color: J.line, borderRadius: BorderRadius.circular(2)))),
            Positioned(
              left: span.offset * c.maxWidth,
              width: (span.width * c.maxWidth).clamp(2.0, c.maxWidth),
              top: 0,
              bottom: 0,
              child: DecoratedBox(decoration: BoxDecoration(color: color.withValues(alpha: 0.8), borderRadius: BorderRadius.circular(2))),
            ),
          ]),
        ),
      );
}

// ------------------------------------------------------------------ Gedankenprozess
const _stepIcon = <String, IconData>{
  'reasoning': Icons.lightbulb_outline_rounded,
  'model': Icons.memory_rounded,
  'memory': Icons.storage_rounded,
  'search': Icons.search_rounded,
  'tool': Icons.build_outlined,
  'plan': Icons.checklist_rounded,
  'action': Icons.shield_outlined,
  'summary': Icons.assignment_outlined,
};

class _ThoughtProcess extends StatefulWidget {
  const _ThoughtProcess({required this.process, required this.running});
  final List<TItem> process;
  final bool running;
  @override
  State<_ThoughtProcess> createState() => _ThoughtProcessState();
}

class _ThoughtProcessState extends State<_ThoughtProcess> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    final live = widget.running ? currentStep(widget.process) : null;
    final steps = widget.process.where((p) => p.step != 'summary').length;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Semantics(
        button: true,
        label: _open ? 'Gedankenprozess ausblenden' : 'Gedankenprozess anzeigen',
        child: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTap: () => setState(() => _open = !_open),
          child: ConstrainedBox(
            constraints: const BoxConstraints(minHeight: 36),
            child: Row(children: [
              AnimatedRotation(turns: _open ? 0.25 : 0, duration: J.quick, child: const Icon(Icons.chevron_right_rounded, size: 18, color: J.text3)),
              const SizedBox(width: 4),
              Icon(Icons.psychology_outlined, size: 16, color: widget.running ? J.cyan : J.text3),
              const SizedBox(width: 6),
              Text('Gedankenprozess', style: J.mono(11.5, color: J.text2, spacing: 0)),
              if (steps > 0) ...[
                const SizedBox(width: J.s2),
                Text('$steps Schritt${steps == 1 ? '' : 'e'}', style: J.mono(11, color: J.text3, spacing: 0)),
              ],
              if (live != null) ...[
                const SizedBox(width: J.s2),
                Container(width: 6, height: 6, decoration: const BoxDecoration(shape: BoxShape.circle, color: J.cyan)),
                const SizedBox(width: 6),
                Expanded(child: Text(live.data['title']?.toString() ?? '', maxLines: 1, overflow: TextOverflow.ellipsis, style: J.mono(11, color: J.text3, spacing: 0))),
              ],
            ]),
          ),
        ),
      ),
      AnimatedSize(
        duration: J.quick,
        alignment: Alignment.topCenter,
        child: !_open
            ? const SizedBox(width: double.infinity)
            : Container(
                margin: const EdgeInsets.only(top: J.s2),
                padding: const EdgeInsets.all(J.s3),
                decoration: BoxDecoration(color: J.void_.withValues(alpha: 0.5), border: Border.all(color: J.line), borderRadius: BorderRadius.circular(J.radius)),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  if (!hasModelReasoning(widget.process))
                    Padding(
                      padding: const EdgeInsets.only(bottom: J.s3),
                      child: Text(
                        'Für diese Antwort hat das Modell keine Gedanken übermittelt. Zu sehen sind nur protokollierte Schritte.',
                        style: J.mono(11, color: J.text3, spacing: 0).copyWith(height: 1.4),
                      ),
                    ),
                  if (widget.process.isEmpty) Text('Noch keine Schritte.', style: J.mono(11, color: J.text3, spacing: 0)),
                  for (final p in widget.process) _entry(p),
                ]),
              ),
      ),
    ]);
  }

  Widget _entry(TItem p) {
    final d = p.data;
    final failed = p.status == 'failed';
    final items = ((d['items'] as List?) ?? const []).map((e) => e.toString()).toList();
    return Padding(
      padding: const EdgeInsets.only(bottom: J.s3),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Padding(
          padding: const EdgeInsets.only(top: 1),
          child: Icon(_stepIcon[p.step] ?? Icons.build_outlined, size: 15, color: p.source == 'model' ? J.cyan : (failed ? J.red : J.text3)),
        ),
        const SizedBox(width: J.s2),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Wrap(spacing: J.s2, runSpacing: 4, crossAxisAlignment: WrapCrossAlignment.center, children: [
              Text(d['title']?.toString() ?? '', style: J.bodyDim.copyWith(fontSize: 14, color: failed ? J.red : J.text)),
              _SourceTag(source: p.source),
              if (p.status == 'waiting') Text('wartet', style: J.mono(10.5, color: J.amber, spacing: 0)),
            ]),
            if (p.text.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: SelectableText(
                  p.text,
                  style: J.bodyDim.copyWith(fontSize: 13.5, fontStyle: p.step == 'reasoning' ? FontStyle.italic : FontStyle.normal),
                ),
              ),
            for (final i in items) Padding(padding: const EdgeInsets.only(top: 2, left: J.s2), child: Text('• $i', style: J.bodyDim.copyWith(fontSize: 13))),
            if (d['result'] != null)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text('Zwischenergebnis: ${d['result']}', maxLines: 2, overflow: TextOverflow.ellipsis, style: J.mono(11, color: J.text3, spacing: 0)),
              ),
            if (d['redacted'] == true)
              Padding(padding: const EdgeInsets.only(top: 4), child: Text('Details auf diesem Gerät ausgeblendet.', style: J.mono(11, color: J.text3, spacing: 0))),
          ]),
        ),
      ]),
    );
  }
}

class _SourceTag extends StatelessWidget {
  const _SourceTag({required this.source});
  final String source;
  @override
  Widget build(BuildContext context) {
    final model = source == 'model';
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
      decoration: BoxDecoration(
        border: Border.all(color: model ? J.cyanDeep : J.lineBright),
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(sourceLabel[source] ?? source, style: J.mono(9.5, color: model ? J.cyan : J.text2, spacing: 0)),
    );
  }
}

class _Thinking extends StatefulWidget {
  const _Thinking();
  @override
  State<_Thinking> createState() => _ThinkingState();
}

class _ThinkingState extends State<_Thinking> with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(vsync: this, duration: const Duration(milliseconds: 1400))..repeat();
  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => SizedBox(
        height: 22,
        child: AnimatedBuilder(
          animation: _c,
          builder: (_, _) => Row(mainAxisSize: MainAxisSize.min, children: [
            for (var i = 0; i < 3; i++)
              Container(
                margin: const EdgeInsets.only(right: 6),
                width: 18,
                height: 1.5,
                color: J.cyan.withValues(alpha: (0.25 + 0.75 * (1 - ((_c.value * 3 - i) % 3).abs().clamp(0, 1))).toDouble()),
              ),
          ]),
        ),
      );
}
