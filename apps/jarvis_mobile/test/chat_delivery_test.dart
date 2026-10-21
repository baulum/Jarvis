import 'dart:async';
import 'dart:io';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/core/api.dart';
import 'package:jarvis_mobile/core/live.dart';
import 'package:jarvis_mobile/core/native.dart';
import 'package:jarvis_mobile/state/chat.dart';
import 'package:jarvis_mobile/state/providers.dart';
import 'package:jarvis_mobile/state/timeline.dart';

/// REST double: `POST /api/chat` answers when the test says so, the stored timeline is whatever the test puts in.
class FakeApi extends Api {
  FakeApi() : super(NativeBridge());
  Completer<Map<String, dynamic>> chat = Completer();
  final Map<String, List<Map<String, dynamic>>> stored = {};
  final List<Map<String, dynamic>> sent = [];
  Future<WebSocket> Function()? connect;

  @override
  Future<dynamic> post(String path, [Object? body]) {
    sent.add((body as Map).cast<String, dynamic>());
    return chat.future;
  }

  @override
  Future<dynamic> get(String path) async {
    final sid = RegExp(r'/api/sessions/([^/]+)/events').firstMatch(path)!.group(1)!;
    return {'events': stored[sid] ?? const [], 'has_more': false};
  }

  @override
  Future<WebSocket> socket(String path) => connect!();
}

/// Socket double: the test pushes the core's events.
class FakeSocket extends LiveSocket {
  FakeSocket(super.api) {
    current = Link.online;
  }
  final bus = StreamController<CoreEvent>.broadcast();
  @override
  Stream<CoreEvent> get events => bus.stream;
}

Map<String, dynamic> item(String sid, String id, int seq, String kind, Map<String, dynamic> data, {String turn = 't'}) =>
    {'id': id, 'session_id': sid, 'turn_id': turn, 'seq': seq, 'ts': 1700000000000 + seq, 'kind': kind, 'data': data};

CoreEvent ev(String type, String sid, Map<String, dynamic> data) => CoreEvent(type, {'session_id': sid, ...data}, sessionId: sid);
CoreEvent up(Map<String, dynamic> i) => ev('chat.event', i['session_id'] as String, {'event': i});

List<String> shown(Timeline t) => [for (final row in buildRows(t.items)) switch (row) {
      UserRow r => 'user:${r.item.text}',
      SystemRow r => 'system:${r.item.text}',
      AnswerRow r => 'jarvis:${r.items.map((e) => e.text).join()}',
    }];

Future<void> settle() => Future<void>.delayed(const Duration(milliseconds: 120)); // stream delivery + delta flush

void main() {
  late FakeApi api;
  late FakeSocket sock;
  late ProviderContainer c;

  setUp(() {
    api = FakeApi();
    sock = FakeSocket(api);
    c = ProviderContainer(overrides: [apiProvider.overrideWithValue(api), liveSocketProvider.overrideWithValue(sock)]);
    c.read(chatProvider);
  });
  tearDown(() => c.dispose());

  test('new conversation: events that arrive before the session id is known are not lost', () async {
    final chat = c.read(chatProvider.notifier);
    final sending = chat.send('Hallo');
    // the core is fast: the turn starts and streams before POST /api/chat has answered
    sock.bus
      ..add(up(item('S', 'u', 1, 'message', {'role': 'user', 'text': 'Hallo', 'status': 'done'})))
      ..add(up(item('S', 'turn', 2, 'turn', {'status': 'running'})))
      ..add(up(item('S', 'm', 3, 'message', {'role': 'assistant', 'text': '', 'status': 'streaming'})))
      ..add(ev('chat.delta', 'S', {'event_id': 'm', 'text': 'Guten Tag, '}))
      ..add(up(item('DESK', 'x', 1, 'message', {'role': 'user', 'text': 'vom Mac', 'status': 'done'})));
    await settle();
    api.chat.complete({'session_id': 'S', 'accepted': true});
    await sending;
    sock.bus.add(ev('chat.delta', 'S', {'event_id': 'm', 'text': 'Sir.'}));
    await settle();

    final st = c.read(chatProvider);
    expect(st.sessionId, 'S');
    expect(shown(st.timeline), ['user:Hallo', 'jarvis:Guten Tag, Sir.']);
    expect(st.running, isTrue);
    sock.bus.add(up(item('S', 'turn', 2, 'turn', {'status': 'done'})));
    await settle();
    expect(c.read(chatProvider).running, isFalse);
  });

  test('expired conversation: the core answers in a new session – the answer is shown, at the end', () async {
    api.stored['OLD'] = [
      item('OLD', 'o1', 1, 'message', {'role': 'user', 'text': 'Gestern', 'status': 'done'}, turn: 'a'),
      item('OLD', 'o2', 2, 'turn', {'status': 'done'}, turn: 'a'),
      item('OLD', 'o3', 3, 'message', {'role': 'assistant', 'text': 'Alte Antwort', 'status': 'done'}, turn: 'a'),
    ];
    final chat = c.read(chatProvider.notifier);
    await chat.open('OLD');
    expect(shown(c.read(chatProvider).timeline), ['user:Gestern', 'jarvis:Alte Antwort']);

    final sending = chat.send('Heute');
    expect(api.sent.single['session_id'], 'OLD');
    api.chat.complete({'session_id': 'NEW', 'accepted': true}); // idle timeout on the core: fresh session
    await sending;
    sock.bus
      ..add(up(item('NEW', 'n1', 1, 'message', {'role': 'user', 'text': 'Heute', 'status': 'done'})))
      ..add(up(item('NEW', 'n2', 2, 'turn', {'status': 'running'})))
      ..add(up(item('NEW', 'n3', 3, 'message', {'role': 'assistant', 'text': 'Neue Antwort', 'status': 'done'})))
      ..add(up(item('NEW', 'n2', 2, 'turn', {'status': 'done'})));
    await settle();

    final st = c.read(chatProvider);
    expect(st.sessionId, 'NEW');
    // before: the new items (seq 1–3) were sorted in front of the old ones, the answer far above the visible end
    expect(shown(st.timeline), ['user:Heute', 'jarvis:Neue Antwort']);
    expect(st.running, isFalse);
  });

  test('same conversation: answer appended after the history', () async {
    api.stored['S'] = [item('S', 'o1', 1, 'message', {'role': 'user', 'text': 'Eins', 'status': 'done'}, turn: 'a')];
    final chat = c.read(chatProvider.notifier);
    await chat.open('S');
    final sending = chat.send('Zwei');
    api.chat.complete({'session_id': 'S', 'accepted': true});
    await sending;
    sock.bus
      ..add(up(item('S', 'u2', 2, 'message', {'role': 'user', 'text': 'Zwei', 'status': 'done'})))
      ..add(up(item('S', 'm2', 3, 'message', {'role': 'assistant', 'text': 'Antwort', 'status': 'done'})));
    await settle();
    expect(shown(c.read(chatProvider).timeline), ['user:Eins', 'user:Zwei', 'jarvis:Antwort']);
  });

  group('live socket', () {
    late HttpServer server;
    var connections = 0;
    var answerPings = true;

    setUp(() async {
      connections = 0;
      answerPings = true;
      server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      server.listen((req) async {
        final ws = await WebSocketTransformer.upgrade(req);
        connections++;
        ws.listen((msg) {
          if (msg == 'ping' && answerPings) ws.add('{"type":"pong"}');
        });
      });
      api.connect = () => WebSocket.connect('ws://127.0.0.1:${server.port}/ws');
    });
    tearDown(() => server.close(force: true));

    test('a connection that no longer answers is replaced', () async {
      final live = LiveSocket(api, pingEvery: const Duration(milliseconds: 40), silentAfter: const Duration(milliseconds: 150));
      final links = <Link>[];
      live.link.listen(links.add);
      await live.connect();
      await Future<void>.delayed(const Duration(milliseconds: 300));
      expect(live.current, Link.online);
      expect(connections, 1); // answered pings: the connection stays

      answerPings = false; // half-open: the socket looks fine, nothing comes back
      await Future<void>.delayed(const Duration(milliseconds: 1400));
      expect(links, contains(Link.offline));
      expect(connections, greaterThanOrEqualTo(2)); // reconnected on its own
      live.close();
    });

    test('back in the foreground: a silent socket is checked and replaced', () async {
      final live = LiveSocket(api, pingEvery: const Duration(minutes: 5));
      await live.connect();
      answerPings = false;
      live.nudge(answerWithin: const Duration(milliseconds: 100));
      await Future<void>.delayed(const Duration(milliseconds: 1200));
      expect(connections, 2);
      expect(live.current, Link.online);
      live.close();
    });
  });
}
