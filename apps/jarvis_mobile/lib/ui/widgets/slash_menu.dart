import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/api.dart';
import '../../core/slash.dart';
import '../../core/theme.dart';
import '../../state/agent.dart';
import '../../state/providers.dart';
import 'confirm_sheet.dart';
import 'hud.dart';

/// Suggestion list above the input while a "/" command is being typed: name, short description, tap to pick.
class SlashMenu extends StatelessWidget {
  const SlashMenu({super.key, required this.items, required this.onPick});
  final List<SlashItem> items;
  final ValueChanged<SlashItem> onPick;

  @override
  Widget build(BuildContext context) {
    final maxH = MediaQuery.sizeOf(context).height * 0.32;
    return Container(
      margin: const EdgeInsets.only(bottom: J.s2),
      constraints: BoxConstraints(maxHeight: maxH),
      decoration: BoxDecoration(
        color: J.panel.withValues(alpha: 0.96),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: J.lineBright),
        boxShadow: [BoxShadow(color: Colors.black.withValues(alpha: 0.6), blurRadius: 24, offset: const Offset(0, 8))],
      ),
      clipBehavior: Clip.antiAlias,
      child: ListView.separated(
        shrinkWrap: true,
        padding: EdgeInsets.zero,
        keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.manual,
        itemCount: items.length,
        separatorBuilder: (_, _) => const Hairline(),
        itemBuilder: (_, i) {
          final it = items[i];
          return Pressable(
            onTap: () => onPick(it),
            semantics: '${it.label}: ${it.description}',
            child: Container(
              constraints: const BoxConstraints(minHeight: J.hit),
              padding: const EdgeInsets.symmetric(horizontal: J.s4, vertical: J.s2),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
                Text(it.label, style: J.mono(13.5, color: J.cyan, spacing: 0.2)),
                const SizedBox(height: 2),
                Text(it.description, style: J.bodyDim.copyWith(fontSize: 13.5), maxLines: 2, overflow: TextOverflow.ellipsis),
              ]),
            ),
          );
        },
      ),
    );
  }
}

const _routes = {
  'agents': '/activity',
  'tasks': '/activity',
  'approvals': '/activity',
  'memory': '/memory',
  'settings': '/settings',
};

void _toast(BuildContext context, String msg) {
  final m = ScaffoldMessenger.maybeOf(context);
  m?.hideCurrentSnackBar();
  m?.showSnackBar(SnackBar(content: Text(msg), duration: const Duration(seconds: 3)));
}

/// Executes a command the way the desktop does (same `action` vocabulary, defined by the core).
Future<void> runSlashCommand(
  BuildContext context,
  WidgetRef ref,
  SlashCommand c,
  String args, {
  required ValueChanged<String> onSend,
  required VoidCallback onNew,
  required VoidCallback onStop,
  String? sessionId,
}) async {
  switch (c.action) {
    case 'prompt':
      final p = promptFor(c, args);
      if (p.error != null) {
        _toast(context, p.error!);
      } else {
        onSend(p.text!);
      }
    case 'model':
      final choice = args.toLowerCase();
      if (!c.choices.contains(choice)) {
        _toast(context, 'Modell wählen: ${c.usage}');
        return;
      }
      try {
        await ref.read(agentInfoProvider.notifier).setModel(
              choice == 'auto' ? null : choice,
              scope: sessionId == null ? 'default' : 'session',
              session: sessionId,
            );
        if (context.mounted) _toast(context, choice == 'auto' ? 'Automatische Modellwahl' : 'Modell: $choice');
      } on ApiError catch (e) {
        if (context.mounted) _toast(context, e.message);
      }
    case 'new':
      onNew();
    case 'stop':
      onStop();
    case 'navigate':
      final route = _routes[c.target];
      if (route != null) context.push(route);
    case 'killswitch':
      final ok = await confirmAction(
        context,
        title: 'Not-Aus auslösen?',
        body: 'Alle laufenden Aktionen und Agenten werden gestoppt, bis der Not-Aus am Mac wieder aufgehoben wird.',
        confirmLabel: 'Not-Aus',
        danger: true,
      );
      if (!ok) return;
      try {
        await ref.read(apiProvider).post('/api/system/killswitch', {'engaged': true, 'reason': 'Not-Aus über Slash-Befehl (Handy)'});
        if (context.mounted) _toast(context, 'Not-Aus ausgelöst');
      } on ApiError catch (e) {
        if (context.mounted) _toast(context, e.message);
      }
  }
}
