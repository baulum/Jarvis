import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api.dart';
import '../../core/theme.dart';
import '../../state/persona.dart';
import '../../state/providers.dart';
import 'hud.dart';

/// Settings › Persönlichkeit: Jarvis or René Redo (and later ones). Switches prompt and voice on the core without a
/// restart – desktop and phone show the same choice. The Hörprobe plays the persona's own voice, switching nothing.
class PersonaSettings extends ConsumerStatefulWidget {
  const PersonaSettings({super.key});
  @override
  ConsumerState<PersonaSettings> createState() => _PersonaSettingsState();
}

class _PersonaSettingsState extends ConsumerState<PersonaSettings> {
  String? _busy; // 'switch:<id>' | 'preview:<id>'
  String? _playing;
  String? _note;

  @override
  void dispose() {
    if (_playing != null) ref.read(nativeProvider).stopWav().ignore();
    super.dispose();
  }

  Future<void> _select(Persona p) async {
    if (_busy != null) return;
    setState(() {
      _busy = 'switch:${p.id}';
      _note = null;
    });
    try {
      await ref.read(personaProvider.notifier).select(p.id);
      _note = '${p.label} ist aktiv – ab der nächsten Antwort.';
    } on ApiError catch (e) {
      _note = e.message;
    }
    if (mounted) setState(() => _busy = null);
  }

  Future<void> _preview(Persona p) async {
    final native = ref.read(nativeProvider);
    if (_playing == p.id) {
      await native.stopWav();
      return;
    }
    if (_playing != null) await native.stopWav();
    setState(() {
      _busy = 'preview:${p.id}';
      _note = null;
    });
    try {
      final wav = await ref.read(apiProvider).personaPreview(p.id);
      if (!mounted) return;
      setState(() {
        _busy = null;
        _playing = p.id;
      });
      if (!await native.playWav(wav) && mounted) setState(() => _note = 'Hörprobe konnte nicht abgespielt werden.');
    } on ApiError catch (e) {
      _note = e.message;
    } on PlatformException catch (e) {
      _note = e.message ?? 'Hörprobe konnte nicht abgespielt werden.';
    }
    if (mounted) {
      setState(() {
        _busy = null;
        if (_playing == p.id) _playing = null;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(personaProvider);
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      const Caps('Persönlichkeit'),
      const SizedBox(height: J.s1),
      Text('Ton und Stimme, mit denen Jarvis antwortet. Werkzeuge, Erinnerungen und Berechtigungen bleiben gleich.',
          style: J.bodyDim.copyWith(fontSize: 13.5)),
      const SizedBox(height: J.s2),
      ...state.when(
        loading: () => [const SizedBox(height: J.s4)],
        error: (e, _) => [Text('$e', style: J.bodyDim)],
        data: (s) => [for (final p in s.personas) _row(p, selected: p.id == s.active)],
      ),
      if (_note != null) Text(_note!, style: J.mono(11.5, color: J.cyan)),
    ]);
  }

  Widget _row(Persona p, {required bool selected}) {
    final switching = _busy == 'switch:${p.id}';
    final loading = _busy == 'preview:${p.id}';
    final playing = _playing == p.id;
    return Container(
      constraints: const BoxConstraints(minHeight: J.hit),
      padding: const EdgeInsets.symmetric(vertical: J.s2),
      child: Row(children: [
        Expanded(
          child: Pressable(
            onTap: selected ? null : () => _select(p),
            semantics: '${p.label} wählen${selected ? ', aktiv' : ''}',
            child: Row(children: [
              Container(
                width: 10,
                height: 10,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: selected ? J.cyan : Colors.transparent,
                  border: Border.all(color: selected ? J.cyan : J.lineBright),
                ),
              ),
              const SizedBox(width: J.s3),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(p.label, style: J.body.copyWith(color: selected ? J.text : J.text2)),
                  if (p.description.isNotEmpty) Text(p.description, style: J.mono(10.5, color: J.text3)),
                  if (!p.voiceReady)
                    Text('Eigene Stimme noch nicht geladen – Systemstimme bis zum Neustart des Sprachservers',
                        style: J.mono(10.5, color: J.amber)),
                ]),
              ),
              if (switching) const Padding(padding: EdgeInsets.only(left: J.s2), child: _Spinner()),
            ]),
          ),
        ),
        Pressable(
          onTap: loading ? null : () => _preview(p),
          semantics: playing ? 'Hörprobe stoppen' : 'Hörprobe ${p.label}',
          child: SizedBox.square(
            dimension: J.hit,
            child: Center(
              child: loading
                  ? const _Spinner()
                  : Icon(playing ? Icons.stop_rounded : Icons.play_arrow_rounded, size: 22, color: J.cyan),
            ),
          ),
        ),
      ]),
    );
  }
}

class _Spinner extends StatelessWidget {
  const _Spinner();
  @override
  Widget build(BuildContext context) =>
      const SizedBox.square(dimension: 14, child: CircularProgressIndicator(strokeWidth: 1.5, color: J.cyan));
}
