import 'dart:math';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'providers.dart';

/// Kundensuche (PJE-Sales-AI Lead-Tool in Jarvis): data of /api/sales and the pure helpers the screen uses. Same
/// endpoints as the Mac app. „Neu formulieren“ goes through the Core's approvals; approving and sending a draft stay
/// in the Lead-Tool itself on the Mac.

/// "ok" | "warn" | "bad" | "idle" | "info" – colour meaning as in the Lead-Tool (needs a human = amber).
String salesStageTone(Object? stage) => switch ('$stage') {
      'review' => 'warn',
      'error' => 'bad',
      'contact' => 'ok',
      'out' || 'null' => 'idle',
      _ => 'info',
    };

String salesScore(Object? score, [Object? priority]) {
  if (score == null) return '–';
  return priority == null ? '$score' : '$score · $priority';
}

/// The Lead-Tool's own interface, if the phone can reach it (a loopback address only works on the Mac itself).
String? salesLeadToolLink(Object? webUrl, [String path = '']) {
  final raw = webUrl is String ? webUrl.trim() : '';
  final u = Uri.tryParse(raw);
  if (raw.isEmpty || u == null || !u.hasScheme || u.host.isEmpty) return null;
  if (u.host == 'localhost' || u.host == '::1' || u.host.startsWith('127.')) return null;
  final base = raw.replaceFirst(RegExp(r'/+$'), '');
  return path.isEmpty ? base : '$base${path.startsWith('/') ? path : '/$path'}';
}

/// What the Core answered to an action, as one line for a snackbar.
String salesActionText(Object? result, String done) {
  final r = result is Map ? result : const {};
  return switch (r['status']) {
    'approval_required' => 'Freigabe nötig – unter Aktivität bestätigen',
    'duplicate' => 'Wurde bereits ausgeführt',
    'in_progress' => 'Läuft bereits',
    _ => done,
  };
}

final _rnd = Random.secure();

/// A fresh id per submission: a retried request does not run twice on the Core, a new submission does.
String salesRequestId() => 'req-${List.generate(16, (_) => _rnd.nextInt(16).toRadixString(16)).join()}';

/// Query of /api/sales/leads (empty filters are left out).
String salesLeadsPath({String? campaignId, String? status, int limit = 50}) {
  final q = <String, String>{'limit': '$limit', 'campaign_id': ?_blank(campaignId), 'status': ?_blank(status)};
  return Uri(path: '/api/sales/leads', queryParameters: q).toString();
}

String? _blank(String? v) => (v == null || v.isEmpty) ? null : v;

Map<String, dynamic> _map(Object? o) => (o as Map).cast<String, dynamic>();
List<Map<String, dynamic>> _list(Object? o) => ((o as List?) ?? const []).map(_map).toList();

final salesStatusProvider = FutureProvider.autoDispose<Map<String, dynamic>>((ref) async {
  return _map(await ref.read(apiProvider).get('/api/sales/status'));
});

final salesAnalyticsProvider = FutureProvider.autoDispose<Map<String, dynamic>>((ref) async {
  return _map(await ref.read(apiProvider).get('/api/sales/analytics'));
});

final salesStatusesProvider = FutureProvider.autoDispose<List<Map<String, dynamic>>>((ref) async {
  return _list(await ref.read(apiProvider).get('/api/sales/statuses'));
});

final salesCampaignsProvider = FutureProvider.autoDispose<List<Map<String, dynamic>>>((ref) async {
  return _list(await ref.read(apiProvider).get('/api/sales/campaigns'));
});

final salesReviewProvider = FutureProvider.autoDispose<List<Map<String, dynamic>>>((ref) async {
  return _list(await ref.read(apiProvider).get('/api/sales/review'));
});

final salesOutboxProvider = FutureProvider.autoDispose<Map<String, dynamic>>((ref) async {
  return _map(await ref.read(apiProvider).get('/api/sales/outbox'));
});

/// Leads for (campaign id, status); '' = no filter.
final salesLeadsProvider = FutureProvider.autoDispose.family<Map<String, dynamic>, (String, String)>((ref, f) async {
  return _map(await ref.read(apiProvider).get(salesLeadsPath(campaignId: f.$1, status: f.$2)));
});

final salesLeadProvider = FutureProvider.autoDispose.family<Map<String, dynamic>, String>((ref, id) async {
  return _map(await ref.read(apiProvider).get('/api/sales/leads/${Uri.encodeComponent(id)}'));
});
