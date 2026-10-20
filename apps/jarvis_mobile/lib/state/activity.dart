import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/api.dart';
import 'providers.dart';

/// Everything Jarvis is doing or waiting for: approvals, tasks, notifications, calls – one timeline.
class ActivityData {
  const ActivityData({
    this.approvals = const [],
    this.tasks = const [],
    this.alerts = const [],
    this.calls = const [],
    this.watches = const [],
    this.jobs = const [],
  });
  final List<Map<String, dynamic>> approvals;
  final List<Map<String, dynamic>> tasks;
  final List<Map<String, dynamic>> alerts;
  final List<Map<String, dynamic>> calls;
  final List<Map<String, dynamic>> watches;

  /// coding jobs (agent monitor), newest first
  final List<Map<String, dynamic>> jobs;
}

class ActivityController extends AsyncNotifier<ActivityData> {
  Timer? _debounce;

  @override
  Future<ActivityData> build() async {
    final sock = ref.watch(liveSocketProvider);
    final sub = sock?.events.listen((ev) {
      final t = ev.type;
      // coding.progress fires per tool call: the open monitor sheet follows it, the list only needs status changes
      if (t.startsWith('task.') || (t.startsWith('coding.') && t != 'coding.progress') || t.startsWith('approval.') || t.startsWith('call.') || t == 'mobile.alert' || t == 'mobile.watch') {
        _debounce?.cancel();
        _debounce = Timer(const Duration(milliseconds: 600), reload);
      }
    });
    ref.onDispose(() {
      sub?.cancel();
      _debounce?.cancel();
    });
    return _load();
  }

  Future<ActivityData> _load() async {
    final api = ref.read(apiProvider);
    List<Map<String, dynamic>> rows(dynamic v) => (v as List).map((e) => (e as Map).cast<String, dynamic>()).toList();
    final r = await Future.wait([
      api.get('/api/approvals'),
      api.get('/api/tasks'),
      api.get('/api/mobile/alerts?limit=40'),
      api.get('/api/mobile/calls?limit=30'),
      api.get('/api/mobile/watches'),
    ]);
    var jobs = <Map<String, dynamic>>[];
    try {
      jobs = rows(await api.get('/api/coding/jobs'));
    } on ApiError {
      // a core without the coding endpoints still shows everything else
    }
    final data = ActivityData(approvals: rows(r[0]), tasks: rows(r[1]), alerts: rows(r[2]), calls: rows(r[3]), watches: rows(r[4]), jobs: jobs);
    ref.read(liveProvider.notifier).setPending(data.approvals.length);
    return data;
  }

  Future<void> reload() async {
    try {
      state = AsyncData(await _load());
    } catch (e, st) {
      if (!state.hasValue) state = AsyncError(e, st);
    }
  }
}

final activityProvider = AsyncNotifierProvider<ActivityController, ActivityData>(ActivityController.new);
