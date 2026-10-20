// Slash commands. The list comes from the core (`GET /api/commands`) – the same source the desktop uses – so
// both stay in sync; this file only does matching and the pure parts of execution (mirrors desktop/src/lib/slash.ts).

class SlashCommand {
  const SlashCommand({
    required this.name,
    required this.description,
    required this.usage,
    required this.action,
    this.category,
    this.prompt,
    this.target,
    this.choices = const [],
    this.confirm = false,
  });

  factory SlashCommand.fromJson(Map<String, dynamic> j) => SlashCommand(
        name: j['name'] as String,
        description: (j['description'] as String?) ?? '',
        usage: (j['usage'] as String?) ?? '/${j['name']}',
        action: (j['action'] as String?) ?? 'prompt',
        category: j['category'] as String?,
        prompt: j['prompt'] as String?,
        target: j['target'] as String?,
        choices: ((j['choices'] as List?) ?? const []).map((e) => e.toString()).toList(),
        confirm: j['confirm'] == true,
      );

  final String name;
  final String description;
  final String usage;

  /// prompt | model | new | stop | navigate | killswitch
  final String action;
  final String? category;
  final String? prompt;
  final String? target;
  final List<String> choices;
  final bool confirm;

  bool get takesArgs => choices.isNotEmpty || usage.contains('<') || (prompt?.contains('{args}') ?? false);
}

/// One row of the suggestion list.
class SlashItem {
  const SlashItem({required this.insert, required this.label, required this.description, required this.command, this.arg, required this.runs});

  /// text put into the input when the row is picked
  final String insert;
  final String label;
  final String description;
  final SlashCommand command;

  /// a choice of the command's argument list (second step)
  final String? arg;

  /// picking runs the command right away (nothing more to type)
  final bool runs;
}

final _first = RegExp(r'^/(\S*)$');
final _second = RegExp(r'^/(\S+)\s+(\S*)$');

/// Suggestions for the current input; empty when the input is not a command being typed.
List<SlashItem> slashItems(String draft, List<SlashCommand> commands) {
  if (!draft.startsWith('/') || draft.contains('\n')) return const [];
  final second = _second.firstMatch(draft);
  if (second != null) {
    final name = second.group(1)!.toLowerCase();
    final prefix = second.group(2)!.toLowerCase();
    final cmd = commands.where((c) => c.name == name).firstOrNull;
    if (cmd == null || cmd.choices.isEmpty) return const [];
    return [
      for (final ch in cmd.choices)
        if (ch.startsWith(prefix) && ch != prefix)
          SlashItem(insert: '/${cmd.name} $ch', label: '/${cmd.name} $ch', description: cmd.description, command: cmd, arg: ch, runs: true),
    ];
  }
  final first = _first.firstMatch(draft);
  if (first == null) return const [];
  final q = first.group(1)!.toLowerCase();
  int score(SlashCommand c) {
    if (c.name.startsWith(q)) return 0;
    if (c.name.contains(q)) return 1;
    if (q.length >= 3 && c.description.toLowerCase().contains(q)) return 2;
    return 9;
  }

  final hits = commands.where((c) => score(c) < 9).toList();
  // stable: equal scores keep the order the core sent
  final order = {for (var i = 0; i < hits.length; i++) hits[i]: i};
  hits.sort((a, b) {
    final d = score(a) - score(b);
    return d != 0 ? d : order[a]! - order[b]!;
  });
  return [
    for (final c in hits)
      SlashItem(insert: c.takesArgs ? '/${c.name} ' : '/${c.name}', label: c.usage, description: c.description, command: c, runs: !c.takesArgs),
  ];
}

/// `/name args` -> command + args, or null if the text is not a known command.
({SlashCommand command, String args})? parseSlash(String text, List<SlashCommand> commands) {
  final m = RegExp(r'^/(\S+)(?:\s+([\s\S]*))?$').firstMatch(text.trim());
  if (m == null) return null;
  final name = m.group(1)!.toLowerCase();
  final cmd = commands.where((c) => c.name == name).firstOrNull;
  if (cmd == null) return null;
  return (command: cmd, args: (m.group(2) ?? '').trim());
}

/// Chat text of a `prompt` command, or an error message when the required argument is missing.
({String? text, String? error}) promptFor(SlashCommand command, String args) {
  final tpl = command.prompt ?? '';
  if (tpl.contains('{args}') && args.isEmpty) return (text: null, error: 'Bitte ergänzen: ${command.usage}');
  return (text: tpl.replaceAll('{args}', args), error: null);
}
