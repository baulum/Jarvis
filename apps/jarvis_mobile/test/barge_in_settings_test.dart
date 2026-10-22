import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/core/theme.dart';
import 'package:jarvis_mobile/ui/widgets/wake_settings.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const native = MethodChannel('jarvis/native');
  const label = 'Jarvis ins Wort fallen';
  late Map<String, dynamic> status;
  final configured = <Map<Object?, Object?>>[];

  setUp(() {
    configured.clear();
    status = {'enabled': false, 'running': false, 'screen_only': true, 'keyword': '„Hey Jarvis“'};
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(native, (c) async {
      if (c.method == 'wakeConfigure') {
        final args = c.arguments as Map<Object?, Object?>;
        configured.add(args);
        if (args['bargeIn'] != null) status['barge_in'] = args['bargeIn'];
      }
      if (c.method == 'wakeStatus' || c.method == 'wakeConfigure') return jsonEncode(status);
      return true;
    });
  });

  Future<void> pump(WidgetTester tester) async {
    tester.view.physicalSize = const Size(900, 2400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(ProviderScope(
      child: MaterialApp(theme: J.theme(), home: const Scaffold(body: SingleChildScrollView(child: WakeSettings()))),
    ));
    await tester.pump(const Duration(milliseconds: 100));
  }

  Switch bargeInSwitch(WidgetTester tester) => tester.widget<Switch>(
        find.descendant(of: find.ancestor(of: find.text(label), matching: find.byType(Row)).first, matching: find.byType(Switch)),
      );

  Future<void> leave(WidgetTester tester) async {
    await tester.pumpWidget(const SizedBox()); // stops the status polling
    await tester.pump(const Duration(seconds: 4));
  }

  testWidgets('barge-in is on by default, also for a status from an older build', (tester) async {
    await pump(tester);
    expect(find.text(label), findsOneWidget);
    expect(bargeInSwitch(tester).value, isTrue);
    await leave(tester);
  });

  testWidgets('switching it off is saved natively and shown', (tester) async {
    status['barge_in'] = true;
    await pump(tester);
    await tester.tap(find.descendant(of: find.ancestor(of: find.text(label), matching: find.byType(Row)).first, matching: find.byType(Switch)));
    await tester.pump(const Duration(milliseconds: 100));
    expect(configured, hasLength(1));
    expect(configured.single['bargeIn'], isFalse);
    expect(configured.single['enabled'], isNull); // the wake word itself stays as it was
    expect(bargeInSwitch(tester).value, isFalse);
    await leave(tester);
  });
}
