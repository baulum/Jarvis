import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/main.dart' show routeForLink;
import 'package:jarvis_mobile/state/social.dart';

void main() {
  test('caption text as pasted into the platform', () {
    expect(captionText('instagram', {'text': ' Hallo ', 'hashtags': ['#Webdesign', '#Wolnzach']}), 'Hallo\n\n#Webdesign #Wolnzach');
    expect(captionText('youtube', {'title': 'T', 'description': 'Beschreibung', 'hashtags': []}), 'Beschreibung');
    expect(captionText('tiktok', null), '');
  });

  test('status labels and validation', () {
    expect(videoStatusLabel({'status': 'producing', 'stage': 'render'}), 'In Arbeit · Rendern');
    expect(videoStatusLabel({'status': 'failed', 'stage': 'qa'}), 'Fehlgeschlagen (Qualitätskontrolle)');
    expect(validTime('08:00'), isTrue);
    expect(validTime('8:00'), isFalse);
    expect(safeVideoName({'file_name': '../x.mp4', 'title': 'Captcha!'}), 'Captcha.mp4');
    expect(safeVideoName({'file_name': '2026-10-28_a.mp4'}), '2026-10-28_a.mp4');
  });

  test('company profile inputs', () {
    expect(splitList(' sir@example.com, bagy@example.com;\n ,x@y.de '), ['sir@example.com', 'bagy@example.com', 'x@y.de']);
    expect(validEmail('hr@apo-hr.example'), isTrue);
    expect(validEmail('kein-mail'), isFalse);
    expect(validHex('#0f9d58'), isTrue);
    expect(validHex('grün'), isFalse);
    expect(missingFields({'missing': ['Website', 'CTA-Button']}), ['Website', 'CTA-Button']);
    expect(companyQuery(null), '');
    expect(companyQuery('apo-hr'), '?company=apo-hr');
    expect(companyQuery('Apo HR', sep: '&'), '&company=Apo+HR');
  });

  test('notification deep link opens Social Media', () {
    expect(routeForLink('jarvis://social'), '/social');
  });
}
