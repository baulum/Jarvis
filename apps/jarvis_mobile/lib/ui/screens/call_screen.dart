import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:permission_handler/permission_handler.dart';

import '../../core/theme.dart';
import '../../state/live_model.dart';
import '../../state/providers.dart';
import '../widgets/core_orb.dart';
import '../widgets/hud.dart';

/// Endpoint types from androidx.core.telecom.CallEndpointCompat.
const _earpiece = 1, _bluetooth = 2, _wired = 3, _speaker = 4;

/// Fixed slots of the call block (the Stopp slot is reserved so the layout does not jump when the button appears).
const _activitySlot = 34.0, _stopSlot = 52.0, _minOrb = 72.0;

class CallScreen extends ConsumerStatefulWidget {
  const CallScreen({super.key});
  @override
  ConsumerState<CallScreen> createState() => _CallScreenState();
}

class _CallScreenState extends ConsumerState<CallScreen> {
  late final _VoiceLevel _level = _VoiceLevel(ref.read(levelsProvider));
  Timer? _clock;
  bool _transcriptOpen = false;
  Timer? _leave;

  @override
  void initState() {
    super.initState();
    _clock = Timer.periodic(const Duration(seconds: 1), (_) => mounted ? setState(() {}) : null);
    Permission.microphone.request();
    Permission.bluetoothConnect.request();
  }

  @override
  void dispose() {
    _clock?.cancel();
    _leave?.cancel();
    _level.dispose();
    super.dispose();
  }

  String _status(CallInfo c) {
    switch (c.status) {
      case 'ringing':
        return 'Eingehender Anruf';
      case 'dialing':
      case 'connecting':
        return 'Verbinde …';
      case 'reconnecting':
        return 'Verbindung wird wiederhergestellt …';
      case 'on_hold':
        return 'Gehalten – anderes Gespräch aktiv';
      case 'active':
        return switch (c.agentState) {
          'speaking' => 'Spricht',
          'thinking' => 'Denkt nach',
          'tool' => 'Arbeitet',
          _ => 'Verbunden',
        };
      case 'ended':
        return switch (c.endReason) {
          'missed' => 'Verpasst',
          'declined' => 'Abgelehnt',
          'error' => 'Verbindung fehlgeschlagen',
          'answered_elsewhere' => 'Auf anderem Gerät angenommen',
          _ => 'Beendet',
        };
      default:
        return 'Kein Gespräch';
    }
  }

  String _elapsed(CallInfo c) {
    if (c.activeSince == null) return '';
    final d = DateTime.now().difference(c.activeSince!);
    return '${d.inMinutes.toString().padLeft(2, '0')}:${(d.inSeconds % 60).toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    final c = ref.watch(callProvider);
    final native = ref.read(nativeProvider);
    ref.listen(callProvider, (prev, next) {
      if (next.status == 'ended' && prev?.status != 'ended') {
        HapticFeedback.heavyImpact();
        _leave = Timer(const Duration(milliseconds: 1800), () {
          if (!mounted) return;
          context.canPop() ? context.pop() : context.go('/'); // opened from the lock screen → it is the root
        });
      }
      if (next.status == 'active' && prev?.status != 'active') HapticFeedback.mediumImpact();
    });
    final ringing = c.status == 'ringing';
    final ended = c.status == 'ended' || c.status == 'idle';
    final orb = (MediaQuery.sizeOf(context).width * 1.1).clamp(200.0, 480.0);
    final speaker = c.endpoint?['type'] == _speaker;
    // Jarvis is talking or working on an answer: the button silences him and drops the answer, the call stays open
    final interruptible = c.status == 'active' && const {'speaking', 'thinking', 'tool'}.contains(c.agentState);

    return Scaffold(
      backgroundColor: J.void_,
      body: SafeArea(
        child: Column(children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(J.s2, J.s2, J.s2, 0),
            child: Row(children: [
              Pressable(
                onTap: () => context.canPop() ? context.pop() : context.go('/'),
                semantics: 'Minimieren',
                child: const SizedBox.square(dimension: J.hit, child: Icon(Icons.keyboard_arrow_down_rounded, color: J.text2)),
              ),
              const Spacer(),
              if (c.endpoint != null) Text((c.endpoint!['name'] as String).toUpperCase(), style: J.caps),
              const SizedBox(width: J.s4),
            ]),
          ),
          Expanded(
            // The orb takes what is left after the fixed lines; if even that is not enough (small display, large system
            // font, open transcript) the block scrolls instead of overflowing.
            child: LayoutBuilder(builder: (context, box) => SingleChildScrollView(
              physics: const ClampingScrollPhysics(),
              child: ConstrainedBox(
                constraints: BoxConstraints(minHeight: box.maxHeight),
                child: Center(child: Column(mainAxisSize: MainAxisSize.min, children: [
              Hero(
                tag: 'core',
                child: CoreOrb(
                  mode: ended ? Mode.idle : Mode.calling,
                  level: _level,
                  size: math.min(orb, math.max(_minOrb, box.maxHeight - _chromeHeight(context, c))),
                  ringing: ringing,
                  warn: false,
                ),
              ),
              Text('J.A.R.V.I.S.', style: J.wordmark.copyWith(fontSize: 18, letterSpacing: 8)),
              const SizedBox(height: J.s3),
              AnimatedSwitcher(
                duration: J.base,
                child: Text(_status(c), key: ValueKey(_status(c)), style: J.mono(13, color: c.status == 'ended' ? J.text2 : J.cyan)),
              ),
              const SizedBox(height: J.s2),
              Text(_elapsed(c), style: J.mono(13, color: J.text3)),
              const SizedBox(height: J.s4),
              SizedBox(
                height: _activitySlot,
                child: AnimatedSwitcher(
                  duration: J.base,
                  child: ringing && c.subject.isNotEmpty
                      ? Padding(
                          key: const ValueKey('subject'),
                          padding: const EdgeInsets.symmetric(horizontal: J.s6),
                          child: Text(c.subject, textAlign: TextAlign.center, maxLines: 2, style: J.bodyDim),
                        )
                      : c.activity != null
                          ? _ActivityChip(text: c.activity!, key: ValueKey(c.activity))
                          : const SizedBox.shrink(),
                ),
              ),
              SizedBox(
                height: _stopSlot,
                child: AnimatedSwitcher(
                  duration: J.base,
                  child: interruptible
                      ? _StopChip(key: const ValueKey('stop'), onTap: () => _interrupt(c))
                      : const SizedBox.shrink(),
                ),
              ),
              if (c.error != null && ended)
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: J.s6, vertical: J.s2),
                  child: Text(c.error!, textAlign: TextAlign.center, style: J.mono(12, color: J.amber)),
                ),
            ]))))),
          ),
          if (!ringing && c.transcript.isNotEmpty) _Transcript(lines: c.transcript, open: _transcriptOpen, onToggle: () => setState(() => _transcriptOpen = !_transcriptOpen)),
          Padding(
            padding: const EdgeInsets.fromLTRB(J.s5, J.s4, J.s5, J.s6),
            child: ringing
                ? Row(mainAxisAlignment: MainAxisAlignment.spaceEvenly, children: [
                    RingButton(size: 72, fill: J.red, label: 'Ablehnen', onTap: native.decline, child: const Icon(Icons.call_end_rounded)),
                    RingButton(
                      size: 72,
                      fill: J.green,
                      label: 'Annehmen',
                      onTap: () async {
                        await Permission.microphone.request();
                        native.answer();
                      },
                      child: const Icon(Icons.call_rounded),
                    ),
                  ])
                : Row(children: [
                  // equal slots: the labels shrink with the slot instead of pushing the row out of the screen
                  for (final button in <Widget>[
                    RingButton(
                      active: c.muted,
                      label: c.muted ? 'Stumm' : 'Mikrofon',
                      onTap: ended ? null : () => native.setMuted(!c.muted),
                      child: Icon(c.muted ? Icons.mic_off_rounded : Icons.mic_none_rounded),
                    ),
                    RingButton(
                      active: speaker,
                      label: 'Lautsprecher',
                      onTap: ended ? null : () => _toggleSpeaker(c),
                      child: const Icon(Icons.volume_up_outlined),
                    ),
                    RingButton(
                      label: 'Audio',
                      onTap: ended || c.endpoints.isEmpty ? null : () => _routes(c),
                      child: Icon(c.endpoint?['type'] == _bluetooth ? Icons.bluetooth_audio_rounded : Icons.headset_mic_outlined),
                    ),
                    RingButton(
                      fill: ended ? null : J.red,
                      label: 'Auflegen',
                      onTap: ended ? null : native.hangup,
                      child: const Icon(Icons.call_end_rounded),
                    ),
                  ])
                    Expanded(child: Center(child: button)),
                ]),
          ),
        ]),
      ),
    );
  }

  /// Height of everything under the orb: wordmark, status, clock, activity line, Stopp slot (+ error line), gaps.
  /// Text sizes follow the system font scale, so it is measured with the current scaler.
  double _chromeHeight(BuildContext context, CallInfo c) {
    final scaler = MediaQuery.textScalerOf(context);
    final text = scaler.scale(18) + 2 * scaler.scale(13 * 1.35); // wordmark, status, clock
    final error = c.error != null && (c.status == 'ended' || c.status == 'idle') ? scaler.scale(12 * 1.35) * 2 + J.s2 * 2 : 0;
    return text + J.s3 + J.s2 + J.s4 + _activitySlot + _stopSlot + error;
  }

  /// Stopp: the core silences Jarvis (synthesis, audio queued towards the phone) and cancels the running answer.
  /// Speaking over him does the same; this is for noisy places and for when he rambles on.
  Future<void> _interrupt(CallInfo c) async {
    final id = c.callId;
    if (id == null) return;
    HapticFeedback.mediumImpact();
    try {
      await ref.read(apiProvider).post('/api/mobile/calls/$id/interrupt');
    } catch (_) {} // the call goes on; if the request failed he simply keeps talking and Sir can tap again
  }

  void _toggleSpeaker(CallInfo c) {
    final native = ref.read(nativeProvider);
    final isSpeaker = c.endpoint?['type'] == _speaker;
    Map<String, dynamic>? target;
    if (isSpeaker) {
      target = c.endpoints.firstWhere((e) => e['type'] == _bluetooth || e['type'] == _wired,
          orElse: () => c.endpoints.firstWhere((e) => e['type'] == _earpiece, orElse: () => const {}));
    } else {
      target = c.endpoints.firstWhere((e) => e['type'] == _speaker, orElse: () => const {});
    }
    if (target.isNotEmpty) native.setEndpoint(target['id'] as String);
  }

  void _routes(CallInfo c) {
    showHudSheet(context, builder: (ctx) => Padding(
          padding: const EdgeInsets.fromLTRB(J.s5, J.s5, J.s5, J.s5),
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Caps('Audioausgabe'),
            const SizedBox(height: J.s3),
            for (final e in c.endpoints)
              Pressable(
                onTap: () {
                  ref.read(nativeProvider).setEndpoint(e['id'] as String);
                  Navigator.pop(ctx);
                },
                child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: J.s3),
                  child: Row(children: [
                    Icon(
                      switch (e['type']) {
                        _bluetooth => Icons.bluetooth_audio_rounded,
                        _speaker => Icons.volume_up_outlined,
                        _wired => Icons.headphones_outlined,
                        _ => Icons.phone_in_talk_outlined,
                      },
                      color: e['id'] == c.endpoint?['id'] ? J.cyan : J.text2,
                    ),
                    const SizedBox(width: J.s4),
                    Expanded(child: Text(e['name'] as String, style: J.body.copyWith(color: e['id'] == c.endpoint?['id'] ? J.cyan : J.text))),
                  ]),
                ),
              ),
          ]),
        ));
  }
}

/// Orb level during a call: Jarvis' voice drives it fully, Sir's voice faintly (so he sees he's heard).
class _VoiceLevel extends ChangeNotifier implements ValueListenable<double> {
  _VoiceLevel(this.levels) {
    levels.addListener(notifyListeners);
  }
  final Levels levels;
  @override
  double get value => math.max(levels.value.out * 1.6, levels.value.inp * 0.7).clamp(0, 1);
  @override
  void dispose() {
    levels.removeListener(notifyListeners);
    super.dispose();
  }
}

class _ActivityChip extends StatelessWidget {
  const _ActivityChip({super.key, required this.text});
  final String text;
  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: J.s4, vertical: J.s2),
        decoration: BoxDecoration(borderRadius: BorderRadius.circular(18), border: Border.all(color: J.cyan.withValues(alpha: 0.35))),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          const SizedBox.square(dimension: 9, child: CircularProgressIndicator(strokeWidth: 1.2, color: J.cyan)),
          const SizedBox(width: J.s2),
          Text(text, style: J.mono(12, color: J.cyan)),
        ]),
      );
}

class _StopChip extends StatelessWidget {
  const _StopChip({super.key, required this.onTap});
  final VoidCallback onTap;
  @override
  Widget build(BuildContext context) => Pressable(
        onTap: onTap,
        semantics: 'Jarvis unterbrechen',
        child: Container(
          constraints: const BoxConstraints(minHeight: J.hit),
          padding: const EdgeInsets.symmetric(horizontal: J.s5),
          decoration: BoxDecoration(borderRadius: BorderRadius.circular(24), border: Border.all(color: J.amber.withValues(alpha: 0.6))),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            const Icon(Icons.stop_rounded, size: 18, color: J.amber),
            const SizedBox(width: J.s2),
            Text('STOPP', style: J.mono(12, color: J.amber)),
          ]),
        ),
      );
}

class _Transcript extends StatelessWidget {
  const _Transcript({required this.lines, required this.open, required this.onToggle});
  final List<CallTranscript> lines;
  final bool open;
  final VoidCallback onToggle;
  @override
  Widget build(BuildContext context) {
    final shown = open ? lines : lines.sublist(math.max(0, lines.length - 1));
    return Pressable(
      onTap: onToggle,
      haptic: false,
      semantics: open ? 'Transkript einklappen' : 'Transkript ausklappen',
      child: AnimatedSize(
        duration: J.base,
        curve: J.enter,
        child: Container(
          constraints: BoxConstraints(maxHeight: open ? MediaQuery.sizeOf(context).height * 0.35 : 64),
          margin: const EdgeInsets.symmetric(horizontal: J.s5),
          padding: const EdgeInsets.symmetric(vertical: J.s2),
          decoration: const BoxDecoration(border: Border(top: BorderSide(color: J.line))),
          child: ListView(reverse: open, shrinkWrap: true, children: [
            for (final l in (open ? shown.reversed : shown))
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 3),
                child: Text.rich(TextSpan(children: [
                  TextSpan(text: l.role == 'user' ? 'SIE  ' : 'JARVIS  ', style: J.mono(10.5, color: l.role == 'user' ? J.text3 : J.cyan)),
                  TextSpan(text: l.text, style: J.bodyDim.copyWith(fontSize: 14)),
                ]), maxLines: open ? null : 2, overflow: open ? null : TextOverflow.ellipsis),
              ),
          ]),
        ),
      ),
    );
  }
}
