import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/main.dart' show routeForLink;
import 'package:jarvis_mobile/state/sales.dart';

void main() {
  test('lead-tool link only when the phone can reach it', () {
    expect(salesLeadToolLink('http://127.0.0.1:3100', '/outbox'), isNull);
    expect(salesLeadToolLink('http://localhost:3100'), isNull);
    expect(salesLeadToolLink(null), isNull);
    expect(salesLeadToolLink('kein url'), isNull);
    expect(salesLeadToolLink('https://leads.pje.example/', '/review?lead=x'), 'https://leads.pje.example/review?lead=x');
    expect(salesLeadToolLink('https://leads.pje.example', 'outbox'), 'https://leads.pje.example/outbox');
  });

  test('leads query leaves empty filters out', () {
    expect(salesLeadsPath(), '/api/sales/leads?limit=50');
    expect(salesLeadsPath(campaignId: 'c1', status: 'AWAITING_APPROVAL', limit: 20),
        '/api/sales/leads?limit=20&campaign_id=c1&status=AWAITING_APPROVAL');
    expect(salesLeadsPath(campaignId: '', status: ''), '/api/sales/leads?limit=50');
  });

  test('action answers: an approval is never reported as done', () {
    expect(salesActionText({'status': 'approval_required'}, 'Erledigt'), 'Freigabe nötig – unter Aktivität bestätigen');
    expect(salesActionText({'status': 'ok'}, 'Erledigt'), 'Erledigt');
    expect(salesActionText({'status': 'duplicate'}, 'Erledigt'), 'Wurde bereits ausgeführt');
  });

  test('tones, scores and request ids', () {
    expect(salesStageTone('review'), 'warn');
    expect(salesStageTone('error'), 'bad');
    expect(salesStageTone(null), 'idle');
    expect(salesScore(null), '–');
    expect(salesScore(78, 'A'), '78 · A');
    final id = salesRequestId();
    expect(RegExp(r'^[A-Za-z0-9_-]{8,64}$').hasMatch(id), isTrue);
    expect(salesRequestId(), isNot(id));
  });

  test('notification deep link opens the Kundensuche', () {
    expect(routeForLink('jarvis://sales'), '/sales');
  });
}
