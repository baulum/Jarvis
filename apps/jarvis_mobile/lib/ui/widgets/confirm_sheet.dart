import 'package:flutter/material.dart';

import '../../core/theme.dart';
import 'hud.dart';

/// Asks before a disruptive action (pause, cancel, emergency stop). Resolves to true only on an explicit
/// confirmation; dismissing the sheet or tapping "Zurück" counts as no.
Future<bool> confirmAction(
  BuildContext context, {
  required String title,
  required String body,
  required String confirmLabel,
  bool danger = false,
}) async {
  final res = await showHudSheet<bool>(
    context,
    builder: (ctx) => Padding(
      padding: const EdgeInsets.fromLTRB(J.s5, J.s5, J.s5, J.s5),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
        Caps(danger ? 'Bestätigung · endgültig' : 'Bestätigung', color: danger ? J.red : J.amber),
        const SizedBox(height: J.s3),
        Text(title, style: J.title),
        const SizedBox(height: J.s3),
        Text(body, style: J.bodyDim),
        const SizedBox(height: J.s5),
        Row(children: [
          Expanded(child: LightAction('Zurück', color: J.text2, onTap: () => Navigator.pop(ctx, false))),
          Expanded(
            child: Align(
              alignment: Alignment.centerRight,
              child: LightAction(confirmLabel, color: danger ? J.red : J.amber, onTap: () => Navigator.pop(ctx, true)),
            ),
          ),
        ]),
      ]),
    ),
  );
  return res ?? false;
}
