"""The live knowledge graph: one node/edge model for the 3D interface, built from real data only.

Node ids (also used in every event envelope, see ``jarvis.events.derive_nodes``):
  memory:<uuid>        a note in the Obsidian vault (kind = its memory kind)
  entity:<name>        a name that is referenced but has no note of its own (kind = typed relation or "entity")
  agent:<name>         jarvis (conversation brain), coding (Claude Code runner), scheduler, voice, telephony
  connector:<id>       an integration (lexware, google, mac, browser, mcp …)
  tool:<name>          a tool the agent can call (registry)
  skill:<name>         an enabled Claude skill
  task:<uuid>          a multi-step task (task engine)
  coding:<uuid>        a Claude Code coding job
  schedule:<uuid>      a scheduled job
  session:<uuid>       a recent conversation
Edges are real relations: wiki links / entities / typed relations of notes, superseded-by, tool ↔ connector,
connector/skill ↔ agent, task/job/schedule ↔ agent, coding job ↔ project note, semantic proximity
(cosine ≥ 0.8 between two notes, labelled "ähnlich").
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from jarvis.db import session_scope
from jarvis.db.models import ChatMessage, ChatSession, CodingJob, MemoryItem, Schedule, Task
from jarvis.events import Event, bus
from jarvis.memory.entities import same_entity
from jarvis.memory.vault import LAYERS

if TYPE_CHECKING:
    from jarvis.app_state import AppState

logger = logging.getLogger(__name__)

AGENTS = {
    "jarvis": ("J.A.R.V.I.S.", "Gesprächsagent (Claude Code)"),
    "coding": ("Claude Code", "Coding-Agent in eigenen Worktrees"),
    "scheduler": ("Zeitplaner", "Wiederkehrende Aufträge"),
    "voice": ("Stimme", "Wake Word, Spracherkennung, Sprachausgabe"),
    "telephony": ("Telefon", "Anrufe über Twilio"),
}
_INVALIDATE = (
    "memory.changed",
    "task.created",
    "task.updated",
    "coding.updated",
    "schedule.updated",
    "session.created",
    "connector.health",
    "skills.changed",
)
_ENTITY_KINDS = {"person", "customer", "company", "project", "tool", "place", "product"}


def _node(nid: str, kind: str, label: str, group: str, weight: float = 0.3, **meta: Any) -> dict[str, Any]:
    return {"id": nid, "kind": kind, "label": label[:120], "group": group, "weight": round(weight, 3), "meta": meta}


class GraphBuilder:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self._cache: dict[str, Any] | None = None
        self._cache_key: tuple[int, int] | None = None
        self._built_at = 0.0
        self.version = 0
        self._lock = asyncio.Lock()
        bus.subscribe("*", self._on_event)

    async def _on_event(self, ev: Event) -> None:
        if ev.type in _INVALIDATE:
            self.version += 1

    async def build(self, limit: int = 1500) -> dict[str, Any]:
        key = (self.version, limit)
        if self._cache is not None and self._cache_key == key and time.monotonic() - self._built_at < 60:
            return self._cache
        async with self._lock:
            if self._cache is not None and self._cache_key == key and time.monotonic() - self._built_at < 60:
                return self._cache
            t0 = time.perf_counter()
            graph = await self._build(limit)
            graph["version"] = self.version
            graph["build_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            self._cache, self._cache_key, self._built_at = graph, key, time.monotonic()
            return graph

    async def _build(self, limit: int) -> dict[str, Any]:
        nodes: dict[str, dict[str, Any]] = {}
        edges: dict[tuple[str, str, str], dict[str, Any]] = {}

        def edge(a: str, b: str, relation: str, weight: float = 1.0) -> None:
            if a == b or a not in nodes or b not in nodes:
                return
            key = (a, b, relation) if a < b else (b, a, relation)
            if key not in edges:
                edges[key] = {"source": a, "target": b, "relation": relation, "weight": round(weight, 3)}

        # ---------------- agents, connectors, tools, skills (what Jarvis can do)
        for name, (label, desc) in AGENTS.items():
            nodes[f"agent:{name}"] = _node(
                f"agent:{name}", "agent", label, "system", 1.0 if name == "jarvis" else 0.7, description=desc
            )
        edge("agent:coding", "agent:jarvis", "delegiert")
        edge("agent:scheduler", "agent:jarvis", "beauftragt")
        edge("agent:voice", "agent:jarvis", "spricht mit")
        edge("agent:telephony", "agent:jarvis", "spricht mit")

        conns = self.state.connectors.connectors
        for cid, conn in conns.items():
            nodes[f"connector:{cid}"] = _node(
                f"connector:{cid}",
                "connector",
                conn.manifest.name,
                "system",
                0.55,
                active=bool(conn.active),
                category=conn.manifest.category,
            )
            edge(f"connector:{cid}", "agent:jarvis", "nutzt", 0.6)
        for srv in self._mcp_servers():
            nid = f"connector:mcp:{srv['name']}"
            nodes[nid] = _node(
                nid,
                "connector",
                f"MCP · {srv['name']}",
                "system",
                0.5,
                active=bool(srv.get("connected")),
                category="mcp",
            )
            edge(nid, "agent:jarvis", "nutzt", 0.6)
        if "connector:coding" in nodes:
            edge("agent:coding", "connector:coding", "läuft über")
        for spec in self.state.registry.all():
            tid = f"tool:{spec.name}"
            nodes[tid] = _node(
                tid,
                "tool",
                spec.name,
                "system",
                0.18,
                risk=self.state.registry.effective_risk(spec),
                connector=spec.connector,
                description=spec.description[:200],
            )
            conn_id = f"connector:{spec.connector}"
            if spec.connector == "mcp":
                srv_name = spec.description.split("]", 1)[0].lstrip("[") if spec.description.startswith("[") else ""
                conn_id = f"connector:mcp:{srv_name}" if f"connector:mcp:{srv_name}" in nodes else conn_id
            edge(tid, conn_id, "gehört zu", 0.8)
        for name in self.state.skills.enabled_names()[:120]:
            sid = f"skill:{name}"
            nodes[sid] = _node(sid, "skill", name, "skills", 0.2)
            edge(sid, "agent:jarvis", "kann", 0.4)

        # ---------------- memory
        items, links = await self.state.memory.graph_rows(limit)
        totals = await self._kind_totals()
        by_title: dict[str, MemoryItem] = {}
        by_path: dict[str, MemoryItem] = {}
        degree: Counter[str] = Counter()
        for link in links:
            degree[link.source_path] += 1
            degree[link.target_title.lower()] += 1
        for it in items:
            by_title[it.title.lower()] = it
            for alias in it.aliases or []:
                by_title.setdefault(str(alias).lower(), it)
            by_path[it.path] = it
            importance = min(
                1.0, 0.25 + 0.06 * (degree[it.path] + degree[it.title.lower()]) + 0.02 * (it.access_count or 0)
            )
            nodes[f"memory:{it.id}"] = _node(
                f"memory:{it.id}",
                it.kind,
                it.title,
                LAYERS.get(it.kind, "semantic"),
                importance,
                layer=LAYERS.get(it.kind, "semantic"),
                path=it.path,
                confidence=it.confidence,
                sensitivity=it.sensitivity,
                updated_at=it.updated_at.isoformat(),
                created_at=it.created_at.isoformat(),
                preview=it.content[:160],
            )
        for link in links:
            src = by_path.get(link.source_path)
            if src is None:
                continue
            tgt = by_title.get(link.target_title.lower())
            if tgt is not None:
                tid = f"memory:{tgt.id}"
            else:
                tid = f"entity:{link.target_title.lower()}"
                kind = link.target_kind if link.target_kind in _ENTITY_KINDS else "entity"
                if tid not in nodes:
                    nodes[tid] = _node(tid, kind, link.target_title, "entities", 0.22, unresolved=True)
                elif nodes[tid]["kind"] == "entity" and kind != "entity":
                    nodes[tid]["kind"] = kind
            edge(f"memory:{src.id}", tid, link.relation or "links_to")
        for it in items:
            if it.superseded_by and f"memory:{it.superseded_by}" in nodes:
                edge(f"memory:{it.id}", f"memory:{it.superseded_by}", "ersetzt durch")
        try:
            for a, b, sim in await self.state.memory.similar_pairs([i.id for i in items[:800]]):
                edge(f"memory:{a}", f"memory:{b}", "ähnlich", sim * 0.5)
        except Exception:
            logger.debug("similarity edges failed", exc_info=True)
        project_titles = [(f"memory:{i.id}", i.title) for i in items if i.kind == "project"]

        # ---------------- work in flight / history: tasks, coding jobs, schedules, sessions
        async with session_scope() as s:
            tasks = (await s.execute(select(Task).order_by(Task.updated_at.desc()).limit(40))).scalars().all()
            jobs = (await s.execute(select(CodingJob).order_by(CodingJob.updated_at.desc()).limit(30))).scalars().all()
            schedules = (await s.execute(select(Schedule).limit(50))).scalars().all()
            sessions = (
                await s.execute(
                    select(ChatSession, func.count(ChatMessage.id))
                    .join(ChatMessage, ChatMessage.session_id == ChatSession.id)
                    .group_by(ChatSession.id)
                    .order_by(ChatSession.last_activity_at.desc())
                    .limit(25)
                )
            ).all()
        for t in tasks:
            nid = f"task:{t.id}"
            nodes[nid] = _node(
                nid, "task", t.title, "work", 0.4, status=t.status, playbook=t.kind, updated_at=t.updated_at.isoformat()
            )
            edge(nid, "agent:jarvis", "Aufgabe", 0.5)
            for pid, title in project_titles:
                if same_entity(title, t.title) or title.lower() in t.title.lower():
                    edge(nid, pid, "betrifft")
        for j in jobs:
            nid = f"coding:{j.id}"
            nodes[nid] = _node(
                nid,
                "coding",
                j.title,
                "work",
                0.45,
                status=j.status,
                branch=j.branch,
                repo=Path(j.repo_path).name,
                updated_at=j.updated_at.isoformat(),
            )
            edge(nid, "agent:coding", "Auftrag", 0.7)
            repo = Path(j.repo_path).name.replace("-", " ")
            for pid, title in project_titles:
                if same_entity(title, repo) or same_entity(title, j.title) or repo.lower() in title.lower():
                    edge(nid, pid, "Projekt")
        for sc in schedules:
            nid = f"schedule:{sc.id}"
            nodes[nid] = _node(
                nid, "schedule", sc.name, "work", 0.3, cron=sc.cron, enabled=sc.enabled, last_status=sc.last_status
            )
            edge(nid, "agent:scheduler", "plant", 0.7)
        for sess, n in sessions:
            nid = f"session:{sess.id}"
            nodes[nid] = _node(
                nid,
                "conversation",
                sess.title or "Gespräch",
                "conversations",
                min(0.5, 0.15 + n * 0.02),
                channel=sess.channel,
                messages=n,
                last_activity_at=sess.last_activity_at.isoformat(),
                live=True,
            )
            edge(nid, "agent:jarvis", "Gespräch", 0.3)
        for t in tasks:
            if t.session_id:
                edge(f"task:{t.id}", f"session:{t.session_id}", "gestartet in", 0.6)
        for it in items:
            src_sid = (it.source or {}).get("session_id")
            if src_sid and f"session:{src_sid}" in nodes:
                edge(f"memory:{it.id}", f"session:{src_sid}", "aus Gespräch", 0.4)

        truncated = {k: max(0, v - sum(1 for i in items if i.kind == k)) for k, v in totals.items()}
        return {
            "nodes": list(nodes.values()),
            "edges": list(edges.values()),
            "counts": dict(Counter(n["kind"] for n in nodes.values())),
            "truncated": {k: v for k, v in truncated.items() if v > 0},
        }

    async def cluster(self, kind: str, offset: int, limit: int = 300) -> dict[str, Any]:
        """Lazy loading: further notes of one kind beyond the initial graph (newest first)."""
        async with session_scope() as s:
            rows = (
                (
                    await s.execute(
                        select(MemoryItem)
                        .where(MemoryItem.status == "active", MemoryItem.kind == kind)
                        .order_by(MemoryItem.updated_at.desc())
                        .offset(offset)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
        return {
            "nodes": [
                _node(
                    f"memory:{i.id}",
                    i.kind,
                    i.title,
                    LAYERS.get(i.kind, "semantic"),
                    0.25,
                    path=i.path,
                    layer=LAYERS.get(i.kind, "semantic"),
                    updated_at=i.updated_at.isoformat(),
                    preview=i.content[:160],
                )
                for i in rows
            ],
            "edges": [],
        }

    async def _kind_totals(self) -> dict[str, int]:
        async with session_scope() as s:
            rows = (
                await s.execute(
                    select(MemoryItem.kind, func.count()).where(MemoryItem.status == "active").group_by(MemoryItem.kind)
                )
            ).all()
        return {k: int(n) for k, n in rows}

    def _mcp_servers(self) -> list[dict[str, Any]]:
        try:
            return list(self.state.mcp_hub.status())
        except Exception:
            return []
