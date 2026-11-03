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

/// Social Media: the daily video per company profile (PJE Systems, Apo HR …) – company switch (= the active profile
/// on the Core), overview, library (player, captions to copy, regenerate, publish/manual posting), automation
/// switches, company profiles (add, edit, delete) and the platform status. Same API as the Mac app.
class SocialScreen extends ConsumerStatefulWidget {
  const SocialScreen({super.key});
  @override
  ConsumerState<SocialScreen> createState() => _SocialScreenState();
}

enum _Tab { overview, library, automation, companies, connector }

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
      ref.invalidate(socialCompaniesProvider);
    } on ApiError catch (e) {
      _toast(e.offline ? 'Jarvis ist nicht erreichbar' : e.message);
    }
  }

  Future<void> _produce({String? parent}) async {
    final companies = ((ref.read(socialCompaniesProvider).valueOrNull?['companies'] as List?) ?? const []).cast<Map>();
    final current = ref.read(socialStatusProvider).valueOrNull?['company']?['id'] as String?;
    final res = await showDialog<(String, String?)>(
      context: context,
      builder: (_) => _HintDialog(regenerate: parent != null, companies: parent == null ? companies : const [], company: current),
    );
    if (res == null) return;
    final (hint, company) = res;
    final api = ref.read(apiProvider);
    await _run(
      () => parent == null
          ? api.post('/api/social/produce', {'hint': hint, 'company': ?company})
          : api.post('/api/social/videos/$parent/regenerate', {'hint': hint}),
      'Produktion gestartet',
    );
  }

  /// Switches the shown company; it also becomes the active profile on the Core (default of Jarvis' tools).
  Future<void> _pick(String id) async {
    ref.read(socialCompanyProvider.notifier).state = id;
    await _run(() => ref.read(apiProvider).post('/api/social/companies/$id/activate'), 'Unternehmen gewechselt');
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
          _CompanyBar(onPick: _pick),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: J.s4),
            child: Rail<_Tab>(
              values: _Tab.values,
              labels: const ['Übersicht', 'Bibliothek', 'Automatik', 'Firmen', 'Connector'],
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
                ref.invalidate(socialCompaniesProvider);
              },
              child: switch (_tab) {
                _Tab.overview => _Overview(onOpen: (id) => _openVideo(id)),
                _Tab.library => _Library(onOpen: _openVideo),
                _Tab.automation => _Automation(run: _run),
                _Tab.companies => _Companies(run: _run, onPick: _pick),
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
  const _HintDialog({required this.regenerate, this.companies = const [], this.company});
  final bool regenerate;
  final List<Map> companies;
  final String? company;
  @override
  State<_HintDialog> createState() => _HintDialogState();
}

class _HintDialogState extends State<_HintDialog> {
  final _c = TextEditingController();
  late String? _company = widget.company;
  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final picked = widget.companies.where((c) => c['id'] == _company).firstOrNull;
    final missing = picked == null ? const <String>[] : missingFields(picked.cast<String, dynamic>());
    return AlertDialog(
      backgroundColor: J.panel,
      title: Text(widget.regenerate ? 'Erneut generieren' : 'Neues Video', style: J.title),
      content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
        if (widget.companies.isNotEmpty)
          DropdownButton<String>(
            value: _company,
            isExpanded: true,
            dropdownColor: J.panel,
            style: J.body,
            items: [for (final c in widget.companies) DropdownMenuItem(value: '${c['id']}', child: Text('${c['name']}'))],
            onChanged: (v) => setState(() => _company = v),
          ),
        if (missing.isNotEmpty) Text('Profil unvollständig – es fehlt: ${missing.join(', ')}', style: J.mono(11, color: J.amber)),
        TextField(
          controller: _c,
          maxLength: 500,
          style: J.body,
          decoration: InputDecoration(hintText: 'Vorgabe (optional), z. B. „Angebot für 299 €“', hintStyle: J.bodyDim),
        ),
        Text('Hat Vorrang vor Preis, Angebot und Thema aus dem Profil.', style: J.bodyDim.copyWith(fontSize: 12)),
      ]),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: Text('Abbrechen', style: J.mono(12))),
        TextButton(
          onPressed: missing.isNotEmpty ? null : () => Navigator.pop(context, (_c.text.trim(), _company)),
          child: Text('Starten', style: J.mono(12, color: missing.isNotEmpty ? J.text3 : J.cyan)),
        ),
      ],
    );
  }
}

/// Horizontal company switch under the title (only when there is more than one profile).
class _CompanyBar extends ConsumerWidget {
  const _CompanyBar({required this.onPick});
  final ValueChanged<String> onPick;
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final data = ref.watch(socialCompaniesProvider).valueOrNull;
    final list = ((data?['companies'] as List?) ?? const []).cast<Map>();
    if (list.length < 2) return const SizedBox.shrink();
    final current = ref.watch(socialCompanyProvider) ?? '${data?['active'] ?? ''}';
    return SizedBox(
      height: 40,
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: J.s4),
        children: [
          for (final c in list)
            Padding(
              padding: const EdgeInsets.only(right: J.s2, bottom: J.s2),
              child: Pressable(
                onTap: () => onPick('${c['id']}'),
                semantics: 'Unternehmen ${c['name']}',
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: J.s3),
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(J.radius),
                    border: Border.all(color: c['id'] == current ? J.cyan.withValues(alpha: 0.7) : J.line),
                    color: c['id'] == current ? J.cyan.withValues(alpha: 0.14) : Colors.transparent,
                  ),
                  child: Text('${c['name']}', style: J.mono(11.5, color: c['id'] == current ? J.cyan : J.text2)),
                ),
              ),
            ),
        ],
      ),
    );
  }
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
        final company = ((s['company'] as Map?) ?? const {}).cast<String, dynamic>();
        final missing = missingFields(company);
        final recipients = ((settings['recipients'] as List?) ?? const []).join(', ');
        return ListView(padding: const EdgeInsets.symmetric(horizontal: J.s4), children: [
          if (missing.isNotEmpty)
            _section('Profil unvollständig', [
              Text('Es fehlt: ${missing.join(', ')} – unter „Firmen“ ergänzen.', style: J.mono(12, color: J.amber)),
            ]),
          if (deps['ok'] == false)
            _section('Produktion nicht möglich', [
              for (final p in (deps['problems'] as List? ?? const [])) Text('• $p', style: J.mono(12, color: J.red)),
            ]),
          for (final v in producing)
            _section('In Arbeit', [
              Text('${v['title'] == '' ? 'Neue Idee entsteht …' : v['title']}', style: J.body),
              Text(videoStatusLabel(v.cast<String, dynamic>()), style: J.mono(12, color: J.cyan)),
            ]),
          _section('Aufgabe · ${company['name'] ?? ''} Daily Social Video', [
            _kv('Status', sched['enabled'] == true ? 'täglich aktiv' : 'pausiert', color: sched['enabled'] == true ? J.green : J.text3),
            _kv('Zeit', '${sched['time']} (${sched['timezone']})'),
            _kv('Nächstes Video', sched['enabled'] == true ? _when(sched['next_run_at']) : '–'),
            _kv('Letzter Lauf', sched['last_run_at'] == null ? 'nie' : '${_when(sched['last_run_at'])} ${sched['last_status'] ?? ''}'),
            _kv('E-Mail', settings['email_enabled'] == true ? (recipients.isEmpty ? 'keine Empfänger' : 'an $recipients') : 'aus'),
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
            if (v['company_name'] != null) Text('${v['company_name']}', style: J.mono(11, color: J.text3)),
            if ('${v['hook'] ?? ''}'.isNotEmpty) Text('Hook: „${v['hook']}“', style: J.bodyDim),
            if ('${((v['concept'] as Map?) ?? const {})['hint'] ?? ''}'.isNotEmpty)
              Text('Vorgabe: „${(v['concept'] as Map)['hint']}“', style: J.bodyDim),
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
                LightAction('E-Mail', onTap: () => run(() => api.post('/api/social/videos/$id/email'), 'E-Mail an die Empfängerliste gesendet')),
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
    return q.when(
      loading: _loading,
      error: (e, _) => _error(e),
      data: (s) {
        Future<void> save(Map<String, dynamic> body) async {
          await run(() => api.put('/api/social/settings', {...body, 'company': s['company_id']}), 'Gespeichert');
          ref.invalidate(socialSettingsProvider);
        }

        final sched = (s['schedule'] as Map).cast<String, dynamic>();
        final recipients = ((s['recipients'] as List?) ?? const []).map((e) => '$e').toList();
        return ListView(padding: const EdgeInsets.symmetric(horizontal: J.s4), children: [
          Caps('Dieses Unternehmen'),
          _switch('Täglich automatisch produzieren', sched['enabled'] == true, (v) => save({'daily_enabled': v})),
          _timeRow(context, 'Produktionszeit', '${sched['time']}', (t) => save({'production_time': t})),
          _switch('Autopost (nur Konten des Unternehmens, mit Freigabe)', s['autopost_enabled'] == true, (v) => save({'autopost_enabled': v})),
          _textRow(context, 'Empfängerliste', recipients.isEmpty ? '–' : recipients.join(', '), recipients.join(', '), (t) {
            final list = splitList(t);
            if (list.every(validEmail)) save({'recipients': list});
          }),
          _textRow(context, 'Ständiger Themenwunsch', '${s['hint'] ?? ''}'.isEmpty ? '–' : '${s['hint']}', '${s['hint'] ?? ''}',
              (t) => save({'hint': t})),
          const SizedBox(height: J.s4),
          Caps('Alle Unternehmen'),
          _switch('E-Mail-Versand', s['email_enabled'] == true, (v) => save({'email_enabled': v})),
          _switch('Voiceover (lokale Stimme)', s['voiceover'] == true, (v) => save({'voiceover': v})),
          _timeRow(context, 'Veröffentlichungszeit', '${s['publish_time'] ?? '–'}', (t) => save({'publish_time': t})),
          const SizedBox(height: J.s4),
          Text(
              'Verpasste Läufe holt Jarvis nur am selben Tag nach; pro Tag entsteht höchstens ein automatisches Video je Unternehmen. '
              'Empfänger außer Sirs Adresse und der freigegebenen Liste brauchen je Versand eine Freigabe.',
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

/// A row that opens a text prompt (recipient list, topic wish).
Widget _textRow(BuildContext context, String label, String shown, String initial, ValueChanged<String> onSaved) => Pressable(
      onTap: () async {
        final t = await showDialog<String>(context: context, builder: (_) => _TextPrompt(title: label, initial: initial));
        if (t != null) onSaved(t);
      },
      semantics: '$label ändern',
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: J.s3),
        child: Row(children: [
          Expanded(child: Text(label, style: J.body)),
          const SizedBox(width: J.s3),
          Flexible(child: Text(shown, maxLines: 2, overflow: TextOverflow.ellipsis, textAlign: TextAlign.right, style: J.mono(12, color: J.cyan))),
        ]),
      ),
    );

class _TextPrompt extends StatefulWidget {
  const _TextPrompt({required this.title, required this.initial});
  final String title;
  final String initial;
  @override
  State<_TextPrompt> createState() => _TextPromptState();
}

class _TextPromptState extends State<_TextPrompt> {
  late final _c = TextEditingController(text: widget.initial);
  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
        backgroundColor: J.panel,
        title: Text(widget.title, style: J.title),
        content: TextField(controller: _c, maxLength: 500, maxLines: null, style: J.body),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: Text('Abbrechen', style: J.mono(12))),
          TextButton(onPressed: () => Navigator.pop(context, _c.text.trim()), child: Text('Speichern', style: J.mono(12, color: J.cyan))),
        ],
      );
}

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
          Align(
            alignment: Alignment.centerLeft,
            child: LightAction('UpTik Autopost (Browser) öffnen', onTap: () => context.push('/uptik')),
          ),
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

// ------------------------------------------------------------------ companies
class _Companies extends ConsumerWidget {
  const _Companies({required this.run, required this.onPick});
  final Future<void> Function(Future<dynamic> Function(), String) run;
  final ValueChanged<String> onPick;

  void _open(BuildContext context, Map<String, dynamic>? company) {
    Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => _CompanyPage(company: company, run: run)));
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final q = ref.watch(socialCompaniesProvider);
    return q.when(
      loading: _loading,
      error: (e, _) => _error(e),
      data: (d) {
        final list = ((d['companies'] as List?) ?? const []).map((e) => (e as Map).cast<String, dynamic>()).toList();
        return ListView(padding: const EdgeInsets.symmetric(horizontal: J.s4), children: [
          for (final c in list)
            Pressable(
              onTap: () => _open(context, c),
              semantics: 'Unternehmen ${c['name']} bearbeiten',
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: J.s3),
                child: Row(children: [
                  Container(width: 8, height: 8, decoration: BoxDecoration(color: _hex(((c['colors'] as Map?) ?? const {})['accent']), shape: BoxShape.circle)),
                  const SizedBox(width: J.s3),
                  Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text('${c['name']}', style: J.body),
                      Text(
                        [
                          if (c['id'] == d['active']) 'aktiv',
                          if (missingFields(c).isNotEmpty) 'unvollständig: ${missingFields(c).join(', ')}',
                          if (c['autopost'] == true) 'Autopost',
                        ].join(' · '),
                        style: J.mono(11, color: missingFields(c).isNotEmpty ? J.amber : J.text3),
                      ),
                    ]),
                  ),
                  if (c['id'] != d['active']) LightAction('Aktiv', onTap: () => onPick('${c['id']}')),
                ]),
              ),
            ),
          const SizedBox(height: J.s3),
          Align(alignment: Alignment.centerLeft, child: LightAction('Unternehmen hinzufügen', onTap: () => _open(context, null))),
        ]);
      },
    );
  }
}

Color _hex(Object? c) {
  final s = '${c ?? ''}';
  return validHex(s) ? Color(int.parse('ff${s.substring(1)}', radix: 16)) : J.cyan;
}

/// Add or edit one company profile. Defaults (offer, price, topic) are only the fallback: what Sir writes when
/// producing wins.
class _CompanyPage extends ConsumerStatefulWidget {
  const _CompanyPage({required this.company, required this.run});
  final Map<String, dynamic>? company;
  final Future<void> Function(Future<dynamic> Function(), String) run;
  @override
  ConsumerState<_CompanyPage> createState() => _CompanyPageState();
}

class _CompanyPageState extends ConsumerState<_CompanyPage> {
  static const _text = {
    'name': 'Name',
    'description': 'Beschreibung / Positionierung',
    'audience': 'Zielgruppe',
    'website': 'Website (Endkarte)',
    'cta_button': 'CTA-Button',
    'offer': 'Standard-Angebot (Fallback)',
    'price': 'Standardpreis (Fallback)',
    'tone': 'Tonalität',
    'hint': 'Ständiger Themenwunsch',
  };
  static const _colors = {'accent': 'Akzentfarbe', 'ink': 'Dunkel', 'canvas': 'Hell'};
  late final Map<String, TextEditingController> _c;
  late bool _autopost = widget.company?['autopost'] == true;
  late bool _official = widget.company?['official_api'] == true;
  late final Set<String> _uptik = {...((widget.company?['uptik_accounts'] as List?) ?? const []).map((e) => '$e')};
  String? _problem;

  @override
  void initState() {
    super.initState();
    final c = widget.company ?? const {};
    final colors = ((c['colors'] as Map?) ?? const {}).cast<String, dynamic>();
    final accounts = ((c['accounts'] as Map?) ?? const {}).cast<String, dynamic>();
    _c = {
      for (final k in _text.keys) k: TextEditingController(text: '${c[k] ?? ''}'),
      for (final k in _colors.keys) 'color_$k': TextEditingController(text: '${colors[k] ?? ''}'),
      for (final p in socialPlatforms) 'account_$p': TextEditingController(text: '${accounts[p] ?? ''}'),
      'hashtags': TextEditingController(text: ((c['hashtags'] as List?) ?? const []).join(' ')),
      'recipients': TextEditingController(text: ((c['recipients'] as List?) ?? const []).join(', ')),
    };
  }

  @override
  void dispose() {
    for (final c in _c.values) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    final recipients = splitList(_c['recipients']!.text);
    final badColor = _colors.keys.where((k) => _c['color_$k']!.text.trim().isNotEmpty && !validHex(_c['color_$k']!.text.trim()));
    if (_c['name']!.text.trim().isEmpty) return setState(() => _problem = 'Name fehlt');
    if (!recipients.every(validEmail)) return setState(() => _problem = 'Ungültige E-Mail-Adresse in der Empfängerliste');
    if (badColor.isNotEmpty) return setState(() => _problem = 'Farben als Hex-Wert angeben, z. B. #2651f0');
    final body = <String, dynamic>{
      for (final k in _text.keys) k: _c[k]!.text.trim(),
      'colors': {for (final k in _colors.keys) k: _c['color_$k']!.text.trim()},
      'accounts': {for (final p in socialPlatforms) p: _c['account_$p']!.text.trim()},
      'hashtags': _c['hashtags']!.text.split(RegExp(r'[\s,]+')).where((t) => t.isNotEmpty).toList(),
      'recipients': recipients,
      'uptik_accounts': _uptik.toList(),
      'autopost': _autopost,
      'official_api': _official,
    };
    final api = ref.read(apiProvider);
    final id = widget.company?['id'];
    await widget.run(() => id == null ? api.post('/api/social/companies', body) : api.put('/api/social/companies/$id', body),
        id == null ? 'Unternehmen angelegt' : 'Gespeichert');
    if (mounted) Navigator.pop(context);
  }

  Future<void> _delete() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        backgroundColor: J.panel,
        title: Text('„${widget.company!['name']}“ löschen?', style: J.title),
        content: Text('Profil und täglicher Zeitplan werden gelöscht. Videos bleiben in der Bibliothek.', style: J.body),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: Text('Abbrechen', style: J.mono(12))),
          TextButton(onPressed: () => Navigator.pop(context, true), child: Text('Löschen', style: J.mono(12, color: J.red))),
        ],
      ),
    );
    if (ok != true) return;
    ref.read(socialCompanyProvider.notifier).state = null;
    await widget.run(() => ref.read(apiProvider).delete('/api/social/companies/${widget.company!['id']}'), 'Unternehmen gelöscht');
    if (mounted) Navigator.pop(context);
  }

  Widget _field(String key, String label, {int lines = 1}) => Padding(
        padding: const EdgeInsets.only(bottom: J.s2),
        child: TextField(
          controller: _c[key],
          minLines: 1,
          maxLines: lines,
          style: J.body,
          decoration: InputDecoration(labelText: label, labelStyle: J.bodyDim),
        ),
      );

  @override
  Widget build(BuildContext context) {
    final c = widget.company;
    final accounts = ref.watch(_uptikAccountsProvider);
    return Scaffold(
      backgroundColor: J.void_,
      appBar: AppBar(
        backgroundColor: J.void_,
        foregroundColor: J.text,
        title: Text(c == null ? 'Neues Unternehmen' : '${c['name']}', style: J.title),
        actions: [
          // the last profile cannot be deleted (the Core refuses it as well)
          if (c != null && (((ref.watch(socialCompaniesProvider).valueOrNull?['companies'] as List?)?.length ?? 0) > 1))
            IconButton(onPressed: _delete, icon: const Icon(Icons.delete_outline), tooltip: 'Löschen'),
        ],
      ),
      body: ListView(padding: const EdgeInsets.all(J.s4), children: [
        if (c != null && missingFields(c).isNotEmpty) Text('Für Videos fehlt noch: ${missingFields(c).join(', ')}', style: J.mono(12, color: J.amber)),
        _section('Marke', [
          _field('name', _text['name']!),
          _field('description', _text['description']!, lines: 3),
          _field('audience', _text['audience']!, lines: 2),
          _field('website', _text['website']!),
          _field('cta_button', _text['cta_button']!),
          _field('offer', _text['offer']!, lines: 2),
          _field('price', _text['price']!),
          for (final k in _colors.keys) _field('color_$k', '${_colors[k]} (#rrggbb)'),
          _field('tone', _text['tone']!, lines: 3),
          _field('hashtags', 'Hashtags'),
          _field('hint', _text['hint']!, lines: 2),
        ]),
        _section('Konten & Versand', [
          for (final p in socialPlatforms) _field('account_$p', 'Ziel-Konto ${platformLabel[p]}'),
          Text('UpTik-Konten dieses Unternehmens', style: J.label),
          ...accounts.when(
            loading: () => [_loading()],
            error: (e, _) => [Text('UpTik nicht verfügbar', style: J.bodyDim)],
            data: (list) => [
              if (list.isEmpty) Text('Noch keine UpTik-Konten.', style: J.bodyDim),
              for (final a in list)
                CheckboxListTile(
                  value: _uptik.contains(a['id']) || _uptik.contains(a['label']),
                  onChanged: (v) => setState(() => v == true ? _uptik.add('${a['id']}') : _uptik.removeAll({a['id'], a['label']})),
                  title: Text('${a['platform_label'] ?? a['platform']} · ${a['label']}', style: J.body),
                  activeColor: J.cyan,
                  contentPadding: EdgeInsets.zero,
                  dense: true,
                ),
            ],
          ),
          _switch('Offizielle Plattform-Konten gehören diesem Unternehmen', _official, (v) => setState(() => _official = v)),
          _switch('Autopost (mit Freigabe)', _autopost, (v) => setState(() => _autopost = v)),
          _field('recipients', 'Empfängerliste (kommagetrennt)', lines: 2),
        ]),
        if (_problem != null) Text(_problem!, style: J.mono(12, color: J.red)),
        Align(alignment: Alignment.centerRight, child: LightAction(c == null ? 'Anlegen' : 'Speichern', onTap: _save)),
      ]),
    );
  }
}

final _uptikAccountsProvider = FutureProvider.autoDispose<List<Map<String, dynamic>>>((ref) async {
  final res = await ref.read(apiProvider).get('/api/uptik/accounts');
  return ((res as List?) ?? const []).map((e) => (e as Map).cast<String, dynamic>()).toList();
});
