import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'providers.dart';

/// Social Media (daily video per company profile – PJE Systems, Apo HR …): data of /api/social and the pure helpers
/// the screen uses.
const socialPlatforms = ['instagram', 'facebook', 'tiktok', 'youtube'];

const platformLabel = {
  'instagram': 'Instagram Reels',
  'facebook': 'Facebook Reels',
  'tiktok': 'TikTok',
  'youtube': 'YouTube Shorts',
};

const manualUpload = {
  'instagram': 'https://www.instagram.com/',
  'facebook': 'https://www.facebook.com/reels/create',
  'tiktok': 'https://www.tiktok.com/upload',
  'youtube': 'https://studio.youtube.com/',
};

const stageLabel = {
  'concept': 'Idee',
  'render': 'Rendern',
  'qa': 'Qualitätskontrolle',
  'captions': 'Captions',
  'library': 'Bibliothek',
  'email': 'E-Mail',
  'done': 'Fertig',
};

const publicationLabel = {
  'scheduled': 'geplant',
  'waiting_approval': 'wartet auf Freigabe',
  'publishing': 'wird veröffentlicht',
  'published': 'veröffentlicht',
  'failed': 'fehlgeschlagen',
  'manual': 'manuell',
};

String videoStatusLabel(Map<String, dynamic> v) {
  final stage = stageLabel[v['stage']] ?? '${v['stage'] ?? ''}';
  return switch (v['status']) {
    'ready' => 'Fertig',
    'failed' => 'Fehlgeschlagen ($stage)',
    _ => 'In Arbeit · $stage',
  };
}

/// The text as it is pasted into the platform: caption (YouTube: description), blank line, hashtags.
String captionText(String platform, Map<String, dynamic>? c) {
  if (c == null) return '';
  final body = ((platform == 'youtube' ? c['description'] : c['text']) ?? '').toString().trim();
  final tags = ((c['hashtags'] as List?) ?? const []).join(' ');
  return tags.isEmpty ? body : '$body\n\n$tags'.trim();
}

bool validTime(String t) => RegExp(r'^([01]\d|2[0-3]):[0-5]\d$').hasMatch(t);

bool validEmail(String a) => RegExp(r'^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$').hasMatch(a);

bool validHex(String c) => RegExp(r'^#[0-9a-fA-F]{6}$').hasMatch(c);

/// Comma/semicolon/line separated input → trimmed, non-empty entries.
List<String> splitList(String raw) => raw.split(RegExp(r'[,;\n]')).map((x) => x.trim()).where((x) => x.isNotEmpty).toList();

/// Labels of the profile fields a production still needs (the Core sends them as `missing`).
List<String> missingFields(Map<String, dynamic> company) => ((company['missing'] as List?) ?? const []).map((e) => '$e').toList();

/// Query string for one company (empty = the active profile on the Core).
String companyQuery(String? company, {String sep = '?'}) =>
    company == null || company.isEmpty ? '' : '${sep}company=${Uri.encodeQueryComponent(company)}';

/// A bare, safe file name for the downloaded MP4 (the Core's name when it is one).
String safeVideoName(Map<String, dynamic> v) {
  final name = v['file_name'];
  if (name is String && RegExp(r'^[\w.-]+\.mp4$').hasMatch(name)) return name;
  final t = '${v['title'] ?? ''}'.replaceAll(RegExp(r'[^\w-]+'), '-').replaceAll(RegExp(r'^-+|-+$'), '');
  return '${t.isEmpty ? 'pje-video' : (t.length > 60 ? t.substring(0, 60) : t)}.mp4';
}

Map<String, dynamic> _map(Object? o) => (o as Map).cast<String, dynamic>();

/// Company profiles and the active one (the default of Jarvis' tools and of this screen).
final socialCompaniesProvider = FutureProvider.autoDispose<Map<String, dynamic>>((ref) async {
  return _map(await ref.read(apiProvider).get('/api/social/companies'));
});

/// The company shown in the screen; null = the active profile on the Core.
final socialCompanyProvider = StateProvider<String?>((ref) => null);

final socialStatusProvider = FutureProvider.autoDispose<Map<String, dynamic>>((ref) async {
  final company = ref.watch(socialCompanyProvider);
  return _map(await ref.read(apiProvider).get('/api/social/status${companyQuery(company)}'));
});

final socialVideosProvider = FutureProvider.autoDispose<List<Map<String, dynamic>>>((ref) async {
  final company = ref.watch(socialCompanyProvider);
  final res = _map(await ref.read(apiProvider).get('/api/social/videos?limit=60${companyQuery(company, sep: '&')}'));
  return ((res['videos'] as List?) ?? const []).map(_map).toList();
});

final socialVideoProvider = FutureProvider.autoDispose.family<Map<String, dynamic>, String>((ref, id) async {
  return _map(await ref.read(apiProvider).get('/api/social/videos/$id'));
});

final socialSettingsProvider = FutureProvider.autoDispose<Map<String, dynamic>>((ref) async {
  final company = ref.watch(socialCompanyProvider);
  return _map(await ref.read(apiProvider).get('/api/social/settings${companyQuery(company)}'));
});
