import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'api.dart';

enum Link { connecting, online, offline }

/// One envelope from the core's event bus (see core/src/jarvis/events.py).
class CoreEvent {
  CoreEvent(this.type, this.data, {this.sessionId, this.taskId, this.nodes = const [], this.ts});
  factory CoreEvent.fromJson(Map<String, dynamic> j) => CoreEvent(
        j['type'] as String,
        (j['data'] as Map?)?.cast<String, dynamic>() ?? const {},
        sessionId: j['session_id'] as String?,
        taskId: j['task_id'] as String?,
        nodes: ((j['nodes'] as List?) ?? const []).cast<String>(),
        ts: DateTime.tryParse(j['ts']?.toString() ?? ''),
      );
  final String type;
  final Map<String, dynamic> data;
  final String? sessionId;
  final String? taskId;
  final List<String> nodes;
  final DateTime? ts;
}

/// Live connection to `/ws`. Reconnects with backoff; `link` says honestly whether Jarvis is reachable.
///
/// A phone loses connections without being told (Wi-Fi ↔ mobile data, Tailscale reconnect, Doze): the socket
/// still looks open while nothing arrives any more. So every ping must be answered – the core replies `pong` –
/// and a socket that stays silent for [silentAfter] counts as lost and is replaced.
class LiveSocket {
  LiveSocket(this.api, {this.pingEvery = const Duration(seconds: 20), this.silentAfter = const Duration(seconds: 45)});
  final Api api;
  final Duration pingEvery;
  final Duration silentAfter;
  final _events = StreamController<CoreEvent>.broadcast();
  final _link = StreamController<Link>.broadcast();
  Link current = Link.connecting;
  WebSocket? _ws;
  Timer? _retry;
  Timer? _ping;
  Timer? _probe;
  DateTime _heard = DateTime.now();
  int _attempt = 0;
  bool _connecting = false;
  bool _closed = false;

  Stream<CoreEvent> get events => _events.stream;
  Stream<Link> get link => _link.stream;

  void _set(Link l) {
    if (current != l) {
      current = l;
      _link.add(l);
    }
  }

  Future<void> connect() async {
    if (_closed || _connecting || _ws != null) return; // one socket at a time, or every event arrives twice
    _connecting = true;
    _retry?.cancel();
    if (current != Link.online) _set(Link.connecting);
    try {
      final ws = await api.socket('/ws');
      _connecting = false;
      if (_closed) {
        ws.close();
        return;
      }
      _attach(ws);
    } catch (_) {
      _connecting = false;
      _lost();
    }
  }

  void _attach(WebSocket ws) {
    _ws = ws;
    _attempt = 0;
    _heard = DateTime.now();
    _set(Link.online);
    _ping = Timer.periodic(pingEvery, (_) {
      if (DateTime.now().difference(_heard) > silentAfter) {
        _drop(ws); // no answer to the last pings: the connection is dead, whatever the socket says
      } else {
        _send(ws, 'ping');
      }
    });
    ws.listen(
      (msg) {
        _heard = DateTime.now();
        if (msg is! String) return;
        final j = jsonDecode(msg) as Map<String, dynamic>;
        final type = j['type'] as String?;
        if (type == null || type == 'pong' || type == 'hello') return;
        _events.add(CoreEvent.fromJson(j));
      },
      onDone: () => _drop(ws),
      onError: (_) => _drop(ws),
      cancelOnError: true,
    );
  }

  void _send(WebSocket ws, String msg) {
    try {
      ws.add(msg);
    } catch (_) {
      _drop(ws);
    }
  }

  /// Ends [ws] once: its own `onDone` after a close must not schedule a second reconnect.
  void _drop(WebSocket ws) {
    if (!identical(_ws, ws)) return;
    _lost();
    ws.close().catchError((Object _) {});
  }

  void _lost() {
    _ping?.cancel();
    _probe?.cancel();
    _ws = null;
    if (_closed) return;
    _set(Link.offline);
    _retry?.cancel();
    final delay = Duration(milliseconds: min(30000, 800 * pow(2, _attempt++).toInt()));
    _retry = Timer(delay, connect);
  }

  /// App came to the foreground / network changed: try now instead of waiting for the backoff. An open socket
  /// has to prove it still works – after a pause it may be dead without anyone having noticed.
  void nudge({Duration answerWithin = const Duration(seconds: 5)}) {
    final ws = _ws;
    if (ws == null) {
      _attempt = 0;
      connect();
      return;
    }
    final asked = DateTime.now();
    _send(ws, 'ping');
    _probe?.cancel();
    _probe = Timer(answerWithin, () {
      if (identical(_ws, ws) && _heard.isBefore(asked)) {
        _attempt = 0;
        _drop(ws);
      }
    });
  }

  void close() {
    _closed = true;
    _retry?.cancel();
    _ping?.cancel();
    _probe?.cancel();
    _ws?.close();
  }
}
