import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/api.dart';
import 'providers.dart';

const modelAliases = ['haiku', 'sonnet', 'opus'];
const modelBlurb = {
  'haiku': 'Schnell und sparsam',
  'sonnet': 'Standard – ausgewogen',
  'opus': 'Stärkste Denkleistung',
};

const efforts = [
  ('auto', 'Auto', 'Automatisch'),
  ('low', 'Niedrig', 'Schnell, wenig Nachdenken'),
  ('medium', 'Mittel', 'Ausgewogen'),
  ('high', 'Hoch', 'Gründlich'),
  ('xhigh', 'Sehr hoch', 'Sehr gründlich, langsamer'),
  ('max', 'Max', 'Maximaler Denkaufwand'),
];

/// One quota window as reported by Claude Code (5 hours / 7 days).
class QuotaWindow {
  const QuotaWindow(this.utilization, this.resetsInS, this.stale);
  final double utilization; // 0..1
  final int? resetsInS;
  final bool stale;

  static QuotaWindow? parse(Object? raw) {
    if (raw is! Map) return null;
    return QuotaWindow(
      (raw['utilization'] as num?)?.toDouble() ?? 0,
      (raw['resets_in_s'] as num?)?.toInt(),
      raw['stale'] == true,
    );
  }

  String get percent => '${(utilization * 100).round()} %';

  String get resetLabel {
    final s = resetsInS;
    if (s == null) return '';
    final h = s ~/ 3600, m = (s % 3600) ~/ 60;
    return h > 0 ? 'Reset in $h h $m min' : 'Reset in $m min';
  }
}

/// Which Claude model Jarvis uses (and the real subscription quota) – mirrors `/api/agent/model` + `/api/agent/usage`.
class AgentInfo {
  const AgentInfo({
    this.defaultModel = 'sonnet',
    this.auto = true,
    this.sessionOverride,
    this.effort,
    this.fiveHour,
    this.sevenDay,
    this.quotaKnown = false,
  });
  final String defaultModel;
  final bool auto; // automatic Haiku for simple requests
  final String? sessionOverride;
  final String? effort; // null = Claude Code default
  final QuotaWindow? fiveHour;
  final QuotaWindow? sevenDay;
  final bool quotaKnown;

  /// What the next answer uses by default: a conversation override beats the standard.
  String get active => sessionOverride ?? defaultModel;
  String get activeLabel => auto && sessionOverride == null && defaultModel == 'sonnet' ? 'Auto' : _cap(active);
  String get effortLabel => efforts.firstWhere((e) => e.$1 == (effort ?? 'auto'), orElse: () => efforts.first).$2;
  double get pressure => [fiveHour?.utilization ?? 0, sevenDay?.utilization ?? 0].reduce((a, b) => a > b ? a : b);

  static String _cap(String s) => s.isEmpty ? s : s[0].toUpperCase() + s.substring(1);
}

class AgentInfoController extends AsyncNotifier<AgentInfo> {
  String? sessionId;

  @override
  Future<AgentInfo> build() async {
    final live = ref.watch(liveSocketProvider);
    if (live != null) {
      final sub = live.events.listen((e) {
        if (e.type == 'agent.model' || e.type == 'agent.usage') unawaited(reload());
      });
      ref.onDispose(sub.cancel);
    }
    return _fetch();
  }

  Future<AgentInfo> _fetch() async {
    final api = ref.read(apiProvider);
    final m = (await api.get('/api/agent/model${sessionId == null ? '' : '?session_id=$sessionId'}') as Map).cast<String, dynamic>();
    Map<String, dynamic> u = const {};
    try {
      u = (await api.get('/api/agent/usage') as Map).cast<String, dynamic>();
    } on ApiError {
      // older core without quota support: the picker still works
    }
    final w = (u['windows'] as Map?) ?? const {};
    return AgentInfo(
      defaultModel: (m['default'] as String?) ?? 'sonnet',
      auto: m['auto_downgrade'] == true,
      sessionOverride: m['session_override'] as String?,
      effort: m['effort'] as String?,
      fiveHour: QuotaWindow.parse(w['five_hour']),
      sevenDay: QuotaWindow.parse(w['seven_day']),
      quotaKnown: u['known'] == true,
    );
  }

  Future<void> reload() async {
    try {
      state = AsyncData(await _fetch());
    } on ApiError {
      // keep the last known value
    }
  }

  /// [model] null = automatic selection. [scope]: `default` (from now on) or `session` (this conversation).
  Future<void> setModel(String? model, {String scope = 'default', String? session}) async {
    sessionId = session ?? sessionId;
    await ref.read(apiProvider).post('/api/agent/model', {
      'model': model,
      'scope': scope,
      'session_id': ?session,
    });
    await reload();
  }

  /// [effort] 'auto' = Claude Code default; applies from the next answer on.
  Future<void> setEffort(String effort) async {
    await ref.read(apiProvider).post('/api/agent/model', {'model': null, 'scope': 'default', 'effort': effort});
    await reload();
  }

  Future<void> refreshQuota() async {
    await ref.read(apiProvider).post('/api/agent/usage/refresh');
    await reload();
  }
}

final agentInfoProvider = AsyncNotifierProvider<AgentInfoController, AgentInfo>(AgentInfoController.new);
