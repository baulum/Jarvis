import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../core/theme.dart';

/// Route of a `route_plan` call: OpenStreetMap tiles (Web Mercator, fitted to the route) with all alternatives
/// drawn on top – the chosen one bright, start A and destination B. Tapping an alternative switches it;
/// "In OpenStreetMap öffnen" hands the route to the browser/maps app for panning and navigation.
/// No map package: tiles are plain network images (with an identifying User-Agent, as the OSM tile policy asks).
class RouteMap extends StatefulWidget {
  const RouteMap({super.key, required this.display, this.onOpen});
  final Map<String, dynamic> display;
  final void Function(String url)? onOpen;

  @override
  State<RouteMap> createState() => _RouteMapState();
}

class RouteData {
  RouteData(this.distanceM, this.durationS, this.summary, this.points);
  final double distanceM;
  final double durationS;
  final String summary;
  final List<(double, double)> points; // (lat, lon)
}

List<RouteData> parseRoutes(Map<String, dynamic> d) => [
      for (final r in (d['routes'] as List? ?? const []).cast<Map>())
        RouteData(
          (r['distance_m'] as num? ?? 0).toDouble(),
          (r['duration_s'] as num? ?? 0).toDouble(),
          r['summary']?.toString() ?? '',
          [
            for (final p in (r['geometry'] as List? ?? const []).cast<List>())
              ((p[0] as num).toDouble(), (p[1] as num).toDouble()),
          ],
        ),
    ];

String formatDistance(double m) {
  if (m < 1000) return '${(m / 10).round() * 10} m';
  final km = m / 1000;
  return km < 100 ? '${km.toStringAsFixed(1).replaceAll('.', ',')} km' : '${km.round()} km';
}

String formatDuration(double s) {
  final minutes = math.max(1, (s / 60).round());
  if (minutes < 60) return '$minutes min';
  final h = minutes ~/ 60;
  final m = minutes % 60;
  return m == 0 ? '$h h' : '$h h ${m.toString().padLeft(2, '0')} min';
}

int fastest(List<RouteData> routes) {
  var best = 0;
  for (var i = 1; i < routes.length; i++) {
    if (routes[i].durationS < routes[best].durationS) best = i;
  }
  return best;
}

/// Web Mercator pixel position at zoom `z` (256 px tiles).
Offset project(double lat, double lon, int z) {
  final scale = 256.0 * (1 << z);
  final x = (lon + 180) / 360 * scale;
  final r = lat.clamp(-85.05, 85.05) * math.pi / 180;
  final y = (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * scale;
  return Offset(x, y);
}

/// Largest zoom at which all points fit into `size` (with padding).
int fitZoom(List<(double, double)> pts, Size size, {double pad = 24}) {
  for (var z = 17; z > 1; z--) {
    final px = [for (final p in pts) project(p.$1, p.$2, z)];
    final w = px.map((o) => o.dx).reduce(math.max) - px.map((o) => o.dx).reduce(math.min);
    final h = px.map((o) => o.dy).reduce(math.max) - px.map((o) => o.dy).reduce(math.min);
    if (w <= size.width - 2 * pad && h <= size.height - 2 * pad) return z;
  }
  return 1;
}

const _engine = {'auto': 'fossgis_osrm_car', 'fahrrad': 'fossgis_osrm_bike', 'fussweg': 'fossgis_osrm_foot'};

String osmDirectionsUrl(Map<String, dynamic> d) {
  final s = (d['start'] as Map?) ?? const {};
  final e = (d['destination'] as Map?) ?? const {};
  final engine = _engine[d['mode']] ?? 'fossgis_osrm_car';
  return 'https://www.openstreetmap.org/directions?engine=$engine&route=${s['lat']}%2C${s['lon']}%3B${e['lat']}%2C${e['lon']}';
}

// dark map matching the panels: luminance inverted, slightly blue
const _darkTiles = ColorFilter.matrix(<double>[
  -0.13, -0.43, -0.04, 0, 168, //
  -0.13, -0.43, -0.04, 0, 182, //
  -0.13, -0.43, -0.04, 0, 196, //
  0, 0, 0, 1, 0,
]);

class _RouteMapState extends State<RouteMap> {
  late List<RouteData> _routes = parseRoutes(widget.display);
  late int _selected = _routes.isEmpty ? 0 : fastest(_routes);

  @override
  void didUpdateWidget(RouteMap old) {
    super.didUpdateWidget(old);
    if (!identical(old.display, widget.display)) {
      _routes = parseRoutes(widget.display);
      _selected = _routes.isEmpty ? 0 : fastest(_routes);
    }
  }

  (double, double) _point(String key) {
    final m = (widget.display[key] as Map?) ?? const {};
    return ((m['lat'] as num? ?? 0).toDouble(), (m['lon'] as num? ?? 0).toDouble());
  }

  String _label(String key) => ((widget.display[key] as Map?)?['label'] ?? '').toString();

  @override
  Widget build(BuildContext context) {
    if (_routes.isEmpty) return const SizedBox.shrink();
    final tiles = (widget.display['tiles'] as Map?) ?? const {};
    final tileUrl = tiles['url']?.toString() ?? 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
    final attribution = tiles['attribution']?.toString() ?? '© OpenStreetMap-Mitwirkende';
    return Container(
      decoration: BoxDecoration(color: J.void_, border: Border.all(color: J.lineBright), borderRadius: BorderRadius.circular(J.radius + 2)),
      clipBehavior: Clip.antiAlias,
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        SizedBox(
          height: 200,
          child: LayoutBuilder(builder: (context, c) {
            final size = Size(c.maxWidth, 200);
            final start = _point('start');
            final end = _point('destination');
            final all = [start, end, for (final r in _routes) ...r.points];
            final z = fitZoom(all, size);
            final px = [for (final p in all) project(p.$1, p.$2, z)];
            final cx = (px.map((o) => o.dx).reduce(math.max) + px.map((o) => o.dx).reduce(math.min)) / 2;
            final cy = (px.map((o) => o.dy).reduce(math.max) + px.map((o) => o.dy).reduce(math.min)) / 2;
            final origin = Offset(cx - size.width / 2, cy - size.height / 2);
            final n = 1 << z;
            final tilesWidgets = <Widget>[];
            for (var tx = (origin.dx / 256).floor(); tx <= ((origin.dx + size.width) / 256).floor(); tx++) {
              for (var ty = math.max(0, (origin.dy / 256).floor()); ty <= math.min(n - 1, ((origin.dy + size.height) / 256).floor()); ty++) {
                final url = tileUrl
                    .replaceAll('{z}', '$z')
                    .replaceAll('{x}', '${((tx % n) + n) % n}')
                    .replaceAll('{y}', '$ty')
                    .replaceAll('{s}', 'a');
                tilesWidgets.add(Positioned(
                  left: tx * 256 - origin.dx,
                  top: ty * 256 - origin.dy,
                  width: 256,
                  height: 256,
                  child: Image.network(
                    url,
                    headers: const {'User-Agent': 'JARVIS-Mobile/1.0 (persoenlicher Assistent)'},
                    fit: BoxFit.cover,
                    gaplessPlayback: true,
                    errorBuilder: (_, _, _) => const ColoredBox(color: J.abyss),
                  ),
                ));
              }
            }
            return Stack(clipBehavior: Clip.hardEdge, children: [
              Positioned.fill(child: ColorFiltered(colorFilter: _darkTiles, child: Stack(children: tilesWidgets))),
              Positioned.fill(
                child: GestureDetector(
                  behavior: HitTestBehavior.opaque,
                  onTapUp: (d) => _pick(d.localPosition, z, origin),
                  child: CustomPaint(
                    painter: _RoutePainter(
                      routes: [for (final r in _routes) [for (final p in r.points) project(p.$1, p.$2, z) - origin]],
                      selected: _selected,
                      start: project(start.$1, start.$2, z) - origin,
                      end: project(end.$1, end.$2, z) - origin,
                    ),
                  ),
                ),
              ),
              Positioned(
                right: 4,
                bottom: 2,
                child: Text(attribution, style: J.mono(9, color: J.text3, spacing: 0)),
              ),
            ]);
          }),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(J.s3, J.s2, J.s3, J.s3),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            _place('A', J.cyan, _label('start')),
            const SizedBox(height: 4),
            _place('B', J.amber, _label('destination')),
            const SizedBox(height: J.s2),
            Wrap(spacing: J.s2, runSpacing: J.s2, children: [
              for (var i = 0; i < _routes.length; i++) _alternative(i),
            ]),
            const SizedBox(height: J.s2),
            Row(children: [
              Expanded(
                child: Text(
                  '${widget.display['mode_label'] ?? widget.display['mode'] ?? ''}, Fahrzeiten ohne aktuelle Verkehrslage',
                  style: J.mono(10.5, color: J.text3, spacing: 0),
                ),
              ),
              if (widget.onOpen != null)
                GestureDetector(
                  behavior: HitTestBehavior.opaque,
                  onTap: () => widget.onOpen!(osmDirectionsUrl(widget.display)),
                  child: Padding(
                    padding: const EdgeInsets.symmetric(vertical: J.s2),
                    child: Text('In OpenStreetMap öffnen', style: J.label.copyWith(color: J.cyan)),
                  ),
                ),
            ]),
          ]),
        ),
      ]),
    );
  }

  void _pick(Offset tap, int z, Offset origin) {
    var best = -1;
    var bestDist = 22.0; // finger tolerance in px
    for (var i = 0; i < _routes.length; i++) {
      for (final p in _routes[i].points) {
        final d = (project(p.$1, p.$2, z) - origin - tap).distance;
        if (d < bestDist) {
          bestDist = d;
          best = i;
        }
      }
    }
    if (best >= 0 && best != _selected) setState(() => _selected = best);
  }

  Widget _place(String letter, Color color, String label) => Row(children: [
        _Pin(letter: letter, color: color, size: 18),
        const SizedBox(width: J.s2),
        Expanded(child: Text(label, maxLines: 1, overflow: TextOverflow.ellipsis, style: J.bodyDim.copyWith(fontSize: 13.5))),
      ]);

  Widget _alternative(int i) {
    final r = _routes[i];
    final on = i == _selected;
    return Semantics(
      selected: on,
      button: true,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: () => setState(() => _selected = i),
        child: AnimatedContainer(
          duration: J.quick,
          constraints: const BoxConstraints(minWidth: 128, minHeight: 44),
          padding: const EdgeInsets.symmetric(horizontal: J.s3, vertical: 6),
          decoration: BoxDecoration(
            color: on ? J.cyan.withValues(alpha: 0.10) : null,
            border: Border.all(color: on ? J.cyanDeep : J.line),
            borderRadius: BorderRadius.circular(J.radius),
          ),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
            Row(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.baseline, textBaseline: TextBaseline.alphabetic, children: [
              Text(formatDuration(r.durationS), style: J.mono(15, color: on ? J.ice : J.text, weight: 500, spacing: 0)),
              const SizedBox(width: J.s2),
              Text(formatDistance(r.distanceM), style: J.mono(11.5, color: J.text2, spacing: 0)),
            ]),
            Text(
              '${i == 0 ? 'Hauptroute' : 'Alternative $i'}${r.summary.isNotEmpty ? ' über ${r.summary}' : ''}',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: J.mono(10.5, color: J.text3, spacing: 0),
            ),
          ]),
        ),
      ),
    );
  }
}

class _Pin extends StatelessWidget {
  const _Pin({required this.letter, required this.color, this.size = 22});
  final String letter;
  final Color color;
  final double size;
  @override
  Widget build(BuildContext context) => Container(
        width: size,
        height: size,
        alignment: Alignment.center,
        decoration: BoxDecoration(color: color, shape: BoxShape.circle, border: Border.all(color: J.void_, width: 2)),
        child: Text(letter, style: J.mono(size * 0.5, color: J.void_, weight: 600, spacing: 0)),
      );
}

class _RoutePainter extends CustomPainter {
  _RoutePainter({required this.routes, required this.selected, required this.start, required this.end});
  final List<List<Offset>> routes;
  final int selected;
  final Offset start;
  final Offset end;

  Path _path(List<Offset> pts) {
    final p = Path();
    if (pts.isEmpty) return p;
    p.moveTo(pts.first.dx, pts.first.dy);
    for (final o in pts.skip(1)) {
      p.lineTo(o.dx, o.dy);
    }
    return p;
  }

  @override
  void paint(Canvas canvas, Size size) {
    final idle = Paint()
      ..color = J.cyanDeep.withValues(alpha: 0.45)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 3.5
      ..strokeJoin = StrokeJoin.round
      ..strokeCap = StrokeCap.round;
    final active = Paint()
      ..color = J.cyan
      ..style = PaintingStyle.stroke
      ..strokeWidth = 4.5
      ..strokeJoin = StrokeJoin.round
      ..strokeCap = StrokeCap.round;
    for (var i = 0; i < routes.length; i++) {
      if (i != selected) canvas.drawPath(_path(routes[i]), idle);
    }
    if (selected < routes.length) canvas.drawPath(_path(routes[selected]), active);
    for (final (o, c, l) in [(start, J.cyan, 'A'), (end, J.amber, 'B')]) {
      canvas.drawCircle(o, 10, Paint()..color = J.void_);
      canvas.drawCircle(o, 8, Paint()..color = c);
      final tp = TextPainter(
        text: TextSpan(text: l, style: J.mono(9.5, color: J.void_, weight: 700, spacing: 0)),
        textDirection: TextDirection.ltr,
      )..layout();
      tp.paint(canvas, o - Offset(tp.width / 2, tp.height / 2));
    }
  }

  @override
  bool shouldRepaint(_RoutePainter old) =>
      old.selected != selected || old.start != start || old.end != end || !identical(old.routes, routes);
}
