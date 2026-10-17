import 'dart:math' as math;

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'providers.dart';

/// Which slice of the knowledge graph the phone shows (the full desktop graph has ~200 tools/skills).
enum Lens { knowledge, work, abilities }

const lensLabel = {Lens.knowledge: 'Wissen', Lens.work: 'Arbeit', Lens.abilities: 'Fähigkeiten'};

const _abilityKinds = {'tool', 'skill', 'connector'};
const _workKinds = {'task', 'coding', 'schedule', 'conversation'};

bool inLens(String kind, Lens lens) => switch (lens) {
      Lens.abilities => _abilityKinds.contains(kind) || kind == 'agent',
      Lens.work => _workKinds.contains(kind) || kind == 'agent',
      Lens.knowledge => !_abilityKinds.contains(kind) && !_workKinds.contains(kind),
    };

class GNode {
  GNode(this.id, this.kind, this.label, this.weight, this.meta);
  final String id, kind, label;
  final double weight;
  final Map<String, dynamic> meta;
  double x = 0, y = 0;
}

class GEdge {
  const GEdge(this.a, this.b, this.relation);
  final int a, b;
  final String relation;
}

class GraphView {
  const GraphView(this.nodes, this.edges);
  final List<GNode> nodes;
  final List<GEdge> edges;
}

/// Force-directed layout (repulsion, springs, gravity) – runs in a background isolate.
List<double> layoutGraph(Map<String, dynamic> input) {
  final n = input['n'] as int;
  final edges = (input['edges'] as List).cast<int>();
  final groups = (input['groups'] as List).cast<int>();
  final rnd = math.Random(7);
  final x = List<double>.generate(n, (i) => math.cos(groups[i] * 1.3) * 220 + rnd.nextDouble() * 60 - 30);
  final y = List<double>.generate(n, (i) => math.sin(groups[i] * 1.3) * 220 + rnd.nextDouble() * 60 - 30);
  final iters = n > 300 ? 220 : 320;
  for (var it = 0; it < iters; it++) {
    final cool = 1 - it / iters;
    final fx = List<double>.filled(n, 0), fy = List<double>.filled(n, 0);
    for (var i = 0; i < n; i++) {
      for (var j = i + 1; j < n; j++) {
        var dx = x[i] - x[j], dy = y[i] - y[j];
        var d2 = dx * dx + dy * dy + 0.01;
        if (d2 > 160000) continue;
        final f = 2600 / d2;
        final d = math.sqrt(d2);
        dx /= d;
        dy /= d;
        fx[i] += dx * f;
        fy[i] += dy * f;
        fx[j] -= dx * f;
        fy[j] -= dy * f;
      }
    }
    for (var k = 0; k < edges.length; k += 2) {
      final a = edges[k], b = edges[k + 1];
      final dx = x[b] - x[a], dy = y[b] - y[a];
      final d = math.sqrt(dx * dx + dy * dy) + 0.01;
      final f = (d - 70) * 0.035;
      fx[a] += dx / d * f;
      fy[a] += dy / d * f;
      fx[b] -= dx / d * f;
      fy[b] -= dy / d * f;
    }
    for (var i = 0; i < n; i++) {
      fx[i] -= x[i] * 0.012;
      fy[i] -= y[i] * 0.012;
      final step = math.sqrt(fx[i] * fx[i] + fy[i] * fy[i]);
      final cap = 18 * cool + 0.5;
      final s = step > cap ? cap / step : 1.0;
      x[i] += fx[i] * s;
      y[i] += fy[i] * s;
    }
  }
  return [for (var i = 0; i < n; i++) ...[x[i], y[i]]];
}

final lensProvider = StateProvider<Lens>((ref) => Lens.knowledge);

final graphProvider = FutureProvider.autoDispose<GraphView>((ref) async {
  final lens = ref.watch(lensProvider);
  final raw = (await ref.read(apiProvider).get('/api/graph?limit=600') as Map).cast<String, dynamic>();
  final all = (raw['nodes'] as List).cast<Map>();
  final nodes = <GNode>[];
  final index = <String, int>{};
  for (final m in all) {
    final kind = m['kind'] as String;
    if (!inLens(kind, lens)) continue;
    index[m['id'] as String] = nodes.length;
    nodes.add(GNode(m['id'] as String, kind, (m['label'] ?? '').toString(), (m['weight'] as num?)?.toDouble() ?? 0.5,
        (m['meta'] as Map?)?.cast<String, dynamic>() ?? const {}));
  }
  final edges = <GEdge>[];
  for (final e in (raw['edges'] as List).cast<Map>()) {
    final a = index[e['source']], b = index[e['target']];
    if (a != null && b != null) edges.add(GEdge(a, b, (e['relation'] ?? '').toString()));
  }
  final kinds = nodes.map((n) => n.kind).toSet().toList();
  final pos = await compute(layoutGraph, {
    'n': nodes.length,
    'edges': [for (final e in edges) ...[e.a, e.b]],
    'groups': [for (final n in nodes) kinds.indexOf(n.kind)],
  });
  for (var i = 0; i < nodes.length; i++) {
    nodes[i].x = pos[i * 2];
    nodes[i].y = pos[i * 2 + 1];
  }
  return GraphView(nodes, edges);
});
