"""Call availability and ringing against the test DB with fake push/LiveKit/agent – no real call is placed."""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.db import session_scope
from jarvis.db.models import CallSession
from jarvis.mobile.calls import CallService
from jarvis.mobile.push import PushResult
from jarvis.mobile.tools import mobile_tools
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext

pytestmark = pytest.mark.integration
CTX = ToolContext(principal=None)  # type: ignore[arg-type]  # the call tools never read it


class Healthy:
    async def healthy(self) -> bool:
        return True


class FakePush:
    def __init__(self, targets: list[str]) -> None:
        self._targets = targets
        self.fcm = SimpleNamespace(configured=lambda: True)
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def targets(self) -> list[str]:
        return self._targets

    async def send_all(self, kind: str, payload: dict[str, Any], **_: Any) -> list[PushResult]:
        self.sent.append((kind, payload))
        return [PushResult(t, True) for t in self._targets]


class FakeAgent:
    async def hangup(self, reason: str = "agent") -> None:
        pass


def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, targets: list[str]) -> tuple[CallService, FakePush]:
    push = FakePush(targets)
    state = SimpleNamespace(
        settings=SimpleNamespace(
            paths=SimpleNamespace(data_dir=tmp_path),
            mobile=SimpleNamespace(
                enabled=True,
                calls=SimpleNamespace(ring_timeout_s=30, max_calls_per_hour=3, min_minutes_between_calls=5),
            ),
        ),
        livekit=Healthy(),
        stt=Healthy(),
        agent=SimpleNamespace(available=lambda: True),
        kill_switch=SimpleNamespace(engaged=False),
        push=push,
        tts=SimpleNamespace(status=_tts_status),
        personality=SimpleNamespace(greetings={}, owner_title="Sir"),
    )
    svc = CallService(state)  # type: ignore[arg-type]

    def fake_agent(call_id: uuid.UUID, room: str, greeting: str, join_timeout: float) -> FakeAgent:
        agent = FakeAgent()
        svc.agents[call_id] = agent  # type: ignore[assignment]
        return agent

    monkeypatch.setattr(svc, "_start_agent", fake_agent)
    return svc, push


async def _tts_status() -> dict[str, Any]:
    return {"ok": True}


async def _stuck_call(status: str, age: timedelta) -> uuid.UUID:
    call_id = uuid.uuid4()
    async with session_scope() as s:
        s.add(
            CallSession(
                id=call_id,
                direction="to_owner",
                room=f"jarvis-{call_id.hex[:12]}",
                status=status,
                reason="test",
                message="alt",
                created_at=datetime.now(UTC) - age,
            )
        )
    return call_id


def _cancel_timers(svc: CallService) -> None:
    for t in svc._ring_timers.values():
        t.cancel()


async def test_no_phone_gives_a_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc, _ = service(tmp_path, monkeypatch, targets=[])
    av = await svc.availability()
    assert not av["can_ring_owner"] and av["ring_problems"] == ["kein gekoppeltes Smartphone mit Push-Registrierung"]


async def test_busy_line_is_named_as_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc, _ = service(tmp_path, monkeypatch, targets=["dev-1"])
    call_id = await _stuck_call("active", timedelta(minutes=2))
    svc.agents[call_id] = FakeAgent()  # type: ignore[assignment]  # a real, live call
    av = await svc.availability()
    assert not av["can_ring_owner"] and av["ring_problems"] == ["es läuft bereits ein Anruf"]
    tools = {t.name: t for t in mobile_tools(SimpleNamespace(calls=svc))}  # type: ignore[arg-type]
    res = await tools["mobile_call_when"].handler(
        CTX, {"trigger": {"type": "time", "at": "2999-01-01T09:00:00"}, "message": "Test"}
    )
    assert "()" not in res["note"] and "es läuft bereits ein Anruf" in res["note"]


async def test_orphaned_call_no_longer_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc, push = service(tmp_path, monkeypatch, targets=["dev-1"])
    stuck = await _stuck_call("ringing", timedelta(hours=3))  # no voice agent in this process
    av = await svc.availability()
    assert av["can_ring_owner"] and not av["busy"]
    async with session_scope() as s:
        row = await s.get(CallSession, stuck)
        assert row is not None and row.status == "missed" and row.end_reason == "orphaned"
    tools = {t.name: t for t in mobile_tools(SimpleNamespace(calls=svc))}  # type: ignore[arg-type]
    res = await tools["mobile_call_owner"].handler(CTX, {"message": "Die Website ist fertig."})
    _cancel_timers(svc)
    assert res["devices"] == ["dev-1"] and push.sent[0][0] == "call"


async def test_fresh_call_without_agent_still_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc, _ = service(tmp_path, monkeypatch, targets=["dev-1"])
    await _stuck_call("ringing", timedelta(seconds=5))  # agent may not be registered yet
    assert (await svc.availability())["busy"]
    tools = {t.name: t for t in mobile_tools(SimpleNamespace(calls=svc))}  # type: ignore[arg-type]
    with pytest.raises(ToolUserError, match="es läuft bereits ein Anruf"):
        await tools["mobile_call_owner"].handler(CTX, {"message": "Hallo Sir"})


# ---------------------------------------------------------------------------------------- Anrufstil beim Klingeln
def _capture_agent(svc: CallService, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    greetings: list[str] = []

    def fake_agent(call_id: uuid.UUID, room: str, greeting: str, join_timeout: float) -> FakeAgent:
        greetings.append(greeting)
        agent = FakeAgent()
        svc.agents[call_id] = agent  # type: ignore[assignment]
        return agent

    monkeypatch.setattr(svc, "_start_agent", fake_agent)
    return greetings


TECHNICAL = "Job 1963367e ist fertig. Commit a3f9c21 liegt in /srv/app/main.py. Mehr gibt es nicht. Auch sonst nichts."


async def test_call_greeting_and_ring_subject_are_speakable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc, push = service(tmp_path, monkeypatch, targets=["dev-1"])
    greetings = _capture_agent(svc, monkeypatch)
    res = await svc.ring_owner(message=TECHNICAL, reason="agent", explicit=True)
    _cancel_timers(svc)
    spoken = greetings[0]
    for token in ("1963367e", "a3f9c21", "/srv", "main.py"):
        assert token not in spoken
    assert "Der Auftrag ist fertig" in spoken
    assert spoken.count(".") <= 4  # greeting + at most three sentences
    subject = push.sent[0][1]["subject"]
    assert subject.startswith("Der Auftrag ist fertig") and "1963367e" not in subject
    # no location service in this state: honest "unknown" with a note for the agent's answer, nothing technical
    assert res["motion"] == "unknown" and "weißt du nicht" in res["motion_note"]


async def test_call_on_the_move_is_announced_in_one_sentence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC

    from jarvis.mobile.location import LocationStore

    svc, push = service(tmp_path, monkeypatch, targets=["dev-1"])
    greetings = _capture_agent(svc, monkeypatch)
    loc = LocationStore(push)  # type: ignore[arg-type]
    dev = uuid.uuid4()
    loc.update(dev, 52.52, 13.405, 10, datetime.now(UTC) - timedelta(minutes=4))
    loc.update(dev, 52.52 + 2000 / 111_195, 13.405, 10, datetime.now(UTC) - timedelta(seconds=5))
    svc.state.location = loc  # type: ignore[attr-defined]
    res = await svc.ring_owner(message=TECHNICAL, reason="agent", explicit=True)
    _cancel_timers(svc)
    assert res["motion"] == "yes" and "motion_note" not in res
    assert greetings[0].endswith("Der Auftrag ist fertig.") and "Mehr gibt es nicht" not in greetings[0]
    assert svc.agents[uuid.UUID(res["call_id"])].motion.value == "yes"  # type: ignore[attr-defined]


async def test_local_device_test_call_skips_the_movement_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc, push = service(tmp_path, monkeypatch, targets=[])
    _capture_agent(svc, monkeypatch)

    class Boom:
        def latest(self) -> None:
            raise AssertionError("must not be asked")

    svc.state.location = Boom()  # type: ignore[attr-defined]
    res = await svc.ring_owner(message="Test", reason="test", skip_push=True)
    _cancel_timers(svc)
    assert res["push"].startswith("skipped") and not push.sent
