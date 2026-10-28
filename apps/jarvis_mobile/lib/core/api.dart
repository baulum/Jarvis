import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:http/http.dart' as http;
import 'package:http/io_client.dart';

import 'native.dart';

class ApiError implements Exception {
  ApiError(this.status, this.message);
  final int status;
  final String message;
  bool get offline => status == 0;
  @override
  String toString() => message;
}

/// HttpClient that trusts exactly the paired gateway certificate (SHA-256 pin from the QR code).
HttpClient pinnedHttpClient(String? pin) {
  final c = HttpClient()..connectionTimeout = const Duration(seconds: 6);
  if (pin != null && pin.isNotEmpty) {
    c.badCertificateCallback = (cert, host, port) => sha256.convert(cert.der).toString() == pin.toLowerCase();
  }
  return c;
}

/// REST access to the Jarvis core through the TLS gateway. Credentials live natively (Keystore);
/// this client only asks for a short-lived access token per request.
class Api {
  Api(this.native);
  final NativeBridge native;
  http.Client? _client;
  String? _pin;
  String? base;

  Future<void> init() async {
    final st = await native.status();
    _pin = st['pin'] as String?;
    _client?.close();
    _client = IOClient(pinnedHttpClient(_pin));
    base = st['base_url'] as String?;
  }

  String? get pin => _pin;

  Future<Uri> _uri(String path) async {
    base ??= await native.baseUrl();
    return Uri.parse('$base$path');
  }

  Future<dynamic> get(String path) => _send('GET', path);
  Future<dynamic> post(String path, [Object? body]) => _send('POST', path, body);
  Future<dynamic> put(String path, Object body) => _send('PUT', path, body);
  Future<dynamic> delete(String path) => _send('DELETE', path);

  /// Request signed with the device key – used for approvals (after biometric confirmation).
  Future<dynamic> signedPost(String path, String purpose, Map<String, dynamic> body) async {
    final raw = jsonEncode(body);
    final sig = await native.sign(purpose, raw);
    return _send('POST', path, null, raw, {
      'X-Jarvis-Ts': sig['ts']!,
      'X-Jarvis-Nonce': sig['nonce']!,
      'X-Jarvis-Signature': sig['signature']!,
    });
  }

  Future<dynamic> _send(String method, String path, [Object? body, String? rawBody, Map<String, String>? extraHeaders]) async {
    final raw = rawBody;
    final headers = extraHeaders;
    if (_client == null) await init();
    for (var attempt = 0; attempt < 2; attempt++) {
      try {
        final token = await native.accessToken();
        final req = http.Request(method, await _uri(path))
          ..headers['Authorization'] = 'Bearer $token'
          ..headers.addAll(headers ?? const {});
        if (raw != null || body != null) {
          req.headers['Content-Type'] = 'application/json';
          req.body = raw ?? jsonEncode(body);
        }
        final res = await http.Response.fromStream(await _client!.send(req).timeout(const Duration(seconds: 30)));
        final text = utf8.decode(res.bodyBytes);
        if (res.statusCode >= 400) {
          String msg = 'Fehler ${res.statusCode}';
          try {
            msg = (jsonDecode(text) as Map)['detail']?.toString() ?? msg;
          } catch (_) {}
          throw ApiError(res.statusCode, msg);
        }
        return text.isEmpty ? null : jsonDecode(text);
      } on ApiError {
        rethrow;
      } on Exception catch (e) {
        if (e is SocketException || e is TimeoutException || e is HandshakeException || e is http.ClientException) {
          base = null; // network changed (Wi-Fi ↔ mobile, Tailscale) → probe the advertised addresses again
          if (attempt == 0) {
            try {
              base = await native.baseUrl(force: true);
            } catch (_) {}
            continue;
          }
          throw ApiError(0, 'Jarvis ist nicht erreichbar');
        }
        // native errors (e.g. not reachable while refreshing)
        final s = e.toString();
        throw ApiError(s.contains('nicht erreichbar') ? 0 : 500, s.replaceFirst(RegExp(r'^PlatformException\([^,]*, '), ''));
      }
    }
    throw ApiError(0, 'Jarvis ist nicht erreichbar');
  }

  /// WebSocket on the same pinned TLS connection settings.
  Future<WebSocket> socket(String path) async {
    final uri = await _uri(path);
    final token = await native.accessToken();
    final ws = uri.replace(
      scheme: uri.scheme == 'https' ? 'wss' : 'ws',
      queryParameters: {...uri.queryParameters, 'token': token},
    );
    return WebSocket.connect(ws.toString(), customClient: pinnedHttpClient(_pin))
        .timeout(const Duration(seconds: 8));
  }

  /// A generated image/video of the chat (GET /api/media/{images|videos}/{file}); the file name is checked by
  /// the caller and again by the core.
  Future<Uint8List> media(String kind, String file) async {
    if (_client == null) await init();
    final token = await native.accessToken();
    final res = await _client!
        .get(await _uri('/api/media/$kind/${Uri.encodeComponent(file)}'), headers: {'Authorization': 'Bearer $token'})
        .timeout(const Duration(seconds: 60));
    if (res.statusCode >= 400 || res.bodyBytes.isEmpty) throw ApiError(res.statusCode, 'Datei nicht verfügbar');
    return res.bodyBytes;
  }

  /// Uploads one chat attachment (POST /api/attachments, raw body). The core checks size, type and file signature.
  Future<Map<String, dynamic>> uploadAttachment(String name, List<int> bytes) async {
    if (_client == null) await init();
    final token = await native.accessToken();
    try {
      final res = await _client!
          .post((await _uri('/api/attachments')).replace(queryParameters: {'name': name}),
              headers: {'Authorization': 'Bearer $token', 'Content-Type': 'application/octet-stream'}, body: bytes)
          .timeout(const Duration(minutes: 2));
      final text = utf8.decode(res.bodyBytes);
      if (res.statusCode >= 400) {
        String msg = 'Fehler ${res.statusCode}';
        try {
          msg = (jsonDecode(text) as Map)['detail']?.toString() ?? msg;
        } catch (_) {}
        throw ApiError(res.statusCode, msg);
      }
      return (jsonDecode(text) as Map).cast<String, dynamic>();
    } on ApiError {
      rethrow;
    } on Exception catch (e) {
      if (e is SocketException || e is TimeoutException || e is HandshakeException || e is http.ClientException) {
        base = null; // network changed → probe the advertised addresses again on the next request
        throw ApiError(0, 'Jarvis ist nicht erreichbar');
      }
      rethrow;
    }
  }

  /// Hörprobe of a personality in its own voice (POST /api/persona/preview → WAV). Synthesis takes a few seconds.
  Future<Uint8List> personaPreview(String id) async {
    if (_client == null) await init();
    final token = await native.accessToken();
    final res = await _client!
        .post(await _uri('/api/persona/preview'),
            headers: {'Authorization': 'Bearer $token', 'Content-Type': 'application/json'}, body: jsonEncode({'id': id}))
        .timeout(const Duration(seconds: 60));
    if (res.statusCode >= 400 || res.bodyBytes.isEmpty) {
      String msg = 'Hörprobe nicht verfügbar';
      try {
        msg = (jsonDecode(utf8.decode(res.bodyBytes)) as Map)['detail']?.toString() ?? msg;
      } catch (_) {}
      throw ApiError(res.statusCode, msg);
    }
    return res.bodyBytes;
  }

  Future<String> transcribe(List<int> wav) async {
    if (_client == null) await init();
    final token = await native.accessToken();
    final res = await _client!.post(await _uri('/api/voice/stt'),
        headers: {'Authorization': 'Bearer $token', 'Content-Type': 'audio/wav'}, body: wav);
    if (res.statusCode >= 400) throw ApiError(res.statusCode, 'Spracherkennung fehlgeschlagen');
    return ((jsonDecode(utf8.decode(res.bodyBytes)) as Map)['text'] ?? '').toString();
  }
}
