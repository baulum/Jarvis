"""Notification & calling engine against the test DB with fake push/call backends."""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.events import Event
from jarvis.mobile import alerts as alerts_mod
from jarvis.mobile.alerts import Alert, AlertEngine
from jarvis.mobile.push import PushResult

pytestmark = pytest.mark.integration


class FakePush:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any], bool]] = []

    async def send_all(self, kind: str, payload: dict[str, Any], *, high_priority: bool = False, ttl_s: int = 0) -> list[PushResult]:
        self.sent.append((kind, payload, high_priority))
        return [PushResult("dev-1", True)]


class FakeCalls:
    def __init__(self, blocker: str | None = None) -> None:
        self.blocker = blocker
        self.rang: list[dict[str, Any]] = []

    async def call_blocker(self, event_key: str | None) -> str | None:
        return self.blocker

    async def ring_owner(self, **kw: Any) -> dict[str, Any]:
        self.rang.append(kw)
        return {"call_id": "00000000-0000-0000-0000-000000000001"}


def engine(tmp_path: Path, calls: FakeCalls) -> tuple[AlertEngine, FakePush]:
    push = FakePush()
    state = SimpleNamespace(
        settings=SimpleNamespace(paths=SimpleNamespace(data_dir=tmp_path)), push=push, calls=calls
    )
    return AlertEngine(state), push  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def daytime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(alerts_mod.AlertPolicy, "quiet", lambda self, now=None: False)


async def test_push_and_dedupe(tmp_path: Path) -> None:
    eng, push = engine(tmp_path, FakeCalls())
    r = await eng.dispatch(Alert("task", "Website fertig", "Sir, …", event_key="task.completed:1"))
    assert r["decision"] == "push" and r["delivered"] == ["dev-1"]
    assert push.sent[0][1]["actions"] == ["open", "reply", "call"]
    again = await eng.dispatch(Alert("task", "Website fertig", event_key="task.completed:1"))
    assert again["decision"] == "duplicate" and len(push.sent) == 1


async def test_explicit_call_rings_once(tmp_path: Path) -> None:
    calls = FakeCalls()
    eng, push = engine(tmp_path, calls)
    r = await eng.dispatch(Alert("task", "Fertig", call_requested=True, call_message="Die Website ist fertig.", event_key="watch:1"))
    assert r["decision"] == "call" and calls.rang[0]["message"] == "Die Website ist fertig." and not push.sent


async def test_call_falls_back_to_notification_when_not_possible(tmp_path: Path) -> None:
    calls = FakeCalls(blocker="Agent (Claude Code) nicht verfügbar")
    eng, push = engine(tmp_path, calls)
    r = await eng.dispatch(Alert("error", "Deploy fehlgeschlagen", call_requested=True, event_key="x:1"))
    assert r["decision"] == "push" and "nicht verfügbar" in r["reason"]
    assert not calls.rang and push.sent[0][2] is False


async def test_missed_call_follow_up_never_rings(tmp_path: Path) -> None:
    calls = FakeCalls()
    eng, push = engine(tmp_path, calls)
    eng.policy.update({"categories": {"task": {"mode": "call"}}})
    await eng._on_call_ended(Event("call.ended", {"call_id": "c1", "status": "missed", "direction": "to_owner", "message": "Die Website ist fertig."}))
    assert not calls.rang
    assert push.sent and push.sent[0][1]["title"] == "Verpasster Anruf von J.A.R.V.I.S."


async def test_snooze_brings_it_back(tmp_path: Path) -> None:
    eng, push = engine(tmp_path, FakeCalls())
    r = await eng.dispatch(Alert("reminder", "Kunden zurückrufen", event_key="r:1"))
    import uuid
    from datetime import UTC, timedelta

    from jarvis.db import session_scope
    from jarvis.db.models import AlertDispatch

    await eng.snooze(uuid.UUID(r["id"]), 5)
    async with session_scope() as s:
        row = await s.get(AlertDispatch, uuid.UUID(r["id"]))
        assert row is not None and row.status == "snoozed"
        row.remind_at = datetime.now(UTC) - timedelta(seconds=1)  # due now
    await eng._due_snoozes()
    assert len(push.sent) == 2 and push.sent[1][1]["title"] == "Kunden zurückrufen"
