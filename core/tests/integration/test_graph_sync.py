"""Live graph: built from real memory/task/coding rows; invalidated by events so changes show up immediately."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.config import get_settings, load_permissions
from jarvis.db import session_scope
from jarvis.db.models import CodingJob, Task
from jarvis.events import bus
from jarvis.graph import GraphBuilder
from jarvis.memory.embeddings import LocalEmbedder
from jarvis.memory.service import MemoryService
from jarvis.memory.vault import Vault
from jarvis.tools.registry import ToolRegistry, ToolSpec

pytestmark = pytest.mark.integration


async def _noop(ctx, a):
    return None


@pytest.fixture
async def state(tmp_path: Path):
    s = get_settings()
    emb = LocalEmbedder(s.paths.data_dir / "models" / "fastembed", s.llm.embedding_model)

    async def llm(*_a):
        return {"facts": []}

    mem = MemoryService(Vault(tmp_path / "vault"), emb, llm)
    mem.vault.ensure()
    reg = ToolRegistry(load_permissions())
    reg.register(ToolSpec("lexware_invoice_create", "Rechnung", {"type": "object"}, _noop, "medium", "lexware"))
    conn = SimpleNamespace(active=True, manifest=SimpleNamespace(name="Lexware Office", category="business"))
    return SimpleNamespace(
        memory=mem,
        registry=reg,
        connectors=SimpleNamespace(connectors={"lexware": conn}),
        skills=SimpleNamespace(enabled_names=lambda: ["high-end-visual-design"]),
        mcp_hub=SimpleNamespace(status=lambda: [{"name": "github", "connected": True}]),
    )


async def test_graph_contains_all_node_types_with_real_relations(state) -> None:
    mem = state.memory
    cust = await mem.remember("Westside Taxi UG", "Taxiunternehmen aus Ingolstadt.", "customer",
                              relations=[{"relation": "Geschäftsführer", "target": "Ali Demir", "kind": "person"}])
    proj = await mem.remember("Westside Taxi Website", "Website mit Tarifrechner.", "project",
                              entities=["Westside Taxi UG"])
    async with session_scope() as s:
        task = Task(title="Neukunde Westside Taxi Website", kind="new_customer_project", status="running")
        job = CodingJob(title="Westside Taxi Website", prompt="p", repo_path="/tmp/westside-taxi-website", status="running")
        s.add_all([task, job])
        await s.flush()
        task_id, job_id = task.id, job.id

    g = await GraphBuilder(state).build()
    ids = {n["id"]: n for n in g["nodes"]}
    edges = {(e["source"], e["target"], e["relation"]) for e in g["edges"]} | {
        (e["target"], e["source"], e["relation"]) for e in g["edges"]
    }
    m_c, m_p = f"memory:{cust['id']}", f"memory:{proj['id']}"
    assert ids[m_c]["kind"] == "customer" and ids[m_p]["kind"] == "project"
    assert ids[m_c]["meta"]["access_count"] == 0 and "last_accessed_at" in ids[m_c]["meta"]  # usage for the brain
    assert ids["entity:ali demir"]["kind"] == "person"
    assert (m_c, "entity:ali demir", "Geschäftsführer") in edges
    assert (m_p, m_c, "betrifft") in edges  # entity reference resolved to the customer note
    assert ("tool:lexware_invoice_create", "connector:lexware", "gehört zu") in edges
    assert ("skill:high-end-visual-design", "agent:jarvis", "kann") in edges
    assert ids["connector:mcp:github"]["kind"] == "connector"
    assert (f"coding:{job_id}", "agent:coding", "Auftrag") in edges
    assert (f"coding:{job_id}", m_p, "Projekt") in edges
    assert (f"task:{task_id}", m_p, "betrifft") in edges
    assert all(e["source"] in ids and e["target"] in ids for e in g["edges"])


async def test_graph_cache_invalidated_by_memory_event(state) -> None:
    builder = GraphBuilder(state)
    first = await builder.build()
    r = await state.memory.remember("Notiz Glasfaser", "Glasfaseranschluss kommt im November.", "note")
    await bus.publish("memory.changed", id=r["id"], change="added")  # (remember publishes too)
    second = await builder.build()
    assert second["version"] > first["version"]
    assert any(n["id"] == f"memory:{r['id']}" for n in second["nodes"])
