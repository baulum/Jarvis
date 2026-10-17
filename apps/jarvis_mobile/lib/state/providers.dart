import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/api.dart';
import '../core/live.dart';
import '../core/native.dart';
import 'live_model.dart';

final nativeProvider = Provider<NativeBridge>((ref) => NativeBridge());
final apiProvider = Provider<Api>((ref) => Api(ref.watch(nativeProvider)));

// ------------------------------------------------------------------ pairing / session
class Pairing {
  const Pairing({required this.paired, this.me = const {}, this.status = const {}, this.push = const {}});
  final bool paired;
  final Map<String, dynamic> me;
  final Map<String, dynamic> status;
  final Map<String, dynamic> push;
}

class PairingController extends AsyncNotifier<Pairing> {
  @override
  Future<Pairing> build() async {
    final native = ref.read(nativeProvider);
    final st = await native.status();
    if (st['paired'] != true) return Pairing(paired: false, status: st);
    await ref.read(apiProvider).init();
    return _withMe(st);
  }

  Future<Pairing> _withMe(Map<String, dynamic> st) async {
    final native = ref.read(nativeProvider);
    Map<String, dynamic> me = const {};
    Map<String, dynamic> push = const {};
    try {
      me = (await ref.read(apiProvider).get('/api/mobile/me') as Map).cast<String, dynamic>();
      final fb = me['firebase'];
      push = await native.configurePush(fb == null ? null : jsonEncode(fb));
    } on ApiError catch (e) {
      if (e.status == 401) {
        await native.forget();
        return Pairing(paired: false, status: await native.status());
      }
      // offline: still paired; the UI shows "nicht erreichbar"
    }
    return Pairing(paired: true, me: me, status: await native.status(), push: push);
  }

  Future<void> pair(String payload, String name) async {
    final native = ref.read(nativeProvider);
    await native.pair(payload, name);
    await ref.read(apiProvider).init();
    state = AsyncData(await _withMe(await native.status()));
  }

  Future<void> refresh() async {
    final native = ref.read(nativeProvider);
    final st = await native.status();
    state = AsyncData(st['paired'] == true ? await _withMe(st) : Pairing(paired: false, status: st));
  }

  Future<void> logout() async {
    await ref.read(nativeProvider).logout();
    state = AsyncData(Pairing(paired: false, status: await ref.read(nativeProvider).status()));
  }
}

final pairingProvider = AsyncNotifierProvider<PairingController, Pairing>(PairingController.new);
final isPairedProvider = Provider<bool>((ref) => ref.watch(pairingProvider).valueOrNull?.paired ?? false);

// ------------------------------------------------------------------ live connection
final liveSocketProvider = Provider<LiveSocket?>((ref) {
  if (!ref.watch(isPairedProvider)) return null;
  final s = LiveSocket(ref.watch(apiProvider))..connect();
  ref.onDispose(s.close);
  return s;
});

final linkProvider = StreamProvider<Link>((ref) async* {
  final s = ref.watch(liveSocketProvider);
  if (s == null) {
    yield Link.offline;
    return;
  }
  yield s.current;
  yield* s.link;
});

final eventsProvider = StreamProvider<CoreEvent>((ref) {
  final s = ref.watch(liveSocketProvider);
  return s?.events ?? const Stream.empty();
});

class LiveController extends Notifier<LiveModel> {
  Timer? _tick;
  @override
  LiveModel build() {
    final s = ref.watch(liveSocketProvider);
    final sub = s?.events.listen((ev) => state = state.apply(ev, DateTime.now()));
    _tick = Timer.periodic(const Duration(seconds: 1), (_) => state = state.expire(DateTime.now()));
    ref.onDispose(() {
      sub?.cancel();
      _tick?.cancel();
    });
    if (s != null) _loadPending();
    return const LiveModel();
  }

  Future<void> _loadPending() async {
    try {
      final list = await ref.read(apiProvider).get('/api/approvals') as List;
      state = state.copyWith(pendingApprovals: list.length);
    } catch (_) {}
  }

  void setPending(int n) => state = state.copyWith(pendingApprovals: n);
}

final liveProvider = NotifierProvider<LiveController, LiveModel>(LiveController.new);

// ------------------------------------------------------------------ calls (native Telecom + LiveKit)
class CallTranscript {
  const CallTranscript(this.role, this.text);
  final String role;
  final String text;
}

class CallInfo {
  const CallInfo({
    this.callId,
    this.direction = '',
    this.status = 'idle',
    this.subject = '',
    this.muted = false,
    this.endpoint,
    this.endpoints = const [],
    this.activeSince,
    this.error,
    this.endReason,
    this.agentState = 'connecting',
    this.agentDetail,
    this.transcript = const [],
    this.activity,
  });
  final String? callId;
  final String direction;
  final String status;
  final String subject;
  final bool muted;
  final Map<String, dynamic>? endpoint;
  final List<Map<String, dynamic>> endpoints;
  final DateTime? activeSince;
  final String? error;
  final String? endReason;
  final String agentState; // listening | thinking | speaking | tool (from Jarvis over the data channel)
  final String? agentDetail;
  final List<CallTranscript> transcript;
  final String? activity; // real tool/memory activity label ("Prüfe den Kalender …")

  bool get live => status != 'idle' && status != 'ended';

  CallInfo merge(Map<String, dynamic> s) {
    final fresh = s['call_id'] != callId && s['status'] == 'ringing' || (status == 'ended' && s['status'] != 'ended');
    final base = fresh ? const CallInfo() : this;
    return CallInfo(
      callId: s['call_id'] as String?,
      direction: s['direction'] as String? ?? '',
      status: s['status'] as String? ?? 'idle',
      subject: s['subject'] as String? ?? '',
      muted: s['muted'] == true,
      endpoint: (s['endpoint'] as Map?)?.cast<String, dynamic>(),
      endpoints: ((s['endpoints'] as List?) ?? const []).map((e) => (e as Map).cast<String, dynamic>()).toList(),
      activeSince: s['active_since'] == null ? null : DateTime.fromMillisecondsSinceEpoch((s['active_since'] as num).toInt()),
      error: s['error'] as String?,
      endReason: s['end_reason'] as String?,
      agentState: base.agentState,
      agentDetail: base.agentDetail,
      transcript: base.transcript,
      activity: base.activity,
    );
  }

  CallInfo withData(Map<String, dynamic> m) {
    switch (m['type']) {
      case 'state':
        final st = m['state'] as String? ?? agentState;
        return _copy(agentState: st, agentDetail: m['detail'] as String?, activity: st == 'tool' ? m['detail'] as String? : (st == 'speaking' ? activity : null));
      case 'transcript':
        return _copy(transcript: [...transcript, CallTranscript(m['role'] as String, m['text'] as String)]);
      case 'activity':
        if (m['kind'] == 'memory') return _copy(activity: m['label'] as String?);
        if (m['status'] == 'started') return _copy(activity: m['label'] as String?);
        return this;
      default:
        return this;
    }
  }

  CallInfo _copy({String? agentState, String? agentDetail, List<CallTranscript>? transcript, String? activity}) => CallInfo(
        callId: callId,
        direction: direction,
        status: status,
        subject: subject,
        muted: muted,
        endpoint: endpoint,
        endpoints: endpoints,
        activeSince: activeSince,
        error: error,
        endReason: endReason,
        agentState: agentState ?? this.agentState,
        agentDetail: agentDetail,
        transcript: transcript ?? this.transcript,
        activity: activity,
      );
}

/// Audio levels (Jarvis' voice, Sir's mic) – a ValueNotifier, not state: 30 Hz must not rebuild widgets.
class Levels extends ValueNotifier<({double out, double inp})> {
  Levels() : super((out: 0, inp: 0));
}

final levelsProvider = Provider<Levels>((ref) => Levels());

class CallController extends Notifier<CallInfo> {
  @override
  CallInfo build() {
    final native = ref.watch(nativeProvider);
    final levels = ref.watch(levelsProvider);
    final sub = native.events.listen((e) {
      switch (e.type) {
        case 'call_state':
          state = state.merge((e.data as Map).cast<String, dynamic>());
        case 'call_data':
          state = state.withData((e.data as Map).cast<String, dynamic>());
        case 'call_level':
          final m = e.data as Map;
          levels.value = (out: (m['out'] as num).toDouble(), inp: (m['in'] as num).toDouble());
      }
    });
    ref.onDispose(sub.cancel);
    native.callState().then((s) => state = state.merge(s)).catchError((Object _) => state);
    return const CallInfo();
  }
}

final callProvider = NotifierProvider<CallController, CallInfo>(CallController.new);

final modeProvider = Provider<Mode>((ref) {
  final link = ref.watch(linkProvider).valueOrNull ?? Link.connecting;
  final call = ref.watch(callProvider);
  final live = ref.watch(liveProvider);
  return deriveMode(link: link, callStatus: call.status, live: live, now: DateTime.now());
});

// ------------------------------------------------------------------ availability (honest call button)
final availabilityProvider = FutureProvider.autoDispose<Map<String, dynamic>>((ref) async {
  ref.watch(linkProvider);
  return (await ref.read(apiProvider).get('/api/mobile/calls/availability') as Map).cast<String, dynamic>();
});
