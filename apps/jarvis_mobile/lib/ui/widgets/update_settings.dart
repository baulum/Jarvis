import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme.dart';
import '../../state/app_update.dart';
import '../../state/providers.dart';
import 'hud.dart';

/// Settings › "App-Updates": installed version, the update in progress and what Sir may have to do once.
class UpdateSettings extends ConsumerStatefulWidget {
  const UpdateSettings({super.key});
  @override
  ConsumerState<UpdateSettings> createState() => _UpdateSettingsState();
}

class _UpdateSettingsState extends ConsumerState<UpdateSettings> with WidgetsBindingObserver {
  String? _note;
  bool _checking = false;

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

  // back from "Install unknown apps": show (and use) the new permission
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) ref.read(appUpdateProvider.notifier).load();
  }

  Future<void> _check() async {
    setState(() {
      _checking = true;
      _note = null;
    });
    final err = await ref.read(appUpdateProvider.notifier).check(manual: true);
    if (mounted) {
      setState(() {
        _checking = false;
        _note = err;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = ref.watch(appUpdateProvider);
    final native = ref.read(nativeProvider);
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      const Caps('App-Updates'),
      const SizedBox(height: J.s1),
      Text('Neue Versionen baut Jarvis selbst und schickt sie signiert über Ihre Verbindung (auch unterwegs per '
          'Tailscale). Daten, Kopplung und Berechtigungen bleiben erhalten.',
          style: J.bodyDim.copyWith(fontSize: 13.5)),
      const SizedBox(height: J.s2),
      if (s == null)
        Text('Status nicht verfügbar.', style: J.mono(11, color: J.text3))
      else ...[
        _kv('Installiert', s.versionName ?? '–'),
        _kv('Status', s.headline, color: _tone(s)),
        if (s.state == 'downloading' && s.progress != null)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: J.s2),
            child: ClipRRect(
              borderRadius: BorderRadius.circular(2),
              child: LinearProgressIndicator(value: s.progress, minHeight: 3, color: J.cyan, backgroundColor: J.panel),
            ),
          ),
        if (s.error != null && s.state != 'up_to_date' && s.state != 'installed')
          Text(s.error!, style: J.mono(11, color: s.state == 'failed' || s.state == 'unsupported' ? J.amber : J.text3)),
        if (!s.canInstall)
          Text('Einmalig nötig: „Unbekannte Apps installieren“ für J.A.R.V.I.S. erlauben – danach kommen Updates von selbst.',
              style: J.mono(11, color: J.text3)),
        if (s.canInstall && !s.unattended)
          Text('Dieses Android fragt bei jedem Update einmal nach (automatisch ohne Nachfrage erst ab Android 12).',
              style: J.mono(11, color: J.text3)),
        if (s.changelog.isNotEmpty && s.state != 'up_to_date' && s.state != 'installed') ...[
          const SizedBox(height: J.s2),
          for (final c in s.changelog.take(5)) Text('— $c', style: J.mono(11, color: J.text2)),
        ],
        if (s.lastCheck != null)
          Padding(
            padding: const EdgeInsets.only(top: J.s1),
            child: Text('Zuletzt geprüft ${_time(s.lastCheck!)}', style: J.mono(10.5, color: J.text3)),
          ),
      ],
      if (_note != null) Text(_note!, style: J.mono(11, color: J.amber)),
      Wrap(spacing: J.s4, children: [
        LightAction('Jetzt prüfen', busy: _checking || (s?.busy ?? false), onTap: _checking ? null : _check),
        if (s != null && s.installable) LightAction('Jetzt installieren', onTap: () => ref.read(appUpdateProvider.notifier).install()),
        if (s != null && !s.canInstall) LightAction('Installation erlauben', onTap: () => native.openSettings('install')),
      ]),
    ]);
  }

  static Color _tone(AppUpdateStatus s) => switch (s.state) {
        'up_to_date' || 'installed' => J.green,
        'failed' || 'unsupported' => J.amber,
        'idle' => J.text2,
        _ => J.cyan,
      };

  static String _time(DateTime t) {
    final d = DateTime.now().difference(t);
    if (d.inMinutes < 1) return 'gerade eben';
    if (d.inHours < 1) return 'vor ${d.inMinutes} min';
    if (d.inDays < 1) return 'vor ${d.inHours} h';
    return 'am ${t.day}.${t.month}.';
  }

  Widget _kv(String k, String v, {Color color = J.text}) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 5),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          SizedBox(width: 150, child: Text(k, style: J.bodyDim.copyWith(fontSize: 14))),
          Expanded(child: Text(v, style: J.mono(12, color: color))),
        ]),
      );
}
