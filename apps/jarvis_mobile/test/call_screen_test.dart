import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/core/api.dart';
import 'package:jarvis_mobile/core/native.dart';
import 'package:jarvis_mobile/core/theme.dart';
import 'package:jarvis_mobile/state/providers.dart';
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

  Future<_RecordingApi> pumpActive(WidgetTester tester, String agentState) async {
    final api = _RecordingApi();
    await tester.pumpWidget(ProviderScope(
      overrides: [
        apiProvider.overrideWithValue(api),
        callProvider.overrideWith(() => _ActiveCall(agentState)),
      ],
      child: MaterialApp(theme: J.theme(), home: const CallScreen()),
    ));
    await tester.pump(const Duration(milliseconds: 600));
    return api;
  }

  testWidgets('Stopp while Jarvis talks silences him through the core and keeps the call', (tester) async {
    final api = await pumpActive(tester, 'speaking');
    expect(find.text('STOPP'), findsOneWidget);
    await tester.tap(find.text('STOPP'));
    await tester.pump(const Duration(milliseconds: 300));
    expect(api.posts, ['/api/mobile/calls/c1/interrupt']);
    expect(calls, isNot(contains('hangup')));
    await tester.pumpWidget(const SizedBox());
    await tester.pump(const Duration(seconds: 2));
  });

  testWidgets('no Stopp button while Jarvis is listening', (tester) async {
    await pumpActive(tester, 'listening');
    expect(find.text('STOPP'), findsNothing);
    await tester.pumpWidget(const SizedBox());
    await tester.pump(const Duration(seconds: 2));
  });
}

class _RecordingApi extends Api {
  _RecordingApi() : super(NativeBridge());
  final posts = <String>[];

  @override
  Future<dynamic> post(String path, [Object? body]) async {
    posts.add(path);
    return {'interrupted': true};
  }
}

class _ActiveCall extends CallController {
  _ActiveCall(this.agentState);
  final String agentState;

  @override
  CallInfo build() => CallInfo(callId: 'c1', status: 'active', direction: 'to_jarvis', agentState: agentState);
}
