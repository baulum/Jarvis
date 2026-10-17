import 'dart:math' as math;
import 'dart:ui' as ui;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

import '../../core/theme.dart';
import '../../state/live_model.dart';

/// Visual parameters per mode. Changes are eased, never jumped (spring-like critically damped lerp).
class _Target {
  const _Target(this.energy, this.turb, this.speed, this.dim, this.tint);
  final double energy, turb, speed, dim;
  final Color tint;
}

const _targets = {
  Mode.idle: _Target(0.25, 0.15, 0.55, 0, J.cyan),
  Mode.listening: _Target(0.55, 0.2, 0.8, 0, J.ice),
  Mode.thinking: _Target(0.6, 0.95, 1.25, 0, J.cyan),
  Mode.retrieving: _Target(0.75, 0.6, 1.1, 0, Color(0xFF8FB8FF)),
  Mode.tools: _Target(0.7, 0.45, 1.0, 0, J.cyan),
  Mode.speaking: _Target(0.8, 0.3, 0.9, 0, J.cyan),
  Mode.calling: _Target(0.7, 0.3, 0.9, 0, J.cyan),
  Mode.offline: _Target(0.05, 0.05, 0.25, 0.85, J.cyan),
};

/// The J.A.R.V.I.S. energy core (fragment shader on the GPU). Reacts to the real audio [level]
/// (Jarvis' voice during calls, Sir's mic while dictating) and the derived [mode]; [warn] tints it amber
/// while an approval waits. Quality adapts to frame timing; reduced-motion users get a calm core.
class CoreOrb extends StatefulWidget {
  const CoreOrb({super.key, required this.mode, this.level, this.warn = false, this.ringing = false, this.size = 320});
  final Mode mode;
  final ValueListenable<double>? level;
  final bool warn;
  final bool ringing;
  final double size;

  @override
  State<CoreOrb> createState() => _CoreOrbState();
}

class _CoreOrbState extends State<CoreOrb> with SingleTickerProviderStateMixin {
  static ui.FragmentProgram? _program;
  ui.FragmentShader? _shader;
  late final Ticker _ticker;
  final _repaint = ValueNotifier<int>(0);
  Duration _last = Duration.zero;
  double _t = 0, _energy = 0.2, _turb = 0.1, _speed = 0.5, _dim = 0, _level = 0, _pulse = 0;
  Color _tint = J.cyan;
  double _quality = 1;
  int _slowFrames = 0;
  late final TimingsCallback _timings;

  @override
  void initState() {
    super.initState();
    _load();
    _ticker = createTicker(_tick)..start();
    _timings = (List<FrameTiming> ts) {
      for (final t in ts) {
        final ms = t.totalSpan.inMicroseconds / 1000;
        if (ms > 18) {
          _slowFrames++;
        } else if (_slowFrames > 0) {
          _slowFrames--;
        }
      }
      if (_slowFrames > 30 && _quality > 0.35) {
        _quality = (_quality - 0.25).clamp(0.35, 1.0); // device struggling → fewer octaves, no rings
        _slowFrames = 0;
      }
    };
    SchedulerBinding.instance.addTimingsCallback(_timings);
  }

  Future<void> _load() async {
    try {
      _program ??= await ui.FragmentProgram.fromAsset('shaders/core.frag');
      if (mounted) setState(() => _shader = _program!.fragmentShader());
    } catch (e) {
      debugPrint('core shader unavailable: $e');
    }
  }

  void _tick(Duration now) {
    final dt = ((now - _last).inMicroseconds / 1e6).clamp(0.0, 0.05);
    _last = now;
    final reduce = MediaQuery.maybeOf(context)?.disableAnimations ?? false;
    final target = _targets[widget.mode]!;
    final k = 1 - math.exp(-dt * 3.2);
    _energy += (target.energy - _energy) * k;
    _turb += (target.turb - _turb) * k;
    _speed += ((reduce ? 0.2 : target.speed) - _speed) * k;
    _dim += (target.dim - _dim) * k;
    final tint = widget.warn ? J.amber : target.tint;
    _tint = Color.lerp(_tint, tint, k)!;
    final lv = widget.level?.value ?? 0;
    // fast attack, slow release – the core "breathes" with the voice instead of flickering
    _level += (lv - _level) * (lv > _level ? 1 - math.exp(-dt * 28) : 1 - math.exp(-dt * 6));
    _pulse += ((widget.ringing ? 1.0 : 0.0) - _pulse) * k;
    _t += dt * _speed;
    _repaint.value++;
  }

  @override
  void dispose() {
    SchedulerBinding.instance.removeTimingsCallback(_timings);
    _ticker.dispose();
    _shader?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: 'J.A.R.V.I.S. – ${modeLabel[widget.mode]}',
      child: SizedBox.square(
        dimension: widget.size,
        child: RepaintBoundary(
          child: CustomPaint(
            painter: _shader == null
                ? _FallbackPainter(this)
                : _OrbPainter(this, _shader!),
          ),
        ),
      ),
    );
  }
}

class _OrbPainter extends CustomPainter {
  _OrbPainter(this.s, this.shader) : super(repaint: s._repaint);
  final _CoreOrbState s;
  final ui.FragmentShader shader;

  @override
  void paint(Canvas canvas, Size size) {
    shader
      ..setFloat(0, size.width)
      ..setFloat(1, size.height)
      ..setFloat(2, s._t)
      ..setFloat(3, s._level)
      ..setFloat(4, s._energy)
      ..setFloat(5, s._turb)
      ..setFloat(6, s._pulse)
      ..setFloat(7, s._tint.r)
      ..setFloat(8, s._tint.g)
      ..setFloat(9, s._tint.b)
      ..setFloat(10, s._dim)
      ..setFloat(11, s._quality);
    canvas.drawRect(Offset.zero & size, Paint()..shader = shader);
  }

  @override
  bool shouldRepaint(covariant _OrbPainter old) => false;
}

/// Same idea without shader support (tests / very old GPUs): layered radial gradients.
class _FallbackPainter extends CustomPainter {
  _FallbackPainter(this.s) : super(repaint: s._repaint);
  final _CoreOrbState s;

  @override
  void paint(Canvas canvas, Size size) {
    final c = size.center(Offset.zero);
    final r = size.shortestSide / 2;
    final breathe = 1 + 0.03 * math.sin(s._t * 2) + 0.15 * s._level;
    canvas.drawCircle(
        c,
        r,
        Paint()
          ..shader = RadialGradient(colors: [
            s._tint.withValues(alpha: 0.35 * (1 - s._dim)),
            Colors.transparent,
          ]).createShader(Rect.fromCircle(center: c, radius: r)));
    canvas.drawCircle(
        c,
        r * 0.22 * breathe,
        Paint()
          ..shader = RadialGradient(colors: [J.ice, s._tint, s._tint.withValues(alpha: 0)], stops: const [0, 0.5, 1])
              .createShader(Rect.fromCircle(center: c, radius: r * 0.22 * breathe)));
    canvas.drawCircle(
        c,
        r * 0.44 * breathe,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.2
          ..color = s._tint.withValues(alpha: 0.5 * (1 - s._dim)));
  }

  @override
  bool shouldRepaint(covariant CustomPainter old) => false;
}
