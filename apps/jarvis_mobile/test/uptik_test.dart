import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/main.dart' show routeForLink;
import 'package:jarvis_mobile/state/uptik.dart';

void main() {
  test('hashtags, caption text and limits', () {
    expect(parseHashtags('#Web, kmu  #web ##Wolnzach'), ['#Web', '#kmu', '#Wolnzach']);
    expect(uptikPostText(' Hallo ', ['#a']), 'Hallo\n\n#a');
    expect(uptikOverLimit('x' * 2195, ['#abcdef'], ['tiktok', 'youtube_shorts']), ['tiktok']);
  });

  test('publish time in the Core format, null = now', () {
    expect(uptikPublishAt(DateTime(2026, 11, 2, 8, 5)), '2026-11-02 08:05');
    expect(uptikPublishAt(null), isNull);
  });

  test('labels for accounts and posts', () {
    expect(uptikAccountState({'circuit_state': 'tripped', 'circuit_reason': 'Captcha erkannt'}), 'Angehalten: Captcha erkannt');
    expect(uptikAccountState({'circuit_state': 'ok', 'status': 'connected'}), 'Verbunden');
    expect(uptikPostLabel('needs_review'), 'prüfen');
    expect(uptikPostTone('blocked'), 'bad');
  });

  test('video source Eingang or Upload → the Core reference', () {
    expect(uptikVideoRef('upload', '3f9a01bc-Clip.mp4'), 'upload:3f9a01bc-Clip.mp4');
    expect(uptikVideoRef('inbox', 'abc'), 'inbox:abc');
    expect(uptikVideoRef('upload', null), isNull);
    expect(uptikVideoRef('upload', ' '), isNull);
    expect(uptikUploadTitle('3f9a01bc-Mein Clip.mp4'), 'Mein Clip');
    expect(uptikUploadTitle('clip.final.mov'), 'clip.final');
  });

  test('notification deep link opens UpTik', () {
    expect(routeForLink('jarvis://uptik'), '/uptik');
  });
}
