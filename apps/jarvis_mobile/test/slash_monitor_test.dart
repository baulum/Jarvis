import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/core/monitor.dart';
import 'package:jarvis_mobile/core/slash.dart';

const _cmds = [
  SlashCommand(name: 'neu', description: 'Neues Gespräch', usage: '/neu', action: 'new'),
  SlashCommand(name: 'modell', description: 'Modell wählen', usage: '/modell auto|haiku|sonnet|opus', action: 'model', choices: ['auto', 'haiku', 'sonnet', 'opus']),
  SlashCommand(name: 'merke', description: 'Etwas merken', usage: '/merke <Text>', action: 'prompt', prompt: 'Merke dir: {args}'),
  SlashCommand(name: 'status', description: 'Systemstatus', usage: '/status', action: 'prompt', prompt: 'Status?'),
];

void main() {
  group('slash suggestions (same behaviour as the desktop)', () {
    test('every command for a bare slash, none for normal text', () {
      expect(slashItems('/', _cmds).map((i) => i.command.name), ['neu', 'modell', 'merke', 'status']);
      expect(slashItems('hallo', _cmds), isEmpty);
      expect(slashItems('', _cmds), isEmpty);
      expect(slashItems('/neu\nzweite Zeile', _cmds), isEmpty);
    });

    test('prefix matches first; descriptions only count from 3 letters', () {
      expect(slashItems('/m', _cmds).map((i) => i.command.name), ['modell', 'merke']);
      expect(slashItems('/gespr', _cmds).map((i) => i.command.name), ['neu']);
      expect(slashItems('/xyz', _cmds), isEmpty);
    });

    test('commands that need arguments are inserted with a trailing space; others run directly', () {
      final by = {for (final i in slashItems('/', _cmds)) i.command.name: i};
      expect((by['neu']!.runs, by['neu']!.insert), (true, '/neu'));
      expect((by['modell']!.runs, by['modell']!.insert), (false, '/modell '));
      expect((by['merke']!.runs, by['merke']!.insert), (false, '/merke '));
    });

    test('choices of a command are the second step', () {
      final items = slashItems('/modell o', _cmds);
      expect(items.map((i) => i.arg), ['opus']);
      expect((items.single.runs, items.single.insert), (true, '/modell opus'));
      expect(slashItems('/modell ', _cmds).length, 4);
      expect(slashItems('/modell opus', _cmds), isEmpty);
    });

    test('parsing and prompt templates', () {
      final p = parseSlash('/merke  Milch kaufen ', _cmds)!;
      expect((p.command.name, p.args), ('merke', 'Milch kaufen'));
      expect(parseSlash('/etc/hosts lesen', _cmds), isNull);
      expect(promptFor(_cmds[2], 'Milch').text, 'Merke dir: Milch');
      expect(promptFor(_cmds[2], '').error, 'Bitte ergänzen: /merke <Text>');
      expect(promptFor(_cmds[3], '').text, 'Status?');
    });

    test('reads the command payload of the core', () {
      final c = SlashCommand.fromJson({'name': 'modell', 'description': 'd', 'usage': '/modell auto|opus', 'action': 'model', 'choices': ['auto', 'opus'], 'confirm': false});
      // records compare fields with ==, which is identity for lists, so the choices are matched on their own
      expect(c.choices, ['auto', 'opus']);
      expect((c.takesArgs, c.confirm), (true, false));
    });
  });

  group('monitor model', () {
    final json = {
      'job_id': 'j1',
      'title': 'Build',
      'status': 'running',
      'elapsed_s': 61.0,
      'current': {'kind': 'tool', 'label': 'Bash: pytest -q'},
      'steps': [
        {'id': 'a', 'tool': 'Read', 'detail': 'x.py', 'status': 'ok', 'duration_s': 1.5, 'result': '12 Zeilen'},
        {'id': 'b', 'tool': 'Bash', 'detail': 'pytest -q', 'status': 'running'},
      ],
      'step_count': 2,
      'running_tools': [{}],
      'usage': {'input_tokens': 1200, 'output_tokens': 800, 'cost_usd': 0.1234, 'cost_final': false},
      'can_pause': true,
      'can_cancel': true,
      'can_message': true,
    };

    test('parses the snapshot', () {
      final t0 = DateTime(2026, 1, 1, 12);
      final m = JobMonitor.fromJson(json, now: t0);
      expect((m.currentKind, m.currentLabel, m.stepCount), ('tool', 'Bash: pytest -q', 2));
      expect(m.steps.map((s) => s.status), ['ok', 'running']);
      expect(m.steps.last.live, true);
      expect((m.usage.io, m.usage.costUsd, m.usage.costFinal), (2000, 0.1234, false));
      expect((m.canPause, m.canResume, m.canCancel, m.canMessage), (true, false, true, true));
    });

    test('only a running job keeps counting', () {
      final t0 = DateTime(2026, 1, 1, 12);
      final running = JobMonitor.fromJson(json, now: t0);
      expect(running.elapsedAt(t0.add(const Duration(seconds: 9))), 70.0);
      final paused = JobMonitor.fromJson({...json, 'status': 'paused'}, now: t0);
      expect(paused.elapsedAt(t0.add(const Duration(seconds: 9))), 61.0);
    });

    test('formatters', () {
      expect(formatDuration(3725), '1 h 2 min');
      expect(formatDuration(42), '42 s');
      expect(formatDuration(125), '2 min 5 s');
      expect(formatTokens(950), '950');
      expect(formatTokens(12345), '12,3 Tsd.');
      expect(formatTokens(2400000), '2,4 Mio.');
      expect(formatUsd(0.1234), '0,123 \$');
      expect(formatUsd(null), '–');
    });
  });
}
