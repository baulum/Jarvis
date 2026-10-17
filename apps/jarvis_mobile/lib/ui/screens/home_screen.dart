import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/live.dart';
import '../../core/theme.dart';
import '../../state/chat.dart';
import '../../state/live_model.dart';
import '../../state/providers.dart';
import '../widgets/composer.dart';
import '../widgets/core_orb.dart';
import '../widgets/hud.dart';

/// The core, alone in the dark. Everything else is one gesture away: memory ←, activity →, conversation ↑.
class HomeScreen extends ConsumerStatefulWidget {
  const HomeScreen({super.key});
  @override
  ConsumerState<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends ConsumerState<HomeScreen> {
  LevelOf? _level;

  @override
  void dispose() {
    _level?.dispose();
    super.dispose();
  }

  void _send(String text) {
    final chat = ref.read(chatProvider.notifier);
    if (ref.read(chatProvider).sessionId == null) chat.fresh();
    chat.send(text);
    context.push('/chat');
  }

  @override
  Widget build(BuildContext context) {
    final mode = ref.watch(modeProvider);
    final live = ref.watch(liveProvider);
    final link = ref.watch(linkProvider).valueOrNull ?? Link.connecting;
    final call = ref.watch(callProvider);
    _level ??= LevelOf(ref.read(levelsProvider), mic: true, boost: 1.2);
    final size = MediaQuery.sizeOf(context);
    final orb = (size.width * 1.05).clamp(200.0, 460.0);
    final latest = live.lines.isEmpty ? null : live.lines.first;

    return Scaffold(
      backgroundColor: J.void_,
      resizeToAvoidBottomInset: true,
      body: GestureDetector(
        behavior: HitTestBehavior.translucent,
        onVerticalDragEnd: (d) {
          if ((d.primaryVelocity ?? 0) < -500) context.push('/chat');
        },
        onHorizontalDragEnd: (d) {
          final v = d.primaryVelocity ?? 0;
          if (v > 600) context.push('/memory');
          if (v < -600) context.push('/activity');
        },
        child: Stack(children: [
          const Positioned.fill(child: _Backdrop()),
          SafeArea(
            child: Column(children: [
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: J.s3, vertical: J.s2),
                child: Row(children: [
                  RingButton(
                    size: 44,
                    semantics: 'Gedächtnis',
                    onTap: () => context.push('/memory'),
                    child: const Glyph(GlyphKind.memory, size: 20),
                  ),
                  Expanded(
                    child: Column(children: [
                      Text('J.A.R.V.I.S.', style: J.wordmark),
                      const SizedBox(height: 6),
                      _LinkLine(link: link),
                    ]),
                  ),
                  RingButton(
                    size: 44,
                    semantics: live.pendingApprovals > 0 ? 'Aktivität – ${live.pendingApprovals} Freigaben warten' : 'Aktivität',
                    onTap: () => context.push('/activity'),
                    child: Glyph(GlyphKind.activity, size: 20, badge: live.pendingApprovals > 0),
                  ),
                ]),
              ),
              Expanded(
                child: LayoutBuilder(builder: (context, box) => Column(mainAxisAlignment: MainAxisAlignment.center, children: [
                  Hero(
                    tag: 'core',
                    child: CoreOrb(
                      mode: mode,
                      level: _level,
                      size: orb.clamp(120.0, (box.maxHeight - (live.pendingApprovals > 0 ? 150 : 100)).clamp(120.0, 1000.0)),
                      warn: live.pendingApprovals > 0,
                      ringing: call.status == 'ringing',
                    ),
                  ),
                  AnimatedSwitcher(
                    duration: J.slow,
                    child: Text(modeLabel[mode]!, key: ValueKey(mode), style: J.display.copyWith(fontSize: 24)),
                  ),
                  const SizedBox(height: J.s3),
                  SizedBox(
                    height: 40,
                    child: Padding(
                      padding: const EdgeInsets.symmetric(horizontal: J.s6),
                      child: AnimatedSwitcher(
                        duration: J.base,
                        child: Text(
                          mode == Mode.offline
                              ? 'Der Mac schläft oder ist offline. Ich melde mich, sobald ich wieder da bin.'
                              : latest == null
                                  ? ' '
                                  : '${clock(latest.at)}  ${latest.text}',
                          key: ValueKey(latest?.at ?? mode),
                          textAlign: TextAlign.center,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: J.mono(12,
                              color: latest?.tone == Tone.bad
                                  ? J.red
                                  : latest?.tone == Tone.warn
                                      ? J.amber
                                      : J.text2),
                        ),
                      ),
                    ),
                  ),
                  if (live.pendingApprovals > 0) ...[
                    const SizedBox(height: J.s3),
                    Pressable(
                      onTap: () => context.push('/activity'),
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: J.s4, vertical: J.s2),
                        decoration: BoxDecoration(
                          borderRadius: BorderRadius.circular(20),
                          border: Border.all(color: J.amber.withValues(alpha: 0.6)),
                          color: J.amber.withValues(alpha: 0.08),
                        ),
                        child: Text(
                          live.pendingApprovals == 1 ? '1 Freigabe wartet' : '${live.pendingApprovals} Freigaben warten',
                          style: J.mono(12, color: J.amber, weight: 560),
                        ),
                      ),
                    ),
                  ],
                ])),
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(J.s4, 0, J.s4, J.s3),
                child: Column(children: [
                  Pressable(
                    onTap: () => context.push('/chat'),
                    semantics: 'Gespräch öffnen',
                    child: Padding(
                      padding: const EdgeInsets.only(bottom: J.s3),
                      child: Column(children: [
                        Container(width: 28, height: 2, color: J.lineBright),
                        const SizedBox(height: 6),
                        const Caps('Gespräch'),
                      ]),
                    ),
                  ),
                  Composer(onSend: _send),
                ]),
              ),
            ]),
          ),
        ]),
      ),
    );
  }
}

class _LinkLine extends StatelessWidget {
  const _LinkLine({required this.link});
  final Link link;
  @override
  Widget build(BuildContext context) {
    final (color, text) = switch (link) {
      Link.online => (J.green, 'VERBUNDEN'),
      Link.connecting => (J.cyan, 'VERBINDE'),
      Link.offline => (J.red, 'OFFLINE'),
    };
    return Row(mainAxisAlignment: MainAxisAlignment.center, children: [
      AnimatedContainer(
        duration: J.base,
        width: 5,
        height: 5,
        decoration: BoxDecoration(shape: BoxShape.circle, color: color, boxShadow: [BoxShadow(color: color, blurRadius: 6)]),
      ),
      const SizedBox(width: 6),
      Text(text, style: J.caps.copyWith(color: J.text3)),
    ]);
  }
}

/// Barely-there depth: a radial falloff and a faint polar grid, static (no battery cost).
class _Backdrop extends StatelessWidget {
  const _Backdrop();
  @override
  Widget build(BuildContext context) => const RepaintBoundary(child: CustomPaint(painter: _BackdropPainter()));
}

class _BackdropPainter extends CustomPainter {
  const _BackdropPainter();
  @override
  void paint(Canvas c, Size s) {
    final center = Offset(s.width / 2, s.height * 0.42);
    c.drawRect(
      Offset.zero & s,
      Paint()
        ..shader = RadialGradient(
          center: Alignment(0, -0.16),
          radius: 0.9,
          colors: const [Color(0xFF071620), J.void_],
        ).createShader(Offset.zero & s),
    );
    final grid = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 0.6
      ..color = J.line.withValues(alpha: 0.45);
    for (var r = 1; r <= 5; r++) {
      c.drawCircle(center, s.width * 0.22 * r, grid);
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter old) => false;
}
