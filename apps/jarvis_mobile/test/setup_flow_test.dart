import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/core/theme.dart';
import 'package:jarvis_mobile/state/setup.dart';
import 'package:jarvis_mobile/ui/screens/setup_screen.dart';

/// Android 14 phone with nothing granted yet (as right after pairing).
Map<String, dynamic> fresh({int sdk = 34}) => {
      'sdk': sdk,
      for (final i in SetupItem.values) i.name: false,
      if (sdk < 34) 'fullscreen': null,
      if (sdk < 31) 'bluetooth': null,
    };

class FakeSetup implements SetupPlatform {
  FakeSetup(this.raw);
  Map<String, dynamic> raw;
  final grants = <SetupItem>[];
  final opened = <String>[];

  /// What a tap on the step's button changes right away (a runtime dialog); settings pages change nothing until
  /// Sir comes back, which the test simulates by editing [raw].
  final grantsAtOnce = <SetupItem>{};
  Completer<void>? hang;
  bool fail = false;

  @override
  Future<SetupStatus> status() async {
    if (fail) throw Exception('no native');
    return SetupStatus.fromNative(raw);
  }

  @override
  Future<String?> grant(SetupItem item) async {
    grants.add(item);
    if (hang != null) await hang!.future;
    if (grantsAtOnce.contains(item)) raw[item.name] = true;
    return null;
  }

  @override
  Future<String?> openSettings(String kind) async {
    opened.add(kind);
    return null;
  }
}

void main() {
  group('SetupFlow (state of the steps)', () {
    SetupFlow started(Map<String, dynamic> raw) => const SetupFlow().withStatus(SetupStatus.fromNative(raw));

    test('offers every declared permission in a sensible order', () {
      expect(setupSteps.map((s) => s.item), [
        SetupItem.notifications,
        SetupItem.microphone,
        SetupItem.bluetooth,
        SetupItem.fullscreen,
        SetupItem.battery,
        SetupItem.overlay,
        SetupItem.assistant,
        SetupItem.accessibility,
        SetupItem.location,
        SetupItem.install,
      ]);
      for (final s in setupSteps) {
        expect(s.why, isNotEmpty, reason: '${s.item} needs a reason');
        expect(s.how, isNotEmpty, reason: '${s.item} needs to say what happens');
      }
    });

    test('nothing counts as granted before Android answered', () {
      const f = SetupFlow();
      expect(f.started, isFalse);
      expect(f.stateOf(SetupItem.battery), SetupState.missing);
    });

    test('starts at the first open step and hides what this Android does not have', () {
      final f = started({...fresh(sdk: 30), 'notifications': true});
      expect(f.current, SetupItem.microphone);
      expect(f.steps.map((s) => s.item), isNot(contains(SetupItem.fullscreen)));
      expect(f.steps.map((s) => s.item), isNot(contains(SetupItem.bluetooth)));
    });

    test('everything granted → straight to the summary', () {
      final f = started({for (final i in SetupItem.values) i.name: true});
      expect(f.atSummary, isTrue);
      expect(f.openEssentials, isEmpty);
    });

    test('skipping every step always reaches the summary – no dead end', () {
      var f = started(fresh());
      var guard = 0;
      while (!f.atSummary) {
        f = f.skip();
        expect(++guard, lessThanOrEqualTo(SetupItem.values.length));
      }
      expect(f.skipped, hasLength(f.steps.length));
      expect(f.openEssentials.map((s) => s.item), [SetupItem.notifications, SetupItem.microphone, SetupItem.fullscreen]);
    });

    test('next skips steps that are already granted, back visits every step', () {
      var f = started({...fresh(), 'bluetooth': true, 'fullscreen': true});
      f = f.open(SetupItem.microphone);
      expect(f.next().current, SetupItem.battery);
      expect(f.next().back().current, SetupItem.fullscreen);
      expect(f.toSummary().back().current, SetupItem.install);
    });

    test('a new status keeps Sir on the step he is on (e.g. back from the battery dialog)', () {
      var f = started(fresh()).open(SetupItem.battery);
      f = f.withStatus(SetupStatus.fromNative({...fresh(), 'battery': true}));
      expect(f.current, SetupItem.battery);
      expect(f.stateOf(SetupItem.battery), SetupState.granted);
    });

    test('back is not offered on the first step', () {
      final f = started(fresh());
      expect(f.canGoBack, isFalse);
      expect(f.back().current, f.current);
      expect(f.skip().canGoBack, isTrue);
    });
  });

  group('SetupFlowView (widget)', () {
    late FakeSetup fake;
    late int finished;

    setUp(() {
      fake = FakeSetup(fresh());
      finished = 0;
    });

    Future<void> pump(WidgetTester tester) async {
      tester.view.physicalSize = const Size(360, 740); // a narrow phone: nothing may overflow
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(ProviderScope(
        overrides: [setupPlatformProvider.overrideWithValue(fake)],
        child: MaterialApp(
          theme: J.theme(),
          home: Scaffold(
            body: SetupFlowView(onFinish: () async => finished++, pollEvery: const Duration(hours: 1)),
          ),
        ),
      ));
      await settle(tester);
    }

    Future<void> leave(WidgetTester tester) async {
      await tester.pumpWidget(const SizedBox()); // stops the status polling
      await tester.pump(const Duration(seconds: 1));
    }

    Future<void> tap(WidgetTester tester, String key) async {
      await tester.tap(find.byKey(Key(key)));
      await settle(tester);
    }

    testWidgets('explains the first step and asks Android for nothing on its own', (tester) async {
      await pump(tester);
      expect(find.text('Benachrichtigungen'), findsOneWidget);
      expect(find.textContaining('Damit Jarvis Sie erreicht'), findsOneWidget);
      expect(find.text('EINRICHTUNG · 1 VON 10'), findsOneWidget);
      expect(fake.grants, isEmpty);
      await leave(tester);
    });

    testWidgets('every step can be skipped; "Fertig" closes even with everything open', (tester) async {
      await pump(tester);
      for (var i = 0; i < 10; i++) {
        await tap(tester, 'setup-skip');
      }
      expect(find.text('Fast fertig.'), findsOneWidget);
      expect(fake.grants, isEmpty);
      await tap(tester, 'setup-finish');
      expect(finished, 1);
      await leave(tester);
    });

    testWidgets('"Später einrichten" leads to the summary, from where a step can be done later', (tester) async {
      await pump(tester);
      await tap(tester, 'setup-later');
      expect(find.text('EINRICHTUNG · ÜBERSICHT'), findsOneWidget);
      await tester.scrollUntilVisible(find.byKey(const Key('setup-open-accessibility')), 80);
      await tester.ensureVisible(find.byKey(const Key('setup-open-accessibility')));
      await settle(tester);
      await tap(tester, 'setup-open-accessibility');
      expect(find.text('Handy-Steuerung (Bedienungshilfen)'), findsOneWidget);
      expect(find.textContaining('Passwortfelder liest und füllt er nie'), findsOneWidget);
      await tap(tester, 'setup-grant');
      expect(fake.grants, [SetupItem.accessibility]);
      await leave(tester);
    });

    testWidgets('a runtime permission granted in the dialog turns the step into "Weiter"', (tester) async {
      fake.grantsAtOnce.add(SetupItem.notifications);
      await pump(tester);
      await tap(tester, 'setup-grant');
      expect(find.text('Erteilt'), findsOneWidget);
      await tap(tester, 'setup-next');
      expect(find.text('Mikrofon'), findsOneWidget);
      await leave(tester);
    });

    testWidgets('battery: the button asks Android and the real state is read again on return', (tester) async {
      await pump(tester);
      await tap(tester, 'setup-later');
      await tester.scrollUntilVisible(find.byKey(const Key('setup-open-battery')), 80);
      await tester.ensureVisible(find.byKey(const Key('setup-open-battery')));
      await settle(tester);
      await tap(tester, 'setup-open-battery');
      expect(find.text('Akku-Optimierung aus'), findsOneWidget);
      await tap(tester, 'setup-grant');
      expect(fake.grants, [SetupItem.battery]);
      expect(find.text('Noch nicht erteilt'), findsOneWidget); // the system dialog is still open

      // Sir taps "Zulassen" in Android's dialog and the app comes back to the foreground
      fake.raw['battery'] = true;
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      await settle(tester);
      expect(find.text('Erteilt'), findsOneWidget);
      expect(find.byKey(const Key('setup-next')), findsOneWidget);

      // the second way in opens the battery list
      fake.raw['battery'] = false;
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      await settle(tester);
      await tester.scrollUntilVisible(find.byKey(const Key('setup-secondary')), 80);
      await tester.ensureVisible(find.byKey(const Key('setup-secondary')));
      await settle(tester);
      await tap(tester, 'setup-secondary');
      expect(fake.opened, ['battery']);
      await leave(tester);
    });

    testWidgets('skipping works while Android\'s dialog is still open', (tester) async {
      fake.hang = Completer<void>();
      await pump(tester);
      await tester.tap(find.byKey(const Key('setup-grant')));
      await tester.pump(const Duration(milliseconds: 300));
      await tap(tester, 'setup-skip');
      expect(find.text('Mikrofon'), findsOneWidget);
      fake.hang!.complete();
      await settle(tester);
      await leave(tester);
    });

    testWidgets('without a readable status the flow still opens and can be closed', (tester) async {
      fake.fail = true;
      await pump(tester);
      expect(find.byKey(const Key('setup-finish')), findsOneWidget);
      await tap(tester, 'setup-finish');
      expect(finished, 1);
      await leave(tester);
    });
  });
}

/// Lets futures, the AnimatedSwitcher and button feedback finish (pumpAndSettle would wait on spinners forever).
Future<void> settle(WidgetTester tester) async {
  for (var i = 0; i < 6; i++) {
    await tester.pump(const Duration(milliseconds: 120));
  }
}
