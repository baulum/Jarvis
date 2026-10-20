import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/slash.dart';
import 'providers.dart';

/// Slash commands from the core (`GET /api/commands`) – the same list the desktop shows.
/// Re-fetched when the connection to the core comes back, so the first "/" after being offline still works.
final commandsProvider = FutureProvider<List<SlashCommand>>((ref) async {
  ref.watch(linkProvider);
  final res = await ref.read(apiProvider).get('/api/commands') as Map;
  return [
    for (final c in (res['commands'] as List).cast<Map>()) SlashCommand.fromJson(c.cast<String, dynamic>()),
  ];
});
