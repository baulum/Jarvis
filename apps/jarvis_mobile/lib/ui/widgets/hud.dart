import 'dart:math' as math;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../core/theme.dart';
import '../../state/providers.dart';

/// Selects one channel of the 30 Hz level notifier without rebuilding widgets.
class LevelOf extends ChangeNotifier implements ValueListenable<double> {
  LevelOf(this.levels, {this.mic = false, this.boost = 1}) {
    levels.addListener(notifyListeners);
  }
  final Levels levels;
  final bool mic;
  final double boost;
  @override
  double get value => ((mic ? levels.value.inp : levels.value.out) * boost).clamp(0, 1);
  @override
  void dispose() {
    levels.removeListener(notifyListeners);
    super.dispose();
  }
}

class Caps extends StatelessWidget {
  const Caps(this.text, {super.key, this.color});
  final String text;
  final Color? color;
  @override
  Widget build(BuildContext context) =>
      Text(text.toUpperCase(), style: J.caps.copyWith(color: color ?? J.text3));
}

class Hairline extends StatelessWidget {
  const Hairline({super.key, this.indent = 0});
  final double indent;
  @override
  Widget build(BuildContext context) => Container(height: 1, margin: EdgeInsets.only(left: indent), color: J.line);
}

/// Press feedback shared by every touchable: slight scale + light haptic.
class Pressable extends StatefulWidget {
  const Pressable({super.key, required this.child, this.onTap, this.onLongPress, this.haptic = true, this.semantics});
  final Widget child;
  final VoidCallback? onTap;
  final VoidCallback? onLongPress;
  final bool haptic;
  final String? semantics;
  @override
  State<Pressable> createState() => _PressableState();
}

class _PressableState extends State<Pressable> {
  bool _down = false;
  @override
  Widget build(BuildContext context) {
    final enabled = widget.onTap != null || widget.onLongPress != null;
    return Semantics(
      button: true,
      enabled: enabled,
      label: widget.semantics,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTapDown: enabled ? (_) => setState(() => _down = true) : null,
        onTapCancel: () => setState(() => _down = false),
        onTapUp: (_) => setState(() => _down = false),
        onTap: widget.onTap == null
            ? null
            : () {
                if (widget.haptic) HapticFeedback.selectionClick();
                widget.onTap!();
              },
        onLongPress: widget.onLongPress,
        child: AnimatedScale(
          scale: _down ? 0.94 : 1,
          duration: J.quick,
          curve: J.enter,
          child: AnimatedOpacity(opacity: enabled ? 1 : 0.4, duration: J.quick, child: widget.child),
        ),
      ),
    );
  }
}

/// Circular hairline control (call controls, header glyphs).
class RingButton extends StatelessWidget {
  const RingButton({
    super.key,
    required this.child,
    this.onTap,
    this.size = 56,
    this.active = false,
    this.fill,
    this.label,
    this.semantics,
  });
  final Widget child;
  final VoidCallback? onTap;
  final double size;
  final bool active;
  final Color? fill;
  final String? label;
  final String? semantics;

  @override
  Widget build(BuildContext context) {
    final circle = AnimatedContainer(
      duration: J.base,
      curve: J.enter,
      width: size,
      height: size,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: fill ?? (active ? J.ice : Colors.transparent),
        border: Border.all(color: fill != null ? Colors.transparent : (active ? J.ice : J.lineBright), width: 1),
        boxShadow: fill != null ? [BoxShadow(color: fill!.withValues(alpha: 0.35), blurRadius: 24)] : null,
      ),
      child: Center(
        child: IconTheme(
          data: IconThemeData(color: fill != null ? J.void_ : (active ? J.void_ : J.text), size: size * 0.4),
          child: child,
        ),
      ),
    );
    return Pressable(
      onTap: onTap,
      semantics: semantics ?? label,
      child: label == null
          ? circle
          : Column(mainAxisSize: MainAxisSize.min, children: [
              circle,
              const SizedBox(height: J.s2),
              // shrinks with a narrow slot (small display, large system font) instead of overflowing
              FittedBox(fit: BoxFit.scaleDown, child: Text(label!, style: J.mono(11, color: J.text2))),
            ]),
    );
  }
}

/// Text action without a box: mono caps with an underline of light.
class LightAction extends StatelessWidget {
  const LightAction(this.text, {super.key, this.onTap, this.color = J.cyan, this.busy = false});
  final String text;
  final VoidCallback? onTap;
  final Color color;
  final bool busy;
  @override
  Widget build(BuildContext context) => Pressable(
        onTap: busy ? null : onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: J.s3, horizontal: J.s1),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            if (busy) ...[
              SizedBox.square(dimension: 10, child: CircularProgressIndicator(strokeWidth: 1.2, color: color)),
              const SizedBox(width: J.s2),
            ],
            Text(text.toUpperCase(), style: J.mono(12, color: color, weight: 560, spacing: 1.8)),
          ]),
        ),
      );
}

/// Segmented choice rendered as a light rail (notification modes, filters).
class Rail<T> extends StatelessWidget {
  const Rail({super.key, required this.values, required this.labels, required this.value, required this.onChanged, this.colors});
  final List<T> values;
  final List<String> labels;
  final T value;
  final ValueChanged<T> onChanged;
  final Map<T, Color>? colors;
  @override
  Widget build(BuildContext context) {
    return Row(children: [
      for (var i = 0; i < values.length; i++)
        Expanded(
          child: Pressable(
            onTap: () => onChanged(values[i]),
            semantics: labels[i],
            child: AnimatedContainer(
              duration: J.base,
              curve: J.enter,
              height: 36,
              margin: EdgeInsets.only(right: i == values.length - 1 ? 0 : 4),
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(J.radius),
                color: values[i] == value ? (colors?[values[i]] ?? J.cyan).withValues(alpha: 0.14) : Colors.transparent,
                border: Border.all(color: values[i] == value ? (colors?[values[i]] ?? J.cyan).withValues(alpha: 0.7) : J.line),
              ),
              alignment: Alignment.center,
              child: Text(labels[i],
                  style: J.mono(11.5, color: values[i] == value ? (colors?[values[i]] ?? J.cyan) : J.text2, weight: 500)),
            ),
          ),
        ),
    ]);
  }
}

// ------------------------------------------------------------------ custom glyphs
enum GlyphKind { memory, activity, settings, back, chat, social }

class Glyph extends StatelessWidget {
  const Glyph(this.kind, {super.key, this.size = 22, this.color = J.text, this.badge = false});
  final GlyphKind kind;
  final double size;
  final Color color;
  final bool badge;
  @override
  Widget build(BuildContext context) =>
      CustomPaint(size: Size.square(size), painter: _GlyphPainter(kind, color, badge));
}

class _GlyphPainter extends CustomPainter {
  _GlyphPainter(this.kind, this.color, this.badge);
  final GlyphKind kind;
  final Color color;
  final bool badge;

  @override
  void paint(Canvas c, Size s) {
    final p = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.4
      ..strokeCap = StrokeCap.round;
    final w = s.width;
    switch (kind) {
      case GlyphKind.memory: // constellation: three linked nodes
        final a = Offset(w * .2, w * .7), b = Offset(w * .5, w * .25), d = Offset(w * .82, w * .62);
        c.drawLine(a, b, p..color = color.withValues(alpha: .6));
        c.drawLine(b, d, p);
        c.drawLine(a, d, p);
        final f = Paint()..color = color;
        for (final o in [a, b, d]) {
          c.drawCircle(o, w * .09, f);
        }
      case GlyphKind.activity: // stacked telemetry lines of different length
        p.color = color;
        for (var i = 0; i < 3; i++) {
          final y = w * (.3 + i * .2);
          c.drawLine(Offset(w * .18, y), Offset(w * (.82 - i * .18), y), p);
        }
      case GlyphKind.settings: // dial
        c.drawCircle(Offset(w / 2, w / 2), w * .32, p);
        for (var i = 0; i < 8; i++) {
          final a = i * math.pi / 4;
          c.drawLine(Offset(w / 2 + math.cos(a) * w * .38, w / 2 + math.sin(a) * w * .38),
              Offset(w / 2 + math.cos(a) * w * .46, w / 2 + math.sin(a) * w * .46), p);
        }
        c.drawCircle(Offset(w / 2, w / 2), w * .08, Paint()..color = color);
      case GlyphKind.social: // a 9:16 frame with a play mark
        c.drawRRect(RRect.fromLTRBR(w * .28, w * .12, w * .72, w * .88, Radius.circular(w * .08)), p);
        final play = Path()
          ..moveTo(w * .44, w * .38)
          ..lineTo(w * .6, w * .5)
          ..lineTo(w * .44, w * .62)
          ..close();
        c.drawPath(play, Paint()..color = color);
      case GlyphKind.back:
        final path = Path()
          ..moveTo(w * .62, w * .2)
          ..lineTo(w * .32, w * .5)
          ..lineTo(w * .62, w * .8);
        c.drawPath(path, p);
      case GlyphKind.chat: // transcript lines inside a bracket
        final path = Path()
          ..moveTo(w * .28, w * .2)
          ..lineTo(w * .18, w * .2)
          ..lineTo(w * .18, w * .8)
          ..lineTo(w * .28, w * .8)
          ..moveTo(w * .72, w * .2)
          ..lineTo(w * .82, w * .2)
          ..lineTo(w * .82, w * .8)
          ..lineTo(w * .72, w * .8);
        c.drawPath(path, p);
        c.drawLine(Offset(w * .36, w * .42), Offset(w * .64, w * .42), p);
        c.drawLine(Offset(w * .36, w * .58), Offset(w * .56, w * .58), p);
    }
    if (badge) c.drawCircle(Offset(w * .92, w * .1), w * .12, Paint()..color = J.amber);
  }

  @override
  bool shouldRepaint(covariant _GlyphPainter old) => old.color != color || old.badge != badge || old.kind != kind;
}

/// Bottom sheet scaffold matching the HUD language (top hairline of light, grabber).
Future<T?> showHudSheet<T>(BuildContext context, {required WidgetBuilder builder, bool scroll = true}) {
  return showModalBottomSheet<T>(
    context: context,
    isScrollControlled: true,
    backgroundColor: Colors.transparent,
    barrierColor: Colors.black.withValues(alpha: 0.6),
    builder: (ctx) => Container(
      decoration: const BoxDecoration(
        color: J.abyss,
        border: Border(top: BorderSide(color: J.lineBright)),
        borderRadius: BorderRadius.vertical(top: Radius.circular(18)),
      ),
      padding: EdgeInsets.only(bottom: MediaQuery.of(ctx).viewInsets.bottom),
      child: SafeArea(
        top: false,
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const SizedBox(height: J.s2),
          Container(width: 36, height: 3, decoration: BoxDecoration(color: J.lineBright, borderRadius: BorderRadius.circular(2))),
          Flexible(child: builder(ctx)),
        ]),
      ),
    ),
  );
}

String clock(DateTime t) {
  final l = t.toLocal();
  return '${l.hour.toString().padLeft(2, '0')}:${l.minute.toString().padLeft(2, '0')}';
}

String relDay(DateTime t) {
  final l = t.toLocal();
  final now = DateTime.now();
  final d = DateTime(now.year, now.month, now.day).difference(DateTime(l.year, l.month, l.day)).inDays;
  if (d == 0) return clock(l);
  if (d == 1) return 'gestern ${clock(l)}';
  return '${l.day}.${l.month}. ${clock(l)}';
}
