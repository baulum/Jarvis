import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/api.dart';
import 'providers.dart';

/// One selectable personality (Jarvis, René Redo …) – mirrors `/api/persona`.
class Persona {
  const Persona({required this.id, required this.label, this.description = '', this.voiceReady = true});
  final String id;
  final String label;
  final String description;

  /// false: the core's local voice server has not loaded this voice yet (it speaks with the system voice meanwhile)
  final bool voiceReady;
}

class PersonaState {
  const PersonaState({this.active = 'jarvis', this.personas = const []});
  final String active;
  final List<Persona> personas;

  Persona? get current => personas.where((p) => p.id == active).firstOrNull;

  static PersonaState parse(Map<String, dynamic> m) => PersonaState(
        active: (m['active'] as String?) ?? 'jarvis',
        personas: [
          for (final raw in (m['personas'] as List? ?? const []))
            if (raw is Map)
              Persona(
                id: raw['id'] as String,
                label: (raw['label'] as String?) ?? raw['id'] as String,
                description: (raw['description'] as String?) ?? '',
                voiceReady: raw['voice_ready'] != false,
              ),
        ],
      );
}

/// Which personality speaks (prompt + voice). Shared with desktop and core: a switch anywhere arrives here as the
/// live event `persona.changed`; the choice itself is stored by the core.
class PersonaController extends AsyncNotifier<PersonaState> {
  @override
  Future<PersonaState> build() async {
    final live = ref.watch(liveSocketProvider);
    if (live != null) {
      final sub = live.events.listen((e) {
        if (e.type == 'persona.changed') unawaited(reload());
      });
      ref.onDispose(sub.cancel);
    }
    return _fetch();
  }

  Future<PersonaState> _fetch() async =>
      PersonaState.parse((await ref.read(apiProvider).get('/api/persona') as Map).cast<String, dynamic>());

  Future<void> reload() async {
    try {
      state = AsyncData(await _fetch());
    } on ApiError {
      // keep the last known value
    }
  }

  /// Switches prompt and voice on the core – from the next answer on, without a restart.
  Future<void> select(String id) async {
    final r = await ref.read(apiProvider).post('/api/persona', {'id': id}) as Map;
    state = AsyncData(PersonaState.parse(r.cast<String, dynamic>()));
  }
}

final personaProvider = AsyncNotifierProvider<PersonaController, PersonaState>(PersonaController.new);
