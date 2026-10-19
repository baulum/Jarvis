import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:permission_handler/permission_handler.dart';

import '../../core/theme.dart';
import '../../state/providers.dart';
import 'hud.dart';

/// Settings › "Hey Jarvis": wake word, voice overlay, default assistant – with the permissions each one needs.
class WakeSettings extends ConsumerStatefulWidget {
  const WakeSettings({super.key});
  @override
  ConsumerState<WakeSettings> createState() => _WakeSettingsState();
}

class _WakeSettingsState extends ConsumerState<WakeSettings> with WidgetsBindingObserver {
  Map<String, dynamic>? _s;
  String? _note;
  final _key = TextEditingController();
  Timer? _poll;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _load();
    // The service changes state on its own (call, screen off, overlay) – keep the rows honest while visible.
    _poll = Timer.periodic(const Duration(seconds: 3), (_) => _load());
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState s) {
    if (s == AppLifecycleState.resumed) _load(); // back from a system settings page
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _poll?.cancel();
    _key.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final s = await ref.read(nativeProvider).wakeStatus();
    if (mounted) setState(() => _s = s);
  }

  Future<bool> _configure({bool? enabled, bool? screenOnly, String? accessKey}) async {
    try {
      final s = await ref.read(nativeProvider).wakeConfigure(enabled: enabled, screenOnly: screenOnly, accessKey: accessKey);
      if (mounted) setState(() => _s = s);
      return true;
    } on PlatformException catch (e) {
      if (mounted) setState(() => _note = e.message ?? 'Speichern fehlgeschlagen.');
      return false;
    }
  }

  Future<void> _saveKey() async {
    final k = _key.text.trim();
    if (k.isEmpty) return;
    if (!await _configure(accessKey: k)) return;
    _key.clear();
    if (mounted) setState(() => _note = 'AccessKey gespeichert (verschlüsselt im Android Keystore).');
  }

  Future<void> _toggle(bool on) async {
    if (!on) {
      await _configure(enabled: false);
      return;
    }
    if (_s?['has_key'] != true) {
      setState(() => _note = 'Bitte zuerst den Picovoice-AccessKey eintragen (siehe unten).');
      return;
    }
    if (!await _explain()) return;
    if (!await _microphone()) return;
    await _notifications();
    await _configure(enabled: true);
    if (mounted && _s?['overlay'] != true && _s?['assistant'] != true) {
      setState(() => _note = 'Aktiv. Damit sich das Sprachfenster über anderen Apps öffnen kann, '
          'bitte noch „Über anderen Apps einblenden“ erlauben oder Jarvis als Standard-Assistent festlegen.');
    }
  }

  Future<bool> _microphone() async {
    final st = await Permission.microphone.request();
    if (st.isGranted) return true;
    if (mounted) setState(() => _note = 'Ohne Mikrofon kann Jarvis nicht auf „Hey Jarvis“ hören.');
    if (st.isPermanentlyDenied) await ref.read(nativeProvider).openSettings('app');
    return false;
  }

  Future<void> _notifications() async {
    final st = await Permission.notification.request();
    if (!st.isGranted && mounted) {
      setState(() => _note = 'Ohne Benachrichtigungen läuft die Erkennung trotzdem – Android zeigt den Dienst dann nur '
          'in der Liste aktiver Apps. Pausieren/Ausschalten geht dann nur hier.');
    }
  }

  Future<bool> _explain() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: J.abyss,
        title: Text('„Hey Jarvis“ aktivieren', style: J.title),
        content: Text(
          'Das Telefon hört dann im Hintergrund auf das Aktivierungswort. Die Erkennung läuft vollständig auf dem '
          'Gerät – kein Ton verlässt das Telefon, bevor Sie „Hey Jarvis“ sagen. Erst Ihre Frage danach geht an Ihren '
          'Jarvis.\n\n'
          'Android verlangt dafür das Mikrofon und einen dauerhaften, stillen Hinweis in der Benachrichtigungsleiste. '
          'Während eines Jarvis-Anrufs pausiert die Erkennung; auf Wunsch auch bei ausgeschaltetem Bildschirm (spart Akku).',
          style: J.bodyDim,
        ),
        actions: [
          LightAction('Abbrechen', color: J.text3, onTap: () => Navigator.pop(ctx, false)),
          LightAction('Aktivieren', onTap: () => Navigator.pop(ctx, true)),
        ],
      ),
    );
    return ok == true;
  }

  @override
  Widget build(BuildContext context) {
    final s = _s;
    final native = ref.read(nativeProvider);
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      const Caps('Hey Jarvis'),
      const SizedBox(height: J.s1),
      Text('Wie Google Assistant: „Hey Jarvis“ sagen, über dem aktuellen Bildschirm öffnet sich das Sprachfenster, '
          'Jarvis antwortet als Text und Sprache.',
          style: J.bodyDim.copyWith(fontSize: 13.5)),
      if (_note != null) Padding(padding: const EdgeInsets.only(top: J.s3), child: Text(_note!, style: J.mono(12, color: J.cyan))),
      if (s == null)
        const SizedBox(height: J.s4)
      else ...[
        const SizedBox(height: J.s2),
        _switch('„Hey Jarvis“ erkennen', s['enabled'] == true, _toggle),
        _row('Status', _state(s), color: _stateColor(s)),
        if ((s['last_wake'] as num? ?? 0) > 0) _row('Zuletzt erkannt', _time(s['last_wake'] as num)),
        _row('Schlüsselwort', s['keyword']?.toString() ?? '–'),
        _switch('Nur bei eingeschaltetem Bildschirm', s['screen_only'] == true, (v) => _configure(screenOnly: v)),
        Text('Spart Akku: bei ausgeschaltetem Bildschirm ist das Mikrofon dann ganz aus.', style: J.mono(11, color: J.text3)),
        const SizedBox(height: J.s4),
        _perm('Mikrofon', s['mic'] == true, 'Für das Aktivierungswort und Ihre Frage.', () async {
          // granted → start the service right away (Android only allows it while the app is in front)
          if (await _microphone()) {
            await _configure();
          } else {
            await _load();
          }
        }),
        _perm('Benachrichtigungen', s['notifications'] == true, 'Android zeigt den laufenden Dienst als stillen Hinweis.', () async {
          if (!(await Permission.notification.request()).isGranted) await native.openSettings('notifications');
          await _load();
        }),
        _perm('Über anderen Apps', s['overlay'] == true, 'Damit sich das Sprachfenster nach „Hey Jarvis“ über jeder App öffnet.',
            () => native.openSettings('overlay'),
            optional: s['assistant'] == true),
        _perm('Standard-Assistent', s['assistant'] == true,
            'Dann startet Jarvis auch per Assistent-Geste (Ecke wischen / Ein-Taste halten) – dort „J.A.R.V.I.S.“ wählen.',
            () => native.openSettings('assistant'),
            optional: true, action: 'Festlegen'),
        _perm('Akku-Optimierung aus', s['battery_unrestricted'] == true,
            'Empfohlen: sonst beenden manche Hersteller den Dienst im Hintergrund. In der Liste J.A.R.V.I.S. auf „Nicht optimieren“.',
            () => native.openSettings('battery'),
            optional: true, action: 'Öffnen'),
        const SizedBox(height: J.s4),
        Text(s['has_key'] == true ? 'Picovoice-AccessKey: gespeichert' : 'Picovoice-AccessKey: fehlt',
            style: J.mono(12, color: s['has_key'] == true ? J.green : J.amber)),
        Row(children: [
          Expanded(
            child: TextField(
              controller: _key,
              obscureText: true,
              autocorrect: false,
              enableSuggestions: false,
              style: J.mono(12, color: J.text),
              decoration: InputDecoration(
                hintText: s['has_key'] == true ? 'neuen Schlüssel einfügen' : 'AccessKey einfügen',
                hintStyle: J.mono(12, color: J.text3),
                enabledBorder: const UnderlineInputBorder(borderSide: BorderSide(color: J.line)),
                focusedBorder: const UnderlineInputBorder(borderSide: BorderSide(color: J.cyan)),
              ),
              onSubmitted: (_) => _saveKey(),
            ),
          ),
          LightAction('Speichern', onTap: _saveKey),
        ]),
        Text('Kostenlos für den privaten Gebrauch unter console.picovoice.ai (Konto nötig). Der Schlüssel bleibt auf diesem '
            'Telefon; einmalig braucht Porcupine Internet zur Aktivierung.',
            style: J.mono(11, color: J.text3)),
        const SizedBox(height: J.s2),
        LightAction('Sprachfenster testen', onTap: native.openAssist),
      ],
    ]);
  }

  /// läuft / gestoppt / Fehler – always with the reason, so a deaf wake word never looks fine.
  String _state(Map<String, dynamic> s) {
    if (s['enabled'] != true) return 'gestoppt · ausgeschaltet';
    if (s['error'] != null) return 'Fehler · ${s['error']}';
    if (s['running'] != true) return s['blocked'] != null ? 'gestoppt · ${s['blocked']}' : 'gestoppt · startet beim Öffnen der App …';
    return s['listening'] == true ? 'läuft · hört zu' : 'läuft · pausiert (${s['reason']})';
  }

  Color _stateColor(Map<String, dynamic> s) {
    if (s['enabled'] != true) return J.text2;
    if (s['error'] != null || s['running'] != true) return J.amber;
    return s['listening'] == true ? J.green : J.text2;
  }

  String _time(num ms) {
    final t = DateTime.fromMillisecondsSinceEpoch(ms.toInt());
    String two(int v) => v.toString().padLeft(2, '0');
    return '${two(t.day)}.${two(t.month)}. ${two(t.hour)}:${two(t.minute)}:${two(t.second)}';
  }

  Widget _row(String k, String v, {Color color = J.text}) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 5),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          SizedBox(width: 150, child: Text(k, style: J.bodyDim.copyWith(fontSize: 14))),
          Expanded(child: Text(v, style: J.mono(12, color: color))),
        ]),
      );

  Widget _perm(String name, bool ok, String why, VoidCallback fix, {bool optional = false, String action = 'Erlauben'}) => Padding(
        padding: const EdgeInsets.symmetric(vertical: J.s1),
        child: Row(crossAxisAlignment: CrossAxisAlignment.center, children: [
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Text(name, style: J.body.copyWith(fontSize: 15)),
                const SizedBox(width: J.s2),
                Text(ok ? 'erteilt' : (optional ? 'optional' : 'fehlt'),
                    style: J.mono(11, color: ok ? J.green : (optional ? J.text3 : J.amber))),
              ]),
              Text(why, style: J.mono(11, color: J.text3)),
            ]),
          ),
          if (!ok) LightAction(action, onTap: fix),
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
