import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:mobile_scanner/mobile_scanner.dart';
import 'package:permission_handler/permission_handler.dart';

import '../../core/theme.dart';
import '../../state/live_model.dart';
import '../../state/providers.dart';
import '../widgets/core_orb.dart';
import '../widgets/hud.dart';

/// First launch: pair with the Mac (QR from `jarvisctl mobile-pair`), watch the core ignite, then grant the
/// permissions a phone call needs – each one explained at the moment it matters.
class PairScreen extends ConsumerStatefulWidget {
  const PairScreen({super.key, this.linkPayload});
  final String? linkPayload; // base64url from jarvis://pair?p=…
  @override
  ConsumerState<PairScreen> createState() => _PairScreenState();
}

enum _Step { intro, scan, pairing, ignite, permissions }

class _PairScreenState extends ConsumerState<PairScreen> {
  _Step _step = _Step.intro;
  String? _error;
  final _name = TextEditingController();
  final _manual = TextEditingController();
  MobileScannerController? _scanner;
  bool _handled = false;
  bool _linkOffered = false;

  @override
  void initState() {
    super.initState();
    _takeLink(widget.linkPayload);
    ref.read(nativeProvider).status().then((s) {
      if (mounted && _name.text.isEmpty) _name.text = (s['model'] as String? ?? 'Smartphone').trim();
    });
  }

  void _takeLink(String? p) {
    if (p == null) return;
    try {
      _manual.text = utf8.decode(base64Url.decode(base64Url.normalize(p)));
      _linkOffered = true;
    } catch (_) {}
  }

  @override
  void didUpdateWidget(PairScreen old) {
    super.didUpdateWidget(old);
    if (widget.linkPayload != old.linkPayload) setState(() => _takeLink(widget.linkPayload));
  }

  @override
  void dispose() {
    _scanner?.dispose();
    _name.dispose();
    _manual.dispose();
    super.dispose();
  }

  Future<void> _startScan() async {
    if (!await Permission.camera.request().isGranted) {
      setState(() => _error = 'Ohne Kamera bitte den Kopplungstext einfügen.');
      return;
    }
    _handled = false;
    _scanner = MobileScannerController(formats: const [BarcodeFormat.qrCode]);
    setState(() {
      _error = null;
      _step = _Step.scan;
    });
  }

  Future<void> _pair(String payload) async {
    if (_handled) return;
    _handled = true;
    try {
      final j = jsonDecode(payload) as Map;
      if (j['t'] != 'jarvis-pair') throw const FormatException();
    } catch (_) {
      _handled = false;
      setState(() => _error = 'Das ist kein J.A.R.V.I.S.-Kopplungscode.');
      return;
    }
    HapticFeedback.mediumImpact();
    await _scanner?.stop();
    setState(() {
      _step = _Step.pairing;
      _error = null;
    });
    try {
      // pairing state changes only after the ignite moment (the router would leave this screen at once)
      await ref.read(nativeProvider).pair(payload, _name.text.trim().isEmpty ? 'Smartphone' : _name.text.trim());
      HapticFeedback.heavyImpact();
      setState(() => _step = _Step.ignite);
      await Future<void>.delayed(J.cinematic + const Duration(milliseconds: 900));
      if (mounted) setState(() => _step = _Step.permissions);
    } catch (e) {
      _handled = false;
      setState(() {
        _step = _Step.intro;
        _error = e.toString().replaceFirst(RegExp(r'^PlatformException\(jarvis, '), '').replaceFirst(RegExp(r', null, null\)$'), '');
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: J.void_,
      body: SafeArea(
        child: AnimatedSwitcher(
          duration: J.slow,
          switchInCurve: J.enter,
          child: switch (_step) {
            _Step.intro => _intro(),
            _Step.scan => _scan(),
            _Step.pairing => _center('Kopple mit Jarvis …'),
            _Step.ignite => const _Ignite(),
            _Step.permissions => _Permissions(onDone: () => ref.read(pairingProvider.notifier).refresh()),
          },
        ),
      ),
    );
  }

  Widget _center(String text) => Center(
        key: ValueKey(text),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const CoreOrb(mode: Mode.thinking, size: 220),
          Text(text, style: J.mono(13, color: J.cyan)),
        ]),
      );

  Widget _intro() => ListView(
        key: const ValueKey('intro'),
        padding: const EdgeInsets.fromLTRB(J.s5, J.s7, J.s5, J.s5),
        children: [
          Text('J.A.R.V.I.S.', style: J.wordmark),
          const SizedBox(height: J.s6),
          Text('Dieses Telefon\nmit Jarvis koppeln.', style: J.display),
          const SizedBox(height: J.s4),
          Text(
            'Auf dem Mac im Terminal „jarvisctl mobile-pair“ ausführen. Der QR-Code enthält die Adresse '
            'Ihres Jarvis, den Zertifikats-Fingerabdruck und einen Einmalcode (10 Minuten gültig).',
            style: J.bodyDim,
          ),
          const SizedBox(height: J.s6),
          const Caps('Gerätename'),
          TextField(
            controller: _name,
            style: J.body,
            decoration: const InputDecoration(
              enabledBorder: UnderlineInputBorder(borderSide: BorderSide(color: J.line)),
              focusedBorder: UnderlineInputBorder(borderSide: BorderSide(color: J.cyan)),
            ),
          ),
          const SizedBox(height: J.s6),
          if (_linkOffered) ...[
            Text('Kopplungslink erhalten für ${_linkHost()}.', style: J.body.copyWith(color: J.cyan)),
            LightAction('Mit diesem Jarvis koppeln', onTap: () => _pair(_manual.text.trim())),
            const SizedBox(height: J.s3),
          ],
          Row(children: [
            Expanded(child: LightAction('QR-Code scannen', onTap: _startScan)),
          ]),
          const SizedBox(height: J.s4),
          ExpansionTile(
            tilePadding: EdgeInsets.zero,
            collapsedIconColor: J.text3,
            iconColor: J.cyan,
            title: Text('Ohne Kamera: Kopplungstext einfügen', style: J.label),
            children: [
              TextField(
                controller: _manual,
                maxLines: 3,
                style: J.mono(11.5, color: J.text),
                decoration: const InputDecoration(
                  hintText: '{"t":"jarvis-pair", …}',
                  enabledBorder: OutlineInputBorder(borderSide: BorderSide(color: J.line)),
                  focusedBorder: OutlineInputBorder(borderSide: BorderSide(color: J.cyan)),
                ),
              ),
              Align(alignment: Alignment.centerRight, child: LightAction('Koppeln', onTap: () => _pair(_manual.text.trim()))),
            ],
          ),
          if (_error != null) Padding(padding: const EdgeInsets.only(top: J.s4), child: Text(_error!, style: J.mono(12.5, color: J.amber))),
        ],
      );

  String _linkHost() {
    try {
      return Uri.parse(((jsonDecode(_manual.text) as Map)['urls'] as List).first as String).host;
    } catch (_) {
      return 'unbekannt';
    }
  }

  Widget _scan() => Stack(key: const ValueKey('scan'), children: [
        Positioned.fill(
          child: MobileScanner(
            controller: _scanner!,
            onDetect: (cap) {
              final v = cap.barcodes.firstOrNull?.rawValue;
              if (v != null) _pair(v);
            },
          ),
        ),
        const Positioned.fill(child: IgnorePointer(child: CustomPaint(painter: _ScanFrame()))),
        Positioned(
          left: J.s5,
          right: J.s5,
          bottom: J.s6,
          child: Column(children: [
            Text('QR-Code vom Mac in den Rahmen halten', style: J.mono(12.5, color: J.ice)),
            if (_error != null) Padding(padding: const EdgeInsets.only(top: J.s2), child: Text(_error!, style: J.mono(12, color: J.amber))),
            const SizedBox(height: J.s3),
            LightAction('Abbrechen', color: J.text2, onTap: () {
              _scanner?.stop();
              setState(() => _step = _Step.intro);
            }),
          ]),
        ),
      ]);
}

class _ScanFrame extends CustomPainter {
  const _ScanFrame();
  @override
  void paint(Canvas c, Size s) {
    final side = s.width * 0.68;
    final r = Rect.fromCenter(center: Offset(s.width / 2, s.height * 0.44), width: side, height: side);
    c.drawPath(
      Path.combine(PathOperation.difference, Path()..addRect(Offset.zero & s), Path()..addRRect(RRect.fromRectAndRadius(r, const Radius.circular(14)))),
      Paint()..color = J.void_.withValues(alpha: 0.72),
    );
    final p = Paint()
      ..color = J.cyan
      ..strokeWidth = 2
      ..style = PaintingStyle.stroke;
    const l = 26.0;
    for (final (o, dx, dy) in [(r.topLeft, 1.0, 1.0), (r.topRight, -1.0, 1.0), (r.bottomLeft, 1.0, -1.0), (r.bottomRight, -1.0, -1.0)]) {
      c.drawLine(o, o + Offset(l * dx, 0), p);
      c.drawLine(o, o + Offset(0, l * dy), p);
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter old) => false;
}

/// Signature moment: the core ignites from darkness when the pairing succeeded.
class _Ignite extends StatefulWidget {
  const _Ignite();
  @override
  State<_Ignite> createState() => _IgniteState();
}

class _IgniteState extends State<_Ignite> with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(vsync: this, duration: J.cinematic)..forward();
  final _level = ValueNotifier<double>(0);

  @override
  void initState() {
    super.initState();
    _c.addListener(() {
      final v = _c.value;
      _level.value = v < 0.35 ? v / 0.35 : (1 - (v - 0.35) / 0.65) * 0.6; // flare, then settle
    });
  }

  @override
  void dispose() {
    _c.dispose();
    _level.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          FadeTransition(
            opacity: CurvedAnimation(parent: _c, curve: const Interval(0, 0.4, curve: Curves.easeOut)),
            child: ScaleTransition(
              scale: Tween(begin: 0.6, end: 1.0).animate(CurvedAnimation(parent: _c, curve: J.settle)),
              child: CoreOrb(mode: Mode.speaking, level: _level, size: 320, ringing: true),
            ),
          ),
          FadeTransition(
            opacity: CurvedAnimation(parent: _c, curve: const Interval(0.55, 1)),
            child: Column(children: [
              Text('Verbunden.', style: J.display),
              const SizedBox(height: J.s2),
              Text('Zu Ihren Diensten, Sir.', style: J.bodyDim),
            ]),
          ),
        ]),
      );
}

/// What a real phone call needs, asked in context and explained – nothing is requested silently.
class _Permissions extends ConsumerStatefulWidget {
  const _Permissions({required this.onDone});
  final VoidCallback onDone;
  @override
  ConsumerState<_Permissions> createState() => _PermissionsState();
}

class _PermissionsState extends ConsumerState<_Permissions> with WidgetsBindingObserver {
  Map<String, bool> _ok = {};

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _check();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState s) {
    if (s == AppLifecycleState.resumed) _check();
  }

  Future<void> _check() async {
    final st = await ref.read(nativeProvider).status();
    final ok = {
      'notifications': await Permission.notification.isGranted,
      'microphone': await Permission.microphone.isGranted,
      'bluetooth': await Permission.bluetoothConnect.isGranted,
      'fullscreen': st['full_screen_allowed'] == true,
      'battery': await Permission.ignoreBatteryOptimizations.isGranted,
    };
    if (mounted) setState(() => _ok = ok);
  }

  Future<void> _grant(String k) async {
    final native = ref.read(nativeProvider);
    switch (k) {
      case 'notifications':
        if (!(await Permission.notification.request()).isGranted) await native.openSettings('notifications');
      case 'microphone':
        await Permission.microphone.request();
      case 'bluetooth':
        await Permission.bluetoothConnect.request();
      case 'fullscreen':
        await native.openSettings('fullscreen');
      case 'battery':
        await Permission.ignoreBatteryOptimizations.request();
    }
    await _check();
  }

  @override
  Widget build(BuildContext context) {
    const items = [
      ('notifications', 'Benachrichtigungen', 'Damit Jarvis klingeln und Sie informieren kann.'),
      ('microphone', 'Mikrofon', 'Für Gespräche und Diktat.'),
      ('bluetooth', 'Bluetooth', 'Headset und Auto als Audioausgabe auswählen.'),
      ('fullscreen', 'Anrufe auf dem Sperrbildschirm', 'Android 14+: eingehende Anrufe im Vollbild zeigen.'),
      ('battery', 'Akku-Optimierung aus', 'Optional: zuverlässigere Zustellung bei Herstellern mit aggressivem Energiesparen.'),
    ];
    return ListView(
      key: const ValueKey('perm'),
      padding: const EdgeInsets.fromLTRB(J.s5, J.s7, J.s5, J.s5),
      children: [
        Text('Damit Jarvis\nSie erreichen kann.', style: J.display),
        const SizedBox(height: J.s5),
        for (final (k, title, why) in items) ...[
          Padding(
            padding: const EdgeInsets.symmetric(vertical: J.s3),
            child: Row(children: [
              Container(
                width: 8,
                height: 8,
                decoration: BoxDecoration(shape: BoxShape.circle, color: _ok[k] == true ? J.green : J.lineBright),
              ),
              const SizedBox(width: J.s4),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(title, style: J.body),
                  Text(why, style: J.bodyDim.copyWith(fontSize: 13.5)),
                ]),
              ),
              if (_ok[k] != true) LightAction('Erlauben', onTap: () => _grant(k)) else Text('OK', style: J.mono(11, color: J.green)),
            ]),
          ),
          const Hairline(),
        ],
        const SizedBox(height: J.s6),
        Align(alignment: Alignment.centerRight, child: LightAction('Weiter', onTap: widget.onDone)),
      ],
    );
  }
}
