import 'dart:async';

import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'providers.dart';

/// Where the app's self-update stands (Kotlin `update/AppUpdater`, docs/MOBILE.md › App-Updates).
class AppUpdateStatus {
  const AppUpdateStatus({
    this.state = 'idle',
    this.versionCode,
    this.versionName,
    this.targetCode,
    this.targetName,
    this.size,
    this.progress,
    this.error,
    this.waitingFor,
    this.changelog = const [],
    this.lastCheck,
    this.canInstall = false,
    this.unattended = false,
  });

  factory AppUpdateStatus.fromJson(Map<String, dynamic> j) => AppUpdateStatus(
        state: j['state'] as String? ?? 'idle',
        versionCode: (j['version_code'] as num?)?.toInt(),
        versionName: j['version_name'] as String?,
        targetCode: (j['target_code'] as num?)?.toInt(),
        targetName: j['target_name'] as String?,
        size: (j['size'] as num?)?.toInt(),
        progress: (j['progress'] as num?)?.toDouble(),
        error: j['error'] as String?,
        waitingFor: j['waiting_for'] as String?,
        changelog: [for (final c in (j['changelog'] as List? ?? const [])) c.toString()],
        lastCheck: j['last_check'] is num ? DateTime.fromMillisecondsSinceEpoch((j['last_check'] as num).toInt()) : null,
        canInstall: j['can_install'] == true,
        unattended: j['unattended'] == true,
      );

  /// idle | checking | up_to_date | downloading | verifying | ready | waiting_user | installing | installed |
  /// failed | unsupported
  final String state;
  final int? versionCode;
  final String? versionName;
  final int? targetCode;
  final String? targetName;
  final int? size;
  final double? progress;
  final String? error;

  /// Why a downloaded update has not been installed yet: background | call | permission | confirm
  final String? waitingFor;
  final List<String> changelog;
  final DateTime? lastCheck;

  /// "Install unknown apps" is allowed for J.A.R.V.I.S.
  final bool canInstall;

  /// Android 12+: updates install without a confirmation dialog.
  final bool unattended;

  bool get busy => const {'checking', 'downloading', 'verifying', 'installing'}.contains(state);

  /// An update is downloaded and checked; "Jetzt installieren" makes sense.
  bool get installable => state == 'ready' || (state == 'waiting_user' && waitingFor == 'confirm');

  String get target => targetName ?? (targetCode?.toString() ?? '');

  /// One line for the settings and the home banner.
  String get headline => switch (state) {
        'checking' => 'Suche nach Updates …',
        'up_to_date' => 'Aktuell',
        'downloading' => progress == null ? 'Lade $target …' : 'Lade $target … ${(progress! * 100).round()} %',
        'verifying' => 'Prüfe Signatur und Prüfsumme …',
        'ready' => switch (waitingFor) {
            'call' => 'Update $target bereit – wird nach dem Anruf installiert',
            'background' => 'Update $target bereit – wird installiert, sobald Sie die App verlassen',
            _ => 'Update $target bereit',
          },
        'waiting_user' => waitingFor == 'permission'
            ? 'Update $target bereit – Installation einmalig erlauben'
            : 'Update $target bereit – bitte Installation bestätigen',
        'installing' => 'Installiere $target …',
        'installed' => 'Aktualisiert auf ${versionName ?? target}',
        'failed' => 'Update fehlgeschlagen',
        'unsupported' => 'Keine automatischen Updates',
        _ => 'Noch nicht geprüft',
      };

  /// Should the start page point at the update? (only when Sir can or must do something)
  bool get worthABanner => installable || (state == 'waiting_user' && waitingFor == 'permission');
}

class AppUpdateController extends Notifier<AppUpdateStatus?> {
  @override
  AppUpdateStatus? build() {
    final native = ref.watch(nativeProvider);
    final sub = native.events.where((e) => e.type == 'app_update').listen((e) {
      if (e.data is Map) state = AppUpdateStatus.fromJson((e.data as Map).cast<String, dynamic>());
    });
    ref.onDispose(sub.cancel);
    unawaited(load());
    return null;
  }

  Future<void> load() async {
    try {
      state = AppUpdateStatus.fromJson(await ref.read(nativeProvider).updateStatus());
    } on PlatformException {
      // older native side or not on Android: no update section content
    } on MissingPluginException {
      // widget tests / other platforms
    }
  }

  /// Returns an error text for the UI, null if the check ran.
  Future<String?> check({bool manual = false}) async {
    try {
      state = AppUpdateStatus.fromJson(await ref.read(nativeProvider).updateCheck(manual: manual));
      return null;
    } on PlatformException catch (e) {
      return e.message ?? 'Prüfung fehlgeschlagen';
    } on MissingPluginException {
      return null;
    }
  }

  Future<void> install() async {
    try {
      await ref.read(nativeProvider).updateInstall();
    } on MissingPluginException {
      // not on Android
    }
  }
}

final appUpdateProvider = NotifierProvider<AppUpdateController, AppUpdateStatus?>(AppUpdateController.new);
