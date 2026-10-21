import 'dart:ui' show Size;

import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/core/markdown.dart';
import 'package:jarvis_mobile/state/timeline.dart';
import 'package:jarvis_mobile/ui/widgets/route_map.dart';

Map<String, dynamic> item(String id, int seq, String kind, Map<String, dynamic> data, {String turn = 't1'}) =>
    {'id': id, 'session_id': 's1', 'turn_id': turn, 'seq': seq, 'ts': 1700000000000 + seq, 'kind': kind, 'data': data};

Timeline fold(Timeline t, List<(String, Map<String, dynamic>)> events) {
  for (final (type, data) in events) {
    t = applyChatEvent(t, type, data);
  }
  return t;
}

(String, Map<String, dynamic>) up(Map<String, dynamic> i) => ('chat.event', {'session_id': 's1', 'event': i});
(String, Map<String, dynamic>) delta(String id, String text) => ('chat.delta', {'session_id': 's1', 'event_id': id, 'text': text});

List<String> describe(Timeline t) =>
    [for (final e in t.items) '${e.kind}:${e.role.isNotEmpty ? e.role : (e.name.isNotEmpty ? e.name : e.status)}${e.text.isNotEmpty ? '=${e.text}' : ''}'];

void main() {
  test('text and tool calls interleave in event order; deltas go to their segment', () {
    var t = addOptimisticUser(const Timeline(), 's1', 'Route nach Potsdam?');
    final key = t.items.single.key;
    t = fold(t, [
      up(item('u', 1, 'message', {'role': 'user', 'text': 'Route nach Potsdam?'})),
      up(item('turn', 2, 'turn', {'status': 'running'})),
      up(item('m1', 3, 'message', {'role': 'assistant', 'text': '', 'status': 'streaming'})),
      delta('m1', 'Ich '),
      delta('m1', 'rechne.'),
      up(item('tool', 4, 'tool', {'name': 'route_plan', 'status': 'running', 'started_ts': 10})),
      up(item('m2', 6, 'message', {'role': 'assistant', 'text': '', 'status': 'streaming'})),
      delta('m2', '35 km.'),
    ]);
    expect(describe(t), [
      'message:user=Route nach Potsdam?',
      'turn:running',
      'message:assistant=Ich rechne.',
      'tool:route_plan',
      'message:assistant=35 km.',
    ]);
    expect(t.items.first.key, key); // optimistic message replaced, widget key kept
    expect(t.running, isTrue);
    t = fold(t, [up(item('turn', 2, 'turn', {'status': 'done'}))]);
    expect(t.running, isFalse);
  });

  test('out-of-order arrival is sorted by seq', () {
    final t = fold(const Timeline(), [
      up(item('c', 3, 'message', {'role': 'assistant', 'text': 'C'})),
      up(item('a', 1, 'message', {'role': 'user', 'text': 'A'})),
      up(item('b', 2, 'tool', {'name': 'b', 'status': 'ok'})),
    ]);
    expect(t.items.map((e) => e.id), ['a', 'b', 'c']);
  });

  test('parallel tool calls stay apart and are marked parallel', () {
    var t = fold(const Timeline(), [
      up(item('a', 4, 'tool', {'name': 'web_search', 'tool_use_id': 'a', 'status': 'running', 'started_ts': 1000})),
      up(item('b', 5, 'tool', {'name': 'calendar_list', 'tool_use_id': 'b', 'status': 'running', 'started_ts': 1010})),
    ]);
    t = fold(t, [
      up(item('b', 5, 'tool', {'name': 'calendar_list', 'tool_use_id': 'b', 'status': 'ok', 'started_ts': 1010, 'ended_ts': 1300})),
      up(item('a', 4, 'tool', {'name': 'web_search', 'tool_use_id': 'a', 'status': 'failed', 'started_ts': 1000, 'ended_ts': 2000})),
    ]);
    expect(t.items.map((e) => '${e.id}:${e.status}'), ['a:failed', 'b:ok']);
    final spans = toolSpans(t.items, 0);
    expect(spans['a']!.parallel, isTrue);
    expect(spans['b']!.parallel, isTrue);
    expect(spans['a']!.offset, 0);
  });

  test('reload merges the stored timeline without shortening live text', () {
    final live = fold(const Timeline(), [
      up(item('m1', 3, 'message', {'role': 'assistant', 'text': '', 'status': 'streaming'})),
      delta('m1', 'Schon weiter'),
    ]);
    final stored = [
      TItem.fromJson(item('u', 1, 'message', {'role': 'user', 'text': 'Hallo'})),
      TItem.fromJson(item('turn', 2, 'turn', {'status': 'running'})),
      TItem.fromJson(item('m1', 3, 'message', {'role': 'assistant', 'text': 'Scho', 'status': 'streaming'})),
    ];
    final merged = mergeServer(live, stored, hasMore: false);
    expect(describe(merged), ['message:user=Hallo', 'turn:running', 'message:assistant=Schon weiter']);
    final fresh = mergeServer(const Timeline(), stored, hasMore: true);
    expect(fresh.items.map((e) => e.seq), [1, 2, 3]);
    expect(fresh.hasMore, isTrue);
  });

  test('rows: user, answer with process, system note; checklist state from other devices', () {
    var t = fold(const Timeline(), [
      up(item('u', 1, 'message', {'role': 'user', 'text': 'Hi'})),
      up(item('turn', 2, 'turn', {'status': 'done'})),
      up(item('p', 3, 'process', {'step': 'memory', 'source': 'protocol', 'title': 'Gedächtnis durchsucht'})),
      up(item('m', 4, 'message', {'role': 'assistant', 'text': '- [ ] a\n- [ ] b'})),
      up(item('s', 5, 'message', {'role': 'system', 'text': 'Antwort wurde unterbrochen.', 'level': 'warning'})),
      ('chat.check', {'session_id': 's1', 'event_id': 'm', 'index': 1, 'checked': true}),
    ]);
    final rows = buildRows(t.items);
    expect(rows.map((r) => r.runtimeType), [UserRow, AnswerRow, SystemRow]);
    final answer = rows[1] as AnswerRow;
    expect(answer.items.single.checks, {'1': true});
    expect(answer.process.single.step, 'memory');
    expect(hasModelReasoning(answer.process), isFalse);
    t = setCheck(t, 'm', 1, false);
    expect(t.items.firstWhere((e) => e.id == 'm').checks, {'1': false});
  });

  test('task items are numbered in reading order across nested lists', () {
    final b = parseMarkdown('- [ ] eins\n  - [x] unter\n- normal\n- [ ] drei');
    final l = b.single as MdList;
    expect(l.tasks, [0, null, 2]);
    expect((l.items[0][1] as MdList).tasks, [1]);
  });

  test('route helpers: German formatting, map fit, OpenStreetMap link', () {
    expect(formatDistance(35200), '35,2 km');
    expect(formatDistance(420), '420 m');
    expect(formatDuration(2460), '41 min');
    expect(formatDuration(3 * 3600 + 5 * 60), '3 h 05 min');
    final z = fitZoom([(52.52, 13.40), (52.40, 13.06)], const Size(360, 200));
    expect(z, inInclusiveRange(9, 11));
    final a = project(52.52, 13.40, z);
    final b = project(52.40, 13.06, z);
    expect(a.dx, greaterThan(b.dx)); // east is right
    expect(a.dy, lessThan(b.dy)); // north is up
    final url = osmDirectionsUrl({
      'mode': 'fahrrad',
      'start': {'lat': 52.52, 'lon': 13.4},
      'destination': {'lat': 52.4, 'lon': 13.06},
    });
    expect(url, 'https://www.openstreetmap.org/directions?engine=fossgis_osrm_bike&route=52.52%2C13.4%3B52.4%2C13.06');
    final routes = parseRoutes({
      'routes': [
        {'distance_m': 38900, 'duration_s': 2700, 'geometry': [[52.5, 13.4]]},
        {'distance_m': 35200, 'duration_s': 2460, 'geometry': [[52.5, 13.4]]},
      ],
    });
    expect(fastest(routes), 1);
  });
}
