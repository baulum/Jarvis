"""Live activity end to end: the real tool executor (parallel calls, a failing call) and the real hybrid memory
search produce exactly the activity steps the 3D brain renders – with the memory ids that were really found."""

import asyncio
import json
import uuid
from pathlib import Path

import pytest

from jarvis.activity import ActivityPipeline
from jarvis.config import get_settings
from jarvis.events import Event, activity, bus
from jarvis.memory.embeddings import LocalEmbedder
from jarvis.memory.service import MemoryService
from jarvis.memory.vault import Vault
from jarvis.security.auth import Principal
from jarvis.tools.registry import ToolContext, ToolSpec

pytestmark = pytest.mark.integration


@pytest.fixture
async def pipe():
    p = ActivityPipeline(bus)
    q = bus.open_queue(5000)
    yield p, q
    bus.close_queue(q)
    p.close()


def _steps(q: asyncio.Queue[Event]) -> list[dict]:
    out = []
    while not q.empty():
        ev = q.get_nowait()
        if ev.type == "activity.step":
            out.append({**ev.data, "nodes": ev.nodes})
    return out


async def test_parallel_tool_calls_and_failure_through_the_real_executor(stack, pipe) -> None:
    p, q = pipe
    gate = asyncio.Event()

    async def slow(ctx, a):
        await gate.wait()
        return {"ok": True}

    async def broken(ctx, a):
        gate.set()
        raise RuntimeError("login failed: password=hunter2 for sir@example.com")

    stack.registry.register(ToolSpec("t_slow", "slow", {"type": "object"}, slow, "read", "test"))
    stack.registry.register(ToolSpec("t_broken", "broken", {"type": "object"}, broken, "read", "test"))
    sid = uuid.uuid4()
    ctx = ToolContext(principal=Principal(id="sir", role="owner", channel="desktop"), session_id=sid)
    with activity(session_id=str(sid)):
        await bus.publish("chat.user", session_id=str(sid), text="geheim", channel="desktop")
        res = await asyncio.gather(
            stack.executor.execute("t_slow", {"secret": "hunter2"}, ctx),
            stack.executor.execute("t_broken", {}, ctx),
        )
        await bus.publish("chat.done", session_id=str(sid), text="geheim", error=None)
    assert [r.status for r in res] == ["ok", "error"]

    steps = _steps(q)
    tools = [s for s in steps if s["phase"] == "tool"]
    starts = [s for s in tools if s["state"] == "start"]
    ends = {s["tool"]: s for s in tools if s["state"] == "end"}
    assert {s["tool"] for s in starts} == {"t_slow", "t_broken"}
    assert max(s["parallel"] for s in starts) == 2  # both ran at the same time
    assert all(s["parent"] == f"turn:{sid}" for s in tools)
    assert ends["t_slow"]["status"] == "ok" and ends["t_broken"]["status"] == "error"
    assert isinstance(ends["t_broken"]["duration_ms"], int)
    assert "hunter2" not in json.dumps(steps) and "example.com" not in json.dumps(steps)
    assert "password=***" in ends["t_broken"]["error"]
    assert steps[-1]["label"] == "Antwort gesendet" and p.running == {}
    assert p.snapshot()["tools"]["t_broken"]["errors"] == 1


async def test_memory_retrieval_lights_exactly_the_found_notes(tmp_path: Path, pipe) -> None:
    p, q = pipe
    s = get_settings()
    emb = LocalEmbedder(s.paths.data_dir / "models" / "fastembed", s.llm.embedding_model)

    async def llm(*_a):
        return {"facts": []}

    mem = MemoryService(Vault(tmp_path / "vault"), emb, llm)
    mem.vault.ensure()
    a = await mem.remember("Staging-Server", "Der Staging-Server läuft auf Port 8443.", "technical")
    await mem.remember("Lieblingsessen", "Sir mag Spaghetti Carbonara.", "preference")
    _steps(q)  # the stores themselves are checked below

    hits = await mem.search("Auf welchem Port läuft der Staging-Server?", limit=3, expand=False)
    steps = [st for st in _steps(q) if st["phase"] == "memory.retrieve"]
    assert len(steps) == 1
    step = steps[0]
    assert step["nodes"] == [f"memory:{h['id']}" for h in hits]
    assert step["nodes"][0] == f"memory:{a['id']}"
    assert step["count"] == len(hits) and step["purpose"] == "search"
    assert "Staging" not in json.dumps(step) and "Port" not in json.dumps(step)  # no titles, no query

    await mem.remember("Neuer Kunde", "Bäckerei Schmidt aus Ingolstadt.", "customer")
    stored = [st for st in _steps(q) if st["phase"] == "memory.store"]
    assert stored and stored[-1]["label"] == "Speichere neue Erinnerung" and stored[-1]["status"] == "ok"
    assert stored[-1]["nodes"][0].startswith("memory:")
