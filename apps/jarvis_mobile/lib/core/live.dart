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
class LiveSocket {
  LiveSocket(this.api);
  final Api api;
  final _events = StreamController<CoreEvent>.broadcast();
  final _link = StreamController<Link>.broadcast();
  Link current = Link.connecting;
  WebSocket? _ws;
  Timer? _retry;
  Timer? _ping;
  int _attempt = 0;
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
    if (_closed) return;
    _retry?.cancel();
    if (current != Link.online) _set(Link.connecting);
    try {
      final ws = await api.socket('/ws');
      _ws = ws;
      _attempt = 0;
      _set(Link.online);
      _ping = Timer.periodic(const Duration(seconds: 20), (_) => ws.add('ping'));
      ws.listen(
        (msg) {
          if (msg is! String) return;
          final j = jsonDecode(msg) as Map<String, dynamic>;
          final type = j['type'] as String?;
          if (type == null || type == 'pong' || type == 'hello') return;
          _events.add(CoreEvent.fromJson(j));
        },
        onDone: _lost,
        onError: (_) => _lost(),
        cancelOnError: true,
      );
    } catch (_) {
      _lost();
    }
  }

  void _lost() {
    _ping?.cancel();
    _ws = null;
    if (_closed) return;
    _set(Link.offline);
    final delay = Duration(milliseconds: min(30000, 800 * pow(2, _attempt++).toInt()));
    _retry = Timer(delay, connect);
  }

  /// App came to the foreground / network changed: try now instead of waiting for the backoff.
  void nudge() {
    if (_ws == null) {
      _attempt = 0;
      connect();
    }
  }

  void close() {
    _closed = true;
    _retry?.cancel();
    _ping?.cancel();
    _ws?.close();
  }
}
