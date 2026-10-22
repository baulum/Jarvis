"""Chat-Zeitleiste: Ereignisreihenfolge beim Streaming, parallele Tool-Aufrufe, Gedankenprozess, Neuladen."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from jarvis.agent.llm import AgentEvent
from jarvis.agent.timeline import (
    MemoryTimelineStore,
    SeqAllocator,
    TurnRecorder,
    legacy_items,
    load_timeline,
    redact_for_mobile,
    tool_result_view,
    turn_starts_statement,
    upsert_statement,
    window_conditions,
)
from jarvis.tools.artifacts import ArtifactStore

SID = str(uuid.uuid4())


class Bus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, type_: str, **data: Any) -> None:
        self.events.append((type_, json.loads(json.dumps(data, default=str))))

    def items(self) -> list[dict[str, Any]]:
        return [d["event"] for t, d in self.events if t == "chat.event"]


class Clock:
    def __init__(self) -> None:
        self.t = 1_700_000_000_000

    def __call__(self) -> int:
        return self.t

    def tick(self, ms: int) -> None:
        self.t += ms


def _recorder(store: MemoryTimelineStore | None = None, alloc: SeqAllocator | None = None, **kw: Any) -> tuple[TurnRecorder, Bus, Clock, MemoryTimelineStore]:
    store = store or MemoryTimelineStore()
    bus, clock = Bus(), Clock()
    rec = TurnRecorder(store, alloc or SeqAllocator(store), SID, publish=bus, clock=clock, **kw)
    return rec, bus, clock, store


def _mcp(data: Any) -> str:
    return json.dumps({"status": "ok", "tool": "x", "data": data})


async def test_text_and_tools_interleave_in_event_order() -> None:
    rec, bus, clock, store = _recorder()
    await rec.start("Wie komme ich nach Potsdam?", "desktop")
    r1 = await rec.on_agent_event(AgentEvent("delta", "Ich prüfe "))
    await rec.on_agent_event(AgentEvent("delta", "die Route."))
    clock.tick(5)
    await rec.on_agent_event(AgentEvent("tool_started", data={"name": "route_plan", "tool_use_id": "u1", "input": {"start": "Berlin"}}))
    clock.tick(800)
    await rec.on_agent_event(AgentEvent("tool_completed", data={"name": "route_plan", "tool_use_id": "u1", "result": _mcp({"km": 35})}))
    r2 = await rec.on_agent_event(AgentEvent("delta", "35 km, Sir."))
    await rec.finish()

    items, _ = await store.list(SID)
    rows = [(i["kind"], i["data"].get("role") or i["data"].get("name") or i["data"].get("step")) for i in items]
    # user, turn, text segment 1, tool, its process step, text segment 2, summary
    assert rows == [
        ("message", "user"),
        ("turn", None),
        ("message", "assistant"),
        ("tool", "route_plan"),
        ("process", "tool"),
        ("message", "assistant"),
        ("process", "summary"),
    ]
    seqs = [i["seq"] for i in items]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    seg1, tool, seg2 = items[2], items[3], items[5]
    assert seg1["data"]["text"] == "Ich prüfe die Route." and seg1["data"]["status"] == "done"
    assert seg2["data"]["text"] == "35 km, Sir."
    assert tool["data"]["status"] == "ok" and tool["data"]["duration_ms"] == 800
    assert '"start": "Berlin"' in tool["data"]["input"] and '"km": 35' in tool["data"]["result"]
    # deltas carry the id of their segment so clients append without re-mounting
    assert r1["event_id"] == seg1["id"] and r2["event_id"] == seg2["id"]
    assert all(i["turn_id"] == rec.turn_id for i in items)
    turn = items[1]
    assert turn["data"]["status"] == "done" and turn["data"]["reasoning"] is False


async def test_parallel_tools_keep_ids_times_and_positions() -> None:
    rec, bus, clock, store = _recorder()
    await rec.start("Wetter und Termine?", "desktop")
    await rec.on_agent_event(AgentEvent("tool_started", data={"name": "web_search", "tool_use_id": "a"}))
    clock.tick(10)
    await rec.on_agent_event(AgentEvent("tool_started", data={"name": "calendar_list", "tool_use_id": "b"}))
    clock.tick(300)
    # b finishes first, a fails later: results must not be mixed up
    await rec.on_agent_event(AgentEvent("tool_completed", data={"name": "calendar_list", "tool_use_id": "b", "result": _mcp(["Termin"])}))
    clock.tick(700)
    await rec.on_agent_event(AgentEvent("tool_failed", data={"name": "web_search", "tool_use_id": "a", "result": "Zeitüberschreitung", "error": "Zeitüberschreitung"}))
    await rec.finish()

    tools = [i for i in (await store.list(SID))[0] if i["kind"] == "tool"]
    assert [t["data"]["tool_use_id"] for t in tools] == ["a", "b"]  # start order = position
    a, b = tools
    assert a["seq"] < b["seq"]
    assert (a["data"]["status"], b["data"]["status"]) == ("failed", "ok")
    assert a["data"]["started_ts"] < b["data"]["started_ts"] < b["data"]["ended_ts"] < a["data"]["ended_ts"]
    assert (a["data"]["duration_ms"], b["data"]["duration_ms"]) == (1010, 300)
    assert a["data"]["error"] == "Zeitüberschreitung" and "Termin" in b["data"]["result"]
    procs = [i for i in (await store.list(SID))[0] if i["kind"] == "process" and i["data"].get("ref")]
    assert {p["data"]["ref"]: p["data"]["status"] for p in procs} == {a["id"]: "failed", b["id"]: "ok"}
    assert [p["data"]["step"] for p in procs] == ["search", "tool"]


async def test_streaming_publishes_segment_before_its_deltas_and_final_text_on_close() -> None:
    rec, bus, clock, store = _recorder()
    await rec.start("Hallo", "desktop")
    ref = await rec.on_agent_event(AgentEvent("delta", "Guten "))
    await rec.on_agent_event(AgentEvent("delta", "Tag."))
    await rec.finish()
    published = bus.items()
    seg_events = [e for e in published if e["id"] == ref["event_id"]]
    assert seg_events[0]["data"]["status"] == "streaming"  # created (empty) before the first delta is applied
    assert seg_events[-1]["data"] == {"role": "assistant", "text": "Guten Tag.", "status": "done"}
    # streaming segments are persisted once, when they are complete
    stored = await store.get(ref["event_id"])
    assert stored is not None and stored["data"]["text"] == "Guten Tag."


async def test_reasoning_only_when_the_model_sends_it() -> None:
    rec, bus, clock, store = _recorder()
    await rec.start("Plane meinen Tag", "desktop")
    r = await rec.on_agent_event(AgentEvent("thinking", "Der Nutzer möchte "))
    await rec.on_agent_event(AgentEvent("thinking", "einen Plan."))
    await rec.on_agent_event(AgentEvent("delta", "Gern."))
    await rec.finish(model_label="Sonnet 5.5")
    items, _ = await store.list(SID)
    reasoning = [i for i in items if i["data"].get("step") == "reasoning"]
    assert len(reasoning) == 1 and reasoning[0]["id"] == r["event_id"]
    assert reasoning[0]["data"] == {"step": "reasoning", "source": "model", "title": "Überlegung des Modells", "text": "Der Nutzer möchte einen Plan.", "status": "done"}
    assert items[1]["data"]["reasoning"] is True
    summary = items[-1]["data"]
    assert summary["source"] == "summary" and "Modell Sonnet 5.5" in summary["text"]
    assert "keine Modellgedanken" not in summary["text"]

    rec2, _, _, store2 = _recorder()
    await rec2.start("Hallo", "desktop")
    await rec2.on_agent_event(AgentEvent("delta", "Hallo, Sir."))
    await rec2.finish()
    items2, _ = await store2.list(SID)
    assert not [i for i in items2 if i["data"].get("source") == "model"]  # nothing invented
    assert "keine Modellgedanken übermittelt" in items2[-1]["data"]["text"]


async def test_cancel_closes_running_tools_and_adds_system_message() -> None:
    rec, bus, clock, store = _recorder()
    await rec.start("Mach was Langes", "desktop")
    await rec.on_agent_event(AgentEvent("tool_started", data={"name": "coding_start_job", "tool_use_id": "c"}))
    clock.tick(50)
    await rec.finish(error="cancelled")
    items, _ = await store.list(SID)
    tool = next(i for i in items if i["kind"] == "tool")
    assert tool["data"]["status"] == "failed" and tool["data"]["error"] == "Abgebrochen"
    system = [i for i in items if i["data"].get("role") == "system"]
    assert system and system[0]["data"]["text"] == "Antwort wurde unterbrochen."
    assert items[1]["data"]["status"] == "cancelled"
    # late events of a finished turn are ignored instead of corrupting it
    assert await rec.on_agent_event(AgentEvent("delta", "zu spät")) == {}


async def test_route_artifact_is_attached_to_the_tool_item() -> None:
    store_art = ArtifactStore()
    aid = store_art.put("route", {"routes": [{"geometry": [[52.5, 13.4]]}]})
    rec, bus, clock, store = _recorder(artifact_store=store_art)
    await rec.start("Route?", "desktop")
    await rec.on_agent_event(AgentEvent("tool_started", data={"name": "route_plan", "tool_use_id": "r"}))
    await rec.on_agent_event(AgentEvent("tool_completed", data={"name": "route_plan", "tool_use_id": "r", "result": _mcp({"artifact_id": aid})}))
    await rec.finish()
    tool = next(i for i in (await store.list(SID))[0] if i["kind"] == "tool")
    assert tool["data"]["display"]["kind"] == "route"
    assert store_art.take(aid) is None  # consumed exactly once


async def test_generated_image_and_video_are_attached_and_survive_a_reload() -> None:
    store_art = ArtifactStore()
    img = store_art.put("image", {"media": "images", "file": "20261022-102738-53c75e52.png", "prompt": "Leuchtturm"})
    vid = store_art.put("video", {"media": "videos", "file": "20261022-103000-aa11bb22.mp4", "duration_s": 5})
    rec, bus, clock, store = _recorder(artifact_store=store_art)
    await rec.start("Mach ein Bild und ein Video", "desktop")
    for name, use, aid in (("image_generate", "i", img), ("video_generate", "v", vid)):
        await rec.on_agent_event(AgentEvent("tool_started", data={"name": name, "tool_use_id": use}))
        await rec.on_agent_event(
            AgentEvent("tool_completed", data={"name": name, "tool_use_id": use, "result": _mcp({"image_path": "/x", "artifact_id": aid})})
        )
    await rec.finish()
    # the live event carries the attachment …
    live = [e for e in bus.items() if e["kind"] == "tool" and e["data"]["status"] == "ok"]
    assert [e["data"]["display"]["kind"] for e in live] == ["image", "video"]
    # … and so does the stored timeline a reload reads
    tools = [i for i in (await store.list(SID))[0] if i["kind"] == "tool"]
    assert tools[0]["data"]["display"] == {"kind": "image", "media": "images", "file": "20261022-102738-53c75e52.png", "prompt": "Leuchtturm"}
    assert tools[1]["data"]["display"]["file"] == "20261022-103000-aa11bb22.mp4"
    # a failed call shows nothing, even with an artifact id in its text
    rec2, _, _, store2 = _recorder(artifact_store=store_art)
    leftover = store_art.put("image", {"media": "images", "file": "a.png"})
    await rec2.start("Nochmal", "desktop")
    await rec2.on_agent_event(AgentEvent("tool_started", data={"name": "image_generate", "tool_use_id": "f"}))
    await rec2.on_agent_event(AgentEvent("tool_failed", data={"name": "image_generate", "tool_use_id": "f", "result": leftover, "error": "x"}))
    await rec2.finish()
    failed = next(i for i in (await store2.list(SID))[0] if i["kind"] == "tool")
    assert "display" not in failed["data"]


async def test_bus_observations_become_protocol_steps() -> None:
    async def plan_steps(task_id: str) -> list[str]:
        return ["Kunde prüfen", "Rechnung entwerfen"]

    rec, bus, clock, store = _recorder(plan_steps=plan_steps)
    await rec.start("Neuer Kunde", "desktop")
    ev = lambda t, **d: SimpleNamespace(type=t, session_id=SID, data=d)  # noqa: E731
    await rec.on_bus_event(ev("memory.recalled", purpose="auto_recall", hits=[{"title": "Müller", "kind": "customer"}], duration_ms=54))
    await rec.on_bus_event(ev("memory.recalled", purpose="extraction", hits=[]))  # background work: not part of the turn
    await rec.on_bus_event(ev("task.created", task_id=str(uuid.uuid4()), title="Neukunde Müller"))
    await rec.on_bus_event(ev("approval.requested", summary="Kunde in Lexware anlegen"))
    await rec.on_bus_event(SimpleNamespace(type="memory.recalled", session_id="andere", data={"hits": []}))
    await rec.finish()
    steps = [(i["data"]["step"], i["data"]["source"]) for i in (await store.list(SID))[0] if i["kind"] == "process"]
    assert steps == [("memory", "protocol"), ("plan", "protocol"), ("action", "protocol"), ("summary", "summary")]
    plan = next(i for i in (await store.list(SID))[0] if i["data"].get("step") == "plan")
    assert plan["data"]["items"] == ["Kunde prüfen", "Rechnung entwerfen"]


async def test_sequence_continues_after_reload_and_order_survives() -> None:
    store = MemoryTimelineStore()
    rec, _, _, _ = _recorder(store=store)
    await rec.start("eins", "desktop")
    await rec.on_agent_event(AgentEvent("delta", "A"))
    await rec.finish()
    # "restart": a fresh allocator must continue after the stored maximum, never reuse a number
    rec2, _, _, _ = _recorder(store=store, alloc=SeqAllocator(store))
    await rec2.start("zwei", "desktop")
    await rec2.on_agent_event(AgentEvent("delta", "B"))
    await rec2.finish()
    items, more = await store.list(SID)
    assert not more
    texts = [i["data"]["text"] for i in items if i["kind"] == "message"]
    assert texts == ["eins", "A", "zwei", "B"]
    assert [i["seq"] for i in items] == list(range(1, len(items) + 1))
    # paging by whole turns
    last, more = await store.list(SID, turns=1)
    assert more and {i["turn_id"] for i in last} == {rec2.turn_id}
    older, more = await store.list(SID, turns=5, before_seq=last[0]["seq"])
    assert not more and {i["turn_id"] for i in older} == {rec.turn_id}


def _msg(role: str, content: str, minutes: int, **meta: Any) -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(), role=role, content=content, meta=meta, created_at=datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=minutes)
    )


def test_legacy_messages_precede_recorded_events() -> None:
    old = [
        _msg("user", "Alt?", 0),
        _msg("assistant", "Alte Antwort", 1, tools=[{"event": "tool_started", "name": "web_search"}, {"event": "tool_failed", "name": "web_search"}]),
        _msg("user", "Neu?", 2, turn_id="t-new"),
    ]
    recorded = [{"id": "msg_x", "session_id": SID, "turn_id": "t-new", "seq": 1, "ts": 0, "kind": "message", "data": {"role": "user", "text": "Neu?"}}]
    out = load_timeline(SID, recorded, old)
    assert [(i["kind"], i["data"].get("text") or i["data"].get("name")) for i in out] == [
        ("message", "Alt?"),
        ("tool", "web_search"),
        ("message", "Alte Antwort"),
        ("message", "Neu?"),
    ]
    seqs = [i["seq"] for i in out]
    assert seqs == sorted(seqs) and seqs[-2] < 0 < seqs[-1]
    assert out[1]["data"]["status"] == "failed"
    assert out[0]["turn_id"] == out[2]["turn_id"]  # user + answer of one legacy exchange
    assert legacy_items(SID, old, covered_turns={"t-new"})[-1]["data"]["text"] == "Alte Antwort"


def test_postgres_statements_compile() -> None:
    """The SQL of the Postgres store (executed in the integration test once the migration is applied)."""
    from sqlalchemy.dialects import postgresql

    item = {"id": "msg_1", "session_id": SID, "turn_id": SID, "seq": 3, "ts": 1, "kind": "message", "data": {"a": 1}}
    upsert = str(upsert_statement(item).compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (id) DO UPDATE SET data = excluded.data" in upsert
    starts = str(turn_starts_statement(window_conditions(SID, 40), 5).compile(dialect=postgresql.dialect()))
    assert "GROUP BY coalesce(CAST(chat_events.turn_id AS VARCHAR), chat_events.id)" in starts
    assert "chat_events.seq < " in starts and "ORDER BY min(chat_events.seq) DESC" in starts


def test_mobile_redaction_and_result_views() -> None:
    proc = {"kind": "process", "data": {"step": "memory", "items": ["Gesundheit: …"], "title": "Gedächtnis durchsucht"}}
    assert redact_for_mobile(proc)["data"] == {"step": "memory", "title": "Gedächtnis durchsucht", "redacted": True}
    tool = {"kind": "tool", "data": {"name": "memory_search", "result": "geheim"}}
    assert "result" not in redact_for_mobile(tool)["data"]
    pretty, preview = tool_result_view(json.dumps({"status": "ok", "tool": "t", "data": {"a": 1}}))
    assert pretty == '{\n  "a": 1\n}' and preview == "a: 1"
    pretty, preview = tool_result_view("kein json")
    assert pretty == preview == "kein json"
