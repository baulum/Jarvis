import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/api.dart';
import 'providers.dart';

class Msg {
  const Msg({required this.role, required this.text, this.tools = const [], this.streaming = false, this.error, this.at});
  final String role; // user | assistant
  final String text;
  final List<ToolMark> tools;
  final bool streaming;
  final String? error;
  final DateTime? at;

  Msg copy({String? text, List<ToolMark>? tools, bool? streaming, String? error}) => Msg(
        role: role,
        text: text ?? this.text,
        tools: tools ?? this.tools,
        streaming: streaming ?? this.streaming,
        error: error ?? this.error,
        at: at,
      );
}

class ToolMark {
  const ToolMark(this.name, this.status);
  final String name;
  final String status; // running | ok | failed
}

class ChatState {
  const ChatState({this.sessionId, this.messages = const [], this.sending = false, this.loading = false, this.error});
  final String? sessionId;
  final List<Msg> messages;
  final bool sending;
  final bool loading;
  final String? error;

  ChatState copy({String? sessionId, List<Msg>? messages, bool? sending, bool? loading, String? error, bool clearError = false}) =>
      ChatState(
        sessionId: sessionId ?? this.sessionId,
        messages: messages ?? this.messages,
        sending: sending ?? this.sending,
        loading: loading ?? this.loading,
        error: clearError ? null : (error ?? this.error),
      );
}

/// Conversation with the same Jarvis core as the Mac: same sessions, same agent, same memory.
/// Replies stream in via `chat.delta` events of the session.
class ChatController extends Notifier<ChatState> {
  @override
  ChatState build() {
    final sock = ref.watch(liveSocketProvider);
    final sub = sock?.events.listen(_onEvent);
    ref.onDispose(() => sub?.cancel());
    return const ChatState();
  }

  Api get _api => ref.read(apiProvider);

  void _onEvent(dynamic ev) {
    final sid = ev.sessionId as String? ?? ev.data['session_id']?.toString();
    if (sid == null || sid != state.sessionId) return;
    final msgs = [...state.messages];
    Msg? last = msgs.isNotEmpty && msgs.last.role == 'assistant' && msgs.last.streaming ? msgs.last : null;
    void put(Msg m) {
      if (last != null) {
        msgs[msgs.length - 1] = m;
      } else {
        msgs.add(m);
      }
      last = m;
    }

    switch (ev.type as String) {
      case 'chat.delta':
        final t = ev.data['text']?.toString() ?? '';
        if (t.isEmpty) return;
        put((last ?? const Msg(role: 'assistant', text: '', streaming: true)).copy(text: (last?.text ?? '') + t));
      case 'chat.tool_started':
        final name = ((ev.data['data'] as Map?)?['name'] ?? 'Werkzeug').toString().split('__').last;
        final base = last ?? const Msg(role: 'assistant', text: '', streaming: true);
        put(base.copy(tools: [...base.tools, ToolMark(name, 'running')]));
      case 'chat.tool_completed':
      case 'chat.tool_failed':
        if (last == null) return;
        final ok = ev.type == 'chat.tool_completed';
        final tools = [...last!.tools];
        final i = tools.lastIndexWhere((t) => t.status == 'running');
        if (i >= 0) tools[i] = ToolMark(tools[i].name, ok ? 'ok' : 'failed');
        put(last!.copy(tools: tools));
      case 'chat.done':
        final text = ev.data['text']?.toString() ?? last?.text ?? '';
        final err = ev.data['error']?.toString();
        put((last ?? const Msg(role: 'assistant', text: '')).copy(text: text, streaming: false, error: err));
        state = state.copy(messages: msgs, sending: false);
        return;
      default:
        return;
    }
    state = state.copy(messages: msgs);
  }

  Future<void> open(String sessionId) async {
    state = ChatState(sessionId: sessionId, loading: true);
    try {
      final rows = await _api.get('/api/sessions/$sessionId/messages') as List;
      state = state.copy(
        loading: false,
        messages: [
          for (final r in rows.cast<Map>())
            if (r['role'] == 'user' || r['role'] == 'assistant')
              Msg(role: r['role'] as String, text: r['content'].toString(), at: DateTime.tryParse(r['created_at'].toString())),
        ],
      );
    } on ApiError catch (e) {
      state = state.copy(loading: false, error: e.message);
    }
  }

  void fresh() => state = const ChatState();

  Future<void> send(String text) async {
    final t = text.trim();
    if (t.isEmpty || state.sending) return;
    state = state.copy(
      sending: true,
      clearError: true,
      messages: [...state.messages, Msg(role: 'user', text: t, at: DateTime.now()), const Msg(role: 'assistant', text: '', streaming: true)],
    );
    try {
      final res = await _api.post('/api/chat', {'message': t, 'session_id': state.sessionId, 'channel': 'mobile'}) as Map;
      state = state.copy(sessionId: res['session_id'] as String);
    } on ApiError catch (e) {
      final msgs = [...state.messages]..removeLast();
      state = state.copy(sending: false, messages: msgs, error: e.offline ? 'Jarvis ist nicht erreichbar – Nachricht nicht gesendet.' : e.message);
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
