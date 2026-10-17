"""Notification & calling engine: pure decision rules."""

from datetime import datetime
from pathlib import Path

import pytest

from jarvis.agent.persona import TZ
from jarvis.mobile.alerts import Alert, AlertPolicy, decide
from jarvis.mobile.voice_agent import tool_label

DAY = datetime(2026, 10, 17, 14, 0, tzinfo=TZ)
NIGHT = datetime(2026, 10, 17, 23, 30, tzinfo=TZ)


@pytest.fixture
def policy(tmp_path: Path) -> AlertPolicy:
    return AlertPolicy(tmp_path / "p.json")


def test_defaults_push_not_call(policy: AlertPolicy) -> None:
    assert decide(policy, Alert("task", "Fertig"), DAY).mode == "push"
    assert decide(policy, Alert("system", "Online"), DAY).mode == "silent"


def test_call_only_on_explicit_request_or_rule(policy: AlertPolicy) -> None:
    assert decide(policy, Alert("error", "Deploy kaputt", level="critical"), DAY).mode == "push"
    assert decide(policy, Alert("task", "Website", call_requested=True), DAY).mode == "call"
    policy.update({"categories": {"error": {"mode": "call"}}})
    assert decide(policy, Alert("error", "Deploy kaputt"), DAY).mode == "call"


def test_follow_up_never_calls(policy: AlertPolicy) -> None:
    policy.update({"categories": {"task": {"mode": "call"}}})
    assert decide(policy, Alert("task", "Verpasst", allow_call=False), DAY).mode == "push"


def test_quiet_hours(policy: AlertPolicy) -> None:
    assert decide(policy, Alert("task", "x"), NIGHT).mode == "silent"
    assert decide(policy, Alert("task", "x", call_requested=True), NIGHT).mode == "push"
    assert decide(policy, Alert("error", "x", level="critical", call_requested=True), NIGHT).mode == "call"
    policy.update({"critical_bypasses_quiet_hours": False})
    assert decide(policy, Alert("error", "x", level="critical", call_requested=True), NIGHT).mode == "push"


def test_off_and_calls_disabled(policy: AlertPolicy) -> None:
    policy.update({"categories": {"customer": {"mode": "off"}}, "calls_enabled": False})
    assert decide(policy, Alert("customer", "Anfrage"), DAY).mode == "none"
    assert decide(policy, Alert("customer", "Anfrage", level="critical"), DAY).mode == "push"
    assert decide(policy, Alert("task", "x", call_requested=True), DAY).mode == "push"


def test_policy_validation_and_persistence(tmp_path: Path) -> None:
    p = AlertPolicy(tmp_path / "p.json")
    with pytest.raises(ValueError):
        p.update({"categories": {"task": {"mode": "scream"}}})
    with pytest.raises(ValueError):
        p.update({"quiet_hours": {"start": "25:99"}})
    p.update({"categories": {"reminder": {"mode": "call"}}, "quiet_hours": {"start": "23:00"}})
    again = AlertPolicy(tmp_path / "p.json")
    assert again.mode_for("reminder") == "call" and again.data["quiet_hours"]["start"] == "23:00"


def test_tool_labels_reflect_real_tools() -> None:
    assert tool_label("mcp__jarvis__calendar_list_events") == "Prüfe den Kalender …"
    assert tool_label("memory_search") == "Durchsuche das Gedächtnis …"
    assert tool_label("weird_tool") == "Führe weird_tool aus …"
