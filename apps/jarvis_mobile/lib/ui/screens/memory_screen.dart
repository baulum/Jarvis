import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/api.dart';
import '../../core/theme.dart';
import '../../state/graph.dart';
import '../../state/live_model.dart';
import '../../state/providers.dart';
import '../widgets/hud.dart';

const _kindColor = {
  'agent': J.ice,
  'person': Color(0xFF9FE6FF),
  'customer': Color(0xFFFFD08A),
  'company': Color(0xFFFFD08A),
  'project': J.cyan,
  'preference': Color(0xFFB9A8FF),
  'decision': Color(0xFF8FB8FF),
  'process': Color(0xFF7FE0C8),
  'task': J.amber,
  'coding': Color(0xFF7FE0C8),
  'conversation': J.text3,
  'tool': J.cyanDeep,
  'skill': Color(0xFF6F86A8),
  'connector': J.text2,
};

/// The memory as a living constellation – the same data as the Mac's 3D graph, laid out flat for the phone.
/// Nodes recalled or used right now light up (real `memory.recalled` / tool events).
class MemoryScreen extends ConsumerStatefulWidget {
  const MemoryScreen({super.key});
  @override
  ConsumerState<MemoryScreen> createState() => _MemoryScreenState();
}

class _MemoryScreenState extends ConsumerState<MemoryScreen> with SingleTickerProviderStateMixin {
  final _tx = TransformationController();
  late final Ticker _ticker;
  final _clock = ValueNotifier<double>(0);
  String? _selected;
  bool _fitted = false;

  @override
  void initState() {
    super.initState();
    _ticker = createTicker((d) => _clock.value = d.inMilliseconds / 1000)..start();
  }

  @override
  void dispose() {
    _ticker.dispose();
    _tx.dispose();
    super.dispose();
  }

  void _fit(GraphView g, Size size) {
    if (_fitted || g.nodes.isEmpty) return;
    _fitted = true;
    var minX = double.infinity, minY = double.infinity, maxX = -double.infinity, maxY = -double.infinity;
    for (final n in g.nodes) {
      minX = math.min(minX, n.x);
      minY = math.min(minY, n.y);
      maxX = math.max(maxX, n.x);
      maxY = math.max(maxY, n.y);
    }
    final w = maxX - minX + 80, h = maxY - minY + 80;
    final s = math.min(size.width / w, size.height / h).clamp(0.3, 2.0);
    _tx.value = Matrix4.identity()
      ..translateByDouble(size.width / 2, size.height / 2, 0, 1)
      ..scaleByDouble(s, s, 1, 1)
      ..translateByDouble(-(minX + maxX) / 2, -(minY + maxY) / 2, 0, 1);
  }

  GNode? _hit(GraphView g, Offset local) {
    final inv = Matrix4.inverted(_tx.value);
    final p = MatrixUtils.transformPoint(inv, local);
    final scale = _tx.value.getMaxScaleOnAxis();
    GNode? best;
    var bestD = 26 / scale;
    for (final n in g.nodes) {
      final d = (Offset(n.x, n.y) - p).distance;
      if (d < bestD) {
        bestD = d;
        best = n;
      }
    }
    return best;
  }

  @override
  Widget build(BuildContext context) {
    final graph = ref.watch(graphProvider);
    final lens = ref.watch(lensProvider);
    final live = ref.watch(liveProvider);
    // animate only while something glows – a still graph costs no battery
    final animate = live.glow.isNotEmpty || _selected != null;
    if (animate && !_ticker.isActive) _ticker.start();
    if (!animate && _ticker.isActive) _ticker.stop();
    return Scaffold(
      backgroundColor: J.void_,
      body: SafeArea(
        child: Column(children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(J.s2, J.s2, J.s4, J.s2),
            child: Row(children: [
              Pressable(
                onTap: () => context.canPop() ? context.pop() : context.go('/'),
                semantics: 'Zurück',
                child: const SizedBox.square(dimension: J.hit, child: Center(child: Glyph(GlyphKind.back))),
              ),
              Expanded(child: Text('Gedächtnis', style: J.title)),
              Text(graph.valueOrNull == null ? '' : '${graph.value!.nodes.length} KNOTEN', style: J.caps),
            ]),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: J.s4),
            child: Rail<Lens>(
              values: Lens.values,
              labels: [for (final l in Lens.values) lensLabel[l]!],
              value: lens,
              onChanged: (l) {
                _fitted = false;
                _selected = null;
                ref.read(lensProvider.notifier).state = l;
              },
            ),
          ),
          const SizedBox(height: J.s2),
          Expanded(
            child: graph.when(
              loading: () => Center(child: Text('Ordne Erinnerungen …', style: J.mono(12))),
              error: (e, _) => Center(child: Text(e is ApiError && e.offline ? 'Jarvis ist nicht erreichbar.' : '$e', style: J.bodyDim)),
              data: (g) => LayoutBuilder(builder: (context, box) {
                _fit(g, box.biggest);
                return GestureDetector(
                  onTapUp: (d) {
                    final n = _hit(g, d.localPosition);
                    setState(() => _selected = n?.id);
                    if (n != null) _openNode(context, n, g);
                  },
                  child: InteractiveViewer(
                    transformationController: _tx,
                    constrained: false,
                    boundaryMargin: const EdgeInsets.all(4000),
                    minScale: 0.15,
                    maxScale: 5,
                    child: SizedBox(
                      width: box.maxWidth,
                      height: box.maxHeight,
                      child: RepaintBoundary(
                        child: CustomPaint(
                          size: box.biggest,
                          painter: _GraphPainter(g, live.glow, _selected, _clock, _tx),
                        ),
                      ),
                    ),
                  ),
                );
              }),
            ),
          ),
        ]),
      ),
    );
  }

  void _openNode(BuildContext context, GNode n, GraphView g) {
    final related = <(GNode, String)>[];
    final i = g.nodes.indexOf(n);
    for (final e in g.edges) {
      if (e.a == i) related.add((g.nodes[e.b], e.relation));
      if (e.b == i) related.add((g.nodes[e.a], e.relation));
    }
    final memoryId = n.id.startsWith('memory:') ? n.id.substring(7) : null;
    showHudSheet(context, builder: (ctx) => ConstrainedBox(
          constraints: BoxConstraints(maxHeight: MediaQuery.sizeOf(ctx).height * 0.75),
          child: ListView(shrinkWrap: true, padding: const EdgeInsets.fromLTRB(J.s5, J.s5, J.s5, J.s6), children: [
            Caps(n.kind, color: _kindColor[n.kind] ?? J.text3),
            const SizedBox(height: J.s2),
            Text(n.label, style: J.title),
            if (n.meta['description'] != null) ...[
              const SizedBox(height: J.s2),
              Text(n.meta['description'].toString(), style: J.bodyDim),
            ],
            if (memoryId != null) _MemoryBody(id: memoryId),
            if (related.isNotEmpty) ...[
              const SizedBox(height: J.s5),
              const Caps('Verknüpft'),
              const SizedBox(height: J.s2),
              for (final (r, rel) in related.take(24))
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 5),
                  child: Row(children: [
                    Container(width: 6, height: 6, decoration: BoxDecoration(shape: BoxShape.circle, color: _kindColor[r.kind] ?? J.text3)),
                    const SizedBox(width: J.s3),
                    Expanded(child: Text(r.label, style: J.body, maxLines: 1, overflow: TextOverflow.ellipsis)),
                    Text(rel, style: J.mono(10.5, color: J.text3)),
                  ]),
                ),
            ],
          ]),
        ));
  }
}

class _MemoryBody extends ConsumerWidget {
  const _MemoryBody({required this.id});
  final String id;
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return FutureBuilder(
      future: ref.read(apiProvider).get('/api/memory/items/$id'),
      builder: (_, snap) {
        if (snap.hasError) {
          final e = snap.error;
          return Padding(
            padding: const EdgeInsets.only(top: J.s3),
            child: Text(e is ApiError && e.status == 403 ? 'Sensible Erinnerung – nur am Mac einsehbar.' : '$e', style: J.mono(12, color: J.amber)),
          );
        }
        if (!snap.hasData) return const Padding(padding: EdgeInsets.all(J.s4), child: LinearProgressIndicator(minHeight: 1, color: J.cyan, backgroundColor: J.line));
        final m = (snap.data as Map).cast<String, dynamic>();
        return Padding(
          padding: const EdgeInsets.only(top: J.s4),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(m['content']?.toString() ?? '', style: J.body.copyWith(fontSize: 15)),
            const SizedBox(height: J.s3),
            Text('Aktualisiert ${relDay(DateTime.parse((m['updated_at'] ?? m['created_at']).toString()))}', style: J.mono(10.5, color: J.text3)),
          ]),
        );
      },
    );
  }
}

class _GraphPainter extends CustomPainter {
  _GraphPainter(this.g, this.glow, this.selected, this.clock, this.tx) : super(repaint: Listenable.merge([clock, tx]));
  final GraphView g;
  final Map<String, NodeGlow> glow;
  final String? selected;
  final ValueNotifier<double> clock;
  final TransformationController tx;

  @override
  void paint(Canvas canvas, Size size) {
    final t = clock.value;
    final scale = tx.value.getMaxScaleOnAxis();
    final edgePaint = Paint()
      ..strokeWidth = 0.7 / scale
      ..color = J.lineBright.withValues(alpha: 0.7);
    final hot = Paint()
      ..strokeWidth = 1.4 / scale
      ..color = J.cyan.withValues(alpha: 0.85);
    for (final e in g.edges) {
      final a = g.nodes[e.a], b = g.nodes[e.b];
      final lit = glow.containsKey(a.id) || glow.containsKey(b.id) || a.id == selected || b.id == selected;
      canvas.drawLine(Offset(a.x, a.y), Offset(b.x, b.y), lit ? hot : edgePaint);
    }
    final labelZoom = scale > 1.1;
    for (final n in g.nodes) {
      final color = _kindColor[n.kind] ?? J.text2;
      final r = 2.5 + n.weight * 4.5;
      final o = Offset(n.x, n.y);
      final gl = glow[n.id];
      if (gl != null || n.id == selected) {
        final pulse = 0.5 + 0.5 * math.sin(t * 5);
        final c = gl?.kind == 'error' ? J.red : gl?.kind == 'approval' ? J.amber : J.cyan;
        canvas.drawCircle(o, r * (2.6 + pulse), Paint()..color = c.withValues(alpha: 0.18 + 0.12 * pulse));
      }
      canvas.drawCircle(o, r, Paint()..color = color);
      if (labelZoom || n.weight >= 0.95 || n.id == selected || gl != null) {
        final tp = TextPainter(
          text: TextSpan(text: n.label.length > 32 ? '${n.label.substring(0, 31)}…' : n.label, style: J.mono(10 / scale.clamp(0.8, 2.5), color: J.text2)),
          textDirection: TextDirection.ltr,
        )..layout(maxWidth: 220);
        tp.paint(canvas, o + Offset(r + 4, -tp.height / 2));
      }
    }
  }

  @override
  bool shouldRepaint(covariant _GraphPainter old) => old.g != g || old.glow != glow || old.selected != selected;
}
