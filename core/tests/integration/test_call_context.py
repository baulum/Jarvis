"""A call Jarvis places stores its open question; the call session starts with that question, not with nothing."""

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.db import session_scope
from jarvis.db.models import CallSession
from jarvis.mobile.call_context import call_context_block, load_call_context, open_question
from jarvis.mobile.calls import CallService
from jarvis.mobile.voice_agent import CallVoiceAgent

pytestmark = pytest.mark.integration

QUESTION = "Neue Google-Bewertung von Westside Taxi UG. Soll ich den Antwortentwurf veröffentlichen?"


def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CallService:
    state = SimpleNamespace(
        settings=SimpleNamespace(
            paths=SimpleNamespace(data_dir=tmp_path),
            mobile=SimpleNamespace(calls=SimpleNamespace(ring_timeout_s=30)),
        ),
        personality=SimpleNamespace(owner_title="Sir"),
    )
    svc = CallService(state)  # type: ignore[arg-type]
    monkeypatch.setattr(svc, "_start_agent", lambda *a, **kw: None)  # no LiveKit room in tests
    return svc


def cancel_timers(svc: CallService) -> None:
    for t in svc._ring_timers.values():
        t.cancel()


async def test_ringing_stores_the_question_with_the_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc = service(tmp_path, monkeypatch)
    alert_id = uuid.uuid4()
    res = await svc.ring_owner(
        message=QUESTION,
        reason="alert:task",
        event_key="task.completed:42",
        alert_id=alert_id,
        origin_session_id="chat-1",
        refs={"task_id": "task-42"},
        skip_push=True,
    )
    cancel_timers(svc)
    async with session_scope() as s:
        row = await s.get(CallSession, uuid.UUID(res["call_id"]))
        assert row is not None
        assert open_question(row.context) == QUESTION
        assert row.context["reason"] == "alert:task"
        assert row.context["refs"] == {
            "task_id": "task-42",
            "event_key": "task.completed:42",
            "alert_id": str(alert_id),
            "origin_session_id": "chat-1",
        }


async def test_session_start_loads_the_question_of_its_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc = service(tmp_path, monkeypatch)
    res = await svc.ring_owner(message=QUESTION, reason="agent", skip_push=True)
    cancel_timers(svc)
    agent: CallVoiceAgent = CallVoiceAgent.__new__(CallVoiceAgent)
    agent.call_id = uuid.UUID(res["call_id"])
    agent.metrics = {}
    await agent._load_call_context()
    assert open_question(agent.call_context) == QUESTION
    assert agent.metrics["open_question"] is True
    assert QUESTION in call_context_block(agent.call_context)  # what the agent gets on its first turn


async def test_call_without_stored_context_starts_empty() -> None:
    assert await load_call_context(uuid.uuid4()) == {}  # e.g. a call Sir placed himself: honestly no question
