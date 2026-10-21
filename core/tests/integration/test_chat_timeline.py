"""Ganzer Chat-Turn über den Orchestrator mit Postgres: Reihenfolge, parallele Tools, Neuladen, Checklisten."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

from jarvis.agent.llm import AgentEvent
from jarvis.agent.orchestrator import Orchestrator
from jarvis.agent.timeline import MemoryTimelineStore, SeqAllocator
from jarvis.db import session_scope
from jarvis.security.auth import Principal

pytestmark = pytest.mark.integration
OWNER = Principal(id="desk", role="owner", channel="device", device_id=uuid.uuid4())


@pytest.fixture(params=["postgres", "memory"])
async def make_orch(request: pytest.FixtureRequest) -> Any:
    """Orchestrator factory; every call is a "new process" sharing the same timeline storage."""
    shared = MemoryTimelineStore()
    if request.param == "postgres":
        async with session_scope() as s:
            exists = (await s.execute(text("SELECT to_regclass('chat_events')"))).scalar_one_or_none()
        if exists is None:
            pytest.skip("Tabelle chat_events fehlt in jarvis_test – Migration f2a3b4c5d6e7 anwenden (alembic upgrade head)")

    def make() -> Orchestrator:
        orch = Orchestrator(_state())  # type: ignore[arg-type]
        if request.param == "memory":
            orch.timeline_store = shared
            orch.seq = SeqAllocator(shared)
        return orch

    return make


def _mcp(data: Any) -> str:
    return json.dumps({"status": "ok", "tool": "x", "data": data})


class FakeAgent:
    """Claude stream: text, two parallel tools (the second finishes first), more text."""

    last_error = None

    def available(self) -> bool:
        return True

    async def chat_stream(self, sid: Any, resume: Any, text_: str, ctx: str, model: Any) -> AsyncIterator[AgentEvent]:
        yield AgentEvent("run_started", data={"run_id": "r1"})
        yield AgentEvent("delta", "Einen Moment, ")
        yield AgentEvent("delta", "ich sehe nach.")
        yield AgentEvent("tool_started", data={"name": "calendar_list", "tool_use_id": "a", "input": {"days": 1}})
        yield AgentEvent("tool_started", data={"name": "web_search", "tool_use_id": "b", "input": {"q": "Wetter"}})
        await asyncio.sleep(0.02)
        yield AgentEvent("tool_completed", data={"name": "web_search", "tool_use_id": "b", "result": _mcp("Sonne")})
        await asyncio.sleep(0.02)
        yield AgentEvent("tool_completed", data={"name": "calendar_list", "tool_use_id": "a", "result": _mcp([])})
        yield AgentEvent("delta", "- [ ] Schirm\n- [x] Kalender")
        yield AgentEvent("assistant_completed", "- [ ] Schirm\n- [x] Kalender")
        yield AgentEvent("completed", data={"session_id": "claude-1"})


def _state() -> SimpleNamespace:
    async def context_for(*a: Any, **k: Any) -> str:
        return ""

    async def get_task(_tid: Any) -> None:
        return None

    return SimpleNamespace(
        settings=SimpleNamespace(session=SimpleNamespace(idle_timeout_minutes=30)),
        agent=FakeAgent(),
        hermes=None,
        kill_switch=SimpleNamespace(engaged=False),
        model_router=SimpleNamespace(choose=lambda *a: ("sonnet", "Standard")),
        personality=SimpleNamespace(error_phrases=["Das hat leider nicht funktioniert, Sir."]),
        memory=SimpleNamespace(context_for=context_for, enqueue_extraction=lambda *a, **k: None),
        tasks=SimpleNamespace(get_task=get_task),
    )


async def test_turn_is_persisted_in_event_order_and_survives_reload(make_orch: Any) -> None:
    orch = make_orch()
    res = await orch.handle(OWNER, "desktop", "Brauche ich heute einen Schirm?")
    assert res.error is None

    # "reload": a new orchestrator (new process) reads the timeline back from storage
    fresh = make_orch()
    out = await fresh.timeline(res.session_id)
    events = out["events"]
    assert not out["has_more"]
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and seqs[0] == 1 and len(set(seqs)) == len(seqs)
    shown = [
        (e["kind"], e["data"].get("role") or e["data"].get("name") or e["data"].get("step"))
        for e in events
        if e["kind"] != "turn"
    ]
    assert shown == [
        ("message", "user"),
        ("process", "model"),
        ("message", "assistant"),
        ("tool", "calendar_list"),
        ("process", "tool"),
        ("tool", "web_search"),
        ("process", "search"),
        ("message", "assistant"),
        ("process", "summary"),
    ]
    tools = {e["data"]["tool_use_id"]: e["data"] for e in events if e["kind"] == "tool"}
    assert tools["b"]["ended_ts"] <= tools["a"]["ended_ts"]  # parallel: b finished first, kept apart
    assert "Sonne" in tools["b"]["result"] and tools["a"]["status"] == tools["b"]["status"] == "ok"
    texts = [e["data"]["text"] for e in events if e["data"].get("role") == "assistant"]
    assert texts == ["Einen Moment, ich sehe nach.", "- [ ] Schirm\n- [x] Kalender"]
    turn = next(e for e in events if e["kind"] == "turn")
    assert turn["data"]["status"] == "done" and turn["data"]["model"] == "sonnet"
    assert all(e["turn_id"] == turn["turn_id"] for e in events)

    # checklist state is stored in the message and comes back after a reload
    last = next(e for e in reversed(events) if e["data"].get("role") == "assistant")
    assert await fresh.set_check(res.session_id, last["id"], 0, True)
    again = await make_orch().timeline(res.session_id)
    stored = next(e for e in again["events"] if e["id"] == last["id"])
    assert stored["data"]["checks"] == {"0": True}
    assert not await fresh.set_check(uuid.uuid4(), last["id"], 0, True)  # other conversation: refused

    # the next turn continues the sequence after the reload
    res2 = await fresh.handle(OWNER, "desktop", "Danke", session_id=res.session_id)
    events2 = (await fresh.timeline(res2.session_id))["events"]
    assert [e["seq"] for e in events2] == list(range(1, len(events2) + 1))
    assert events2[len(events)]["data"] == {"role": "user", "text": "Danke", "status": "done", "channel": "desktop"}
    page = await fresh.timeline(res2.session_id, turns=1)
    assert page["has_more"] and page["events"][0]["data"].get("text") == "Danke"
