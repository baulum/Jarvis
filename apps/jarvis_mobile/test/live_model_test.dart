import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/core/live.dart';
import 'package:jarvis_mobile/state/live_model.dart';

CoreEvent ev(String type, Map<String, dynamic> data, {String? sid, List<String> nodes = const []}) =>
    CoreEvent(type, data, sessionId: sid, nodes: nodes);

void main() {
  final t0 = DateTime(2026, 10, 17, 12);
  Mode mode(LiveModel m, {Link link = Link.online, String call = 'idle', DateTime? now}) =>
      deriveMode(link: link, callStatus: call, live: m, now: now ?? t0);

  test('idle without activity, offline when the core is unreachable', () {
    const m = LiveModel();
    expect(mode(m), Mode.idle);
    expect(mode(m, link: Link.offline), Mode.offline);
  });

  test('thinking while a turn runs, tools while a tool runs, back to idle when done', () {
    var m = const LiveModel().apply(ev('chat.user', {'text': 'hi'}, sid: 's1'), t0);
    expect(mode(m), Mode.thinking);
    m = m.apply(ev('tool.started', {'tool': 'calendar_list_events'}, sid: 's1'), t0);
    expect(mode(m), Mode.tools);
    m = m.apply(ev('tool.finished', {'tool': 'calendar_list_events', 'status': 'ok'}, sid: 's1'), t0);
    expect(mode(m), Mode.thinking);
    m = m.apply(ev('chat.done', {'text': 'ok'}, sid: 's1'), t0);
    expect(mode(m), Mode.idle);
  });

  test('retrieving memory only right after a real recall during a turn', () {
    var m = const LiveModel().apply(ev('chat.user', {}, sid: 's1'), t0);
    m = m.apply(ev('memory.recalled', {'purpose': 'recall'}, sid: 's1', nodes: ['memory:a', 'memory:b']), t0);
    expect(mode(m), Mode.retrieving);
    expect(m.glow.keys, containsAll(['memory:a', 'memory:b']));
    expect(mode(m, now: t0.add(const Duration(seconds: 2))), Mode.thinking);
    // extraction lookups (after the reply) never claim "retrieving"
    final e = const LiveModel().apply(ev('chat.user', {}, sid: 's2'), t0).apply(ev('memory.recalled', {'purpose': 'extraction'}, sid: 's2', nodes: ['memory:x']), t0);
    expect(mode(e), Mode.thinking);
  });

  test('recall without memory hits is ignored (no invented activity)', () {
    final m = const LiveModel().apply(ev('memory.recalled', {'purpose': 'recall'}, sid: 's1', nodes: ['session:s1']), t0);
    expect(m.lines, isEmpty);
    expect(m.glow, isEmpty);
  });

  test('calling has priority over everything, speaking from the Mac voice daemon', () {
    final m = const LiveModel().apply(ev('voice.status', {'state': 'speaking'}), t0);
    expect(mode(m), Mode.speaking);
    expect(mode(m, call: 'active'), Mode.calling);
    expect(mode(m, call: 'ended'), Mode.speaking);
  });

  test('approvals counted, glows expire', () {
    var m = const LiveModel().apply(ev('approval.requested', {'tool': 'gmail_send', 'summary': 'Mail an Müller'}), t0);
    expect(m.pendingApprovals, 1);
    expect(m.lines.first.tone, Tone.warn);
    m = m.apply(ev('approval.decided', {}), t0);
    expect(m.pendingApprovals, 0);
    m = m.apply(ev('tool.finished', {'tool': 'x', 'status': 'ok'}), t0);
    expect(m.glow.containsKey('tool:x'), isTrue);
    expect(m.expire(t0.add(const Duration(seconds: 10))).glow.containsKey('tool:x'), isFalse);
  });

  test('a failed turn is reported, a cancelled one is not', () {
    var m = const LiveModel().apply(ev('chat.user', {}, sid: 's'), t0).apply(ev('chat.done', {'error': 'Kontingent erschöpft'}, sid: 's'), t0);
    expect(m.lines.first.tone, Tone.bad);
    m = const LiveModel().apply(ev('chat.user', {}, sid: 's'), t0).apply(ev('chat.done', {'error': 'cancelled'}, sid: 's'), t0);
    expect(m.lines, isEmpty);
  });
}
