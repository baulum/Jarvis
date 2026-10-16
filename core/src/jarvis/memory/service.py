"""Memory engine: Obsidian vault + Postgres index (pgvector + full text) + automatic knowledge extraction.

Layers (``vault.LAYERS``): the live conversation is the short-term memory (Claude Code session);
episodic memory is one note per finished conversation (``conversation``), semantic memory holds facts
(``technical``/``note``/``document``), plus project, customer (customer/company/person), preference,
decision and procedural memory (process/recurring/commitment).

Write path – every source (agent tool, API, extractor) goes through ``remember``:
  1. entity resolution: same canonical name within the kind group (``entities.same_entity``), a
     matching alias, or an embedding similarity ≥ 0.93 → the existing note is extended, not duplicated;
  2. only sentences that add information are appended (``entities.new_information``);
  3. sensitive content waits for Sir's confirmation; corrections supersede (old note kept, marked).
The extractor additionally sees the closest existing notes and decides create / update (consolidated
rewrite) / supersede (contradiction) / skip, with a confidence; turns are persisted, so a restart or a
temporarily unavailable model does not lose them.

Read path – ``search`` is hybrid: full-text (Postgres tsvector) and vector search run concurrently,
are fused with reciprocal rank fusion, expanded along graph links and re-ranked by similarity,
keyword match, recency, use and confidence. ``context_for`` compresses hits into a short prompt block.
Every lookup publishes ``memory.recalled`` (node ids, scores) for the live graph.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import math
import re
import uuid
import zipfile
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from watchfiles import Change, awatch

from jarvis.db import session_scope
from jarvis.db.models import ChatMessage, ChatSession, MemoryItem, MemoryLink
from jarvis.events import bus, current_activity, spawn
from jarvis.memory.embeddings import LocalEmbedder
from jarvis.memory.entities import (
    best_sentences,
    canonical_name,
    new_information,
    query_terms,
    same_entity,
)
from jarvis.memory.vault import ENTITY_KINDS, KIND_FOLDERS, LAYERS, Note, Vault
from jarvis.security.audit import audit

logger = logging.getLogger(__name__)

DUPLICATE_THRESHOLD = 0.93  # cosine similarity: treat as the same fact
RELATED_THRESHOLD = 0.82  # same title + this similarity: update the existing note
RRF_K = 60
OWNER_ROLES = ("owner", "voice_owner", "telegram_owner", "phone_owner")
KIND_GROUPS: dict[str, tuple[str, ...]] = {
    "customer": ("customer", "company", "person"),
    "company": ("customer", "company", "person"),
    "person": ("person", "customer"),
    "project": ("project",),
}
ENTITY_TYPES = ["person", "customer", "company", "project", "tool", "place", "product", "other"]

_SENSITIVE_PATTERNS = [
    r"\b[A-Z]{2}\d{2}(?:\s?\d{4}){4,7}\s?\d{0,2}\b",  # IBAN
    r"\b(?:\d[ -]?){13,19}\b",  # card-like numbers
    r"\b(passwort|password|kennwort|pin|tan|api[- ]?key|token|secret)\b\s*[:=]",
    r"\b(diagnose|krankheit|medikament|therapie|gesundheit)\w*",
    r"\b(steuer-?id|sozialversicherungsnummer|personalausweisnummer)\b",
]
_SENSITIVE_RE = re.compile("|".join(_SENSITIVE_PATTERNS), re.IGNORECASE)
_TS_TERM = re.compile(r"[^\wäöüß]", re.I)


def content_hash(title: str, body: str) -> str:
    return hashlib.sha256(f"{title}\n{body}".encode()).hexdigest()


def looks_sensitive(text: str) -> bool:
    return bool(_SENSITIVE_RE.search(text))


_ENTITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "type": {"type": "string", "enum": ENTITY_TYPES}},
    "required": ["name", "type"],
}
EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["create", "update", "supersede", "skip"]},
                    "target_id": {"type": "string"},
                    "title": {"type": "string"},
                    "kind": {"type": "string", "enum": list(KIND_FOLDERS)},
                    "content": {"type": "string"},
                    "entities": {"type": "array", "items": _ENTITY_SCHEMA},
                    "relations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "relation": {"type": "string"},
                                "target": {"type": "string"},
                                "type": {"type": "string", "enum": ENTITY_TYPES},
                            },
                            "required": ["relation", "target", "type"],
                        },
                    },
                    "sensitive": {"type": "boolean"},
                    "confidence": {"type": "number"},
                },
                "required": ["action", "target_id", "title", "kind", "content", "entities", "sensitive", "confidence"],
            },
        }
    },
    "required": ["facts"],
}

EXTRACTION_PROMPT = """Du pflegst das Langzeitgedächtnis von Jarvis, dem Assistenten von "Sir" (dem Besitzer).
Du bekommst den letzten Gesprächsausschnitt und die ähnlichsten VORHANDENEN Erinnerungen.

Speichere NUR, was in Wochen noch nützlich ist:
- neue Kunden, Firmen, Personen (Name, Rolle, Ansprechpartner, Konditionen, Kontaktwege)
- Projektinformationen (Umfang, Preis, Termine, Stand), Beziehungen zwischen Personen, Firmen und Projekten
- persönliche Präferenzen von Sir, wichtige Entscheidungen (mit Begründung)
- wiederkehrende Abläufe (Schritt für Schritt), Aufgaben und Verpflichtungen, die Sir nennt (kind "commitment")
- Änderungen an bestehenden Fakten
NICHT speichern: Smalltalk, einmalige Fragen, Rechenaufgaben, Wetter, Webseiteninhalte, bloße Anweisungen an Jarvis.
QUELLE: ausschließlich Sirs Aussagen. Jarvis' Antworten sind nur Kontext – Behauptungen von Jarvis
("ich habe angelegt/gestartet/gesendet") sind KEINE Fakten. Erfinde nichts, was Sir nicht gesagt hat.
Löse Pronomen und Bezüge mit dem Gesprächsverlauf auf ("er" → der genannte Ansprechpartner).

Für jeden Fakt wähle action:
- "update": betrifft dieselbe Entität bzw. dasselbe Thema wie eine vorhandene Erinnerung → target_id dieser
  Erinnerung, content = vollständige, konsolidierte Fassung (alle bisherigen Fakten + die neuen, ohne Wiederholungen)
- "supersede": widerspricht einer vorhandenen Erinnerung (z. B. neuer Preis, neue Adresse) → target_id der alten,
  content = die neue, jetzt gültige Fassung
- "create": wirklich neues Thema, keine passende vorhandene Erinnerung (target_id = "")
- "skip": schon vollständig bekannt
Titel: kurz und eindeutig, bei Entitäten nur der Name ("Müller Gebäudereinigung", nicht "Kunde Müller …").
content: ein bis vier vollständige Sätze. entities: beteiligte Namen mit Typ. relations: Beziehungen vom Titel zu
anderen Entitäten (z. B. relation "Ansprechpartner", target "Max Müller", type "person").
sensitive=true bei Gesundheits-, Finanzkonto-, Zugangs- oder Ausweisdaten. confidence 0..1 (wie sicher und
dauerhaft ist der Fakt). Wenn nichts Relevantes vorkommt: {"facts": []}."""

EPISODE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "worth_saving": {"type": "boolean"},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "entities": {"type": "array", "items": _ENTITY_SCHEMA},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "open_items": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["worth_saving", "title", "summary", "entities", "decisions", "open_items"],
}
EPISODE_PROMPT = """Fasse ein abgeschlossenes Gespräch zwischen Sir und seinem Assistenten Jarvis als Eintrag \
für das episodische Gedächtnis zusammen. Nur Tatsachen: was Sir wollte, was Jarvis nachweislich getan hat \
(nur Werkzeuge mit Status ok zählen als erledigt), Ergebnisse, Entscheidungen und offene Punkte. \
title: drei bis sechs Wörter zum Thema. summary: zwei bis fünf Sätze. worth_saving=false bei reinem Smalltalk, \
Tests oder einzelnen Wissensfragen ohne Bezug zu Sirs Arbeit oder Leben."""


def _split_body(body: str) -> tuple[str, str, list[str]]:
    """note body -> (main text, 'Bezug: …' line, history lines)."""
    history: list[str] = []
    m = re.search(r"\n*## Verlauf\n(.*)$", body, flags=re.S)
    if m:
        history = [ln for ln in m.group(1).splitlines() if ln.strip()]
        body = body[: m.start()]
    bezug = ""
    bm = re.search(r"\n*(Bezug:[^\n]*)", body)
    if bm:
        bezug = bm.group(1).strip()
        body = (body[: bm.start()] + body[bm.end() :]).strip()
    return body.strip(), bezug, history


def _join_body(main: str, bezug: str, history: list[str]) -> str:
    out = main.strip()
    if bezug and bezug.strip() != "Bezug:":
        out += f"\n\n{bezug}"
    if history:
        out += "\n\n## Verlauf\n" + "\n".join(history[-20:])
    return out


def _bezug(title: str, names: list[str]) -> str:
    links = [f"[[{n}]]" for n in dict.fromkeys(names) if n and n.lower() != title.lower()]
    return ("Bezug: " + ", ".join(links)) if links else ""


def _norm_entities(entities: list[Any] | None) -> tuple[list[str], list[dict[str, str]]]:
    """Accepts names or {"name", "type"} dicts → (names, typed 'betrifft' relations)."""
    names: list[str] = []
    typed: list[dict[str, str]] = []
    for e in entities or []:
        if isinstance(e, dict):
            name = str(e.get("name") or "").strip()
            if name:
                names.append(name)
                typed.append({"relation": "betrifft", "target": name, "kind": str(e.get("type") or "")})
        elif e and str(e).strip():
            names.append(str(e).strip())
    return list(dict.fromkeys(names))[:12], typed


def _merge_relations(a: list[dict[str, str]], b: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: dict[tuple[str, str], dict[str, str]] = {}
    for r in [*a, *b]:
        if not r.get("target"):
            continue
        key = (r.get("relation", "bezug").lower(), r["target"].lower())
        if key not in seen or (r.get("kind") and not seen[key].get("kind")):
            seen[key] = {"relation": r.get("relation") or "bezug", "target": r["target"], "kind": r.get("kind") or ""}
    return list(seen.values())[:30]


class MemoryService:
    def __init__(
        self,
        vault: Vault,
        embedder: LocalEmbedder,
        structured_llm: Callable[[str, str, dict[str, Any]], Awaitable[Any]],
    ) -> None:
        self.vault = vault
        self.embedder = embedder
        self.structured_llm = structured_llm
        self._watch_task: asyncio.Task[None] | None = None
        self._extract_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        self._extract_task: asyncio.Task[None] | None = None
        self._episode_task: asyncio.Task[None] | None = None
        self._self_writes: dict[str, float] = {}
        self._qcache: OrderedDict[str, list[float]] = OrderedDict()
        self._write_lock = asyncio.Lock()
        self.episode_idle_minutes = 20

    # ------------------------------------------------------------------ lifecycle
    async def start(self, marker: Path | None = None, *, background: bool = True) -> None:
        """``marker`` stores the embedding model the index was built with; a different model re-embeds all."""
        self.vault.ensure()
        model = self.embedder.model_name
        stale = marker is not None and (not marker.exists() or marker.read_text(encoding="utf-8").strip() != model)
        if stale:
            logger.info("Embedding model changed to %s: re-embedding all notes", model)
        await self.reindex_all(force=stale)
        if marker is not None:
            marker.write_text(model, encoding="utf-8")
        if background:
            self._watch_task = asyncio.create_task(self._watch())
            self._extract_task = asyncio.create_task(self._extract_worker())
            self._episode_task = asyncio.create_task(self._episode_worker())
            spawn(self._recover_unextracted(), name="memory-recover")

    async def stop(self) -> None:
        for t in (self._watch_task, self._extract_task, self._episode_task):
            if t:
                t.cancel()

    # ------------------------------------------------------------------ indexing
    async def _embed(self, text: str) -> list[float]:
        return (await self.embedder.embed([text]))[0]

    async def _embed_query(self, query: str) -> list[float]:
        key = query.strip().lower()[:500]
        hit = self._qcache.get(key)
        if hit is not None:
            self._qcache.move_to_end(key)
            return hit
        emb = await self._embed(query)
        self._qcache[key] = emb
        if len(self._qcache) > 256:
            self._qcache.popitem(last=False)
        return emb

    async def index_note(self, note: Note, force: bool = False) -> uuid.UUID:
        chash = content_hash(note.title, note.body)
        async with session_scope() as s:
            item = (await s.execute(select(MemoryItem).where(MemoryItem.path == note.path))).scalar_one_or_none()
            if (
                not force
                and item is not None
                and item.content_hash == chash
                and item.status == note.status
                and (item.aliases or []) == note.aliases
                and abs((item.confidence or 1.0) - note.confidence) < 1e-6
            ):
                return item.id
        embedding = await self._embed(f"{note.title}\n{note.body}")
        async with session_scope() as s:
            item = (await s.execute(select(MemoryItem).where(MemoryItem.path == note.path))).scalar_one_or_none()
            if item is None:
                try:
                    item_id = uuid.UUID(note.id)
                except ValueError:
                    item_id = uuid.uuid4()
                if await s.get(MemoryItem, item_id) is not None:  # copied note with duplicate id
                    item_id = uuid.uuid4()
                item = MemoryItem(id=item_id, path=note.path)
                s.add(item)
            item.title = note.title
            item.kind = note.kind if note.kind in KIND_FOLDERS else "note"
            item.content = note.body
            item.content_hash = chash
            item.embedding = embedding
            item.sensitivity = note.sensitivity
            item.status = note.status if note.status in ("active", "superseded") else "active"
            item.source = note.source
            item.entities = note.entities
            item.aliases = note.aliases
            item.confidence = note.confidence
            item.updated_at = datetime.now(UTC)
            await s.execute(delete(MemoryLink).where(MemoryLink.source_path == note.path))
            typed = {r["target"].lower(): r for r in note.relations}
            seen: set[tuple[str, str]] = set()
            for target in note.links:
                rel = typed.get(target.lower())
                relation = (rel or {}).get("relation") or ("betrifft" if target in note.entities else "links_to")
                key = (target.lower()[:300], relation[:60])
                if key in seen:
                    continue
                seen.add(key)
                s.add(
                    MemoryLink(
                        source_path=note.path,
                        target_title=target[:300],
                        relation=relation[:60],
                        target_kind=((rel or {}).get("kind") or None),
                    )
                )
            await s.flush()
            return item.id

    async def reindex_all(self, force: bool = False) -> dict[str, int]:
        """Index new/changed notes; ``force`` re-embeds every note (e.g. after an embedding-model change)."""
        paths = self.vault.iter_notes()
        indexed = 0
        for rel in paths:
            try:
                await self.index_note(self.vault.read(rel), force=force)
                indexed += 1
            except Exception:
                logger.exception("Indexing %s failed", rel)
        async with session_scope() as s:
            res = await s.execute(
                delete(MemoryItem).where(MemoryItem.status != "pending", MemoryItem.path.not_in(paths or [""]))
            )
            await s.execute(delete(MemoryLink).where(MemoryLink.source_path.not_in(paths or [""])))
            removed = res.rowcount or 0  # type: ignore[attr-defined]
        logger.info("Memory index: %d notes indexed, %d stale entries removed", indexed, removed)
        return {"indexed": indexed, "removed": removed}

    async def _watch(self) -> None:
        try:
            async for changes in awatch(self.vault.root, debounce=800, recursive=True):
                for change, raw in changes:
                    if not raw.endswith(".md") or "/." in raw:
                        continue
                    try:
                        rel = Path(raw).resolve().relative_to(self.vault.root).as_posix()
                    except ValueError:
                        continue
                    if self._self_writes.pop(rel, None):
                        continue
                    try:
                        item_id: str | None = None
                        if change == Change.deleted:
                            async with session_scope() as s:
                                old = (
                                    await s.execute(select(MemoryItem.id).where(MemoryItem.path == rel))
                                ).scalar_one_or_none()
                                item_id = str(old) if old else None
                                await s.execute(delete(MemoryItem).where(MemoryItem.path == rel))
                                await s.execute(delete(MemoryLink).where(MemoryLink.source_path == rel))
                        else:
                            item_id = str(await self.index_note(self.vault.read(rel)))
                        await bus.publish("memory.changed", id=item_id, path=rel, change=change.name, origin="vault")
                    except Exception:
                        logger.exception("Vault sync failed for %s", rel)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Vault watcher stopped")

    def _write(self, note: Note) -> str:
        path = self.vault.write(note)
        self._self_writes[path] = 1.0
        return path

    # ------------------------------------------------------------------ queries
    async def _keyword(
        self, terms: list[str], statuses: list[str], kinds: list[str] | None, include_sensitive: bool, n: int
    ) -> list[tuple[MemoryItem, float]]:
        clean = [t for t in (_TS_TERM.sub("", t) for t in terms) if len(t) >= 3]
        if not clean:
            return []
        expr = " | ".join(f"{t}:*" for t in clean)
        tsq = func.to_tsquery("simple", expr).op("||")(func.to_tsquery("german", expr))
        rank = func.ts_rank_cd(MemoryItem.search_tsv, tsq, 1)  # 1 = normalise by document length
        q = (
            select(MemoryItem, rank.label("rank"))
            .where(MemoryItem.search_tsv.op("@@")(tsq), MemoryItem.status.in_(statuses))
            .order_by(rank.desc())
            .limit(n)
        )
        if kinds:
            q = q.where(MemoryItem.kind.in_(kinds))
        if not include_sensitive:
            q = q.where(MemoryItem.sensitivity != "sensitive")
        async with session_scope() as s:
            return [(i, float(r)) for i, r in (await s.execute(q)).all()]

    async def _vector(
        self, emb: list[float], statuses: list[str], kinds: list[str] | None, include_sensitive: bool, n: int
    ) -> list[tuple[MemoryItem, float]]:
        distance = MemoryItem.embedding.cosine_distance(emb)
        q = (
            select(MemoryItem, distance.label("dist"))
            .where(MemoryItem.embedding.is_not(None), MemoryItem.status.in_(statuses))
            .order_by(distance)
            .limit(n)
        )
        if kinds:
            q = q.where(MemoryItem.kind.in_(kinds))
        if not include_sensitive:
            q = q.where(MemoryItem.sensitivity != "sensitive")
        async with session_scope() as s:
            return [(i, 1 - float(d)) for i, d in (await s.execute(q)).all()]

    async def _neighbours(self, items: list[MemoryItem], statuses: list[str]) -> dict[uuid.UUID, MemoryItem]:
        """Graph expansion: notes linked from or to the given notes (wiki links, entities, relations)."""
        if not items:
            return {}
        paths = [i.path for i in items]
        titles = [i.title.lower() for i in items]
        async with session_scope() as s:
            out_titles = (
                (await s.execute(select(MemoryLink.target_title).where(MemoryLink.source_path.in_(paths))))
                .scalars()
                .all()
            )
            in_paths = (
                (await s.execute(select(MemoryLink.source_path).where(func.lower(MemoryLink.target_title).in_(titles))))
                .scalars()
                .all()
            )
            conds = []
            if out_titles:
                conds.append(func.lower(MemoryItem.title).in_([t.lower() for t in out_titles]))
            if in_paths:
                conds.append(MemoryItem.path.in_(list(in_paths)))
            if not conds:
                return {}
            rows = (
                (await s.execute(select(MemoryItem).where(or_(*conds), MemoryItem.status.in_(statuses)).limit(24)))
                .scalars()
                .all()
            )
        return {r.id: r for r in rows}

    async def search(
        self,
        query: str,
        limit: int = 6,
        kinds: list[str] | None = None,
        include_sensitive: bool = True,
        include_superseded: bool = False,
        *,
        budget_ms: float | None = None,
        expand: bool = True,
        purpose: str = "search",
    ) -> list[dict[str, Any]]:
        """Hybrid search. ``budget_ms`` caps the wait for the query embedding (voice); keyword hits still count."""
        t0 = asyncio.get_running_loop().time()
        statuses = ["active", "superseded"] if include_superseded else ["active"]
        terms = query_terms(query)
        emb_task = asyncio.ensure_future(self._embed_query(query))
        kw = await self._keyword(terms, statuses, kinds, include_sensitive, limit * 4)
        emb: list[float] | None = None
        try:
            emb = await (asyncio.wait_for(asyncio.shield(emb_task), budget_ms / 1000) if budget_ms else emb_task)
        except TimeoutError:
            logger.info("Memory search: embedding over budget (%.0f ms) – keyword results only", budget_ms or 0)
        vec = await self._vector(emb, statuses, kinds, include_sensitive, limit * 4) if emb is not None else []

        items: dict[uuid.UUID, MemoryItem] = {}
        sim: dict[uuid.UUID, float] = {}
        kwr: dict[uuid.UUID, float] = {}
        rrf: dict[uuid.UUID, float] = {}
        match: dict[uuid.UUID, set[str]] = {}
        for rank, (item, s_) in enumerate(vec):
            items[item.id], sim[item.id] = item, s_
            rrf[item.id] = rrf.get(item.id, 0) + 1 / (RRF_K + rank + 1)
            if s_ >= 0.5:
                match.setdefault(item.id, set()).add("semantic")
        max_kw = max((r for _, r in kw), default=0) or 1.0
        for rank, (item, r) in enumerate(kw):
            items[item.id] = item
            kwr[item.id] = r / max_kw
            rrf[item.id] = rrf.get(item.id, 0) + 1 / (RRF_K + rank + 1)
            match.setdefault(item.id, set()).add("keyword")

        now = datetime.now(UTC)

        def score(i: MemoryItem) -> float:
            base = 0.55 * sim.get(i.id, 0.0) + 0.25 * kwr.get(i.id, 0.0) + 0.2 * rrf.get(i.id, 0.0) / (2 / (RRF_K + 1))
            age_days = max(0.0, (now - (i.updated_at or now)).total_seconds() / 86400)
            base += 0.05 * math.exp(-age_days / 90)
            base += min(0.03, 0.005 * math.log1p(i.access_count or 0))
            return base * (0.85 + 0.15 * float(i.confidence if i.confidence is not None else 1.0))

        scores = {iid: score(i) for iid, i in items.items()}
        if expand and scores:
            top = sorted(scores, key=lambda k: scores[k], reverse=True)[:3]
            neigh = await self._neighbours([items[t] for t in top], statuses)
            parent = max(scores[t] for t in top)
            for nid, n in neigh.items():
                if nid in scores or (kinds and n.kind not in kinds):
                    continue
                if not include_sensitive and n.sensitivity == "sensitive":
                    continue
                items[nid] = n
                scores[nid] = parent * 0.55
                match.setdefault(nid, set()).add("graph")

        ranked = sorted(scores, key=lambda k: scores[k], reverse=True)[:limit]
        results = [
            {
                "id": str(items[i].id),
                "title": items[i].title,
                "kind": items[i].kind,
                "layer": LAYERS.get(items[i].kind, "semantic"),
                "content": items[i].content[:1500],
                "path": items[i].path,
                "score": round(scores[i], 4),
                "similarity": round(sim.get(i, 0.0), 4),
                "match": sorted(match.get(i, set())),
                "status": items[i].status,
                "sensitivity": items[i].sensitivity,
                "entities": items[i].entities,
                "source": items[i].source,
                "confidence": items[i].confidence,
                "updated_at": items[i].updated_at.isoformat(),
            }
            for i in ranked
        ]
        duration = round((asyncio.get_running_loop().time() - t0) * 1000, 1)
        if results:
            spawn(self._touch([items[i].id for i in ranked]), name="memory-touch")
        await bus.publish(
            "memory.recalled",
            nodes=[f"memory:{r['id']}" for r in results],
            purpose=purpose,
            query=query[:120],
            via=current_activity().tool,
            duration_ms=duration,
            semantic=emb is not None,
            hits=[{"id": r["id"], "title": r["title"], "kind": r["kind"], "score": r["score"]} for r in results],
        )
        return results

    async def _touch(self, ids: list[uuid.UUID]) -> None:
        try:
            async with session_scope() as s:
                await s.execute(
                    update(MemoryItem)
                    .where(MemoryItem.id.in_(ids))
                    .values(
                        access_count=MemoryItem.access_count + 1,
                        last_accessed_at=datetime.now(UTC),
                        updated_at=MemoryItem.updated_at,  # use is not an edit
                    )
                )
        except Exception:
            logger.debug("memory access stats failed", exc_info=True)

    async def context_for(
        self, query: str, *, include_sensitive: bool, budget_ms: float | None = None, max_chars: int = 1800
    ) -> str:
        """Compressed memory block for a turn: most relevant sentences of the best hits, with sources."""
        if len(query.strip()) < 8:
            return ""
        hits = await self.search(
            query, limit=6, include_sensitive=include_sensitive, budget_ms=budget_ms, purpose="auto_recall"
        )
        terms = query_terms(query)
        lines: list[str] = []
        used = 0
        for h in hits:
            relevant = h["score"] >= 0.42 or "keyword" in h["match"]
            if not relevant:
                continue
            per = 420 if not lines else 260
            text = best_sentences(h["content"], terms, per)
            date = h["updated_at"][:10]
            line = f"- [{h['kind']}] {h['title']} ({date}): {text}"
            if used + len(line) > max_chars:
                break
            lines.append(line)
            used += len(line)
        return "\n".join(lines)

    async def get(self, item_id: uuid.UUID) -> dict[str, Any] | None:
        async with session_scope() as s:
            item = await s.get(MemoryItem, item_id)
            if item is None:
                return None
            backlinks = (
                (await s.execute(select(MemoryLink.source_path).where(MemoryLink.target_title == item.title)))
                .scalars()
                .all()
            )
            links = (await s.execute(select(MemoryLink).where(MemoryLink.source_path == item.path))).scalars().all()
            titles = {link.target_title.lower() for link in links}
            back_items: Sequence[MemoryItem] = []
            if backlinks:
                back_items = (
                    (await s.execute(select(MemoryItem).where(MemoryItem.path.in_(list(backlinks))))).scalars().all()
                )
            linked_items: Sequence[MemoryItem] = []
            if titles:
                linked_items = (
                    (await s.execute(select(MemoryItem).where(func.lower(MemoryItem.title).in_(list(titles)))))
                    .scalars()
                    .all()
                )
            by_title = {i.title.lower(): i for i in linked_items}
            related = []
            for link in links:
                tgt = by_title.get(link.target_title.lower())
                related.append(
                    {
                        "id": str(tgt.id) if tgt else None,
                        "node": f"memory:{tgt.id}" if tgt else f"entity:{link.target_title.lower()}",
                        "title": link.target_title,
                        "kind": tgt.kind if tgt else (link.target_kind or "entity"),
                        "relation": link.relation,
                        "direction": "out",
                    }
                )
            for b in back_items:
                if b.id == item.id:
                    continue
                related.append(
                    {
                        "id": str(b.id),
                        "node": f"memory:{b.id}",
                        "title": b.title,
                        "kind": b.kind,
                        "relation": "verweist hierher",
                        "direction": "in",
                    }
                )
            return {
                "id": str(item.id),
                "title": item.title,
                "kind": item.kind,
                "layer": LAYERS.get(item.kind, "semantic"),
                "content": item.content,
                "path": item.path,
                "status": item.status,
                "sensitivity": item.sensitivity,
                "entities": item.entities,
                "aliases": item.aliases or [],
                "confidence": item.confidence,
                "access_count": item.access_count,
                "last_accessed_at": item.last_accessed_at.isoformat() if item.last_accessed_at else None,
                "source": item.source,
                "backlinks": list(backlinks),
                "related": related,
                "superseded_by": str(item.superseded_by) if item.superseded_by else None,
                "created_at": item.created_at.isoformat(),
                "updated_at": item.updated_at.isoformat(),
            }

    async def list_items(
        self,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
        q: str | None = None,
    ) -> dict[str, Any]:
        stmt = select(MemoryItem)
        count = select(func.count()).select_from(MemoryItem)
        conds = []
        if kind:
            conds.append(MemoryItem.kind == kind)
        if status:
            conds.append(MemoryItem.status == status)
        if q:
            like = f"%{q}%"
            conds.append(or_(MemoryItem.title.ilike(like), MemoryItem.content.ilike(like)))
        for c in conds:
            stmt = stmt.where(c)
            count = count.where(c)
        async with session_scope() as s:
            total = (await s.execute(count)).scalar_one()
            rows = (
                (await s.execute(stmt.order_by(MemoryItem.updated_at.desc()).limit(limit).offset(offset)))
                .scalars()
                .all()
            )
        return {
            "total": total,
            "items": [
                {
                    "id": str(r.id),
                    "title": r.title,
                    "kind": r.kind,
                    "path": r.path,
                    "status": r.status,
                    "sensitivity": r.sensitivity,
                    "preview": r.content[:240],
                    "entities": r.entities,
                    "updated_at": r.updated_at.isoformat(),
                }
                for r in rows
            ],
        }

    async def graph_rows(self, limit: int = 2000) -> tuple[list[MemoryItem], list[MemoryLink]]:
        async with session_scope() as s:
            items = (
                (
                    await s.execute(
                        select(MemoryItem)
                        .where(MemoryItem.status == "active")
                        .order_by(MemoryItem.updated_at.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            links = (await s.execute(select(MemoryLink))).scalars().all()
        return list(items), list(links)

    async def similar_pairs(
        self, ids: list[uuid.UUID], k: int = 2, min_sim: float = 0.8
    ) -> list[tuple[str, str, float]]:
        """Nearest neighbours per note (a real, measurable relation: semantic proximity)."""
        if not ids:
            return []
        async with session_scope() as s:
            rows = (
                await s.execute(
                    select(MemoryItem.id, MemoryItem.embedding).where(
                        MemoryItem.id.in_(ids), MemoryItem.embedding.is_not(None)
                    )
                )
            ).all()
        if len(rows) < 2:
            return []
        import numpy as np

        mat = np.asarray([r[1] for r in rows], dtype=np.float32)
        mat /= np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9
        sims = mat @ mat.T
        np.fill_diagonal(sims, -1)
        out: list[tuple[str, str, float]] = []
        seen: set[tuple[str, str]] = set()
        for i, row in enumerate(sims):
            for j in np.argsort(-row)[:k]:
                if row[j] < min_sim:
                    break
                a, b = str(rows[i][0]), str(rows[int(j)][0])
                key = (min(a, b), max(a, b))
                if key not in seen:
                    seen.add(key)
                    out.append((a, b, float(row[j])))
        return out

    async def graph(self, limit: int = 400) -> dict[str, Any]:
        """Memory-only graph (legacy endpoint); the full live graph is built in ``jarvis.graph``."""
        items, links = await self.graph_rows(limit)
        by_title = {i.title.lower(): i for i in items}
        by_path = {i.path: i for i in items}
        nodes: dict[str, dict[str, Any]] = {
            str(i.id): {"id": str(i.id), "label": i.title, "kind": i.kind, "path": i.path} for i in items
        }
        edges = []
        for link in links:
            src = by_path.get(link.source_path)
            if src is None:
                continue
            tgt = by_title.get(link.target_title.lower())
            if tgt is not None:
                tid = str(tgt.id)
            else:  # unresolved link -> entity node (exists only as a name)
                tid = f"entity:{link.target_title.lower()}"
                nodes.setdefault(tid, {"id": tid, "label": link.target_title, "kind": "entity", "path": None})
            edges.append({"source": str(src.id), "target": tid, "relation": link.relation})
        return {"nodes": list(nodes.values()), "edges": edges}

    # ------------------------------------------------------------------ writes
    async def _resolve_target(self, title: str, kind: str, emb: list[float]) -> tuple[MemoryItem | None, float]:
        """Existing active note describing the same thing (entity name, alias, title or near-identical content)."""
        group = KIND_GROUPS.get(kind)
        async with session_scope() as s:
            if group:
                rows = (
                    (
                        await s.execute(
                            select(MemoryItem).where(MemoryItem.status == "active", MemoryItem.kind.in_(group))
                        )
                    )
                    .scalars()
                    .all()
                )
                for r in rows:
                    if same_entity(title, r.title) or any(same_entity(title, a) for a in (r.aliases or [])):
                        return r, 1.0
            same = (
                (
                    await s.execute(
                        select(MemoryItem).where(
                            MemoryItem.status == "active",
                            func.lower(MemoryItem.title) == title.strip().lower(),
                            MemoryItem.kind == kind,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if same is not None:
                return same, 1.0
            dist = MemoryItem.embedding.cosine_distance(emb)
            row = (
                await s.execute(
                    select(MemoryItem, dist.label("d"))
                    .where(MemoryItem.status == "active", MemoryItem.embedding.is_not(None))
                    .order_by(dist)
                    .limit(1)
                )
            ).first()
        if row is not None:
            existing, d = row
            sim = 1 - float(d)
            if sim >= DUPLICATE_THRESHOLD or (sim >= RELATED_THRESHOLD and existing.title.lower() == title.lower()):
                return existing, sim
        return None, 0.0

    async def remember(
        self,
        title: str,
        content: str,
        kind: str = "note",
        *,
        entities: list[Any] | None = None,
        source: dict[str, Any] | None = None,
        sensitive: bool | None = None,
        actor: str = "system",
        confirmed: bool = False,
        confidence: float = 1.0,
        relations: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Store a fact with entity resolution and dedupe. Sensitive facts wait for explicit confirmation."""
        kind = kind if kind in KIND_FOLDERS else "note"
        names, typed = _norm_entities(entities)
        rels = _merge_relations(typed, relations or [])
        confidence = max(0.0, min(1.0, float(confidence)))
        is_sensitive = bool(sensitive) or looks_sensitive(f"{title} {content}")
        src = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), **(source or {})}

        if is_sensitive and not confirmed:
            async with session_scope() as s:
                item = MemoryItem(
                    path=f"_pending/{uuid.uuid4()}",
                    title=title[:300],
                    kind=kind,
                    content=content,
                    content_hash=content_hash(title, content),
                    embedding=None,
                    sensitivity="sensitive",
                    status="pending",
                    source={**src, "_relations": rels, "_confidence": confidence},
                    entities=names,
                )
                s.add(item)
                await s.flush()
                pid = item.id
            await bus.publish("memory.pending", id=str(pid), title=title, kind=kind)
            await audit(actor, "memory.pending", target=str(pid), details={"title": title, "kind": kind})
            return {
                "status": "pending_confirmation",
                "id": str(pid),
                "message": "Sensible Information – wird erst nach Bestätigung durch Sir gespeichert.",
            }

        async with self._write_lock:  # concurrent extractor + agent writes must not race into two notes
            emb = await self._embed(f"{title}\n{content}")
            existing, sim = await self._resolve_target(title, kind, emb)
            if existing is not None:
                note = self.vault.read(existing.path)
                main, _bezug_line, history = _split_body(note.body)
                fresh = new_information(content, note.body)
                new_names = [n for n in names if n not in note.entities]
                new_alias = (
                    title.strip()
                    if title.strip().lower() != note.title.lower()
                    and title.strip().lower() not in {a.lower() for a in note.aliases}
                    and (existing.kind in ENTITY_KINDS)
                    else None
                )
                merged_rels = _merge_relations(note.relations, rels)
                if not fresh and not new_names and merged_rels == note.relations and not new_alias:
                    return {
                        "status": "duplicate",
                        "id": str(existing.id),
                        "path": existing.path,
                        "similarity": round(sim, 3),
                    }
                stamp = datetime.now(UTC).strftime("%Y-%m-%d")
                if fresh:
                    main = f"{main}\n\n**Ergänzung {stamp}:** {' '.join(fresh)}"
                note.entities = sorted(set(note.entities) | set(names))
                note.relations = merged_rels
                if new_alias:
                    note.aliases = [*note.aliases, new_alias][:12]
                note.confidence = max(note.confidence, confidence)
                note.body = _join_body(
                    main, _bezug(note.title, [*note.entities, *[r["target"] for r in merged_rels]]), history
                )
                note.updated = datetime.now(UTC).isoformat(timespec="seconds")
                self._write(note)
                await self.index_note(note)
                await bus.publish(
                    "memory.changed",
                    id=str(existing.id),
                    path=note.path,
                    change="updated",
                    origin="jarvis",
                    title=note.title,
                    kind=note.kind,
                )
                await audit(actor, "memory.update", target=note.path, details={"title": note.title})
                return {"status": "updated", "id": str(existing.id), "path": note.path, "similarity": round(sim, 3)}

            body = content.strip()
            bezug = _bezug(title, [*names, *[r["target"] for r in rels]])
            if bezug:
                body += f"\n\n{bezug}"
            note = Note(
                title=title.strip()[:200],
                body=body,
                kind=kind,
                entities=names,
                source=src,
                sensitivity="sensitive" if is_sensitive else "normal",
                confidence=confidence,
                relations=rels,
            )
            self._write(note)
            item_id = await self.index_note(note)
        await bus.publish(
            "memory.changed",
            id=str(item_id),
            path=note.path,
            change="added",
            origin="jarvis",
            title=note.title,
            kind=kind,
        )
        await audit(actor, "memory.create", target=note.path, details={"title": note.title, "kind": kind})
        return {"status": "created", "id": str(item_id), "path": note.path}

    async def consolidate(
        self,
        item_id: uuid.UUID,
        merged_content: str,
        *,
        entities: list[Any] | None = None,
        relations: list[dict[str, str]] | None = None,
        confidence: float = 1.0,
        actor: str = "memory-extractor",
        reason: str = "Gespräch",
    ) -> dict[str, Any]:
        """Rewrite a note with a consolidated version (old facts + new ones) instead of appending paraphrases."""
        async with session_scope() as s:
            item = await s.get(MemoryItem, item_id)
            if item is None or item.status != "active":
                raise LookupError("Erinnerung nicht gefunden")
            path = item.path
        async with self._write_lock:
            note = self.vault.read(path)
            main, _, history = _split_body(note.body)
            merged = merged_content.strip()
            missing = new_information(main, merged)  # old sentences the rewrite dropped
            if len(merged) < 0.5 * len(main) or len(missing) > max(1, len(main) // 400):
                # The rewrite lost information → keep the old text and only add what is new.
                fresh = new_information(merged, note.body)
                if not fresh:
                    return {"status": "duplicate", "id": str(item_id), "path": path}
                merged = f"{main}\n\n**Ergänzung {datetime.now(UTC).strftime('%Y-%m-%d')}:** {' '.join(fresh)}"
            names, typed = _norm_entities(entities)
            note.entities = sorted(set(note.entities) | set(names))
            note.relations = _merge_relations(note.relations, [*typed, *(relations or [])])
            note.confidence = max(note.confidence, max(0.0, min(1.0, confidence)))
            history.append(f"- {datetime.now(UTC).strftime('%Y-%m-%d')}: zusammengeführt ({reason})")
            note.body = _join_body(
                merged, _bezug(note.title, [*note.entities, *[r["target"] for r in note.relations]]), history
            )
            note.updated = datetime.now(UTC).isoformat(timespec="seconds")
            self._write(note)
            await self.index_note(note)
        await bus.publish(
            "memory.changed",
            id=str(item_id),
            path=path,
            change="consolidated",
            origin="jarvis",
            title=note.title,
            kind=note.kind,
        )
        await audit(actor, "memory.consolidate", target=path, details={"title": note.title})
        return {"status": "updated", "id": str(item_id), "path": path}

    async def confirm_pending(self, item_id: uuid.UUID, approve: bool, actor: str) -> dict[str, Any]:
        async with session_scope() as s:
            item = await s.get(MemoryItem, item_id)
            if item is None or item.status != "pending":
                raise LookupError("Kein ausstehender Eintrag")
            src = dict(item.source or {})
            data: dict[str, Any] = {
                "title": item.title,
                "content": item.content,
                "kind": item.kind,
                "entities": item.entities,
                "relations": src.pop("_relations", []),
                "confidence": src.pop("_confidence", 1.0),
                "source": src,
            }
            await s.delete(item)
        await audit(actor, "memory.pending." + ("confirm" if approve else "reject"), target=str(item_id))
        if not approve:
            return {"status": "discarded"}
        return await self.remember(
            data["title"],
            data["content"],
            data["kind"],
            entities=data["entities"],
            source=data["source"],
            sensitive=True,
            actor=actor,
            confirmed=True,
            confidence=data["confidence"],
            relations=data["relations"],
        )

    async def correct(
        self, item_id: uuid.UUID, new_content: str, actor: str, new_title: str | None = None
    ) -> dict[str, Any]:
        """Replace outdated information: the old note is kept but marked superseded."""
        async with session_scope() as s:
            old = await s.get(MemoryItem, item_id)
            if old is None:
                raise LookupError("Erinnerung nicht gefunden")
            old_path, old_title, kind, entities = old.path, old.title, old.kind, list(old.entities or [])
            sensitivity = old.sensitivity
        old_note = self.vault.read(old_path)
        new_note = Note(
            title=new_title or old_title,
            kind=kind,
            entities=entities,
            sensitivity=sensitivity,
            aliases=old_note.aliases,
            relations=old_note.relations,
            body=f"{new_content.strip()}\n\nErsetzt: [[{old_note.title}]] (veraltet)",
            source={"ts": datetime.now(UTC).isoformat(timespec="seconds"), "type": "correction", "actor": actor},
        )
        if new_note.title == old_title:
            old_note.title = f"{old_title} (veraltet {datetime.now(UTC).strftime('%Y-%m-%d')})"
            new_note.body = new_note.body.replace(f"[[{old_title}]]", f"[[{old_note.title}]]")
        new_path = self._write(new_note)
        new_id = await self.index_note(new_note)
        old_note.status = "superseded"
        old_note.aliases = []
        old_note.extra["superseded_by"] = new_path
        old_note.updated = datetime.now(UTC).isoformat(timespec="seconds")
        self._write(old_note)
        await self.index_note(old_note)
        async with session_scope() as s:
            o = await s.get(MemoryItem, item_id)
            if o is not None:
                o.superseded_by = new_id
        await audit(actor, "memory.correct", target=old_path, details={"new_path": new_path})
        await bus.publish(
            "memory.changed",
            id=str(new_id),
            path=new_path,
            change="corrected",
            origin="jarvis",
            title=new_note.title,
            kind=kind,
            superseded=str(item_id),
        )
        return {"status": "corrected", "new_id": str(new_id), "new_path": new_path, "old_path": old_path}

    async def edit(
        self,
        item_id: uuid.UUID,
        actor: str,
        *,
        title: str | None = None,
        content: str | None = None,
        kind: str | None = None,
    ) -> dict[str, Any]:
        """Edit a note in place (Sir's own edit from the app). Use ``correct`` when a fact became outdated."""
        async with session_scope() as s:
            item = await s.get(MemoryItem, item_id)
            if item is None or item.status == "pending":
                raise LookupError("Erinnerung nicht gefunden")
            path = item.path
        async with self._write_lock:
            note = self.vault.read(path)
            if title and title.strip() and title.strip() != note.title:
                note.aliases = list(dict.fromkeys([*note.aliases, note.title]))[:12]
                note.title = title.strip()[:200]
            if kind and kind in KIND_FOLDERS:
                note.kind = kind
            if content is not None:
                _, bezug, history = _split_body(note.body)
                history.append(f"- {datetime.now(UTC).strftime('%Y-%m-%d')}: von Sir bearbeitet")
                note.body = _join_body(content, bezug, history)
            note.updated = datetime.now(UTC).isoformat(timespec="seconds")
            self._write(note)
            await self.index_note(note)
        await audit(actor, "memory.edit", target=path, details={"title": note.title})
        await bus.publish(
            "memory.changed",
            id=str(item_id),
            path=path,
            change="edited",
            origin="owner",
            title=note.title,
            kind=note.kind,
        )
        return {"status": "updated", "id": str(item_id), "path": path}

    async def forget(self, item_id: uuid.UUID, actor: str) -> dict[str, Any]:
        async with session_scope() as s:
            item = await s.get(MemoryItem, item_id)
            if item is None:
                raise LookupError("Erinnerung nicht gefunden")
            path, status = item.path, item.status
            await s.delete(item)
            await s.execute(delete(MemoryLink).where(MemoryLink.source_path == path))
        if status != "pending":
            self._self_writes[path] = 1.0
            self.vault.delete(path)
        await audit(actor, "memory.delete", target=path)
        await bus.publish("memory.changed", id=str(item_id), path=path, change="deleted", origin="jarvis")
        return {"status": "deleted", "path": path}

    async def export_zip(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for rel in self.vault.iter_notes():
                z.write(self.vault.resolve(rel), arcname=f"vault/{rel}")
            async with session_scope() as s:
                items = (await s.execute(select(MemoryItem).where(MemoryItem.status != "pending"))).scalars().all()
                links = (await s.execute(select(MemoryLink))).scalars().all()
            z.writestr(
                "index.json",
                json.dumps(
                    {
                        "exported_at": datetime.now(UTC).isoformat(),
                        "items": [
                            {
                                "id": str(i.id),
                                "path": i.path,
                                "title": i.title,
                                "kind": i.kind,
                                "status": i.status,
                                "sensitivity": i.sensitivity,
                                "entities": i.entities,
                                "aliases": i.aliases,
                                "confidence": i.confidence,
                                "source": i.source,
                            }
                            for i in items
                        ],
                        "links": [
                            {
                                "source": link.source_path,
                                "target": link.target_title,
                                "relation": link.relation,
                                "target_kind": link.target_kind,
                            }
                            for link in links
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        return buf.getvalue()

    async def stats(self) -> dict[str, Any]:
        async with session_scope() as s:
            rows = (await s.execute(select(MemoryItem.status, func.count()).group_by(MemoryItem.status))).all()
        return {status: n for status, n in rows}

    # ------------------------------------------------------------------ consolidation of existing notes
    async def find_duplicates(self) -> list[list[dict[str, Any]]]:
        """Groups of active entity notes that describe the same entity (for ``consolidate_duplicates``)."""
        async with session_scope() as s:
            rows = (
                (
                    await s.execute(
                        select(MemoryItem)
                        .where(MemoryItem.status == "active", MemoryItem.kind.in_(ENTITY_KINDS))
                        .order_by(MemoryItem.created_at)
                    )
                )
                .scalars()
                .all()
            )
        groups: list[list[MemoryItem]] = []
        for r in rows:
            for g in groups:
                head = g[0]
                if r.kind in KIND_GROUPS.get(head.kind, (head.kind,)) and same_entity(r.title, head.title):
                    g.append(r)
                    break
            else:
                groups.append([r])
        return [
            [{"id": str(i.id), "title": i.title, "kind": i.kind, "path": i.path} for i in g]
            for g in groups
            if len(g) > 1
        ]

    async def merge_notes(self, keep_id: uuid.UUID, merge_ids: list[uuid.UUID], actor: str) -> dict[str, Any]:
        """Merge notes into ``keep_id``; merged notes are kept as superseded (nothing is deleted)."""
        async with session_scope() as s:
            keep = await s.get(MemoryItem, keep_id)
            others = [await s.get(MemoryItem, m) for m in merge_ids]
        if keep is None:
            raise LookupError("Zielnotiz nicht gefunden")
        async with self._write_lock:
            target = self.vault.read(keep.path)
            main, _, history = _split_body(target.body)
            for o in others:
                if o is None or o.status != "active":
                    continue
                src = self.vault.read(o.path)
                fresh = new_information(_split_body(src.body)[0], main)
                if fresh:
                    main += "\n\n" + " ".join(fresh)
                target.entities = sorted(set(target.entities) | set(src.entities))
                target.relations = _merge_relations(target.relations, src.relations)
                if src.title.lower() != target.title.lower():
                    target.aliases = list(dict.fromkeys([*target.aliases, src.title, *src.aliases]))[:12]
                history.append(f"- {datetime.now(UTC).strftime('%Y-%m-%d')}: zusammengeführt mit „{src.title}“")
                src.status = "superseded"
                src.extra["superseded_by"] = target.path
                src.updated = datetime.now(UTC).isoformat(timespec="seconds")
                self._write(src)
                await self.index_note(src)
                async with session_scope() as s:
                    row = await s.get(MemoryItem, o.id)
                    if row is not None:
                        row.superseded_by = keep_id
                await bus.publish("memory.changed", id=str(o.id), path=o.path, change="merged", origin="jarvis")
            target.body = _join_body(
                main, _bezug(target.title, [*target.entities, *[r["target"] for r in target.relations]]), history
            )
            target.updated = datetime.now(UTC).isoformat(timespec="seconds")
            self._write(target)
            await self.index_note(target)
        await audit(actor, "memory.merge", target=keep.path, details={"merged": [str(m) for m in merge_ids]})
        await bus.publish("memory.changed", id=str(keep_id), path=keep.path, change="updated", origin="jarvis")
        return {"status": "merged", "id": str(keep_id), "path": keep.path, "merged": len(merge_ids)}

    async def consolidate_duplicates(self, actor: str = "memory-consolidation") -> list[dict[str, Any]]:
        """Merges every duplicate group into its most complete note (canonical name preferred)."""
        out = []
        for group in await self.find_duplicates():
            best = max(group, key=lambda g: (canonical_name(g["title"]) == g["title"].lower(), -len(g["title"])))
            others = [uuid.UUID(g["id"]) for g in group if g["id"] != best["id"]]
            out.append(await self.merge_notes(uuid.UUID(best["id"]), others, actor))
        return out

    # ------------------------------------------------------------------ automatic extraction
    def enqueue_extraction(
        self,
        user_text: str,
        assistant_text: str,
        source: dict[str, Any],
        *,
        context: list[dict[str, str]] | None = None,
        message_id: str | None = None,
    ) -> None:
        if len(user_text.strip()) < 25:
            spawn(self._mark_extracted(message_id), name="memory-mark")
            return
        try:
            self._extract_queue.put_nowait(
                {
                    "user": user_text,
                    "assistant": assistant_text,
                    "source": source,
                    "context": context or [],
                    "message_id": message_id,
                    "attempt": 0,
                }
            )
        except asyncio.QueueFull:
            logger.warning("Memory extraction queue full; turn stays unextracted and is retried after restart")

    async def _candidates(self, text: str) -> list[dict[str, Any]]:
        try:
            hits = await self.search(text, limit=8, include_sensitive=True, purpose="extraction")
        except Exception:
            logger.warning("candidate search failed", exc_info=True)
            return []
        return hits

    async def extract(
        self, user_text: str, assistant_text: str, context: list[dict[str, str]] | None = None
    ) -> list[dict[str, Any]]:
        candidates = await self._candidates(user_text)
        cand_lines = "\n".join(
            f"- id={c['id']} | {c['kind']} | {c['title']} | {c['content'][:300].replace(chr(10), ' ')}"
            for c in candidates
        )
        history = "\n".join(f"{m['role']}: {m['content'][:500]}" for m in (context or [])[-6:])
        prompt = (
            (f"Bisheriger Gesprächsverlauf (nur zum Auflösen von Bezügen):\n{history}\n\n" if history else "")
            + f"Aussage von Sir (einzige Quelle):\n{user_text[:3000]}\n\n"
            f"Antwort von Jarvis (nur Kontext, keine Quelle):\n{assistant_text[:800]}\n\n"
            f"Vorhandene Erinnerungen:\n{cand_lines or '(keine)'}"
        )
        data = await self.structured_llm(EXTRACTION_PROMPT, prompt, EXTRACTION_SCHEMA)
        facts = data.get("facts", []) if isinstance(data, dict) else []
        valid_ids = {c["id"] for c in candidates}
        out = []
        for f in facts:
            if not isinstance(f, dict) or f.get("action") == "skip":
                continue
            try:
                conf = float(f.get("confidence", 0))
            except (TypeError, ValueError):
                conf = 0.0
            if conf < 0.6 or len(str(f.get("content", ""))) < 12:
                continue
            if f.get("target_id") not in valid_ids:
                f["target_id"] = ""
                if f.get("action") in ("update", "supersede"):
                    f["action"] = "create"
            f["confidence"] = conf
            out.append(f)
        return out[:8]

    async def apply_fact(self, f: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
        rels = [
            {
                "relation": str(r.get("relation") or "bezug"),
                "target": str(r["target"]),
                "kind": str(r.get("type") or ""),
            }
            for r in (f.get("relations") or [])
            if isinstance(r, dict) and r.get("target")
        ]
        action, target = f.get("action", "create"), f.get("target_id") or ""
        if f.get("sensitive") or looks_sensitive(f"{f['title']} {f['content']}"):
            action = "create"  # sensitive facts always go through the confirmation flow
        if action == "update" and target:
            return await self.consolidate(
                uuid.UUID(target),
                str(f["content"]),
                entities=f.get("entities"),
                relations=rels,
                confidence=f["confidence"],
            )
        if action == "supersede" and target:
            res = await self.correct(uuid.UUID(target), str(f["content"]), "memory-extractor", str(f["title"]))
            return {**res, "status": "superseded"}
        return await self.remember(
            str(f["title"]),
            str(f["content"]),
            str(f.get("kind", "note")),
            entities=f.get("entities") or [],
            relations=rels,
            sensitive=bool(f.get("sensitive")),
            confidence=f["confidence"],
            source={"type": "conversation", **source},
            actor="memory-extractor",
        )

    async def _mark_extracted(self, message_id: str | None) -> None:
        if not message_id:
            return
        try:
            async with session_scope() as s:
                msg = await s.get(ChatMessage, uuid.UUID(message_id))
                if msg is not None:
                    msg.meta = {**(msg.meta or {}), "extracted": True}
        except Exception:
            logger.debug("mark extracted failed", exc_info=True)

    async def _extract_worker(self) -> None:
        while True:
            job = await self._extract_queue.get()
            try:
                facts = await self.extract(job["user"], job["assistant"], job.get("context"))
                for f in facts:
                    try:
                        res = await self.apply_fact(f, job["source"])
                        logger.info("Memory extraction: %s (%s) -> %s", f.get("title"), f.get("action"), res["status"])
                    except LookupError:
                        f["action"], f["target_id"] = "create", ""
                        await self.apply_fact(f, job["source"])
                await self._mark_extracted(job.get("message_id"))
            except Exception:
                job["attempt"] += 1
                if job["attempt"] < 3:
                    logger.warning("Memory extraction failed (attempt %d); retrying", job["attempt"], exc_info=True)
                    spawn(self._requeue(job, 30 * job["attempt"]), name="memory-retry")
                else:
                    logger.exception("Memory extraction failed permanently; retried after next restart")

    async def _requeue(self, job: dict[str, Any], delay: float) -> None:
        await asyncio.sleep(delay)
        with_full = self._extract_queue.full()
        if not with_full:
            self._extract_queue.put_nowait(job)

    async def _recover_unextracted(self, max_age_days: int = 3) -> int:
        """Turns that were never extracted (restart, model unavailable) are queued again."""
        since = datetime.now(UTC) - timedelta(days=max_age_days)
        async with session_scope() as s:
            rows = (
                await s.execute(
                    select(ChatMessage, ChatSession)
                    .join(ChatSession, ChatSession.id == ChatMessage.session_id)
                    .where(
                        ChatMessage.role == "user",
                        ChatMessage.created_at >= since,
                        ChatSession.actor_role.in_(OWNER_ROLES),
                        ChatMessage.meta["extracted"].astext.is_(None),
                    )
                    .order_by(ChatMessage.created_at)
                    .limit(60)
                )
            ).all()
            jobs = []
            for msg, sess in rows:
                reply = (
                    (
                        await s.execute(
                            select(ChatMessage.content)
                            .where(
                                ChatMessage.session_id == msg.session_id,
                                ChatMessage.role == "assistant",
                                ChatMessage.created_at >= msg.created_at,
                            )
                            .order_by(ChatMessage.created_at)
                            .limit(1)
                        )
                    )
                    .scalars()
                    .first()
                )
                if reply is None:
                    continue
                jobs.append((msg, sess, reply))
        for msg, sess, reply in jobs:
            self.enqueue_extraction(
                msg.content,
                reply,
                {"session_id": str(sess.id), "channel": sess.channel, "recovered": True},
                message_id=str(msg.id),
            )
        if jobs:
            logger.info("Memory: %d unextracted turns re-queued", len(jobs))
        return len(jobs)

    # ------------------------------------------------------------------ episodic memory
    async def summarize_session(self, session_id: uuid.UUID) -> dict[str, Any] | None:
        async with session_scope() as s:
            sess = await s.get(ChatSession, session_id)
            msgs = (
                (
                    await s.execute(
                        select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at)
                    )
                )
                .scalars()
                .all()
            )
        if sess is None:
            return None
        user_msgs = [m for m in msgs if m.role == "user"]
        result: dict[str, Any] | None = None
        if len(user_msgs) >= 2:
            lines = []
            for m in msgs:
                if m.role not in ("user", "assistant"):
                    continue
                tools = [
                    f"{t.get('name')}:{'fehler' if t.get('event') == 'tool_failed' else 'ok'}"
                    for t in (m.meta or {}).get("tools", [])
                    if t.get("event") in ("tool_completed", "tool_failed", "tool_started") and t.get("name")
                ]
                who = "Sir" if m.role == "user" else "Jarvis"
                lines.append(f"{who}: {m.content[:600]}" + (f"  [Werkzeuge: {', '.join(tools[:8])}]" if tools else ""))
            transcript = "\n".join(lines)[-7000:]
            data = await self.structured_llm(EPISODE_PROMPT, transcript, EPISODE_SCHEMA)
            if isinstance(data, dict) and data.get("worth_saving") and data.get("summary"):
                day = (sess.created_at or datetime.now(UTC)).strftime("%Y-%m-%d")
                body = str(data["summary"]).strip()
                if data.get("decisions"):
                    body += "\n\nEntscheidungen:\n" + "\n".join(f"- {d}" for d in data["decisions"][:8])
                if data.get("open_items"):
                    body += "\n\nOffen:\n" + "\n".join(f"- {o}" for o in data["open_items"][:8])
                result = await self.remember(
                    f"Gespräch {day} – {str(data.get('title') or 'Gespräch').strip()[:80]}",
                    body,
                    "conversation",
                    entities=data.get("entities") or [],
                    source={"type": "conversation_summary", "session_id": str(session_id), "channel": sess.channel},
                    actor="memory-episodes",
                    confidence=0.9,
                )
        async with session_scope() as s:
            row = await s.get(ChatSession, session_id)
            if row is not None:
                row.summarized_at = datetime.now(UTC)
        return result

    async def _episode_worker(self) -> None:
        while True:
            await asyncio.sleep(300)
            try:
                cutoff = datetime.now(UTC) - timedelta(minutes=self.episode_idle_minutes)
                async with session_scope() as s:
                    ids = (
                        (
                            await s.execute(
                                select(ChatSession.id)
                                .where(
                                    ChatSession.summarized_at.is_(None),
                                    ChatSession.last_activity_at < cutoff,
                                    ChatSession.last_activity_at > datetime.now(UTC) - timedelta(days=7),
                                    ChatSession.actor_role.in_(OWNER_ROLES),
                                )
                                .order_by(ChatSession.last_activity_at)
                                .limit(5)
                            )
                        )
                        .scalars()
                        .all()
                    )
                for sid in ids:
                    res = await self.summarize_session(sid)
                    if res:
                        logger.info("Episodic memory for session %s: %s", sid, res.get("status"))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Episode summarisation failed", exc_info=True)
