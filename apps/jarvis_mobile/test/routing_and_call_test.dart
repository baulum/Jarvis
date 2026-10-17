import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/main.dart';
import 'package:jarvis_mobile/state/providers.dart';

void main() {
  test('deep links from notifications map onto routes', () {
    expect(routeForLink('jarvis://approval/abc?decide=approve'), '/activity?approval=abc&decide=approve');
    expect(routeForLink('jarvis://task/t1'), '/activity?task=t1');
    expect(routeForLink('jarvis://call'), '/call');
    expect(routeForLink('jarvis://call/c1'), '/activity?call=c1');
    expect(routeForLink('jarvis://chat/s1'), '/chat?session=s1');
    expect(routeForLink('jarvis://home'), '/');
    expect(routeForLink('https://evil.example/approval/x'), '/');
  });

  test('call state from Telecom + data channel from Jarvis', () {
    var c = const CallInfo().merge({'call_id': 'c1', 'status': 'ringing', 'direction': 'to_owner', 'subject': 'Website fertig', 'endpoints': []});
    expect(c.live, isTrue);
    c = c.merge({'call_id': 'c1', 'status': 'active', 'active_since': 1792227000000, 'endpoints': [
      {'id': 'e', 'name': 'Hörer', 'type': 1},
      {'id': 'bt', 'name': 'Auto', 'type': 2},
    ], 'endpoint': {'id': 'bt', 'name': 'Auto', 'type': 2}});
    expect(c.endpoints, hasLength(2));
    expect(c.endpoint!['name'], 'Auto');
    c = c.withData({'type': 'state', 'state': 'tool', 'detail': 'Prüfe den Kalender …'});
    expect(c.activity, 'Prüfe den Kalender …');
    c = c.withData({'type': 'transcript', 'role': 'jarvis', 'text': 'Guten Abend, Sir.'});
    c = c.withData({'type': 'state', 'state': 'listening'});
    expect(c.transcript.single.text, 'Guten Abend, Sir.');
    expect(c.activity, isNull); // hint disappears when the tool is done – nothing simulated
    // a new incoming call starts clean
    final next = c.merge({'call_id': 'c2', 'status': 'ringing', 'endpoints': []});
    expect(next.transcript, isEmpty);
  });
}
