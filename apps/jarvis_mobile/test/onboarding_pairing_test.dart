import 'dart:async';
import 'dart:convert';

import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/core/api.dart';
import 'package:jarvis_mobile/core/native.dart';
import 'package:jarvis_mobile/main.dart';
import 'package:jarvis_mobile/state/providers.dart';

/// The core's REST API without a network: [onGet] decides per test.
class FakeApi extends Api {
  FakeApi(this.onGet) : super(NativeBridge());
  final Future<dynamic> Function(String path) onGet;
  @override
  Future<void> init() async {}
  @override
  Future<dynamic> get(String path) => onGet(path);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const channel = MethodChannel('jarvis/native');
  late Map<String, dynamic> native;
  late List<String> calls;
  Object? pushError;

  setUp(() {
    native = {'paired': false, 'setup_done': false};
    calls = [];
    pushError = null;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel, (c) async {
      calls.add(c.method);
      switch (c.method) {
        case 'status':
          return jsonEncode(native);
        case 'configurePush':
          if (pushError != null) throw pushError!;
          return jsonEncode({'ready': true});
        case 'setSetupDone':
          native['setup_done'] = (c.arguments as Map)['done'];
          return true;
        case 'forget':
          native['paired'] = false;
          return true;
      }
      return null;
    });
  });

  tearDown(() => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel, null));

  ProviderContainer container(Future<dynamic> Function(String) onGet) {
    final c = ProviderContainer(overrides: [apiProvider.overrideWithValue(FakeApi(onGet))]);
    addTearDown(c.dispose);
    return c;
  }

  group('leaving the pairing screen ("Weiter")', () {
    test('switches to paired at once, even while the core does not answer', () async {
      final never = Completer<dynamic>();
      final c = container((_) => never.future);
      expect((await c.read(pairingProvider.future)).paired, isFalse);

      native['paired'] = true; // the native pairing just stored the credentials
      final ok = await c.read(pairingProvider.notifier).adoptLocalPairing().timeout(const Duration(seconds: 1));
      expect(ok, isTrue);
      final p = c.read(pairingProvider).value!;
      expect(p.paired, isTrue);
      expect(p.setupDone, isFalse); // → the router opens the setup flow next
    });

    test('a failing push setup (native error) no longer blocks the pairing state', () async {
      pushError = PlatformException(code: 'jarvis', message: 'Firebase kaputt');
      final c = container((_) async => {'firebase': {'apiKey': 'x'}});
      await c.read(pairingProvider.future);
      native['paired'] = true;
      await c.read(pairingProvider.notifier).refresh(); // used to throw here and leave Sir on the screen
      final p = c.read(pairingProvider).value!;
      expect(p.paired, isTrue);
      expect(p.push['ready'], isFalse);
    });

    test('profile loaded in the background replaces the local state, keeping a setup decision', () async {
      final me = Completer<dynamic>();
      final c = container((_) => me.future);
      await c.read(pairingProvider.future);
      native['paired'] = true;
      await c.read(pairingProvider.notifier).adoptLocalPairing();
      await c.read(pairingProvider.notifier).markSetupDone();
      me.complete({'name': 'Sir'});
      await pumpEventQueue();
      final p = c.read(pairingProvider).value!;
      expect(p.me['name'], 'Sir');
      expect(p.setupDone, isTrue);
    });

    test('not paired natively → stays on the pairing screen with a message instead of hanging', () async {
      final c = container((_) async => {});
      await c.read(pairingProvider.future);
      expect(await c.read(pairingProvider.notifier).adoptLocalPairing(), isFalse);
      expect(c.read(pairingProvider).value!.paired, isFalse);
    });

    test('"Fertig" in the setup flow is remembered natively', () async {
      final c = container((_) async => {});
      await c.read(pairingProvider.future);
      native['paired'] = true;
      await c.read(pairingProvider.notifier).adoptLocalPairing();
      await c.read(pairingProvider.notifier).markSetupDone();
      expect(calls, contains('setSetupDone'));
      expect(native['setup_done'], isTrue);
      expect(c.read(pairingProvider).value!.setupDone, isTrue);
    });
  });

  group('router', () {
    test('pairing → setup flow (once) → app', () {
      expect(redirectFor(loading: true, paired: false, setupDone: true, location: '/'), '/boot');
      expect(redirectFor(loading: false, paired: false, setupDone: true, location: '/'), '/pair');
      expect(redirectFor(loading: false, paired: false, setupDone: true, location: '/setup'), '/pair');
      expect(redirectFor(loading: false, paired: true, setupDone: false, location: '/pair'), '/setup');
      expect(redirectFor(loading: false, paired: true, setupDone: false, location: '/'), '/setup');
      expect(redirectFor(loading: false, paired: true, setupDone: false, location: '/setup'), isNull);
      expect(redirectFor(loading: false, paired: true, setupDone: true, location: '/pair'), '/');
      expect(redirectFor(loading: false, paired: true, setupDone: true, location: '/setup'), isNull); // from Einstellungen
    });

    test('a call or a notification target is never blocked by the setup flow', () {
      expect(redirectFor(loading: false, paired: true, setupDone: false, location: '/call'), isNull);
      expect(redirectFor(loading: false, paired: true, setupDone: false, location: '/activity'), isNull);
    });

    test('an older native build without the flag counts as set up', () {
      expect(const Pairing(paired: true, status: {}).setupDone, isTrue);
      expect(const Pairing(paired: true, status: {'setup_done': false}).setupDone, isFalse);
    });
  });
}
