import 'package:flutter_test/flutter_test.dart';
import 'package:jarvis_mobile/state/agent.dart';

void main() {
  test('quota windows parse the core payload', () {
    final w = QuotaWindow.parse({'utilization': 0.42, 'resets_in_s': 3700, 'stale': false})!;
    expect(w.percent, '42 %');
    expect(w.resetLabel, 'Reset in 1 h 1 min');
    expect(QuotaWindow.parse(null), isNull);
    expect(QuotaWindow.parse({'utilization': 0.5, 'resets_in_s': 600})!.resetLabel, 'Reset in 10 min');
  });

  test('active model: conversation override beats the default; Auto only for the untouched default', () {
    const auto = AgentInfo(defaultModel: 'sonnet', auto: true);
    expect(auto.activeLabel, 'Auto');
    expect(const AgentInfo(defaultModel: 'opus', auto: false).activeLabel, 'Opus');
    expect(const AgentInfo(defaultModel: 'sonnet', auto: true, sessionOverride: 'haiku').activeLabel, 'Haiku');
    expect(
      AgentInfo(fiveHour: const QuotaWindow(0.3, 10, false), sevenDay: const QuotaWindow(0.9, 10, false)).pressure,
      0.9,
    );
  });
}
