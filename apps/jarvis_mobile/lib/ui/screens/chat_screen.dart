import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/theme.dart';
import '../../state/chat.dart';
import '../../state/live_model.dart';
import '../../state/providers.dart';
import '../widgets/composer.dart';
import '../widgets/core_orb.dart';
import '../widgets/hud.dart';
import '../widgets/markdown_view.dart';
import '../widgets/model_picker.dart';

/// Conversation with the same Jarvis as on the Mac. Sir's words in a tinted bubble on the right, Jarvis' answers
/// in a wide panel bubble with rendered Markdown, tool use shown as the real tool calls of that answer.
class ChatScreen extends ConsumerStatefulWidget {
  const ChatScreen({super.key, this.sessionId, this.initial});
  final String? sessionId;
  final String? initial;
  @override
  ConsumerState<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends ConsumerState<ChatScreen> {
  final _scroll = ScrollController();

  @override
  void initState() {
    super.initState();
    if (widget.sessionId != null && widget.sessionId != ref.read(chatProvider).sessionId) {
      Future.microtask(() => ref.read(chatProvider.notifier).open(widget.sessionId!));
    }
  }

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final chat = ref.watch(chatProvider);
    final mode = ref.watch(modeProvider);
    ref.listen(chatProvider, (a, b) {
      if ((a?.messages.length ?? 0) != b.messages.length || (b.messages.isNotEmpty && b.messages.last.streaming)) {
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (_scroll.hasClients) {
            _scroll.animateTo(_scroll.position.maxScrollExtent, duration: J.base, curve: J.enter);
          }
        });
      }
    });

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
                : chat.messages.isEmpty
                    ? Center(
                        child: Padding(
                          padding: const EdgeInsets.all(J.s6),
                          child: Text('Was kann ich für Sie tun, Sir?', style: J.display.copyWith(fontSize: 24, color: J.text2), textAlign: TextAlign.center),
                        ),
                      )
                    : ListView.builder(
                        controller: _scroll,
                        padding: const EdgeInsets.fromLTRB(J.s4, J.s5, J.s4, J.s5),
                        itemCount: chat.messages.length,
                        itemBuilder: (_, i) => _MessageView(m: chat.messages[i]),
                      ),
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
              onSend: (t) => ref.read(chatProvider.notifier).send(t),
              busy: chat.sending,
              onStop: () => ref.read(chatProvider.notifier).interrupt(),
            ),
          ),
        ]),
      ),
    );
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

class _MessageView extends ConsumerWidget {
  const _MessageView({required this.m});
  final Msg m;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    if (m.role == 'user') {
      // Sir: right-aligned, tinted bubble with a flat corner at the sender's side
      return Padding(
        padding: const EdgeInsets.only(bottom: J.s4, left: J.s6),
        child: Align(
          alignment: Alignment.centerRight,
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
            child: SelectableText(m.text, style: J.body.copyWith(color: J.ice)),
          ),
        ),
      );
    }
    // Jarvis: wide panel bubble with rendered Markdown
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
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        if (m.tools.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(bottom: J.s2),
            child: Wrap(spacing: J.s2, runSpacing: J.s1, children: [
              for (final t in m.tools)
                Row(mainAxisSize: MainAxisSize.min, children: [
                  Container(
                    width: 5,
                    height: 5,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: t.status == 'running' ? J.cyan : t.status == 'ok' ? J.green : J.red,
                    ),
                  ),
                  const SizedBox(width: 5),
                  Text(t.name, style: J.mono(10.5, color: J.text3)),
                ]),
            ]),
          ),
        if (m.text.isEmpty && m.streaming)
          const _Thinking()
        else
          SelectionArea(
            child: MarkdownView(m.text, onLink: (url) => _openLink(context, ref, url)),
          ),
        if (m.error != null && m.error != 'cancelled')
          Padding(padding: const EdgeInsets.only(top: J.s1), child: Text(m.error!, style: J.mono(11, color: J.amber))),
      ]),
      ),
    );
  }

  Future<void> _openLink(BuildContext context, WidgetRef ref, String url) async {
    final opened = await ref.read(nativeProvider).openUrl(url).catchError((Object _) => false);
    if (!opened && context.mounted) {
      await Clipboard.setData(ClipboardData(text: url));
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Link kopiert')));
      }
    }
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
  Widget build(BuildContext context) => AnimatedBuilder(
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
      );
}
