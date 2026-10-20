import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/monitor.dart';
import 'providers.dart';

/// Live snapshot of one coding job for the agent monitor: refetched when the job's events arrive over the
/// socket (debounced) and every few seconds as a safety net. Controls go through the same endpoints as the desktop.
class JobMonitorController extends AutoDisposeFamilyAsyncNotifier<JobMonitor, String> {
  Timer? _debounce;
  bool _disposed = false;

  @override
  Future<JobMonitor> build(String arg) async {
    final sock = ref.watch(liveSocketProvider);
    final sub = sock?.events.listen((ev) {
      if ((ev.type == 'coding.progress' || ev.type == 'coding.updated') && ev.data['job_id'] == arg) {
        _debounce?.cancel();
        _debounce = Timer(const Duration(milliseconds: 500), reload);
      }
    });
    final poll = Timer.periodic(const Duration(seconds: 6), (_) => reload());
    ref.onDispose(() {
      _disposed = true;
      sub?.cancel();
      _debounce?.cancel();
      poll.cancel();
    });
    return _fetch();
  }

  Future<JobMonitor> _fetch() async {
    final raw = await ref.read(apiProvider).get('/api/coding/jobs/$arg/monitor') as Map;
    return JobMonitor.fromJson(raw.cast<String, dynamic>());
  }

  /// Keeps the last snapshot when the refetch fails (offline blips).
  Future<void> reload() async {
    if (_disposed) return;
    try {
      final fresh = await _fetch();
      if (!_disposed) state = AsyncData(fresh);
    } catch (_) {}
  }

  Future<void> pause() => _act('pause');
  Future<void> resume() => _act('resume');
  Future<void> cancel() => _act('cancel');

  Future<void> _act(String action) async {
    await ref.read(apiProvider).post('/api/coding/jobs/$arg/$action');
    await reload();
  }

  /// [immediate] stops the agent's current run and continues the session with the message; otherwise the
  /// message is delivered when the current run ends.
  Future<void> message(String text, {bool immediate = false}) async {
    await ref.read(apiProvider).post('/api/coding/jobs/$arg/message', {'text': text, 'immediate': immediate});
    await reload();
  }
}

final jobMonitorProvider =
    AsyncNotifierProvider.autoDispose.family<JobMonitorController, JobMonitor, String>(JobMonitorController.new);
