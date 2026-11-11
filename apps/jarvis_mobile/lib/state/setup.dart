import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:permission_handler/permission_handler.dart';

import '../core/native.dart';
import 'providers.dart';

/// Everything the setup flow after pairing offers, in the order it is offered: first what a call from Jarvis needs,
/// then the voice assistant, phone control, location and self-updates.
enum SetupItem { notifications, microphone, bluetooth, fullscreen, battery, overlay, assistant, accessibility, location, install }

enum SetupNeed { essential, recommended, optional }

extension SetupNeedLabel on SetupNeed {
  String get label => switch (this) {
        SetupNeed.essential => 'Wichtig',
        SetupNeed.recommended => 'Empfohlen',
        SetupNeed.optional => 'Optional',
      };
}

/// granted · missing · notNeeded (not applicable on this Android version – the step is hidden)
enum SetupState { granted, missing, notNeeded }

class SetupStep {
  const SetupStep(this.item, this.title, this.why, this.need, {required this.action, required this.how, this.hint, this.secondary});
  final SetupItem item;
  final String title;

  /// Why Jarvis needs it – shown before anything is asked.
  final String why;
  final SetupNeed need;

  /// Label of the button that asks Android (dialog or settings page).
  final String action;

  /// What happens after the tap.
  final String how;

  /// Extra help, e.g. when Android greys a switch out.
  final String? hint;

  /// Second way in (label, settings kind), e.g. app info when the direct page is blocked.
  final (String, String)? secondary;
}

const setupSteps = <SetupStep>[
  SetupStep(
    SetupItem.notifications,
    'Benachrichtigungen',
    'Damit Jarvis Sie erreicht: Rückfragen, Freigaben, fertige Aufträge und eingehende Anrufe kommen als Benachrichtigung.',
    SetupNeed.essential,
    action: 'Erlauben',
    how: 'Android fragt nach. Ist die Frage schon einmal abgelehnt worden, öffnet sich die Benachrichtigungsseite der App.',
  ),
  SetupStep(
    SetupItem.microphone,
    'Mikrofon',
    'Für Gespräche mit Jarvis, Diktat und „Hey Jarvis“. Ohne Mikrofon hört Jarvis Sie nicht.',
    SetupNeed.essential,
    action: 'Erlauben',
    how: 'Android fragt nach. Ist die Frage gesperrt, öffnet sich die App-Info – dort unter Berechtigungen das Mikrofon erlauben.',
  ),
  SetupStep(
    SetupItem.bluetooth,
    'Bluetooth-Geräte',
    'Damit Sie im Anruf Headset oder Auto als Audioausgabe wählen können. Jarvis liest dafür nur die Gerätenamen.',
    SetupNeed.recommended,
    action: 'Erlauben',
    how: 'Android fragt nach „Geräte in der Nähe“.',
  ),
  SetupStep(
    SetupItem.fullscreen,
    'Anrufe auf dem Sperrbildschirm',
    'Wenn Jarvis anruft, erscheint der Anruf im Vollbild – auch bei gesperrtem Handy. Ab Android 14 muss das einmal erlaubt werden.',
    SetupNeed.essential,
    action: 'Einstellung öffnen',
    how: 'Es öffnet sich „Vollbild-Benachrichtigungen“: J.A.R.V.I.S. einschalten und zurückkehren.',
  ),
  SetupStep(
    SetupItem.battery,
    'Akku-Optimierung aus',
    'Sonst bremst Android Jarvis im Hintergrund: Anrufe und Benachrichtigungen kommen verspätet, „Hey Jarvis“ wird beendet.',
    SetupNeed.recommended,
    action: 'Erlauben',
    how: 'Android fragt „Akku-Optimierung ignorieren?“ – mit „Zulassen“ bestätigen.',
    hint: 'Erscheint keine Frage: über „Akku-Einstellungen“ J.A.R.V.I.S. auf „Nicht optimieren“ bzw. „Uneingeschränkt“ stellen.',
    secondary: ('Akku-Einstellungen', 'battery'),
  ),
  SetupStep(
    SetupItem.overlay,
    'Über anderen Apps einblenden',
    'Damit sich nach „Hey Jarvis“ das Sprachfenster über jeder App öffnet, auch wenn gerade eine andere App läuft.',
    SetupNeed.recommended,
    action: 'Einstellung öffnen',
    how: 'Es öffnet sich „Über anderen Apps einblenden“: J.A.R.V.I.S. einschalten und zurückkehren.',
  ),
  SetupStep(
    SetupItem.assistant,
    'Standard-Assistent',
    'Jarvis startet dann auch per Assistent-Geste (Ecke wischen oder Ein-Taste halten). Er ersetzt dafür Google Assistant bzw. Gemini.',
    SetupNeed.optional,
    action: 'Festlegen',
    how: 'Unter „Digitaler Assistent“ bzw. „Standard-Assistent-App“ J.A.R.V.I.S. wählen.',
  ),
  SetupStep(
    SetupItem.accessibility,
    'Handy-Steuerung (Bedienungshilfen)',
    'Jarvis kann dann auf Ihre Anweisung Apps öffnen, den Bildschirm lesen, tippen und scrollen („Öffne WhatsApp“). '
        'Er handelt nur auf Ihren Auftrag; Passwortfelder liest und füllt er nie.',
    SetupNeed.recommended,
    action: 'Bedienungshilfen öffnen',
    how: 'Dort „J.A.R.V.I.S.“ bzw. „Handy-Steuerung durch Jarvis“ antippen und einschalten.',
    hint: 'Ist der Schalter ausgegraut („Eingeschränkte Einstellung“): App-Info › ⋮ › „Eingeschränkte Einstellungen zulassen“, dann erneut öffnen.',
    secondary: ('App-Info', 'app'),
  ),
  SetupStep(
    SetupItem.location,
    'Standort teilen',
    'Jarvis kennt dann Ihren letzten Standort, etwa für „Wie lange brauche ich zu …?“. Nur der letzte Standort liegt auf Ihrem '
        'Jarvis-Server; Ausschalten in den Einstellungen löscht ihn dort.',
    SetupNeed.optional,
    action: 'Erlauben',
    how: 'Android fragt nach dem Standort. Damit Jarvis Sie auch bei geschlossener App orten kann, danach „Immer zulassen“ wählen.',
  ),
  SetupStep(
    SetupItem.install,
    'App-Updates installieren',
    'Ihr Jarvis liefert neue Versionen dieser App selbst aus. Dafür muss Android der App einmal erlauben, Updates zu installieren.',
    SetupNeed.recommended,
    action: 'Einstellung öffnen',
    how: 'Es öffnet sich „Unbekannte Apps installieren“: „Aus dieser Quelle zulassen“ einschalten und zurückkehren.',
  ),
];

SetupStep stepFor(SetupItem item) => setupSteps.firstWhere((s) => s.item == item);

/// The real state of every item, read natively (`setupStatus`). JSON `null`/missing = not applicable.
class SetupStatus {
  const SetupStatus(this.states, {this.loaded = true, this.sdk});
  const SetupStatus.unknown()
      : states = const {},
        loaded = false,
        sdk = null;

  factory SetupStatus.fromNative(Map<String, dynamic> j) => SetupStatus(sdk: (j['sdk'] as num?)?.toInt(), {
        for (final item in SetupItem.values)
          item: switch (j[item.name]) {
            true => SetupState.granted,
            false => SetupState.missing,
            _ => SetupState.notNeeded,
          },
      });

  final Map<SetupItem, SetupState> states;

  /// Android API level of the phone (null: unknown).
  final int? sdk;

  /// false until the first answer from Android – nothing counts as granted before that.
  final bool loaded;

  SetupState of(SetupItem item) => loaded ? (states[item] ?? SetupState.notNeeded) : SetupState.missing;
}

/// Where Sir is in the setup flow. Immutable; every step can be skipped, and from every position there is a way
/// forward to the summary, whose "Fertig" always closes the flow – the flow has no dead end.
class SetupFlow {
  const SetupFlow({this.status = const SetupStatus.unknown(), this.current, this.skipped = const {}, this.started = false});

  final SetupStatus status;

  /// The step on screen; null = summary.
  final SetupItem? current;
  final Set<SetupItem> skipped;

  /// false until the first status arrived and the flow chose its first step.
  final bool started;

  /// Steps that apply on this phone, in order.
  List<SetupStep> get steps => [for (final s in setupSteps) if (status.of(s.item) != SetupState.notNeeded) s];

  bool get atSummary => started && current == null;
  SetupStep? get step => current == null ? null : stepFor(current!);
  SetupState stateOf(SetupItem item) => status.of(item);
  bool isOpen(SetupItem item) => status.of(item) == SetupState.missing;

  int get position => current == null ? steps.length : steps.indexWhere((s) => s.item == current) + 1;
  int get grantedCount => steps.where((s) => status.of(s.item) == SetupState.granted).length;
  List<SetupStep> get openEssentials => [for (final s in steps) if (s.need == SetupNeed.essential && isOpen(s.item)) s];

  SetupFlow _at(SetupItem? item, {Set<SetupItem>? skipped}) =>
      SetupFlow(status: status, current: item, skipped: skipped ?? this.skipped, started: true);

  /// The first open step after [from] (exclusive), or null = summary.
  SetupItem? _nextOpen(SetupItem? from) {
    final list = steps;
    final start = from == null ? 0 : list.indexWhere((s) => s.item == from) + 1;
    for (var i = start; i < list.length; i++) {
      if (isOpen(list[i].item)) return list[i].item;
    }
    return null;
  }

  /// New status from Android. The first one picks the starting step (the first open one; all granted → summary);
  /// later ones keep Sir where he is unless that step no longer applies.
  SetupFlow withStatus(SetupStatus s) {
    final next = SetupFlow(status: s, current: current, skipped: skipped, started: started);
    if (!started) return next._at(next._nextOpen(null));
    if (current != null && s.of(current!) == SetupState.notNeeded) return next._at(next._nextOpen(current));
    return next;
  }

  /// "Weiter" after a granted step: the next open step.
  SetupFlow next() => _at(_nextOpen(current));

  /// "Überspringen": remembered as skipped (can be done later from the summary or the settings).
  SetupFlow skip() => current == null ? this : _at(_nextOpen(current), skipped: {...skipped, current!});

  /// "Zurück": the previous applicable step, whatever its state.
  SetupFlow back() {
    final list = steps;
    if (list.isEmpty) return this;
    if (current == null) return _at(list.last.item);
    final i = list.indexWhere((s) => s.item == current);
    return i <= 0 ? this : _at(list[i - 1].item);
  }

  bool get canGoBack => steps.isNotEmpty && (current == null || steps.indexWhere((s) => s.item == current) > 0);

  /// From the summary into one step.
  SetupFlow open(SetupItem item) => _at(item);

  /// "Später einrichten": straight to the summary.
  SetupFlow toSummary() => _at(null);
}

// ------------------------------------------------------------------ platform (permission_handler + native bridge)

/// What the setup flow asks Android. An interface so tests can drive the flow without a phone.
abstract class SetupPlatform {
  Future<SetupStatus> status();

  /// Asks for [item] – only ever called from a tap of Sir on the step's button. Returns a hint to show, or null.
  Future<String?> grant(SetupItem item);

  /// Opens a settings page directly (secondary button). Returns a hint to show, or null.
  Future<String?> openSettings(String kind);
}

class NativeSetupPlatform implements SetupPlatform {
  NativeSetupPlatform(this.native);
  final NativeBridge native;

  static const _settingsKind = {
    SetupItem.fullscreen: 'fullscreen',
    SetupItem.battery: 'battery_request',
    SetupItem.overlay: 'overlay',
    SetupItem.assistant: 'assistant',
    SetupItem.accessibility: 'accessibility',
    SetupItem.install: 'install',
  };

  @override
  Future<SetupStatus> status() async => SetupStatus.fromNative(await native.setupStatus());

  @override
  Future<String?> openSettings(String kind) async =>
      await native.openSettings(kind) ? null : 'Android konnte die Einstellung nicht öffnen. Bitte über die System-Einstellungen › Apps › J.A.R.V.I.S.';

  /// Runtime permission: Android's dialog; refused for good → the app's settings page instead of nothing.
  Future<String?> _runtime(Permission p, String deniedHint) async {
    final st = await p.request();
    if (st.isGranted || st.isLimited) return null;
    if (st.isPermanentlyDenied || st.isRestricted) {
      await native.openSettings('app');
      return deniedHint;
    }
    return 'Nicht erlaubt. Sie können es jederzeit erneut versuchen oder überspringen.';
  }

  @override
  Future<String?> grant(SetupItem item) async {
    try {
      switch (item) {
        case SetupItem.notifications:
          final st = await Permission.notification.request();
          final now = await status();
          if (now.of(SetupItem.notifications) == SetupState.granted) return null;
          // Sir just said "Nicht zulassen" in Android's dialog: respect it, the next tap asks again
          if (st.isDenied && (now.sdk ?? 33) >= 33) return 'Nicht erlaubt. Sie können es erneut versuchen oder überspringen.';
          // below Android 13 there is no dialog; blocked for good, or switched off in the app's notification page
          return await native.openSettings('notifications')
              ? 'Bitte „Benachrichtigungen zulassen“ einschalten und zurückkehren.'
              : 'Android konnte die Benachrichtigungsseite nicht öffnen.';
        case SetupItem.microphone:
          return _runtime(Permission.microphone, 'Android fragt nicht mehr nach: in der App-Info unter Berechtigungen › Mikrofon › Zulassen.');
        case SetupItem.bluetooth:
          return _runtime(Permission.bluetoothConnect, 'Android fragt nicht mehr nach: in der App-Info unter Berechtigungen › Geräte in der Nähe › Zulassen.');
        case SetupItem.location:
          final hint = await _runtime(Permission.locationWhenInUse, 'Android fragt nicht mehr nach: in der App-Info unter Berechtigungen › Standort.');
          if (hint != null) return hint;
          // "Immer zulassen" (Android 11+: its own settings page) – optional, sharing works without it while the app is open
          await Permission.locationAlways.request();
          final r = await native.setLocationSharing(true);
          return r['sent'] == true ? null : 'Standortfreigabe an. Der erste Standort wird gesendet, sobald Ortung und Jarvis erreichbar sind.';
        case SetupItem.fullscreen:
        case SetupItem.battery:
        case SetupItem.overlay:
        case SetupItem.assistant:
        case SetupItem.accessibility:
        case SetupItem.install:
          return openSettings(_settingsKind[item]!);
      }
    } on PlatformException catch (e) {
      return e.message ?? 'Das hat nicht geklappt. Bitte erneut versuchen oder überspringen.';
    }
  }
}

final setupPlatformProvider = Provider<SetupPlatform>((ref) => NativeSetupPlatform(ref.watch(nativeProvider)));
