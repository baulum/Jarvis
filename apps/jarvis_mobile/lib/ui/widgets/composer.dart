import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:path_provider/path_provider.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:record/record.dart';

import '../../core/api.dart';
import '../../core/theme.dart';
import '../../state/providers.dart';
import 'hud.dart';

/// Floating input: text, hold-to-dictate (Whisper on the Mac), and the call button – always one tap away.
class Composer extends ConsumerStatefulWidget {
  const Composer({super.key, required this.onSend, this.busy = false, this.onStop, this.autofocus = false});
  final ValueChanged<String> onSend;
  final bool busy;
  final VoidCallback? onStop;
  final bool autofocus;
  @override
  ConsumerState<Composer> createState() => _ComposerState();
}

class _ComposerState extends ConsumerState<Composer> {
  final _text = TextEditingController();
  final _rec = AudioRecorder();
  StreamSubscription<Amplitude>? _amp;
  bool _recording = false;
  bool _transcribing = false;
  String? _hint;

  @override
  void dispose() {
    _amp?.cancel();
    _rec.dispose();
    _text.dispose();
    super.dispose();
  }

  void _send() {
    final t = _text.text.trim();
    if (t.isEmpty) return;
    HapticFeedback.lightImpact();
    _text.clear();
    widget.onSend(t);
  }

  Future<void> _startDictation() async {
    if (!await Permission.microphone.request().isGranted) {
      setState(() => _hint = 'Mikrofon nicht erlaubt');
      return;
    }
    final dir = await getTemporaryDirectory();
    await _rec.start(const RecordConfig(encoder: AudioEncoder.wav, sampleRate: 16000, numChannels: 1, echoCancel: true, noiseSuppress: true),
        path: '${dir.path}/dictation.wav');
    HapticFeedback.mediumImpact();
    final levels = ref.read(levelsProvider);
    _amp = _rec.onAmplitudeChanged(const Duration(milliseconds: 50)).listen((a) {
      final v = ((a.current + 50) / 50).clamp(0.0, 1.0); // dBFS → 0..1
      levels.value = (out: levels.value.out, inp: v);
    });
    setState(() {
      _recording = true;
      _hint = null;
    });
  }

  Future<void> _stopDictation() async {
    if (!_recording) return;
    final path = await _rec.stop();
    await _amp?.cancel();
    final levels = ref.read(levelsProvider);
    levels.value = (out: levels.value.out, inp: 0);
    setState(() {
      _recording = false;
      _transcribing = true;
    });
    try {
      final bytes = await File(path!).readAsBytes();
      if (bytes.length < 16000) {
        setState(() => _hint = 'Zu kurz – gedrückt halten und sprechen');
        return;
      }
      final text = (await ref.read(apiProvider).transcribe(bytes)).trim();
      if (text.isEmpty) {
        setState(() => _hint = 'Nichts verstanden');
      } else {
        widget.onSend(text);
      }
    } on ApiError catch (e) {
      setState(() => _hint = e.offline ? 'Jarvis ist nicht erreichbar' : e.message);
    } finally {
      if (mounted) setState(() => _transcribing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final offline = ref.watch(linkProvider).valueOrNull?.name == 'offline';
    return Column(mainAxisSize: MainAxisSize.min, children: [
      AnimatedSize(
        duration: J.base,
        curve: J.enter,
        child: _hint == null && !_recording && !_transcribing
            ? const SizedBox(width: double.infinity)
            : Padding(
                padding: const EdgeInsets.only(bottom: J.s2),
                child: Text(
                  _recording ? 'Ich höre, Sir … loslassen zum Senden' : _transcribing ? 'Transkribiere …' : _hint!,
                  style: J.mono(11.5, color: _hint != null ? J.amber : J.cyan),
                ),
              ),
      ),
      Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
        Expanded(
          child: AnimatedContainer(
            duration: J.base,
            constraints: const BoxConstraints(minHeight: 52),
            padding: const EdgeInsets.only(left: J.s4, right: J.s1),
            decoration: BoxDecoration(
              color: J.panel.withValues(alpha: 0.92),
              borderRadius: BorderRadius.circular(26),
              border: Border.all(color: _recording ? J.cyan : J.lineBright),
              boxShadow: [BoxShadow(color: Colors.black.withValues(alpha: 0.6), blurRadius: 30, offset: const Offset(0, 10))],
            ),
            child: Row(crossAxisAlignment: CrossAxisAlignment.center, children: [
              Expanded(
                child: TextField(
                  controller: _text,
                  autofocus: widget.autofocus,
                  minLines: 1,
                  maxLines: 5,
                  enabled: !offline,
                  style: J.body,
                  textInputAction: TextInputAction.send,
                  onSubmitted: (_) => _send(),
                  onChanged: (v) {
                    if (v.endsWith('\n')) {
                      _text.text = v.trimRight(); // hardware keyboard Enter = send
                      _send();
                    }
                    setState(() {});
                  },
                  decoration: InputDecoration(
                    isCollapsed: true,
                    border: InputBorder.none,
                    hintText: offline ? 'Jarvis ist nicht erreichbar' : 'Nachricht an Jarvis',
                    hintStyle: J.bodyDim.copyWith(color: J.text3),
                  ),
                ),
              ),
              if (widget.busy && widget.onStop != null)
                _IconTap(icon: Icons.stop_rounded, label: 'Antwort abbrechen', onTap: widget.onStop)
              else if (_text.text.trim().isNotEmpty)
                _IconTap(icon: Icons.arrow_upward_rounded, label: 'Senden', onTap: _send, color: J.cyan)
              else
                GestureDetector(
                  onLongPressStart: offline ? null : (_) => _startDictation(),
                  onLongPressEnd: (_) => _stopDictation(),
                  onTap: offline ? null : () => setState(() => _hint = 'Zum Diktieren gedrückt halten'),
                  child: Semantics(
                    button: true,
                    label: 'Diktieren (gedrückt halten)',
                    child: SizedBox.square(
                      dimension: J.hit,
                      child: Icon(_recording ? Icons.graphic_eq_rounded : Icons.mic_none_rounded,
                          color: _recording ? J.cyan : J.text2, size: 22),
                    ),
                  ),
                ),
            ]),
          ),
        ),
        const SizedBox(width: J.s3),
        const CallButton(),
      ]),
    ]);
  }
}

class _IconTap extends StatelessWidget {
  const _IconTap({required this.icon, required this.label, this.onTap, this.color = J.text2});
  final IconData icon;
  final String label;
  final VoidCallback? onTap;
  final Color color;
  @override
  Widget build(BuildContext context) => Pressable(
        onTap: onTap,
        semantics: label,
        child: SizedBox.square(dimension: J.hit, child: Icon(icon, color: color, size: 22)),
      );
}

/// Starts a real call to Jarvis – only when the core says it can actually answer.
class CallButton extends ConsumerWidget {
  const CallButton({super.key, this.size = 52});
  final double size;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final call = ref.watch(callProvider);
    final av = ref.watch(availabilityProvider);
    final can = av.valueOrNull?['can_call_jarvis'] == true;
    return RingButton(
      size: size,
      semantics: call.live ? 'Zum Anruf' : 'Jarvis anrufen',
      fill: call.live ? J.green : (can ? J.cyan : null),
      onTap: () async {
        if (call.live) {
          context.push('/call');
          return;
        }
        final fresh = await ref.refresh(availabilityProvider.future).catchError((_) => <String, dynamic>{});
        if (!context.mounted) return;
        if (fresh['can_call_jarvis'] == true) {
          HapticFeedback.mediumImpact();
          await Permission.microphone.request();
          await ref.read(nativeProvider).startCall();
          if (context.mounted) context.push('/call');
        } else {
          final problems = ((fresh['problems'] as List?) ?? const ['Jarvis ist nicht erreichbar']).cast<String>();
          showHudSheet(context, builder: (_) => _Unavailable(problems: fresh['busy'] == true ? ['Es läuft bereits ein Anruf'] : problems));
        }
      },
      child: Icon(call.live ? Icons.call_rounded : Icons.call_outlined),
    );
  }
}

class _Unavailable extends StatelessWidget {
  const _Unavailable({required this.problems});
  final List<String> problems;
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(J.s5, J.s5, J.s5, J.s6),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
          const Caps('Anruf nicht möglich'),
          const SizedBox(height: J.s3),
          Text('Jarvis kann gerade kein Gespräch annehmen.', style: J.title),
          const SizedBox(height: J.s4),
          for (final p in problems)
            Padding(
              padding: const EdgeInsets.only(bottom: J.s2),
              child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text('—  ', style: J.mono(13, color: J.amber)),
                Expanded(child: Text(p, style: J.bodyDim)),
              ]),
            ),
        ]),
      );
}
