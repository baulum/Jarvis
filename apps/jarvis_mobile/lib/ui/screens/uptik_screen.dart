import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/api.dart';
import '../../core/theme.dart';
import '../../state/providers.dart';
import '../../state/uptik.dart';
import '../widgets/confirm_sheet.dart';
import '../widgets/hud.dart';

/// UpTik Autopost: accounts (login opens a browser window on the Mac/PC running it), publish form, queue and
/// history – the same /api/uptik endpoints as the Mac app. Publishing, scheduling and connecting end in approvals.
class UptikScreen extends ConsumerStatefulWidget {
  const UptikScreen({super.key});
  @override
  ConsumerState<UptikScreen> createState() => _UptikScreenState();
}

enum _Tab { accounts, publish, queue, history }

class _UptikScreenState extends ConsumerState<UptikScreen> {
  _Tab _tab = _Tab.accounts;

  void _toast(String msg) {
    ScaffoldMessenger.maybeOf(context)?.showSnackBar(SnackBar(content: Text(msg), backgroundColor: J.panel));
  }

  Future<void> _run(Future<dynamic> Function() fn, String done) async {
    try {
      final r = await fn();
      _toast(r is Map && r['status'] == 'approval_required' ? 'Freigabe nötig – unter Aktivität bestätigen' : done);
    } on ApiError catch (e) {
      _toast(e.offline ? 'Jarvis ist nicht erreichbar' : e.message);
    }
    _refresh();
  }

  void _refresh() {
    ref.invalidate(uptikStatusProvider);
    ref.invalidate(uptikInboxProvider);
    ref.invalidate(uptikUploadsProvider);
    ref.invalidate(uptikQueueProvider);
    ref.invalidate(uptikHistoryProvider);
  }

  @override
  Widget build(BuildContext context) {
    final status = ref.watch(uptikStatusProvider);
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
              Expanded(child: Text('UpTik Autopost', style: J.title)),
            ]),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: J.s4),
            child: Rail<_Tab>(
              values: _Tab.values,
              labels: const ['Konten', 'Posten', 'Queue', 'Historie'],
              value: _tab,
              onChanged: (t) => setState(() => _tab = t),
            ),
          ),
          const SizedBox(height: J.s3),
          Expanded(
            child: RefreshIndicator(
              color: J.cyan,
              backgroundColor: J.panel,
              onRefresh: () async => _refresh(),
              child: status.when(
                loading: _loading,
                error: (e, _) => _error(e),
                data: (s) => s['enabled'] != true
                    ? _Disabled(run: _run)
                    : switch (_tab) {
                        _Tab.accounts => _Accounts(status: s, run: _run),
                        _Tab.publish => _Publish(status: s, run: _run, done: () => setState(() => _tab = _Tab.queue)),
                        _Tab.queue => _Queue(status: s, run: _run),
                        _Tab.history => _History(status: s, run: _run),
                      },
              ),
            ),
          ),
        ]),
      ),
    );
  }
}

typedef _Runner = Future<void> Function(Future<dynamic> Function(), String);

Widget _loading() => const Center(child: SizedBox.square(dimension: 20, child: CircularProgressIndicator(strokeWidth: 1.5, color: J.text3)));

Widget _error(Object e) => ListView(padding: const EdgeInsets.all(J.s5), children: [
      Text(e is ApiError ? (e.offline ? 'Jarvis ist nicht erreichbar.' : e.message) : '$e', style: J.bodyDim),
    ]);

Widget _section(String title, List<Widget> children, {Color? color}) => Padding(
      padding: const EdgeInsets.only(bottom: J.s5),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Caps(title, color: color),
        const SizedBox(height: J.s2),
        ...children,
      ]),
    );

Widget _kv(String k, String v, {Color color = J.text}) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SizedBox(width: 120, child: Text(k, style: J.bodyDim.copyWith(fontSize: 14))),
        Expanded(child: Text(v, style: J.mono(12, color: color))),
      ]),
    );

Widget _switch(String label, bool value, ValueChanged<bool>? onChanged) => Padding(
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

Color _tone(String tone) => switch (tone) { 'ok' => J.green, 'bad' => J.red, 'warn' => J.amber, _ => J.text3 };

String _when(Object? iso) {
  final d = DateTime.tryParse('${iso ?? ''}')?.toLocal();
  if (d == null) return '–';
  String two(int n) => n.toString().padLeft(2, '0');
  return '${two(d.day)}.${two(d.month)}. ${two(d.hour)}:${two(d.minute)}';
}

List<Map<String, dynamic>> _accounts(Map<String, dynamic> s) =>
    ((s['accounts'] as List?) ?? const []).map((e) => (e as Map).cast<String, dynamic>()).toList();

// ------------------------------------------------------------------ disabled
class _Disabled extends ConsumerWidget {
  const _Disabled({required this.run});
  final _Runner run;
  @override
  Widget build(BuildContext context, WidgetRef ref) => ListView(padding: const EdgeInsets.all(J.s4), children: [
        Text('UpTik veröffentlicht Videos auf TikTok, Instagram Reels und optional YouTube Shorts über deren Webseiten – mit '
            'deiner eigenen Anmeldung, ohne Developer-App. Jede Veröffentlichung braucht deine Freigabe; bei Captcha, '
            'Prüfung oder Ratenlimit stoppt das Konto sofort.', style: J.bodyDim),
        const SizedBox(height: J.s3),
        Text('Browser-Automatisierung verstößt gegen die Nutzungsbedingungen der Plattformen und kann Konten gefährden.',
            style: J.mono(12, color: J.amber)),
        const SizedBox(height: J.s4),
        LightAction('Connector aktivieren', onTap: () => run(() => ref.read(apiProvider).put('/api/uptik/connector', {'enabled': true}), 'UpTik aktiviert')),
      ]);
}

// ------------------------------------------------------------------ accounts
class _Accounts extends ConsumerWidget {
  const _Accounts({required this.status, required this.run});
  final Map<String, dynamic> status;
  final _Runner run;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final api = ref.read(apiProvider);
    final settings = (status['settings'] as Map?)?.cast<String, dynamic>() ?? const {};
    final accounts = _accounts(status);
    return ListView(padding: const EdgeInsets.symmetric(horizontal: J.s4), children: [
      if (status['kill_switch'] == true) Text('Not-Aus ist aktiv – die Warteschlange ruht.', style: J.mono(12, color: J.red)),
      _section('Connector', [
        _switch('Aktiv', true, (v) => run(() => api.put('/api/uptik/connector', {'enabled': v}), v ? 'Aktiviert' : 'Deaktiviert')),
        _switch('Videos der Videoproduktion übernehmen', settings['handoff_enabled'] == true,
            (v) => run(() => api.put('/api/uptik/connector', {'settings': {'handoff_enabled': v}}), 'Gespeichert')),
      ]),
      for (final a in accounts) _account(context, api, a),
      Align(
        alignment: Alignment.centerLeft,
        child: LightAction('Konto hinzufügen', onTap: () async {
          final r = await showDialog<(String, String)>(context: context, builder: (_) => _AddDialog(status: status));
          if (r != null) await run(() => api.post('/api/uptik/accounts', {'platform': r.$1, 'label': r.$2}), 'Konto angelegt – jetzt anmelden');
        }),
      ),
      const SizedBox(height: J.s6),
    ]);
  }

  Widget _account(BuildContext context, Api api, Map<String, dynamic> a) {
    final id = a['id'];
    final tripped = a['circuit_state'] == 'tripped';
    final connected = a['status'] == 'connected';
    return _section('${a['label']} · ${uptikPlatformLabel[a['platform']] ?? a['platform']}', color: tripped ? J.red : null, [
      _kv('Status', uptikAccountState(a), color: tripped ? J.red : (connected ? J.green : J.text3)),
      _kv('Browser', a['runner'] == 'device' ? 'Device Agent ${a['device'] ?? ''}' : 'Core-Rechner'),
      _kv('Zuletzt', _when(a['last_used_at'])),
      _switch('Autopost', a['autopost'] == true, !connected
          ? null
          : (v) async {
              if (v &&
                  !await confirmAction(context,
                      title: 'Autopost einschalten?',
                      body: 'Fertige Videos werden für „${a['label']}“ automatisch zur Veröffentlichung vorbereitet. '
                          'Jede Veröffentlichung geht weiterhin über deine Freigaben.',
                      confirmLabel: 'Einschalten')) {
                return;
              }
              await run(() => api.patch('/api/uptik/accounts/$id', {'autopost': v}), v ? 'Autopost an' : 'Autopost aus');
            }),
      Wrap(spacing: J.s4, children: [
        if (tripped)
          LightAction('Konto freigeben', color: J.amber, onTap: () async {
            final ok = await confirmAction(context,
                title: 'Konto wieder freigeben?',
                body: 'Grund: ${a['circuit_reason']}. Nur freigeben, wenn du das Konto selbst im Browser geprüft hast. '
                    'Angehaltene Beiträge bleiben angehalten; du kannst sie in der Queue abbrechen oder am Mac wieder einreihen.',
                confirmLabel: 'Freigeben');
            if (ok) await run(() => api.post('/api/uptik/accounts/$id/resume', {'confirm': true, 'requeue': false}), 'Freigegeben');
          }),
        LightAction(connected ? 'Neu anmelden' : 'Anmelden',
            onTap: a['login_running'] == true ? null : () => run(() => api.post('/api/uptik/accounts/$id/connect'), 'Anmeldefenster geöffnet')),
        if (a['session_saved'] == true) LightAction('Prüfen', color: J.text2, onTap: () => run(() => api.post('/api/uptik/accounts/$id/check'), 'Geprüft')),
      ]),
    ]);
  }
}

class _AddDialog extends StatefulWidget {
  const _AddDialog({required this.status});
  final Map<String, dynamic> status;
  @override
  State<_AddDialog> createState() => _AddDialogState();
}

class _AddDialogState extends State<_AddDialog> {
  final _name = TextEditingController();
  late String _platform = '${((widget.status['platforms'] as List?)?.firstOrNull as Map?)?['id'] ?? 'tiktok'}';

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final platforms = ((widget.status['platforms'] as List?) ?? const []).cast<Map>();
    return AlertDialog(
      backgroundColor: J.panel,
      title: Text('Konto hinzufügen', style: J.title),
      content: Column(mainAxisSize: MainAxisSize.min, children: [
        DropdownButton<String>(
          value: _platform,
          isExpanded: true,
          dropdownColor: J.panel,
          style: J.body,
          items: [for (final p in platforms) DropdownMenuItem(value: '${p['id']}', child: Text('${p['label']}'))],
          onChanged: (v) => setState(() => _platform = v ?? _platform),
        ),
        TextField(controller: _name, maxLength: 80, style: J.body, decoration: InputDecoration(hintText: 'Name, z. B. PJE Systems', hintStyle: J.bodyDim)),
      ]),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: Text('Abbrechen', style: J.mono(12))),
        TextButton(
          onPressed: () => _name.text.trim().isEmpty ? null : Navigator.pop(context, (_platform, _name.text.trim())),
          child: Text('Anlegen', style: J.mono(12, color: J.cyan)),
        ),
      ],
    );
  }
}

// ------------------------------------------------------------------ publish
class _Publish extends ConsumerStatefulWidget {
  const _Publish({required this.status, required this.run, required this.done});
  final Map<String, dynamic> status;
  final _Runner run;
  final VoidCallback done;
  @override
  ConsumerState<_Publish> createState() => _PublishState();
}

class _PublishState extends ConsumerState<_Publish> {
  final _caption = TextEditingController();
  final _tags = TextEditingController();
  final _title = TextEditingController();
  String _source = 'inbox'; // 'inbox' | 'upload'
  String? _video; // inbox id or upload file name, depending on _source
  final Set<String> _chosen = {};
  DateTime? _when;

  @override
  void dispose() {
    _caption.dispose();
    _tags.dispose();
    _title.dispose();
    super.dispose();
  }

  Future<void> _pickTime() async {
    final now = DateTime.now();
    final day = await showDatePicker(context: context, firstDate: now, lastDate: now.add(const Duration(days: 365)), initialDate: now);
    if (day == null || !mounted) return;
    final t = await showTimePicker(context: context, initialTime: TimeOfDay.fromDateTime(now.add(const Duration(hours: 1))));
    if (t == null) return;
    setState(() => _when = DateTime(day.year, day.month, day.day, t.hour, t.minute));
  }

  Widget _inboxPicker(List<Map<String, dynamic>> items) => items.isEmpty
      ? Text('Eingang leer – Videos kommen aus der Videoproduktion oder per Upload am Mac.', style: J.bodyDim)
      : DropdownButton<String>(
          value: _video,
          isExpanded: true,
          hint: Text('Video wählen', style: J.bodyDim),
          dropdownColor: J.panel,
          style: J.body,
          items: [for (final i in items) DropdownMenuItem(value: '${i['id']}', child: Text('${(i['title'] as String?)?.isNotEmpty == true ? i['title'] : i['name']}', overflow: TextOverflow.ellipsis))],
          onChanged: (v) {
            final item = items.firstWhere((i) => i['id'] == v);
            final caps = (item['captions'] as Map?) ?? const {};
            final c = (caps['tiktok'] ?? caps['instagram_reels']) as Map?;
            setState(() {
              _video = v;
              if (_caption.text.isEmpty && c?['caption'] != null) _caption.text = '${c!['caption']}';
              if (_tags.text.isEmpty && c?['hashtags'] is List) _tags.text = (c!['hashtags'] as List).join(' ');
              if (_title.text.isEmpty) _title.text = '${(caps['youtube_shorts'] as Map?)?['title'] ?? item['title'] ?? ''}';
            });
          },
        );

  Widget _uploadPicker(List<Map<String, dynamic>> items) => items.isEmpty
      ? Text('Noch keine Uploads – Videos per „Hochladen“ am Mac in den Upload-Ordner legen.', style: J.bodyDim)
      : DropdownButton<String>(
          value: items.any((u) => u['name'] == _video) ? _video : null,
          isExpanded: true,
          hint: Text('Upload wählen', style: J.bodyDim),
          dropdownColor: J.panel,
          style: J.body,
          items: [for (final u in items) DropdownMenuItem(value: '${u['name']}', child: Text(uptikUploadTitle('${u['name']}'), overflow: TextOverflow.ellipsis))],
          onChanged: (v) => setState(() {
            _video = v;
            if (_title.text.isEmpty && v != null) _title.text = uptikUploadTitle(v);
          }),
        );

  @override
  Widget build(BuildContext context) {
    final picker = _source == 'upload' ? ref.watch(uptikUploadsProvider) : ref.watch(uptikInboxProvider);
    final usable = _accounts(widget.status).where((a) => a['status'] == 'connected' && a['circuit_state'] == 'ok').toList();
    if (usable.isEmpty) {
      return ListView(padding: const EdgeInsets.all(J.s4), children: [Text('Kein verbundenes Konto – unter Konten anlegen und anmelden.', style: J.bodyDim)]);
    }
    final platforms = {for (final a in usable) if (_chosen.contains(a['id'])) '${a['platform']}'};
    final tags = parseHashtags(_tags.text);
    final tooLong = uptikOverLimit(_caption.text, tags, platforms);
    return ListView(padding: const EdgeInsets.symmetric(horizontal: J.s4), children: [
      _section('Video', [
        Rail<String>(
          values: const ['inbox', 'upload'],
          labels: const ['Eingang', 'Upload'],
          value: _source,
          onChanged: (s) => setState(() {
            _source = s;
            _video = null;
          }),
        ),
        const SizedBox(height: J.s2),
        picker.when(
          loading: _loading,
          error: (e, _) => _error(e),
          data: (items) => _source == 'upload' ? _uploadPicker(items) : _inboxPicker(items),
        ),
      ]),
      _section('Konten', [
        for (final a in usable)
          _switch('${a['label']} · ${uptikPlatformLabel[a['platform']]}', _chosen.contains(a['id']),
              (v) => setState(() => v ? _chosen.add('${a['id']}') : _chosen.remove(a['id']))),
      ]),
      TextField(
        controller: _caption,
        maxLines: 5,
        maxLength: 5000,
        style: J.body,
        onChanged: (_) => setState(() {}),
        decoration: InputDecoration(hintText: 'Text', hintStyle: J.bodyDim),
      ),
      TextField(controller: _tags, style: J.body, onChanged: (_) => setState(() {}), decoration: InputDecoration(hintText: 'Hashtags', hintStyle: J.bodyDim)),
      if (platforms.contains('youtube_shorts'))
        TextField(controller: _title, maxLength: 100, style: J.body, decoration: InputDecoration(hintText: 'Titel (YouTube)', hintStyle: J.bodyDim)),
      if (tooLong.isNotEmpty) Text('Zu lang für ${tooLong.map((p) => uptikPlatformLabel[p]).join(', ')}', style: J.mono(12, color: J.red)),
      const SizedBox(height: J.s3),
      _kv('Zeitpunkt', _when == null ? 'sofort nach Freigabe' : '${_when!.day}.${_when!.month}. ${uptikPublishAt(_when)!.substring(11)} (Berlin)'),
      Wrap(spacing: J.s4, children: [
        LightAction('Zeit wählen', color: J.text2, onTap: _pickTime),
        if (_when != null) LightAction('Sofort', color: J.text2, onTap: () => setState(() => _when = null)),
      ]),
      const SizedBox(height: J.s4),
      Align(
        alignment: Alignment.centerRight,
        child: LightAction(
          _when == null ? 'Veröffentlichen (Freigabe)' : 'Planen (Freigabe)',
          onTap: _video == null || _chosen.isEmpty || tooLong.isNotEmpty
              ? null
              : () async {
                  await widget.run(
                    () => ref.read(apiProvider).post('/api/uptik/publish', {
                      'video': uptikVideoRef(_source, _video),
                      'caption': _caption.text.trim(),
                      'hashtags': tags,
                      'title': _title.text.trim(),
                      'accounts': _chosen.toList(),
                      'publish_at': ?uptikPublishAt(_when),
                    }),
                    'Eingereiht',
                  );
                  widget.done();
                },
        ),
      ),
      const SizedBox(height: J.s6),
    ]);
  }
}

// ------------------------------------------------------------------ queue and history
Widget _post(Map<String, dynamic> p, Map<String, String> labels, List<Widget> actions) => Padding(
      padding: const EdgeInsets.symmetric(vertical: J.s2),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('${uptikPlatformLabel[p['platform']] ?? p['platform']} · ${labels[p['account_id']] ?? 'entferntes Konto'}', style: J.body),
        Text('${uptikPostLabel(p['status'])} · ${p['finished_at'] != null ? _when(p['finished_at']) : _when(p['run_at'])}'
            '${(p['attempts'] as int? ?? 0) > 0 ? ' · Versuch ${p['attempts']}/${p['max_attempts']}' : ''}',
            style: J.mono(12, color: _tone(uptikPostTone(p['status'])))),
        Text('${p['video_name']}', style: J.mono(11, color: J.text3), overflow: TextOverflow.ellipsis),
        if (p['last_error'] != null) Text('${p['last_error']}', style: J.mono(11, color: J.amber)),
        if (actions.isNotEmpty) Wrap(spacing: J.s4, children: actions),
        const Hairline(),
      ]),
    );

Map<String, String> _labels(Map<String, dynamic> s) => {for (final a in _accounts(s)) '${a['id']}': '${a['label']}'};

class _Queue extends ConsumerWidget {
  const _Queue({required this.status, required this.run});
  final Map<String, dynamic> status;
  final _Runner run;
  @override
  Widget build(BuildContext context, WidgetRef ref) => ref.watch(uptikQueueProvider).when(
        loading: _loading,
        error: (e, _) => _error(e),
        data: (posts) => ListView(padding: const EdgeInsets.symmetric(horizontal: J.s4), children: [
          if (posts.isEmpty) Text('Nichts geplant.', style: J.bodyDim),
          for (final p in posts)
            _post(p, _labels(status), [
              if (p['status'] != 'running')
                LightAction('Abbrechen', color: J.red, onTap: () async {
                  final ok = await confirmAction(context, title: 'Beitrag abbrechen?', body: 'Er wird nicht veröffentlicht.', confirmLabel: 'Abbrechen', danger: true);
                  if (ok) await run(() => ref.read(apiProvider).post('/api/uptik/posts/${p['id']}/cancel'), 'Abgebrochen');
                }),
            ]),
        ]),
      );
}

class _History extends ConsumerWidget {
  const _History({required this.status, required this.run});
  final Map<String, dynamic> status;
  final _Runner run;
  @override
  Widget build(BuildContext context, WidgetRef ref) => ref.watch(uptikHistoryProvider).when(
        loading: _loading,
        error: (e, _) => _error(e),
        data: (posts) => ListView(padding: const EdgeInsets.symmetric(horizontal: J.s4), children: [
          if (posts.isEmpty) Text('Noch nichts veröffentlicht.', style: J.bodyDim),
          for (final p in posts)
            _post(p, _labels(status), [
              if (p['status'] == 'needs_review') ...[
                LightAction('Ist online', onTap: () => run(() => ref.read(apiProvider).post('/api/uptik/posts/${p['id']}/review', {'outcome': 'published'}), 'Markiert')),
                LightAction('Nicht online', color: J.text2, onTap: () => run(() => ref.read(apiProvider).post('/api/uptik/posts/${p['id']}/review', {'outcome': 'failed'}), 'Markiert')),
              ],
            ]),
        ]),
      );
}
