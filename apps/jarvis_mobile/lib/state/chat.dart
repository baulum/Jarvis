import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/api.dart';
import '../core/live.dart';
import 'providers.dart';
import 'timeline.dart';

class ChatState {
  const ChatState({this.sessionId, this.timeline = const Timeline(), this.sending = false, this.loading = false, this.loadingOlder = false, this.error});
  final String? sessionId;
  final Timeline timeline;
  final bool sending;
  final bool loading;
  final bool loadingOlder;
  final String? error;

  bool get running => timeline.running;

  ChatState copy({
    String? sessionId,
    Timeline? timeline,
    bool? sending,
    bool? loading,
    bool? loadingOlder,
    String? error,
    bool clearError = false,
  }) =>
      ChatState(
        sessionId: sessionId ?? this.sessionId,
        timeline: timeline ?? this.timeline,
        sending: sending ?? this.sending,
        loading: loading ?? this.loading,
        loadingOlder: loadingOlder ?? this.loadingOlder,
        error: clearError ? null : (error ?? this.error),
      );
}

const _pageTurns = 40;
const _deltaFlush = Duration(milliseconds: 60);
const _maxEarly = 2000;

/// Conversation with the same Jarvis core as the Mac: same sessions, same agent, same memory, same timeline.
/// Text and tool calls arrive as `chat.event` items ordered by `seq`; streamed text as `chat.delta` for an item id.
class ChatController extends Notifier<ChatState> {
  final Map<String, String> _deltas = {};
  Timer? _flush;

  /// While a message is on its way: events of sessions the phone does not show yet. The core may answer in a
  /// session that only `POST /api/chat` names (a new conversation, or a fresh one because the old had expired),
  /// and its first events can arrive before that response.
  List<CoreEvent>? _early;

  @override
  ChatState build() {
    final sock = ref.watch(liveSocketProvider);
    final sub = sock?.events.listen(_onEvent);
    // back online: what happened meanwhile is in the stored timeline – merge it in by seq
    ref.listen(linkProvider, (prev, next) {
      if (next.valueOrNull == Link.online && prev?.valueOrNull != Link.online) _resync();
    });
    ref.onDispose(() {
      sub?.cancel();
      _flush?.cancel();
    });
    return const ChatState();
  }

  Api get _api => ref.read(apiProvider);

  void _resync() {
    final sid = state.sessionId;
    if (sid != null) unawaited(_load(sid).catchError((Object _) {}));
  }

  static String? _sessionOf(CoreEvent ev) => ev.sessionId ?? ev.data['session_id']?.toString();

  void _onEvent(CoreEvent ev) {
    if (!ev.type.startsWith('chat.')) return;
    final sid = _sessionOf(ev);
    if (sid == null) return;
    if (sid != state.sessionId) {
      final early = _early;
      if (early != null && early.length < _maxEarly) early.add(ev);
      return;
    }
    if (ev.type == 'chat.delta' || ev.type == 'chat.thinking') {
      final id = ev.data['event_id']?.toString();
      final text = ev.data['text']?.toString() ?? '';
      if (id == null || text.isEmpty) return;
      _deltas[id] = (_deltas[id] ?? '') + text;
      _flush ??= Timer(_deltaFlush, _flushDeltas);
      return;
    }
    _flushDeltas(); // buffered text belongs before this event
    final next = applyChatEvent(state.timeline, ev.type, ev.data);
    if (!identical(next, state.timeline)) {
      state = state.copy(timeline: next, sending: ev.type == 'chat.done' ? false : null);
    }
  }

  void _flushDeltas() {
    _flush?.cancel();
    _flush = null;
    if (_deltas.isEmpty) return;
    final deltas = Map<String, String>.of(_deltas);
    _deltas.clear();
    state = state.copy(timeline: applyDeltas(state.timeline, deltas));
  }

  Future<void> _load(String sessionId, {bool older = false}) async {
    final first = state.timeline.items.where((e) => !e.local).map((e) => e.seq).firstOrNull;
    final q = older && first != null ? '&before_seq=$first' : '';
    final res = await _api.get('/api/sessions/$sessionId/events?turns=$_pageTurns$q') as Map;
    if (state.sessionId != sessionId) return;
    final items = ((res['events'] as List?) ?? const []).map((e) => TItem.fromJson((e as Map).cast<String, dynamic>())).toList();
    _flushDeltas();
    state = state.copy(timeline: mergeServer(state.timeline, items, hasMore: res['has_more'] == true, older: older));
  }

  Future<void> open(String sessionId) async {
    _deltas.clear();
    state = ChatState(sessionId: sessionId, loading: true);
    try {
      await _load(sessionId);
      state = state.copy(loading: false, clearError: true);
    } on ApiError catch (e) {
      state = state.copy(loading: false, error: e.message);
    }
  }

  Future<void> loadOlder() async {
    final sid = state.sessionId;
    if (sid == null || state.loadingOlder) return;
    state = state.copy(loadingOlder: true);
    try {
      await _load(sid, older: true);
      state = state.copy(loadingOlder: false);
    } on ApiError catch (e) {
      state = state.copy(loadingOlder: false, error: e.message);
    }
  }

  void fresh() {
    _deltas.clear();
    state = const ChatState();
  }

  Future<void> send(String text) async {
    final t = text.trim();
    if (t.isEmpty || state.sending) return;
    final before = state.timeline;
    final prev = state.sessionId;
    state = state.copy(sending: true, clearError: true, timeline: addOptimisticUser(before, prev ?? '', t));
    final early = <CoreEvent>[];
    _early = early;
    try {
      final res = await _api.post('/api/chat', {'message': t, 'session_id': prev, 'channel': 'mobile'}) as Map;
      final sid = res['session_id'] as String;
      if (state.sessionId != prev) return; // Sir opened another conversation meanwhile; this one is in the history
      if (sid == prev) {
        state = state.copy(sending: false);
      } else {
        // a new conversation – or the core started a fresh one because this one had expired. Its items are numbered
        // from 1 again: merged into the old list they would sort above it, out of sight. Show that session alone.
        _deltas.clear();
        state = ChatState(sessionId: sid, timeline: startSession(state.timeline));
      }
      for (final ev in early) {
        if (_sessionOf(ev) == sid) _onEvent(ev);
      }
      // what happened before the id was known and is not in the replay above: take it from the store
      if (sid != prev) unawaited(_load(sid).catchError((Object _) {}));
    } on ApiError catch (e) {
      state = state.copy(
        sending: false,
        timeline: before,
        error: e.offline ? 'Jarvis ist nicht erreichbar – Nachricht nicht gesendet.' : e.message,
      );
    } finally {
      if (identical(_early, early)) _early = null;
    }
  }

  /// Ticks a task-list item; the state is stored in the message on the core (and synced to the Mac).
  Future<void> toggleCheck(String itemId, int index, bool checked) async {
    final sid = state.sessionId;
    if (sid == null) return;
    state = state.copy(timeline: setCheck(state.timeline, itemId, index, checked));
    try {
      await _api.post('/api/sessions/$sid/events/$itemId/check', {'index': index, 'checked': checked});
    } on ApiError catch (e) {
      state = state.copy(timeline: setCheck(state.timeline, itemId, index, !checked), error: 'Haken nicht gespeichert: ${e.message}');
    }
  }

  Future<void> interrupt() async {
    try {
      await _api.post('/api/chat/interrupt');
    } catch (_) {}
  }
}

final chatProvider = NotifierProvider<ChatController, ChatState>(ChatController.new);

final sessionsProvider = FutureProvider.autoDispose<List<Map<String, dynamic>>>((ref) async {
  final rows = await ref.read(apiProvider).get('/api/sessions?limit=40') as List;
  return rows.map((e) => (e as Map).cast<String, dynamic>()).toList();
});
