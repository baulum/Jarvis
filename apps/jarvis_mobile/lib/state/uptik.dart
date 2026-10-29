import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'providers.dart';

/// UpTik connector (publishing over the platforms' web pages): data of /api/uptik and the pure helpers the screen
/// uses. Same endpoints as the Mac app; publishing and connecting end in approvals on the Core.
const uptikPlatformLabel = {
  'tiktok': 'TikTok',
  'instagram_reels': 'Instagram Reels',
  'youtube_shorts': 'YouTube Shorts',
};

const uptikCaptionMax = {'tiktok': 2200, 'instagram_reels': 2200, 'youtube_shorts': 5000};

const _postLabel = {
  'queued': 'wartet',
  'running': 'läuft',
  'retry_wait': 'neuer Versuch geplant',
  'published': 'veröffentlicht',
  'failed': 'fehlgeschlagen',
  'cancelled': 'abgebrochen',
  'blocked': 'angehalten',
  'needs_review': 'prüfen',
};

String uptikPostLabel(Object? status) => _postLabel['$status'] ?? '$status';

/// "ok" | "warn" | "bad" | "idle" – the screen maps it to the theme colours.
String uptikPostTone(Object? status) => switch ('$status') {
      'published' => 'ok',
      'failed' || 'blocked' => 'bad',
      'needs_review' || 'retry_wait' => 'warn',
      _ => 'idle',
    };

String uptikAccountState(Map<String, dynamic> a) {
  if (a['circuit_state'] == 'tripped') return 'Angehalten: ${a['circuit_reason'] ?? 'manuelle Prüfung nötig'}';
  if (a['login_running'] == true || a['status'] == 'connecting') return 'Anmeldung läuft im Browserfenster';
  return switch (a['status']) {
    'connected' => 'Verbunden',
    'login_failed' => 'Anmeldung fehlgeschlagen${a['last_error'] != null ? ': ${a['last_error']}' : ''}',
    _ => 'Nicht angemeldet',
  };
}

/// "#Web, kmu  #web" → ["#Web", "#kmu"]; the Core normalizes again and rejects invalid tags.
List<String> parseHashtags(String raw) {
  final seen = <String>{};
  final out = <String>[];
  for (final part in raw.split(RegExp(r'[\s,;]+'))) {
    final tag = part.trim().replaceFirst(RegExp(r'^#+'), '');
    if (tag.isEmpty || !seen.add(tag.toLowerCase())) continue;
    out.add('#$tag');
  }
  return out;
}

String uptikPostText(String caption, List<String> tags) {
  final body = caption.trim();
  return tags.isEmpty ? body : '$body\n\n${tags.join(' ')}'.trim();
}

List<String> uptikOverLimit(String caption, List<String> tags, Iterable<String> platforms) {
  final len = uptikPostText(caption, tags).length;
  return [for (final p in platforms) if (len > (uptikCaptionMax[p] ?? 2200)) p];
}

/// Picked date + time (phone's local clock = Berlin for Sir) → the Core's wall-clock format; null = publish now.
String? uptikPublishAt(DateTime? local) {
  if (local == null) return null;
  String two(int n) => n.toString().padLeft(2, '0');
  return '${local.year}-${two(local.month)}-${two(local.day)} ${two(local.hour)}:${two(local.minute)}';
}

/// Video source of the publish form: 'inbox' (Eingang) → `inbox:<id>`, 'upload' (Upload-Ordner) → `upload:<name>`.
String? uptikVideoRef(String source, String? value) {
  final v = value?.trim() ?? '';
  return v.isEmpty ? null : '$source:$v';
}

/// "3f9a01bc-Mein Clip.mp4" → "Mein Clip" (the Core prefixes uploads with 8 hex characters).
String uptikUploadTitle(String name) =>
    name.replaceFirst(RegExp(r'^[0-9a-f]{8}-'), '').replaceFirst(RegExp(r'\.[^.]+$'), '');

Map<String, dynamic> _map(Object? o) => (o as Map).cast<String, dynamic>();
List<Map<String, dynamic>> _list(Object? o) => ((o as List?) ?? const []).map(_map).toList();

final uptikStatusProvider = FutureProvider.autoDispose<Map<String, dynamic>>((ref) async {
  return _map(await ref.read(apiProvider).get('/api/uptik/status'));
});

final uptikInboxProvider = FutureProvider.autoDispose<List<Map<String, dynamic>>>((ref) async {
  return _list(await ref.read(apiProvider).get('/api/uptik/inbox'));
});

final uptikUploadsProvider = FutureProvider.autoDispose<List<Map<String, dynamic>>>((ref) async {
  return _list(await ref.read(apiProvider).get('/api/uptik/uploads'));
});

final uptikQueueProvider = FutureProvider.autoDispose<List<Map<String, dynamic>>>((ref) async {
  return _list(await ref.read(apiProvider).get('/api/uptik/queue'));
});

final uptikHistoryProvider = FutureProvider.autoDispose<List<Map<String, dynamic>>>((ref) async {
  return _list(await ref.read(apiProvider).get('/api/uptik/history?limit=60'));
});
