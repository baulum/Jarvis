"""Unified event envelope: source, session/task attribution via activity context, graph nodes."""

import asyncio

from jarvis.events import EventBus, activity, derive_nodes, make_event


def test_envelope_fields_and_source() -> None:
    ev = make_event("tool.started", {"tool": "lexware_invoice_create", "connector": "lexware", "session_id": "s1"})
    d = ev.to_dict()
    assert d["source"] == "tool" and d["session_id"] == "s1" and d["seq"] > 0
    assert d["nodes"] == ["tool:lexware_invoice_create", "connector:lexware", "session:s1"]
    assert make_event("tool.started", {"tool": "x_y_z", "connector": "mcp"}).source == "mcp"
    assert make_event("tool.started", {"tool": "browser_open", "connector": "browser"}).source == "browser"
    assert make_event("coding.updated", {"job_id": "j1"}).nodes == ["agent:coding", "coding:j1"]
    assert make_event("schedule.ran", {"id": "x"}).source == "scheduler"
    assert make_event("voice.status", {"state": "listening"}).nodes == ["agent:voice"]


def test_activity_context_attributes_nested_events() -> None:
    with activity(session_id="s9", task_id="t1"):
        with activity(tool="memory_search"):
            ev = make_event("memory.recalled", {"id": "m1"})
        outer = make_event("task.step", {})
    assert ev.session_id == "s9" and ev.task_id == "t1" and "memory:m1" in ev.nodes and "session:s9" in ev.nodes
    assert outer.nodes == ["task:t1"]
    assert make_event("memory.recalled", {}).session_id is None  # context is gone outside the block


def test_seq_is_monotonic_and_explicit_nodes_first() -> None:
    a = make_event("x.y", {})
    b = make_event("x.y", {}, nodes=["memory:1", "memory:1"])
    assert b.seq > a.seq and b.nodes == ["memory:1"]


def test_derive_nodes_pending_memory_has_no_node() -> None:
    assert derive_nodes("memory.pending", {"id": "p"}, None, None) == []


async def test_bus_publish_with_nodes_reaches_queue() -> None:
    bus = EventBus()
    q = bus.open_queue()
    await bus.publish("memory.recalled", nodes=["memory:a"], hits=[])
    ev = await asyncio.wait_for(q.get(), 1)
    assert ev.nodes == ["memory:a"] and "nodes" not in ev.data
