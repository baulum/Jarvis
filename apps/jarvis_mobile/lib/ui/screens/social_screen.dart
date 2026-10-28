import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:path_provider/path_provider.dart';
import 'package:video_player/video_player.dart';

import '../../core/api.dart';
import '../../core/theme.dart';
import '../../state/providers.dart';
import '../../state/social.dart';
import '../widgets/hud.dart';

/// Social Media: the daily PJE Systems video – overview, library (player, captions to copy, regenerate,
/// publish/manual posting), automation switches and the platform status. Same API as the Mac app.
class SocialScreen extends ConsumerStatefulWidget {
  const SocialScreen({super.key});
  @override
  ConsumerState<SocialScreen> createState() => _SocialScreenState();
}

enum _Tab { overview, library, automation, connector }

class _SocialScreenState extends ConsumerState<SocialScreen> {
  _Tab _tab = _Tab.overview;

  void _toast(String msg) {
    ScaffoldMessenger.maybeOf(context)?.showSnackBar(SnackBar(content: Text(msg), backgroundColor: J.panel));
  }

  Future<void> _run(Future<dynamic> Function() fn, String done) async {
    try {
      final r = await fn();
      if (r is Map && r['status'] == 'approval_required') {
        _toast('Freigabe nötig – unter Aktivität bestätigen');
      } else {
        _toast(done);
      }
      ref.invalidate(socialStatusProvider);
      ref.invalidate(socialVideosProvider);
    } on ApiError catch (e) {
      _toast(e.offline ? 'Jarvis ist nicht erreichbar' : e.message);
    }
  }

  Future<void> _produce({String? parent}) async {
    final hint = await showDialog<String>(context: context, builder: (_) => _HintDialog(regenerate: parent != null));
    if (hint == null) return;
    final api = ref.read(apiProvider);
    await _run(
      () => parent == null ? api.post('/api/social/produce', {'hint': hint}) : api.post('/api/social/videos/$parent/regenerate', {'hint': hint}),
      'Produktion gestartet',
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: J.void_,
      body: SafeArea(
        child: Column(children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(J.s2, J.s2, J.s4, J.s2),
            child: Row(children: [
              Pressable(
                onTap: () => context.canPop() ? context.pop() : context.go('/'),
                semantics: 'Zurück',
                child: const SizedBox.square(dimension: J.hit, child: Center(child: Glyph(GlyphKind.back))),
              ),
              Expanded(child: Text('Social Media', style: J.title)),
              LightAction('Produzieren', onTap: () => _produce()),
            ]),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: J.s4),
            child: Rail<_Tab>(
              values: _Tab.values,
              labels: const ['Übersicht', 'Bibliothek', 'Automatik', 'Connector'],
              value: _tab,
              onChanged: (t) => setState(() => _tab = t),
            ),
          ),
          const SizedBox(height: J.s3),
          Expanded(
            child: RefreshIndicator(
              color: J.cyan,
              backgroundColor: J.panel,
              onRefresh: () async {
                ref.invalidate(socialStatusProvider);
                ref.invalidate(socialVideosProvider);
                ref.invalidate(socialSettingsProvider);
              },
              child: switch (_tab) {
                _Tab.overview => _Overview(onOpen: (id) => _openVideo(id)),
                _Tab.library => _Library(onOpen: _openVideo),
                _Tab.automation => _Automation(run: _run),
                _Tab.connector => _Connector(run: _run),
              },
            ),
          ),
        ]),
      ),
    );
  }

  void _openVideo(String id) {
    Navigator.of(context).push(MaterialPageRoute<void>(
      builder: (_) => _VideoPage(id: id, run: _run, regenerate: () => _produce(parent: id), toast: _toast),
    ));
  }
}

class _HintDialog extends StatefulWidget {
  const _HintDialog({required this.regenerate});
  final bool regenerate;
  @override
  State<_HintDialog> createState() => _HintDialogState();
}

class _HintDialogState extends State<_HintDialog> {
  final _c = TextEditingController();
  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
        backgroundColor: J.panel,
        title: Text(widget.regenerate ? 'Erneut generieren' : 'Neues Video', style: J.title),
        content: TextField(
          controller: _c,
          maxLength: 500,
          style: J.body,
          decoration: InputDecoration(hintText: 'Wunsch (optional)', hintStyle: J.bodyDim),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: Text('Abbrechen', style: J.mono(12))),
          TextButton(onPressed: () => Navigator.pop(context, _c.text.trim()), child: Text('Starten', style: J.mono(12, color: J.cyan))),
        ],
      );
}

Widget _loading() => const Center(child: SizedBox.square(dimension: 20, child: CircularProgressIndicator(strokeWidth: 1.5, color: J.text3)));

Widget _error(Object e) => ListView(padding: const EdgeInsets.all(J.s5), children: [
      Text(e is ApiError ? (e.offline ? 'Jarvis ist nicht erreichbar.' : e.message) : '$e', style: J.bodyDim),
    ]);

Widget _section(String title, List<Widget> children) => Padding(
      padding: const EdgeInsets.only(bottom: J.s5),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Caps(title),
        const SizedBox(height: J.s2),
        ...children,
      ]),
    );

Widget _kv(String k, String v, {Color color = J.text}) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SizedBox(width: 130, child: Text(k, style: J.bodyDim.copyWith(fontSize: 14))),
        Expanded(child: Text(v, style: J.mono(12, color: color))),
      ]),
    );

String _when(Object? iso) {
  final d = DateTime.tryParse('${iso ?? ''}')?.toLocal();
  if (d == null) return '–';
  String two(int n) => n.toString().padLeft(2, '0');
  return '${two(d.day)}.${two(d.month)}. ${two(d.hour)}:${two(d.minute)}';
}

// ------------------------------------------------------------------ overview
class _Overview extends ConsumerWidget {
  const _Overview({required this.onOpen});
  final ValueChanged<String> onOpen;
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final st = ref.watch(socialStatusProvider);
    return st.when(
      loading: _loading,
      error: (e, _) => _error(e),
      data: (s) {
        final sched = (s['schedule'] as Map).cast<String, dynamic>();
        final deps = (s['dependencies'] as Map).cast<String, dynamic>();
        final settings = (s['settings'] as Map).cast<String, dynamic>();
        final producing = ((s['producing'] as List?) ?? const []).cast<Map>();
        final last = s['last_video'] as Map?;
        final errors = ((s['recent_errors'] as List?) ?? const []).cast<Map>();
        final pubs = ((s['recent_publications'] as List?) ?? const []).cast<Map>();
        return ListView(padding: const EdgeInsets.symmetric(horizontal: J.s4), children: [
          if (deps['ok'] == false)
            _section('Produktion nicht möglich', [
              for (final p in (deps['problems'] as List? ?? const [])) Text('• $p', style: J.mono(12, color: J.red)),
            ]),
          for (final v in producing)
            _section('In Arbeit', [
              Text('${v['title'] == '' ? 'Neue Idee entsteht …' : v['title']}', style: J.body),
              Text(videoStatusLabel(v.cast<String, dynamic>()), style: J.mono(12, color: J.cyan)),
            ]),
          _section('Aufgabe · PJE Systems Daily Social Video', [
            _kv('Status', sched['enabled'] == true ? 'täglich aktiv' : 'pausiert', color: sched['enabled'] == true ? J.green : J.text3),
            _kv('Zeit', '${sched['time']} (${sched['timezone']})'),
            _kv('Nächstes Video', sched['enabled'] == true ? _when(sched['next_run_at']) : '–'),
            _kv('Letzter Lauf', sched['last_run_at'] == null ? 'nie' : '${_when(sched['last_run_at'])} ${sched['last_status'] ?? ''}'),
            _kv('E-Mail', settings['email_enabled'] == true ? 'an ${settings['email_recipient']}' : 'aus'),
            _kv('Autopost', settings['autopost_enabled'] == true ? 'an' : 'aus'),
          ]),
          if (last != null)
            _section('Letztes Video', [
              Pressable(
                onTap: () => onOpen('${last['id']}'),
                semantics: 'Letztes Video öffnen',
                child: Container(
                  padding: const EdgeInsets.all(J.s3),
                  decoration: BoxDecoration(border: Border.all(color: J.lineBright), borderRadius: BorderRadius.circular(J.radius)),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Text('${last['title']}', style: J.body),
                    const SizedBox(height: J.s1),
                    Text('${last['summary'] ?? ''}', style: J.bodyDim.copyWith(fontSize: 14)),
                    const SizedBox(height: J.s2),
                    Text('${_when(last['created_at'])} · Kategorie ${last['category']} · ${last['tone']}', style: J.mono(11, color: J.text3)),
                  ]),
                ),
              ),
            ]),
          if (pubs.isNotEmpty)
            _section('Veröffentlichungen', [
              for (final p in pubs) _kv(platformLabel[p['platform']] ?? '${p['platform']}', publicationLabel[p['status']] ?? '${p['status']}'),
            ]),
          if (errors.isNotEmpty)
            _section('Fehler', [
              for (final e in errors)
                Pressable(
                  onTap: () => onOpen('${e['id']}'),
                  child: Padding(
                    padding: const EdgeInsets.symmetric(vertical: J.s1),
                    child: Text('${e['title'] ?? 'Produktion'}: ${e['error'] ?? ''}', maxLines: 3, overflow: TextOverflow.ellipsis, style: J.mono(12, color: J.red)),
                  ),
                ),
            ]),
        ]);
      },
    );
  }
}

// ------------------------------------------------------------------ library
class _Library extends ConsumerWidget {
  const _Library({required this.onOpen});
  final ValueChanged<String> onOpen;
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final list = ref.watch(socialVideosProvider);
    return list.when(
      loading: _loading,
      error: (e, _) => _error(e),
      data: (videos) => videos.isEmpty
          ? ListView(padding: const EdgeInsets.all(J.s5), children: [
              Text('Die Bibliothek ist leer. Das erste Video entsteht zur geplanten Zeit.', style: J.bodyDim),
            ])
          : ListView.separated(
              padding: const EdgeInsets.symmetric(horizontal: J.s4),
              itemCount: videos.length,
              separatorBuilder: (_, _) => const Divider(color: J.line, height: 1),
              itemBuilder: (_, i) {
                final v = videos[i];
                final tone = v['status'] == 'ready' ? J.green : v['status'] == 'failed' ? J.red : J.cyan;
                return Pressable(
                  onTap: () => onOpen('${v['id']}'),
                  semantics: '${v['title']}',
                  child: Padding(
                    padding: const EdgeInsets.symmetric(vertical: J.s3),
                    child: Row(children: [
                      Container(width: 6, height: 6, decoration: BoxDecoration(color: tone, shape: BoxShape.circle)),
                      const SizedBox(width: J.s3),
                      Expanded(
                        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                          Text('${v['title'] == '' ? 'Ohne Titel' : v['title']}', style: J.body, maxLines: 2, overflow: TextOverflow.ellipsis),
                          Text('${_when(v['created_at'])} · ${videoStatusLabel(v)}${v['category'] == '' ? '' : ' · ${v['category']}'}',
                              style: J.mono(11, color: J.text3)),
                        ]),
                      ),
                    ]),
                  ),
                );
              },
            ),
    );
  }
}

class _VideoPage extends ConsumerWidget {
  const _VideoPage({required this.id, required this.run, required this.regenerate, required this.toast});
  final String id;
  final Future<void> Function(Future<dynamic> Function(), String) run;
  final VoidCallback regenerate;
  final ValueChanged<String> toast;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final q = ref.watch(socialVideoProvider(id));
    final api = ref.read(apiProvider);
    return Scaffold(
      backgroundColor: J.void_,
      appBar: AppBar(backgroundColor: J.void_, foregroundColor: J.text, title: Text('Video', style: J.title)),
      body: q.when(
        loading: _loading,
        error: (e, _) => _error(e),
        data: (v) {
          final ready = v['status'] == 'ready';
          final captions = ((v['captions'] as Map?) ?? const {}).cast<String, dynamic>();
          final pubs = {for (final p in ((v['publications'] as List?) ?? const []).cast<Map>()) p['platform']: p};
          final qa = ((v['qa'] as Map?) ?? const {}).cast<String, dynamic>();
          final checks = ((qa['checks'] as List?) ?? const []).cast<Map>();
          return ListView(padding: const EdgeInsets.all(J.s4), children: [
            if (ready) _Player(id: id, name: safeVideoName(v)),
            const SizedBox(height: J.s3),
            Text('${v['title']}', style: J.title),
            if ('${v['hook'] ?? ''}'.isNotEmpty) Text('Hook: „${v['hook']}“', style: J.bodyDim),
            const SizedBox(height: J.s2),
            Text('${((v['concept'] as Map?) ?? const {})['summary'] ?? ''}', style: J.body),
            if (v['error'] != null) Text('${v['error']}', style: J.mono(12, color: J.red)),
            const SizedBox(height: J.s3),
            if (ready)
              Wrap(spacing: J.s3, children: [
                LightAction('Neu generieren', onTap: () {
                  Navigator.pop(context);
                  regenerate();
                }),
                LightAction('E-Mail', onTap: () => run(() => api.post('/api/social/videos/$id/email'), 'E-Mail gesendet')),
              ]),
            const SizedBox(height: J.s3),
            if (ready)
              _section('Captions & Hashtags', [
                for (final pf in socialPlatforms)
                  _CaptionBlock(
                    platform: pf,
                    caption: (captions[pf] as Map?)?.cast<String, dynamic>(),
                    publication: pubs[pf]?.cast<String, dynamic>(),
                    onCopy: (text) async {
                      await Clipboard.setData(ClipboardData(text: text));
                      toast('Caption kopiert');
                    },
                    onPublish: () => run(() => api.post('/api/social/videos/$id/publish', {'platform': pf}), 'Veröffentlichung angestoßen'),
                    onManual: (text) async {
                      await Clipboard.setData(ClipboardData(text: text));
                      toast('Caption kopiert – ${platformLabel[pf]} öffnet');
                      await ref.read(nativeProvider).openUrl(manualUpload[pf]!);
                    },
                  ),
              ]),
            if (checks.isNotEmpty)
              _section(qa['passed'] == true ? 'Qualitätskontrolle bestanden' : 'Qualitätskontrolle', [
                for (final c in checks)
                  Text('${c['ok'] == true ? '✓' : '✕'} ${c['label']}${c['ok'] == true ? '' : ' – ${c['detail']}'}',
                      style: J.mono(11.5, color: c['ok'] == true ? J.text2 : J.red)),
              ]),
            _section('Protokoll', [
              for (final l in ((v['log'] as List?) ?? const []).cast<Map>())
                Text('${_when(l['ts'])}  ${l['msg']}',
                    style: J.mono(11, color: l['level'] == 'error' ? J.red : l['level'] == 'warning' ? J.amber : J.text3)),
            ]),
          ]);
        },
      ),
    );
  }
}

class _CaptionBlock extends ConsumerWidget {
  const _CaptionBlock({required this.platform, required this.caption, required this.publication, required this.onCopy, required this.onPublish, required this.onManual});
  final String platform;
  final Map<String, dynamic>? caption;
  final Map<String, dynamic>? publication;
  final ValueChanged<String> onCopy;
  final VoidCallback onPublish;
  final ValueChanged<String> onManual;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final text = captionText(platform, caption);
    final connected = ref.watch(socialStatusProvider).valueOrNull?['platforms'] is List &&
        ((ref.watch(socialStatusProvider).value!['platforms'] as List).cast<Map>())
            .any((p) => p['platform'] == platform && p['connected'] == true && p['enabled'] == true);
    return Padding(
      padding: const EdgeInsets.only(bottom: J.s4),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Expanded(child: Text(platformLabel[platform]!, style: J.label)),
          if (publication != null) Text(publicationLabel[publication!['status']] ?? '', style: J.mono(11, color: J.cyan)),
        ]),
        if (platform == 'youtube' && caption?['title'] != null) Text('Titel: ${caption!['title']}', style: J.body.copyWith(fontSize: 14)),
        SelectableText(text.isEmpty ? '–' : text, style: J.bodyDim.copyWith(fontSize: 14)),
        Wrap(spacing: J.s3, children: [
          LightAction('Kopieren', onTap: () => onCopy(text)),
          connected ? LightAction('Veröffentlichen', onTap: onPublish) : LightAction('Manuell posten', onTap: () => onManual(text)),
        ]),
      ]),
    );
  }
}

/// Downloads the MP4 over the pinned API connection into the app cache, then plays it.
class _Player extends ConsumerStatefulWidget {
  const _Player({required this.id, required this.name});
  final String id;
  final String name;
  @override
  ConsumerState<_Player> createState() => _PlayerState();
}

class _PlayerState extends ConsumerState<_Player> {
  VideoPlayerController? _c;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final dir = Directory('${(await getTemporaryDirectory()).path}/social');
      await dir.create(recursive: true);
      final file = File('${dir.path}/${widget.name}');
      if (!await file.exists() || await file.length() == 0) {
        await ref.read(apiProvider).socialVideo(widget.id, file);
      }
      final c = VideoPlayerController.file(file);
      await c.initialize();
      await c.setLooping(true);
      if (!mounted) {
        await c.dispose();
        return;
      }
      setState(() => _c = c);
      await c.play();
    } catch (e) {
      if (mounted) setState(() => _error = e is ApiError ? e.message : 'Video konnte nicht geladen werden');
    }
  }

  @override
  void dispose() {
    _c?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = _c;
    final h = MediaQuery.sizeOf(context).height * 0.6;
    if (_error != null) return Text(_error!, style: J.mono(12, color: J.red));
    if (c == null) return SizedBox(height: h, child: _loading());
    return Center(
      child: SizedBox(
        height: h,
        child: AspectRatio(
          aspectRatio: c.value.aspectRatio,
          child: GestureDetector(
            onTap: () => c.value.isPlaying ? c.pause() : c.play(),
            child: Semantics(label: 'Video, antippen für Pause oder Wiedergabe', child: VideoPlayer(c)),
          ),
        ),
      ),
    );
  }
}

// ------------------------------------------------------------------ automation
class _Automation extends ConsumerWidget {
  const _Automation({required this.run});
  final Future<void> Function(Future<dynamic> Function(), String) run;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final q = ref.watch(socialSettingsProvider);
    final api = ref.read(apiProvider);
    Future<void> save(Map<String, dynamic> body) async {
      await run(() => api.put('/api/social/settings', body), 'Gespeichert');
      ref.invalidate(socialSettingsProvider);
    }

    return q.when(
      loading: _loading,
      error: (e, _) => _error(e),
      data: (s) {
        final sched = (s['schedule'] as Map).cast<String, dynamic>();
        return ListView(padding: const EdgeInsets.symmetric(horizontal: J.s4), children: [
          _switch('Täglich automatisch produzieren', sched['enabled'] == true, (v) => save({'daily_enabled': v})),
          _timeRow(context, 'Produktionszeit', '${sched['time']}', (t) => save({'production_time': t})),
          _switch('E-Mail an ${s['email_recipient']}', s['email_enabled'] == true, (v) => save({'email_enabled': v})),
          _switch('Voiceover (lokale Stimme)', s['voiceover'] == true, (v) => save({'voiceover': v})),
          _switch('Autopost (nur verbundene Plattformen, mit Freigabe)', s['autopost_enabled'] == true, (v) => save({'autopost_enabled': v})),
          _timeRow(context, 'Veröffentlichungszeit', '${s['publish_time'] ?? '–'}', (t) => save({'publish_time': t})),
          const SizedBox(height: J.s4),
          Text('Verpasste Läufe holt Jarvis nur am selben Tag nach; pro Tag entsteht höchstens ein automatisches Video.',
              style: J.bodyDim.copyWith(fontSize: 13)),
        ]);
      },
    );
  }
}

Widget _switch(String label, bool value, ValueChanged<bool> onChanged) => Padding(
      padding: const EdgeInsets.symmetric(vertical: J.s1),
      child: Row(children: [
        Expanded(child: Text(label, style: J.body)),
        Switch(
          value: value,
          onChanged: onChanged,
          activeThumbColor: J.void_,
          activeTrackColor: J.cyan,
          inactiveTrackColor: J.panel,
          inactiveThumbColor: J.text3,
          trackOutlineColor: WidgetStateProperty.all(J.lineBright),
        ),
      ]),
    );

Widget _timeRow(BuildContext context, String label, String value, ValueChanged<String> onPicked) => Pressable(
      onTap: () async {
        final parts = value.split(':');
        final initial = parts.length == 2
            ? TimeOfDay(hour: int.tryParse(parts[0]) ?? 8, minute: int.tryParse(parts[1]) ?? 0)
            : const TimeOfDay(hour: 8, minute: 0);
        final t = await showTimePicker(context: context, initialTime: initial);
        if (t == null) return;
        final s = '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
        if (validTime(s)) onPicked(s);
      },
      semantics: '$label ändern',
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: J.s3),
        child: Row(children: [
          Expanded(child: Text(label, style: J.body)),
          Text(value, style: J.mono(14, color: J.cyan)),
        ]),
      ),
    );

// ------------------------------------------------------------------ connector
class _Connector extends ConsumerWidget {
  const _Connector({required this.run});
  final Future<void> Function(Future<dynamic> Function(), String) run;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final q = ref.watch(socialStatusProvider);
    final api = ref.read(apiProvider);
    return q.when(
      loading: _loading,
      error: (e, _) => _error(e),
      data: (s) {
        final platforms = ((s['platforms'] as List?) ?? const []).cast<Map>();
        return ListView(padding: const EdgeInsets.symmetric(horizontal: J.s4), children: [
          Text('Verbinden geschieht am Mac (Social Media › Connector). Hier: Status und Autopost je Plattform.',
              style: J.bodyDim.copyWith(fontSize: 13)),
          const SizedBox(height: J.s3),
          for (final p in platforms)
            _section(platformLabel[p['platform']] ?? '${p['platform']}', [
              _kv('Verbindung', p['connected'] == true ? (p['enabled'] == true ? 'verbunden' : 'verbunden, deaktiviert') : 'nicht verbunden',
                  color: p['connected'] == true && p['enabled'] == true ? J.green : J.text3),
              if ((p['health'] as Map?)?['message'] != null) _kv('Status', '${(p['health'] as Map)['message']}'),
              _switch('Automatisch veröffentlichen', p['autopost'] == true,
                  (v) => run(() => api.put('/api/social/settings', {'platforms': {'${p['platform']}': {'autopost': v}}}), 'Gespeichert')),
              if (p['will_autopost'] == true) Text('postet automatisch (mit Freigabe)', style: J.mono(11, color: J.green)),
            ]),
        ]);
      },
    );
  }
}
