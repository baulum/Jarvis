import 'dart:async';
import 'dart:convert';

import 'package:flutter/services.dart';

/// Typed wrapper around the Kotlin side (credentials, Keystore signing, Telecom calls, push).
class NativeBridge {
  NativeBridge({MethodChannel? channel, EventChannel? events})
      : _ch = channel ?? const MethodChannel('jarvis/native'),
        _ev = events ?? const EventChannel('jarvis/events');

  final MethodChannel _ch;
  final EventChannel _ev;
  Stream<NativeEvent>? _events;

  Stream<NativeEvent> get events => _events ??= _ev
      .receiveBroadcastStream()
      .map((e) => NativeEvent.fromJson(jsonDecode(e as String) as Map<String, dynamic>))
      .asBroadcastStream();

  Future<Map<String, dynamic>> status() async =>
      jsonDecode(await _ch.invokeMethod<String>('status') ?? '{}') as Map<String, dynamic>;

  Future<Map<String, dynamic>> pair(String payload, String name) async =>
      jsonDecode(await _ch.invokeMethod<String>('pair', {'payload': payload, 'name': name}) ?? '{}')
          as Map<String, dynamic>;

  Future<String> accessToken() async => (await _ch.invokeMethod<String>('accessToken'))!;
  Future<String> baseUrl({bool force = false}) async => (await _ch.invokeMethod<String>('baseUrl', {'force': force}))!;
  Future<void> logout() => _ch.invokeMethod('logout');
  Future<void> forget() => _ch.invokeMethod('forget');

  Future<Map<String, dynamic>> configurePush(String? optionsJson) async =>
      jsonDecode(await _ch.invokeMethod<String>('configurePush', {'options': optionsJson}) ?? '{}')
          as Map<String, dynamic>;

  /// Device-key signature (Android Keystore) over `purpose|device|ts|nonce|sha256(body)`.
  Future<Map<String, String>> sign(String purpose, String body) async =>
      (await _ch.invokeMapMethod<String, String>('sign', {'purpose': purpose, 'body': body}))!;

  Future<Map<String, dynamic>> callState() async =>
      jsonDecode(await _ch.invokeMethod<String>('callState') ?? '{}') as Map<String, dynamic>;
  Future<void> startCall() => _ch.invokeMethod('startCall');
  Future<void> answer() => _ch.invokeMethod('answer');
  Future<void> decline() => _ch.invokeMethod('decline');
  Future<void> hangup() => _ch.invokeMethod('hangup');
  Future<void> setMuted(bool muted) => _ch.invokeMethod('setMuted', {'muted': muted});
  Future<void> setEndpoint(String id) => _ch.invokeMethod('setEndpoint', {'id': id});
  Future<void> openSettings(String kind) => _ch.invokeMethod('openSettings', {'kind': kind});

  /// Location sharing with Jarvis: `{enabled, permission: none|while_in_use|always, precise, location_on}`.
  Future<Map<String, dynamic>> locationStatus() async =>
      jsonDecode(await _ch.invokeMethod<String>('locationStatus') ?? '{}') as Map<String, dynamic>;

  /// Switches sharing on (sends a fix right away; `sent` in the result) or off (deletes it on the server).
  Future<Map<String, dynamic>> setLocationSharing(bool enabled) async =>
      jsonDecode(await _ch.invokeMethod<String>('setLocationSharing', {'enabled': enabled}) ?? '{}')
          as Map<String, dynamic>;

  /// Sends the current position if sharing is on; false otherwise.
  Future<bool> reportLocation() async => await _ch.invokeMethod<bool>('reportLocation') ?? false;

  /// Is "Handy-Steuerung durch Jarvis" (the accessibility service) switched on in Android's settings?
  Future<bool> phoneControlEnabled() async => await _ch.invokeMethod<bool>('phoneControlStatus') ?? false;

  /// "Hey Jarvis": wake word state, permissions and default-assistant status.
  Future<Map<String, dynamic>> wakeStatus() async =>
      jsonDecode(await _ch.invokeMethod<String>('wakeStatus') ?? '{}') as Map<String, dynamic>;

  /// Only the given values change.
  Future<Map<String, dynamic>> wakeConfigure({bool? enabled, bool? screenOnly, bool? bargeIn}) async =>
      jsonDecode(await _ch.invokeMethod<String>(
                'wakeConfigure',
                {'enabled': enabled, 'screenOnly': screenOnly, 'bargeIn': bargeIn},
              ) ??
              '{}') as Map<String, dynamic>;

  /// Opens the voice overlay (same as saying "Hey Jarvis").
  Future<void> openAssist() => _ch.invokeMethod('openAssist');

  /// Opens a web/mail link from a chat answer in the system's default app; false if that is not possible.
  Future<bool> openUrl(String url) async => await _ch.invokeMethod<bool>('openUrl', {'url': url}) ?? false;
}

class NativeEvent {
  const NativeEvent(this.type, this.data);
  factory NativeEvent.fromJson(Map<String, dynamic> j) => NativeEvent(j['type'] as String, j['data']);
  final String type;
  final dynamic data;
}
