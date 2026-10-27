import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/core/theme.dart';
import 'package:jarvis_mobile/main.dart';
import 'package:jarvis_mobile/state/app_update.dart';
import 'package:jarvis_mobile/ui/widgets/update_settings.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('AppUpdateStatus', () {
    test('parses the native status', () {
      final s = AppUpdateStatus.fromJson({
        'state': 'downloading',
        'version_code': 3001,
        'version_name': '1.0.0+3001.abc1234',
        'target_code': 3002,
        'target_name': '1.0.0+3002.def5678',
        'size': 1000,
        'progress': 0.42,
        'changelog': ['Updater', 'Fix'],
        'last_check': 1700000000000,
        'can_install': true,
        'unattended': true,
      });
      expect(s.busy, isTrue);
      expect(s.installable, isFalse);
      expect(s.headline, 'Lade 1.0.0+3002.def5678 … 42 %');
      expect(s.changelog, ['Updater', 'Fix']);
      expect(s.lastCheck, DateTime.fromMillisecondsSinceEpoch(1700000000000));
    });

    test('tolerates an empty or old status', () {
      final s = AppUpdateStatus.fromJson({});
      expect(s.state, 'idle');
      expect(s.canInstall, isFalse);
      expect(s.headline, 'Noch nicht geprüft');
      expect(s.worthABanner, isFalse);
    });

    test('says why a downloaded update waits', () {
      AppUpdateStatus ready(String? why) =>
          AppUpdateStatus.fromJson({'state': 'ready', 'target_code': 3002, 'waiting_for': why, 'can_install': true});
      expect(ready('background').headline, contains('sobald Sie die App verlassen'));
      expect(ready('call').headline, contains('nach dem Anruf'));
      expect(ready(null).installable, isTrue);
      final perm = AppUpdateStatus.fromJson({'state': 'waiting_user', 'waiting_for': 'permission', 'target_code': 3002});
      expect(perm.installable, isFalse);
      expect(perm.worthABanner, isTrue);
      expect(perm.headline, contains('erlauben'));
      final confirm = AppUpdateStatus.fromJson({'state': 'waiting_user', 'waiting_for': 'confirm', 'target_code': 3002});
      expect(confirm.installable, isTrue);
    });
  });

  test('the update notification opens the settings', () {
    expect(routeForLink('jarvis://settings'), '/settings');
  });

  group('UpdateSettings', () {
    const native = MethodChannel('jarvis/native');
    const events = EventChannel('jarvis/events');
    late Map<String, dynamic> status;
    final calls = <MethodCall>[];

    setUp(() {
      calls.clear();
      status = {
        'state': 'ready',
        'version_code': 3001,
        'version_name': '1.0.0+3001.abc1234',
        'target_code': 3002,
        'target_name': '1.0.0+3002.def5678',
        'waiting_for': 'background',
        'changelog': ['Neuer Updater'],
        'can_install': false,
        'unattended': true,
      };
      final messenger = TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
      messenger.setMockMethodCallHandler(native, (c) async {
        calls.add(c);
        if (c.method == 'updateStatus' || c.method == 'updateCheck') return jsonEncode(status);
        return true;
      });
      messenger.setMockStreamHandler(events, MockStreamHandler.inline(onListen: (_, _) {}));
    });

    Future<void> pump(WidgetTester tester) async {
      tester.view.physicalSize = const Size(900, 2400);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(ProviderScope(
        child: MaterialApp(theme: J.theme(), home: const Scaffold(body: SingleChildScrollView(child: UpdateSettings()))),
      ));
      await tester.pump(const Duration(milliseconds: 100));
    }

    testWidgets('shows version, state, changelog and the one-time permission', (tester) async {
      await pump(tester);
      expect(find.text('1.0.0+3001.abc1234'), findsOneWidget);
      expect(find.textContaining('sobald Sie die App verlassen'), findsOneWidget);
      expect(find.text('— Neuer Updater'), findsOneWidget);
      expect(find.text('JETZT INSTALLIEREN'), findsOneWidget);
      expect(find.text('INSTALLATION ERLAUBEN'), findsOneWidget);

      await tester.tap(find.text('INSTALLATION ERLAUBEN'));
      await tester.pump();
      expect(calls.where((c) => c.method == 'openSettings').single.arguments, {'kind': 'install'});

      await tester.tap(find.text('JETZT INSTALLIEREN'));
      await tester.pump();
      expect(calls.any((c) => c.method == 'updateInstall'), isTrue);
    });

    testWidgets('"Jetzt prüfen" runs a manual check', (tester) async {
      status = {'state': 'up_to_date', 'version_name': '1.0.0+3002.def5678', 'can_install': true, 'unattended': true};
      await pump(tester);
      expect(find.text('Aktuell'), findsOneWidget);
      expect(find.text('JETZT INSTALLIEREN'), findsNothing);
      expect(find.text('INSTALLATION ERLAUBEN'), findsNothing);

      await tester.tap(find.text('JETZT PRÜFEN'));
      await tester.pump(const Duration(milliseconds: 50));
      expect(calls.where((c) => c.method == 'updateCheck').single.arguments, {'manual': true});
    });
  });
}
