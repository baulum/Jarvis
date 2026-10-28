"""Live activity pipeline of the 3D brain: real events → filtered activity steps, resync, error states."""

import asyncio
import json
from types import SimpleNamespace

from jarvis.activity import ActivityPipeline, redact, tool_label
from jarvis.api.realtime import _visible
from jarvis.events import Event, EventBus, activity

SECRETS = [
    "hunter2",
    "sk-live-abcdefghijklmnop1234",
    "ghp_0123456789abcdefABCDEF",
    "sir@example.com",
    "+49 170 1234567",
    "DE89 3704 0044 0532 0130 00",
    "Geheimprojekt Phoenix",  # private text of the user / memory titles / queries
    "Rechnung an Müller",
]


async def _drain(q: asyncio.Queue[Event]) -> list[dict]:
    out = []
    while not q.empty():
        ev = q.get_nowait()
        if ev.type == "activity.step":
            out.append({**ev.data, "nodes": ev.nodes})
    return out


def _pipeline() -> tuple[EventBus, ActivityPipeline, asyncio.Queue[Event]]:
    bus = EventBus()
    pipe = ActivityPipeline(bus)
    return bus, pipe, bus.open_queue(5000)


async def _turn(bus: EventBus, sid: str = "s1") -> None:
    """One real-shaped turn: request, recall, model, streaming, two parallel tools (one fails), answer."""
    with activity(session_id=sid):
        await bus.publish(
            "chat.user",
            session_id=sid,
            text="Erstelle die Rechnung an Müller, Geheimprojekt Phoenix",
            channel="desktop",
        )
        await bus.publish(
            "memory.recalled",
            nodes=["memory:m1", "memory:m2"],
            purpose="context",
            query="Geheimprojekt Phoenix",
            via=None,
            duration_ms=12.4,
            semantic=True,
            hits=[
                {"id": "m1", "title": "Geheimprojekt Phoenix", "kind": "project", "score": 0.91},
                {"id": "m2", "title": "Rechnung an Müller", "kind": "customer", "score": 0.5},
            ],
        )
        await bus.publish("chat.model", session_id=sid, model="sonnet", reason="auto")
        await bus.publish("chat.thinking", session_id=sid, text="Ich denke über Geheimprojekt Phoenix nach")
        for _ in range(3):
            await bus.publish("chat.delta", session_id=sid, text="Geheimprojekt Phoenix …")
        await bus.publish(
            "tool.started",
            tool="memory_search",
            connector="memory",
            call_id="c1",
            risk="read",
            actor="owner",
            session_id=sid,
            args={"query": "Geheimprojekt Phoenix"},
        )
        await bus.publish(
            "tool.started",
            tool="lexware_create_invoice_draft",
            connector="lexware",
            call_id="c2",
            risk="medium",
            actor="owner",
            session_id=sid,
            args={"password": "hunter2"},
        )
        with activity(tool="memory_search"):
            await bus.publish(
                "memory.recalled",
                nodes=["memory:m3"],
                purpose="search",
                query="Phoenix",
                via="memory_search",
                duration_ms=4,
                hits=[{"id": "m3", "title": "Phoenix", "kind": "note", "score": 0.7}],
            )
        await bus.publish(
            "tool.finished",
            tool="memory_search",
            connector="memory",
            call_id="c1",
            status="ok",
            duration_ms=40,
            actor="owner",
            message="",
            session_id=sid,
        )
        await bus.publish(
            "tool.finished",
            tool="lexware_create_invoice_draft",
            connector="lexware",
            call_id="c2",
            status="error",
            duration_ms=1200,
            actor="owner",
            session_id=sid,
            message="401 password=hunter2 token: sk-live-abcdefghijklmnop1234 for sir@example.com, call +49 170 1234567, "
            "IBAN DE89 3704 0044 0532 0130 00 ghp_0123456789abcdefABCDEF",
        )
        await bus.publish("chat.delta", session_id=sid, text="Fertig.")
        await bus.publish(
            "chat.done", session_id=sid, text="Rechnung an Müller erstellt", error=None, provider="claude"
        )


async def test_turn_becomes_ordered_steps_with_parents_and_parallel_tools() -> None:
    bus, pipe, q = _pipeline()
    await _turn(bus)
    steps = await _drain(q)
    seq = [(s["phase"], s["state"]) for s in steps]
    assert seq == [
        ("request", "start"),
        ("memory.retrieve", "end"),
        ("llm", "progress"),
        ("response", "progress"),  # first token
        ("tool", "start"),
        ("tool", "start"),
        ("memory.retrieve", "end"),
        ("tool", "end"),
        ("tool", "end"),
        ("response", "progress"),  # the model writes again after the tool results
        ("response", "end"),
    ]
    by = {(s["phase"], s.get("tool"), s["state"]): s for s in steps}
    assert steps[0]["label"] == "Analysiere Anfrage" and steps[0]["nodes"] == ["agent:jarvis", "session:s1"]
    recall = steps[1]
    assert recall["label"] == "Durchsuche Erinnerungen" and recall["nodes"] == ["memory:m1", "memory:m2"]
    assert recall["parent"] == "turn:s1" and recall["count"] == 2
    assert [h["id"] for h in recall["hits"]] == ["m1", "m2"] and recall["hits"][0]["kind"] == "project"
    assert steps[2]["model"] == "sonnet" and steps[3]["label"] == "Generiere Antwort"
    first, second = by[("tool", "memory_search", "start")], by[("tool", "lexware_create_invoice_draft", "start")]
    assert first["parallel"] == 1 and second["parallel"] == 2  # two streams at once
    assert first["parent"] == "turn:s1" and first["label"] == "Durchsuche Erinnerungen"
    assert first["nodes"] == ["tool:memory_search", "connector:memory"]
    assert steps[6]["parent"] == "call:c1"  # the lookup inside memory_search belongs to that call
    failed = by[("tool", "lexware_create_invoice_draft", "end")]
    assert failed["status"] == "error" and failed["duration_ms"] == 1200 and failed["parent"] == "turn:s1"
    done = steps[-1]
    assert done["status"] == "ok" and done["label"] == "Antwort gesendet" and done["duration_ms"] >= 0
    assert pipe.running == {}  # everything that started also ended
    stats = pipe.snapshot()["tools"]
    assert stats["lexware_create_invoice_draft"] == {
        "calls": 1,
        "errors": 1,
        "avg_ms": 1200,
        "last_ms": 1200,
        "last_status": "error",
        "last_at": stats["lexware_create_invoice_draft"]["last_at"],
        "running": 0,
    }
    assert [s["seq"] for s in steps] == list(range(1, len(steps) + 1))


async def test_no_private_content_or_credentials_reach_the_stream() -> None:
    bus, pipe, q = _pipeline()
    await _turn(bus)
    steps = await _drain(q)
    blob = json.dumps(steps, ensure_ascii=False) + json.dumps(pipe.snapshot(), ensure_ascii=False)
    for secret in SECRETS:
        assert secret not in blob, secret
    allowed = {
        "phase",
        "state",
        "status",
        "key",
        "label",
        "parent",
        "session_id",
        "task_id",
        "nodes",
        "t",
        "seq",
        "boot",
        "channel",
        "model",
        "tool",
        "connector",
        "risk",
        "parallel",
        "duration_ms",
        "error",
        "purpose",
        "via",
        "hits",
        "count",
        "semantic",
        "change",
        "origin",
        "agent",
        "kind",
    }
    for s in steps:
        assert set(s) <= allowed, set(s) - allowed
        for h in s.get("hits", []):
            assert set(h) == {"id", "kind", "score"}  # ids only – titles stay out
    err = next(s for s in steps if s.get("status") == "error")["error"]
    assert err.startswith("401 password=***") and "***@***" in err


def test_redact() -> None:
    assert "abc.def" not in redact("Authorization: Bearer abc.def")
    assert "abc.def" not in redact("bearer abc.def")
    assert "s3cr3t" not in redact("https://user:s3cr3t@example.org/x")
    assert "eyJ" not in redact("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefgh")
    assert redact("Zeitüberschreitung nach 30s") == "Zeitüberschreitung nach 30s"  # useful errors stay readable
    assert len(redact("x" * 500 + " y", limit=50)) <= 50
    assert redact(None) == ""
    assert tool_label("browser_open") == "Starte Browser"
    assert tool_label("shell_run") == "Führe Befehl aus"
    assert tool_label("memory_save") == "Speichere neue Erinnerung"
    assert tool_label("brand_new_tool") == "Nutze Werkzeug brand_new_tool"


async def test_resync_snapshot_since_seq_and_after_restart() -> None:
    bus, pipe, q = _pipeline()
    await bus.publish("chat.user", session_id="s1", text="hi", channel="voice")
    await bus.publish("tool.started", tool="web_search", connector="browser", call_id="c9", session_id="s1")
    snap = pipe.snapshot(1, pipe.boot)
    assert snap["complete"] and [s["seq"] for s in snap["steps"]] == [2]
    assert {s["key"] for s in snap["running"]} == {"turn:s1", "call:c9"}
    # client from an older core process: must replace its state with "running"
    other = pipe.snapshot(5, "old-boot")
    assert not other["complete"] and len(other["steps"]) == 2
    # a client that fell behind the ring buffer gets complete=False as well
    small = ActivityPipeline(EventBus(), buffer=3)
    for i in range(6):
        small.record({"phase": "system", "state": "end", "status": "ok", "key": f"k{i}", "nodes": []})
    late = small.snapshot(1, small.boot)
    assert not late["complete"] and [s["seq"] for s in late["steps"]] == [4, 5, 6]
    assert small.snapshot(4, small.boot)["complete"]


async def test_error_states_and_malformed_events() -> None:
    bus, pipe, q = _pipeline()
    # a broken turn
    await bus.publish("chat.user", session_id="s2", text="x", channel="desktop")
    await bus.publish(
        "chat.done", session_id="s2", text="", error="Claude API: api_key=sk-live-abcdefghijklmnop1234 invalid"
    )
    # an interrupted one
    await bus.publish("chat.user", session_id="s3", text="x", channel="desktop")
    await bus.publish("chat.interrupted", session_id="s3")
    # malformed: no call id, odd ids, hits that are not dicts, unknown types – nothing may crash
    await bus.publish("tool.started", tool="x", connector="y")
    await bus.publish("tool.started", tool="evil name; rm -rf /", call_id="c1")
    await bus.publish(
        "memory.recalled", hits=["nope", {"id": "../../etc"}, {"id": "ok1", "kind": "note", "score": "high"}]
    )
    await bus.publish("tool.finished", tool="never_started", call_id="zz", status="error", message=None)
    await bus.publish("something.else", foo="bar")
    await bus.publish("chat.delta", session_id="unknown-session", text="x")
    steps = await _drain(q)
    failed = steps[1]
    assert failed["status"] == "error" and failed["label"] == "Antwort fehlgeschlagen"
    assert "sk-live" not in failed["error"] and "api_key=***" in failed["error"]
    assert steps[3]["status"] == "cancelled" and steps[3]["label"] == "Antwort abgebrochen"
    recall = next(s for s in steps if s["phase"] == "memory.retrieve")
    assert recall["nodes"] == ["memory:ok1"] and recall["hits"][0]["score"] is None
    orphan = next(s for s in steps if s.get("tool") == "never_started")
    assert orphan["state"] == "end" and orphan["status"] == "error" and orphan["error"] == ""
    assert not any(s.get("tool", "").startswith("evil") for s in steps)
    assert len(steps) == 6
    assert pipe.running == {}


async def test_agents_tasks_approvals_memory_store() -> None:
    bus, pipe, q = _pipeline()
    await bus.publish("coding.updated", job_id="j1", status="queued", title="Geheimprojekt Phoenix")
    await bus.publish("coding.updated", job_id="j1", status="running")
    await bus.publish("coding.progress", job_id="j1", type="tool", tool="Bash", text="cat ~/.ssh/id_rsa")
    await bus.publish("coding.progress", job_id="j1", type="tool", tool="Edit")  # throttled
    await bus.publish("coding.progress", job_id="j1", type="text", text="Geheimprojekt Phoenix")  # text: dropped
    await bus.publish("coding.updated", job_id="j1", status="completed", title="Geheimprojekt Phoenix")
    await bus.publish("task.created", task_id="t1", title="Rechnung an Müller", playbook="x")
    await bus.publish("task.step", task_id="t1", step=1, name="Rechnung erstellen", status="running")
    await bus.publish("tool.started", tool="lexware_get_contact", connector="lexware", call_id="c5", task_id="t1")
    await bus.publish("task.updated", task_id="t1", status="failed")
    await bus.publish(
        "approval.requested", approval_id="a1", tool="gmail_send", summary="Mail an sir@example.com", risk="high"
    )
    await bus.publish(
        "approval.decided", approval_id="a1", approved=True, status="executed", tool="gmail_send", summary="x"
    )
    await bus.publish(
        "memory.changed",
        id="m9",
        path="Geheimprojekt Phoenix.md",
        change="added",
        origin="jarvis",
        title="Geheimprojekt Phoenix",
    )
    await bus.publish("memory.pending", id="p1", title="Rechnung an Müller", kind="customer")
    await bus.publish("schedule.ran", id="sc1", name="Rechnung an Müller", status="failed")
    steps = await _drain(q)
    labels = [s["label"] for s in steps]
    assert labels == [
        "Coding-Auftrag eingereiht",
        "Coding-Agent arbeitet",
        "Coding-Agent nutzt Bash",
        "Coding-Auftrag abgeschlossen",
        "Aufgabe gestartet",
        "Schritt: Rechnung erstellen",
        "Arbeite in Lexware",
        "Aufgabe fehlgeschlagen",
        "Wartet auf Freigabe",
        "Freigegeben und ausgeführt",
        "Speichere neue Erinnerung",
        "Erinnerung vorgemerkt – wartet auf Bestätigung",
        "Zeitplan fehlgeschlagen",
    ]
    assert steps[0]["state"] == "start" and steps[1]["state"] == "progress" and steps[3]["state"] == "end"
    assert steps[0]["nodes"] == ["agent:coding", "coding:j1"] and steps[0]["agent"] == "coding"
    assert steps[6]["parent"] == "task:t1"
    assert steps[10]["nodes"] == ["memory:m9"] and steps[10]["change"] == "added"
    assert steps[11]["nodes"] == []  # pending notes are not in the graph
    blob = json.dumps(steps, ensure_ascii=False)
    assert "Phoenix" not in blob and "Müller" not in blob and "id_rsa" not in blob and "example.com" not in blob
    assert pipe.agent_stats["coding"] == {"runs": 1, "errors": 0} and pipe.agent_stats["tasks"]["errors"] == 1
    assert set(pipe.running) == {"call:c5"}  # the task's tool call is still running


async def test_voice_state_changes_only() -> None:
    bus, pipe, q = _pipeline()
    for st in ["listening", "listening", "speaking", "idle"]:
        await bus.publish("voice.status", state=st, muted=False)
    await bus.publish("voice.level", level=0.3, output=0.0)
    steps = await _drain(q)
    assert [s["label"] for s in steps] == ["Hört zu", "Spricht", "Stimme bereit"]


def test_activity_steps_only_for_sirs_devices() -> None:
    ev = Event(type="activity.step", data={})
    assert _visible(ev, SimpleNamespace(role="owner"))
    assert _visible(ev, SimpleNamespace(role="mobile_owner"))
    assert not _visible(ev, SimpleNamespace(role="guest"))
    assert not _visible(ev, SimpleNamespace(role="voice_owner"))
