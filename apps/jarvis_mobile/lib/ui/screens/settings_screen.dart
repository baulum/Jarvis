import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:permission_handler/permission_handler.dart';

import '../../core/api.dart';
import '../../core/live.dart';
import '../../core/theme.dart';
import '../../state/providers.dart';
import '../widgets/hud.dart';
import '../widgets/model_picker.dart';
import '../widgets/wake_settings.dart';

const _categories = [
  ('task', 'Aufgaben', 'Website fertig, Auftrag erledigt'),
  ('appointment', 'Termine', 'Kalender, 30 min vorher'),
  ('customer', 'Kunden', 'Neue Anfragen'),
  ('error', 'Fehler', 'Fehlgeschlagene Abläufe, Deployments'),
  ('approval', 'Freigaben', 'Jarvis braucht Ihre Zustimmung'),
  ('reminder', 'Erinnerungen', 'Rückrufe, Zusagen'),
  ('system', 'Systemstatus', 'Jarvis wieder online'),
];
const _modes = ['off', 'silent', 'push', 'call'];
const _modeLabels = ['Aus', 'Still', 'Push', 'Anruf'];

final _policyProvider = FutureProvider.autoDispose<Map<String, dynamic>>(
    (ref) async => (await ref.read(apiProvider).get('/api/mobile/alert-policy') as Map).cast<String, dynamic>());
final _statusProvider = FutureProvider.autoDispose<Map<String, dynamic>>(
    (ref) async => (await ref.read(apiProvider).get('/api/mobile/status') as Map).cast<String, dynamic>());
final _devicesProvider = FutureProvider.autoDispose<List<Map<String, dynamic>>>((ref) async =>
    ((await ref.read(apiProvider).get('/api/mobile/devices')) as List).map((e) => (e as Map).cast<String, dynamic>()).toList());
final _locationProvider = FutureProvider.autoDispose<Map<String, dynamic>>((ref) => ref.read(nativeProvider).locationStatus());
final _phoneControlProvider = FutureProvider.autoDispose<bool>((ref) => ref.read(nativeProvider).phoneControlEnabled());

class SettingsScreen extends ConsumerStatefulWidget {
  const SettingsScreen({super.key});
  @override
  ConsumerState<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends ConsumerState<SettingsScreen> with WidgetsBindingObserver {
  String? _toast;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  // back from Android's accessibility settings: show the new state of "Handy-Steuerung durch Jarvis"
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) ref.invalidate(_phoneControlProvider);
  }

  Future<void> _patch(Map<String, dynamic> body) async {
    try {
      await ref.read(apiProvider).put('/api/mobile/alert-policy', body);
      ref.invalidate(_policyProvider);
    } on ApiError catch (e) {
      setState(() => _toast = e.message);
    }
  }

  Future<void> _test(String kind) async {
    setState(() => _toast = kind == 'call' ? 'Jarvis ruft gleich an …' : 'Sende …');
    try {
      final r = await ref.read(apiProvider).post('/api/mobile/test/$kind', kind == 'notify' ? {'category': 'system'} : null) as Map;
      setState(() => _toast = kind == 'call'
          ? 'Anruf ausgelöst.'
          : r['delivered'] is List && (r['delivered'] as List).isNotEmpty
              ? 'Zugestellt.'
              : 'Nicht zugestellt: ${r['reason'] ?? 'Push nicht eingerichtet'}');
    } on ApiError catch (e) {
      setState(() => _toast = e.message);
    }
  }

  Future<void> _setLocationSharing(bool on) async {
    final native = ref.read(nativeProvider);
    try {
      if (on) {
        if (!(await Permission.locationWhenInUse.request()).isGranted) {
          setState(() => _toast = 'Ohne Ortungsrecht kann Jarvis Ihren Standort nicht sehen.');
          return;
        }
        // Answering Jarvis while the app is closed needs "Immer zulassen" (Android opens its settings page)
        await Permission.locationAlways.request();
      }
      final ok = (await native.setLocationSharing(on))['sent'] == true;
      setState(() => _toast = switch ((on, ok)) {
            (true, true) => 'Standortfreigabe an. Standort gesendet.',
            (true, false) => 'Standortfreigabe an, aber kein Standort gesendet – Ortung an, Jarvis erreichbar?',
            (false, true) => 'Standortfreigabe aus. Jarvis hat den gespeicherten Standort gelöscht.',
            (false, false) => 'Standortfreigabe aus. Jarvis ist gerade nicht erreichbar – der gespeicherte Standort '
                'wird beim nächsten Öffnen der App gelöscht.',
          });
    } on PlatformException catch (e) {
      setState(() => _toast = e.message ?? 'Fehler');
    }
    ref.invalidate(_locationProvider);
  }

  Future<void> _sendLocation() async {
    setState(() => _toast = 'Ermittle Standort …');
    final sent = await ref.read(nativeProvider).reportLocation().catchError((Object _) => false);
    setState(() => _toast = sent ? 'Standort gesendet.' : 'Kein Standort ermittelt oder Jarvis nicht erreichbar.');
  }

  Future<void> _pickTime(String key, String current) async {
    final parts = current.split(':');
    final t = await showTimePicker(
      context: context,
      initialTime: TimeOfDay(hour: int.parse(parts[0]), minute: int.parse(parts[1])),
      builder: (ctx, child) => Theme(data: J.theme(), child: child!),
    );
    if (t == null) return;
    await _patch({
      'quiet_hours': {key: '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}'}
    });
  }

  @override
  Widget build(BuildContext context) {
    final policy = ref.watch(_policyProvider);
    final status = ref.watch(_statusProvider);
    final devices = ref.watch(_devicesProvider);
    final location = ref.watch(_locationProvider);
    final link = ref.watch(linkProvider).valueOrNull ?? Link.connecting;
    final pairing = ref.watch(pairingProvider).valueOrNull;

    return Scaffold(
      backgroundColor: J.void_,
      body: SafeArea(
        child: Column(children: [
          Padding(
            padding: const EdgeInsets.all(J.s2),
            child: Row(children: [
              Pressable(
                onTap: () => context.pop(),
                semantics: 'Zurück',
                child: const SizedBox.square(dimension: J.hit, child: Center(child: Glyph(GlyphKind.back))),
              ),
              Expanded(child: Text('Einstellungen', style: J.title)),
            ]),
          ),
          const Hairline(),
          if (_toast != null)
            Container(
              width: double.infinity,
              color: J.panel,
              padding: const EdgeInsets.symmetric(horizontal: J.s5, vertical: J.s3),
              child: Text(_toast!, style: J.mono(12, color: J.cyan)),
            ),
          Expanded(
            child: ListView(padding: const EdgeInsets.fromLTRB(J.s5, J.s4, J.s5, J.s7), children: [
              const Caps('Verbindung'),
              const SizedBox(height: J.s2),
              _kv('Status', link == Link.online ? 'verbunden' : link == Link.offline ? 'nicht erreichbar' : 'verbinde …',
                  color: link == Link.online ? J.green : J.amber),
              _kv('Adresse', pairing?.status['base_url']?.toString() ?? '–'),
              _kv('Zertifikat', pairing?.status['pin'] == null ? 'öffentlich (CA)' : 'gepinnt ${pairing!.status['pin'].toString().substring(0, 12)}…'),
              ...status.when(
                loading: () => [const SizedBox(height: J.s3)],
                error: (_, _) => [_kv('Core', 'nicht erreichbar', color: J.amber)],
                data: (s) {
                  final push = (s['push'] as Map).cast<String, dynamic>();
                  final calls = (s['calls'] as Map).cast<String, dynamic>();
                  final lk = (s['livekit'] as Map).cast<String, dynamic>();
                  return [
                    _kv('Push', push['configured'] == true ? 'eingerichtet' : 'fehlt', color: push['configured'] == true ? J.green : J.amber),
                    if (push['configured'] != true)
                      for (final m in (push['missing'] as List).cast<String>()) Text('— $m', style: J.mono(11, color: J.text3)),
                    _kv('Push auf diesem Gerät', pairing?.status['push_token_uploaded'] == true ? 'registriert' : 'nicht registriert',
                        color: pairing?.status['push_token_uploaded'] == true ? J.green : J.amber),
                    _kv('Sprachserver', lk['running'] == true ? 'LiveKit läuft' : 'LiveKit aus', color: lk['running'] == true ? J.green : J.amber),
                    _kv('Jarvis anrufen', calls['can_call_jarvis'] == true ? 'möglich' : (calls['problems'] as List).join(', '),
                        color: calls['can_call_jarvis'] == true ? J.green : J.amber),
                    _kv('Jarvis ruft an', calls['can_ring_owner'] == true ? 'möglich' : (calls['ring_problems'] as List).join(', '),
                        color: calls['can_ring_owner'] == true ? J.green : J.amber),
                  ];
                },
              ),
              const SizedBox(height: J.s2),
              Wrap(spacing: J.s4, children: [
                LightAction('Testbenachrichtigung', onTap: () => _test('notify')),
                LightAction('Testanruf', onTap: () => _test('call')),
                LightAction('Modell & Kontingent', onTap: () => showModelSheet(context)),
              ]),
              const SizedBox(height: J.s6),
              const Caps('Wann Jarvis Sie erreicht'),
              const SizedBox(height: J.s1),
              Text('Anrufe nur bei ausdrücklichem Auftrag („ruf mich an, wenn …“) oder wenn Sie eine Kategorie auf „Anruf“ stellen.',
                  style: J.bodyDim.copyWith(fontSize: 13.5)),
              ...policy.when(
                loading: () => [const SizedBox(height: J.s4)],
                error: (e, _) => [Text('$e', style: J.bodyDim)],
                data: (p) {
                  final cats = (p['categories'] as Map).cast<String, dynamic>();
                  final qh = (p['quiet_hours'] as Map).cast<String, dynamic>();
                  return [
                    for (final (id, name, hint) in _categories) ...[
                      const SizedBox(height: J.s4),
                      Row(children: [
                        Expanded(child: Text(name, style: J.body)),
                        Text(hint, style: J.mono(10.5, color: J.text3)),
                      ]),
                      const SizedBox(height: J.s2),
                      Rail<String>(
                        values: _modes,
                        labels: _modeLabels,
                        value: (cats[id] as Map)['mode'] as String,
                        colors: const {'call': J.green, 'off': J.text3},
                        onChanged: (m) => _patch({
                          'categories': {id: {'mode': m}}
                        }),
                      ),
                    ],
                    const SizedBox(height: J.s5),
                    _switch('Anrufe erlaubt', p['calls_enabled'] == true, (v) => _patch({'calls_enabled': v})),
                    _switch('Ruhezeit', qh['enabled'] == true, (v) => _patch({'quiet_hours': {'enabled': v}})),
                    if (qh['enabled'] == true)
                      Row(children: [
                        Text('von', style: J.bodyDim),
                        LightAction(qh['start'] as String, onTap: () => _pickTime('start', qh['start'] as String)),
                        Text('bis', style: J.bodyDim),
                        LightAction(qh['end'] as String, onTap: () => _pickTime('end', qh['end'] as String)),
                      ]),
                    Text('In der Ruhezeit: Push wird still, Anrufe werden zur Benachrichtigung.', style: J.mono(11, color: J.text3)),
                    _switch('Kritisches durchlassen', p['critical_bypasses_quiet_hours'] == true,
                        (v) => _patch({'critical_bypasses_quiet_hours': v})),
                  ];
                },
              ),
              const SizedBox(height: J.s6),
              const Caps('Standortfreigabe'),
              const SizedBox(height: J.s1),
              Text('Jarvis kennt dann Ihren letzten Standort, z. B. für „Wie lange brauche ich zu …?“. Er geht nur an Ihren '
                  'Jarvis-Server, nur der letzte wird gespeichert; Ausschalten löscht ihn dort.',
                  style: J.bodyDim.copyWith(fontSize: 13.5)),
              ...location.when(
                loading: () => [const SizedBox(height: J.s4)],
                error: (e, _) => [Text('$e', style: J.bodyDim)],
                data: (l) {
                  final perm = l['permission'] as String? ?? 'none';
                  return [
                    _switch('Standort mit Jarvis teilen', l['enabled'] == true, _setLocationSharing),
                    _kv('Ortung', perm == 'always' ? 'immer erlaubt' : perm == 'while_in_use' ? 'nur bei geöffneter App' : 'nicht erlaubt',
                        color: perm == 'always' ? J.green : J.amber),
                    if (l['enabled'] == true && perm == 'while_in_use')
                      Text('Damit Jarvis Sie auch bei geschlossener App orten kann: Standort „Immer zulassen“.',
                          style: J.mono(11, color: J.text3)),
                    if (l['precise'] != true && perm != 'none') Text('Nur ungefährer Standort erlaubt.', style: J.mono(11, color: J.text3)),
                    if (l['location_on'] != true) Text('Die Standortermittlung des Handys ist ausgeschaltet.', style: J.mono(11, color: J.amber)),
                    Wrap(spacing: J.s4, children: [
                      LightAction('Ortungsrechte', onTap: () => ref.read(nativeProvider).openSettings('app')),
                      if (l['enabled'] == true) LightAction('Jetzt senden', onTap: _sendLocation),
                    ]),
                  ];
                },
              ),
              const SizedBox(height: J.s6),
              const WakeSettings(),
              const SizedBox(height: J.s6),
              const Caps('Handy-Steuerung durch Jarvis'),
              const SizedBox(height: J.s1),
              Text('Jarvis kann dann auf Ihre Anweisung Apps öffnen, den Bildschirm lesen, tippen und scrollen '
                  '(„Öffne WhatsApp auf meinem Handy“). Passwortfelder liest und füllt er nie. '
                  'Wird über die Bedienungshilfen von Android eingeschaltet.',
                  style: J.bodyDim.copyWith(fontSize: 13.5)),
              ...ref.watch(_phoneControlProvider).when(
                    loading: () => [const SizedBox(height: J.s4)],
                    error: (e, _) => [Text('$e', style: J.bodyDim)],
                    data: (on) => [
                      _kv('Status', on ? 'aktiv' : 'inaktiv', color: on ? J.green : J.amber),
                      if (!on)
                        Text('Tippen Sie unter „Bedienungshilfen“ auf „J.A.R.V.I.S.“ › „Handy-Steuerung durch Jarvis“ und schalten Sie sie ein. '
                            'Ist der Schalter ausgegraut: App-Info › ⋮ › „Eingeschränkte Einstellungen zulassen“.',
                            style: J.mono(11, color: J.text3)),
                      Wrap(spacing: J.s4, children: [
                        LightAction('Bedienungshilfen öffnen', onTap: () => ref.read(nativeProvider).openSettings('accessibility')),
                        if (!on) LightAction('App-Info', onTap: () => ref.read(nativeProvider).openSettings('app')),
                      ]),
                    ],
                  ),
              const SizedBox(height: J.s6),
              const Caps('Berechtigungen'),
              Wrap(spacing: J.s4, children: [
                LightAction('Benachrichtigungen', onTap: () => ref.read(nativeProvider).openSettings('notifications')),
                LightAction('Vollbild-Anrufe', onTap: () => ref.read(nativeProvider).openSettings('fullscreen')),
                LightAction('Akku', onTap: () => ref.read(nativeProvider).openSettings('battery')),
              ]),
              const SizedBox(height: J.s6),
              const Caps('Gekoppelte Telefone'),
              ...devices.when(
                loading: () => [const SizedBox()],
                error: (_, _) => [const SizedBox()],
                data: (rows) => [
                  for (final d in rows.where((d) => d['revoked'] != true))
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: J.s2),
                      child: Row(children: [
                        Expanded(
                          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                            Text(d['name'] as String, style: J.body),
                            Text('${d['model']} · ${d['push'] == true ? 'Push' : 'kein Push'}${d['current'] == true ? ' · dieses Gerät' : ''}',
                                style: J.mono(10.5, color: J.text3)),
                          ]),
                        ),
                        if (d['current'] != true)
                          LightAction('Entfernen', color: J.red, onTap: () async {
                            await ref.read(apiProvider).delete('/api/mobile/devices/${d['id']}');
                            ref.invalidate(_devicesProvider);
                          }),
                      ]),
                    ),
                ],
              ),
              const SizedBox(height: J.s6),
              LightAction('Abmelden und Kopplung entfernen', color: J.red, onTap: () async {
                await ref.read(pairingProvider.notifier).logout();
              }),
            ]),
          ),
        ]),
      ),
    );
  }

  Widget _kv(String k, String v, {Color color = J.text}) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 5),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          SizedBox(width: 150, child: Text(k, style: J.bodyDim.copyWith(fontSize: 14))),
          Expanded(child: Text(v, style: J.mono(12, color: color))),
        ]),
      );

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
}
