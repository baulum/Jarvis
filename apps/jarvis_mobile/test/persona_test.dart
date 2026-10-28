import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/state/persona.dart';

void main() {
  test('personas parse the core payload (/api/persona and persona.changed)', () {
    final s = PersonaState.parse({
      'active': 'rene_redo',
      'personas': [
        {'id': 'jarvis', 'label': 'J.A.R.V.I.S.', 'description': 'Butler', 'voice': 'jarvis', 'voice_ready': true},
        {'id': 'rene_redo', 'label': 'René Redo', 'description': 'Kumpel', 'voice': 'rene_redo', 'voice_ready': false},
      ],
    });
    expect(s.active, 'rene_redo');
    expect(s.personas.map((p) => p.id), ['jarvis', 'rene_redo']);
    expect(s.current?.label, 'René Redo');
    expect(s.current?.voiceReady, isFalse);
    expect(s.personas.first.voiceReady, isTrue);
  });

  test('an older core without personas means Jarvis', () {
    final s = PersonaState.parse({});
    expect(s.active, 'jarvis');
    expect(s.personas, isEmpty);
    expect(s.current, isNull);
  });
}
