import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/core/theme.dart';
import 'package:jarvis_mobile/ui/screens/call_screen.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const native = MethodChannel('jarvis/native');
  final calls = <String>[];

  setUp(() {
    calls.clear();
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(native, (c) async {
      calls.add(c.method);
      if (c.method == 'callState') {
        return jsonEncode({'call_id': 'c1', 'status': 'ringing', 'direction': 'to_owner', 'subject': 'Die Website ist fertig.', 'endpoints': []});
      }
      return true;
    });
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(const MethodChannel('jarvis/events'), (c) async => null);
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(const MethodChannel('flutter.baseflow.com/permissions/methods'), (c) async => c.method == 'requestPermissions' ? {7: 1, 21: 1} : 1);
  });

  testWidgets('incoming call shows the subject and answers through Telecom', (tester) async {
    await tester.pumpWidget(ProviderScope(child: MaterialApp(theme: J.theme(), home: const CallScreen())));
    await tester.pump(const Duration(milliseconds: 600));
    expect(find.text('Eingehender Anruf'), findsOneWidget);
    expect(find.text('Die Website ist fertig.'), findsOneWidget);
    expect(find.text('Annehmen'), findsOneWidget);
    await tester.tap(find.text('Annehmen'));
    await tester.pump(const Duration(milliseconds: 300));
    expect(calls, contains('answer'));
    await tester.pumpWidget(const SizedBox());
    await tester.pump(const Duration(seconds: 2));
  });
}
