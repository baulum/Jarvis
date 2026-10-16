"""Memory engine: Obsidian vault + pgvector index + automatic knowledge extraction."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import re
import uuid
import zipfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, or_, select
from watchfiles import Change, awatch

from jarvis.db import session_scope
from jarvis.db.models import MemoryItem, MemoryLink
from jarvis.events import bus
from jarvis.memory.embeddings import LocalEmbedder
from jarvis.memory.vault import KIND_FOLDERS, Note, Vault
from jarvis.security.audit import audit

logger = logging.getLogger(__name__)

DUPLICATE_THRESHOLD = 0.93  # cosine similarity: treat as the same fact
RELATED_THRESHOLD = 0.82  # same title + this similarity: update the existing note

_SENSITIVE_PATTERNS = [
    r"\b[A-Z]{2}\d{2}(?:\s?\d{4}){4,7}\s?\d{0,2}\b",  # IBAN
    r"\b(?:\d[ -]?){13,19}\b",  # card-like numbers
    r"\b(passwort|password|kennwort|pin|tan|api[- ]?key|token|secret)\b\s*[:=]",
    r"\b(diagnose|krankheit|medikament|therapie|gesundheit)\w*",
    r"\b(steuer-?id|sozialversicherungsnummer|personalausweisnummer)\b",
]
_SENSITIVE_RE = re.compile("|".join(_SENSITIVE_PATTERNS), re.IGNORECASE)


def content_hash(title: str, body: str) -> str:
    return hashlib.sha256(f"{title}\n{body}".encode()).hexdigest()


def looks_sensitive(text: str) -> bool:
    return bool(_SENSITIVE_RE.search(text))


EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "kind": {"type": "string", "enum": list(KIND_FOLDERS)},
                    "content": {"type": "string"},
                    "entities": {"type": "array", "items": {"type": "string"}},
                    "sensitive": {"type": "boolean"},
                    "confidence": {"type": "number"},
                },
                "required": ["title", "kind", "content", "entities", "sensitive", "confidence"],
            },
        }
    },
    "required": ["facts"],
}

EXTRACTION_PROMPT = """Du extrahierst dauerhaft relevantes Wissen aus einem Gesprächsausschnitt zwischen \
"Sir" (dem Besitzer) und seinem Assistenten Jarvis.
Speichere NUR Fakten, die in Wochen noch nützlich sind: Präferenzen von Sir, Projekte, Kunden \
(Name, Ansprechpartner, Konditionen), Geschäftsabläufe, Entscheidungen, wiederkehrende Aufgaben, \
technische Informationen, wichtige Personen.
NICHT speichern: Smalltalk, einmalige Fragen, Rechenaufgaben, Wetter, Inhalte von Webseiten, \
Anweisungen an Jarvis, Dinge die Jarvis nur vermutet.
QUELLE: Ausschließlich die Aussagen von Sir. Die Antwort von Jarvis ist nur Kontext – Behauptungen von Jarvis \
(z. B. "ich habe angelegt/gesendet") sind KEINE Fakten und dürfen nicht gespeichert werden. \
Erfinde keine Details (Namen, Daten, Adressen), die Sir nicht genannt hat.
Jeder Fakt: kurzer eindeutiger Titel (z. B. "Müller Gebäudereinigung" oder "Präferenz Rechnungsversand"), \
kind, ein bis drei vollständige Sätze als content, beteiligte Entitäten (Namen von Firmen, Personen, Projekten), \
sensitive=true bei Gesundheits-, Finanzkonto-, Zugangs- oder Ausweisdaten, confidence 0..1.
Wenn nichts Relevantes vorkommt, gib {"facts": []} zurück."""


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
        self._extract_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        self._extract_task: asyncio.Task[None] | None = None
        self._self_writes: dict[str, float] = {}

    # ------------------------------------------------------------------ lifecycle
    async def start(self, marker: Path | None = None) -> None:
        """``marker`` stores the embedding model the index was built with; a different model re-embeds all."""
        self.vault.ensure()
        model = self.embedder.model_name
        stale = marker is not None and (not marker.exists() or marker.read_text(encoding="utf-8").strip() != model)
        if stale:
            logger.info("Embedding model changed to %s: re-embedding all notes", model)
        await self.reindex_all(force=stale)
        if marker is not None:
            marker.write_text(model, encoding="utf-8")
        self._watch_task = asyncio.create_task(self._watch())
        self._extract_task = asyncio.create_task(self._extract_worker())

    async def stop(self) -> None:
        for t in (self._watch_task, self._extract_task):
            if t:
                t.cancel()

    # ------------------------------------------------------------------ indexing
    async def _embed(self, text: str) -> list[float]:
        return (await self.embedder.embed([text]))[0]

    async def index_note(self, note: Note, force: bool = False) -> uuid.UUID:
        chash = content_hash(note.title, note.body)
        async with session_scope() as s:
            item = (await s.execute(select(MemoryItem).where(MemoryItem.path == note.path))).scalar_one_or_none()
            if not force and item is not None and item.content_hash == chash and item.status == note.status:
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
            item.updated_at = datetime.now(UTC)
            await s.execute(delete(MemoryLink).where(MemoryLink.source_path == note.path))
            for target in note.links:
                s.add(MemoryLink(source_path=note.path, target_title=target[:300], relation="links_to"))
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
                        if change == Change.deleted:
                            async with session_scope() as s:
                                await s.execute(delete(MemoryItem).where(MemoryItem.path == rel))
                                await s.execute(delete(MemoryLink).where(MemoryLink.source_path == rel))
                        else:
                            await self.index_note(self.vault.read(rel))
                        await bus.publish("memory.changed", path=rel, change=change.name, origin="vault")
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
    async def search(
        self,
        query: str,
        limit: int = 6,
        kinds: list[str] | None = None,
        include_sensitive: bool = True,
        include_superseded: bool = False,
    ) -> list[dict[str, Any]]:
        emb = await self._embed(query)
        distance = MemoryItem.embedding.cosine_distance(emb)
        q = select(MemoryItem, distance.label("dist")).where(MemoryItem.embedding.is_not(None))
        statuses = ["active", "superseded"] if include_superseded else ["active"]
        q = q.where(MemoryItem.status.in_(statuses))
        if kinds:
            q = q.where(MemoryItem.kind.in_(kinds))
        if not include_sensitive:
            q = q.where(MemoryItem.sensitivity != "sensitive")
        q = q.order_by(distance).limit(limit * 3)
        terms = [t.lower() for t in re.findall(r"\w{3,}", query)]
        async with session_scope() as s:
            rows = (await s.execute(q)).all()
        results = []
        for item, dist in rows:
            sim = 1 - float(dist)
            hay = f"{item.title} {item.content}".lower()
            boost = 0.03 * sum(1 for t in terms if t in hay)
            results.append(
                {
                    "id": str(item.id),
                    "title": item.title,
                    "kind": item.kind,
                    "content": item.content[:1500],
                    "path": item.path,
                    "score": round(sim + boost, 4),
                    "status": item.status,
                    "sensitivity": item.sensitivity,
                    "entities": item.entities,
                    "source": item.source,
                    "updated_at": item.updated_at.isoformat(),
                }
            )
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:limit]

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
            return {
                "id": str(item.id),
                "title": item.title,
                "kind": item.kind,
                "content": item.content,
                "path": item.path,
                "status": item.status,
                "sensitivity": item.sensitivity,
                "entities": item.entities,
                "source": item.source,
                "backlinks": list(backlinks),
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

    async def graph(self, limit: int = 400) -> dict[str, Any]:
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
    async def remember(
        self,
        title: str,
        content: str,
        kind: str = "note",
        *,
        entities: list[str] | None = None,
        source: dict[str, Any] | None = None,
        sensitive: bool | None = None,
        actor: str = "system",
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """Store a fact with duplicate detection. Sensitive facts wait for explicit confirmation."""
        kind = kind if kind in KIND_FOLDERS else "note"
        entities = [e.strip() for e in (entities or []) if e and e.strip()][:12]
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
                    source=src,
                    entities=entities,
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

        emb = await self._embed(f"{title}\n{content}")
        async with session_scope() as s:
            dist = MemoryItem.embedding.cosine_distance(emb)
            row = (
                await s.execute(
                    select(MemoryItem, dist.label("d"))
                    .where(MemoryItem.status == "active", MemoryItem.embedding.is_not(None))
                    .order_by(dist)
                    .limit(1)
                )
            ).first()
            same_title = (
                await s.execute(
                    select(MemoryItem, dist.label("d"))
                    .where(
                        MemoryItem.status == "active",
                        func.lower(MemoryItem.title) == title.strip().lower(),
                        MemoryItem.kind == kind,
                    )
                    .limit(1)
                )
            ).first()
        if same_title is not None:
            row = same_title  # the title is the note's identity in Obsidian -> extend that note
        if row is not None:
            existing, d = row
            sim = 1 - float(d) if d is not None else 0.0
            if (
                same_title is not None
                or sim >= DUPLICATE_THRESHOLD
                or (sim >= RELATED_THRESHOLD and existing.title.lower() == title.lower())
            ):
                if content.strip() in existing.content:
                    return {
                        "status": "duplicate",
                        "id": str(existing.id),
                        "path": existing.path,
                        "similarity": round(sim, 3),
                    }
                note = self.vault.read(existing.path)
                stamp = datetime.now(UTC).strftime("%Y-%m-%d")
                note.body = f"{note.body}\n\n**Ergänzung {stamp}:** {content.strip()}"
                note.entities = sorted(set(note.entities) | set(entities))
                note.updated = datetime.now(UTC).isoformat(timespec="seconds")
                self._write(note)
                await self.index_note(note)
                await bus.publish("memory.changed", path=note.path, change="updated", origin="jarvis")
                await audit(actor, "memory.update", target=note.path, details={"title": note.title})
                return {"status": "updated", "id": str(existing.id), "path": note.path, "similarity": round(sim, 3)}

        body = content.strip()
        if entities:
            body += "\n\nBezug: " + ", ".join(f"[[{e}]]" for e in entities if e.lower() != title.lower())
        note = Note(
            title=title.strip()[:200],
            body=body,
            kind=kind,
            entities=entities,
            source=src,
            sensitivity="sensitive" if is_sensitive else "normal",
        )
        self._write(note)
        item_id = await self.index_note(note)
        await bus.publish("memory.changed", path=note.path, change="added", origin="jarvis")
        await audit(actor, "memory.create", target=note.path, details={"title": note.title, "kind": kind})
        return {"status": "created", "id": str(item_id), "path": note.path}

    async def confirm_pending(self, item_id: uuid.UUID, approve: bool, actor: str) -> dict[str, Any]:
        async with session_scope() as s:
            item = await s.get(MemoryItem, item_id)
            if item is None or item.status != "pending":
                raise LookupError("Kein ausstehender Eintrag")
            data: dict[str, Any] = {
                "title": item.title,
                "content": item.content,
                "kind": item.kind,
                "entities": item.entities,
                "source": item.source,
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
        new_note = Note(
            title=new_title or old_title,
            kind=kind,
            entities=entities,
            sensitivity=sensitivity,
            body=f"{new_content.strip()}\n\nErsetzt: [[{self.vault.read(old_path).title}]] (veraltet)",
            source={"ts": datetime.now(UTC).isoformat(timespec="seconds"), "type": "correction", "actor": actor},
        )
        if new_note.title == old_title:
            old_note = self.vault.read(old_path)
            old_note.title = f"{old_title} (veraltet {datetime.now(UTC).strftime('%Y-%m-%d')})"
            new_note.body = new_note.body.replace(f"[[{old_title}]]", f"[[{old_note.title}]]")
        else:
            old_note = self.vault.read(old_path)
        new_path = self._write(new_note)
        new_id = await self.index_note(new_note)
        old_note.status = "superseded"
        old_note.extra["superseded_by"] = new_path
        old_note.updated = datetime.now(UTC).isoformat(timespec="seconds")
        self._write(old_note)
        await self.index_note(old_note)
        async with session_scope() as s:
            o = await s.get(MemoryItem, item_id)
            if o is not None:
                o.superseded_by = new_id
        await audit(actor, "memory.correct", target=old_path, details={"new_path": new_path})
        await bus.publish("memory.changed", path=new_path, change="corrected", origin="jarvis")
        return {"status": "corrected", "new_id": str(new_id), "new_path": new_path, "old_path": old_path}

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
        await bus.publish("memory.changed", path=path, change="deleted", origin="jarvis")
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
                                "source": i.source,
                            }
                            for i in items
                        ],
                        "links": [
                            {"source": link.source_path, "target": link.target_title, "relation": link.relation}
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

    # ------------------------------------------------------------------ automatic extraction
    def enqueue_extraction(self, user_text: str, assistant_text: str, source: dict[str, Any]) -> None:
        if len(user_text.strip()) < 25:
            return
        try:
            self._extract_queue.put_nowait({"user": user_text, "assistant": assistant_text, "source": source})
        except asyncio.QueueFull:
            logger.warning("Memory extraction queue full; dropping turn")

    async def extract(self, user_text: str, assistant_text: str) -> list[dict[str, Any]]:
        prompt = (
            f"Aussage von Sir (einzige Quelle):\n{user_text[:3000]}\n\n"
            f"Antwort von Jarvis (nur Kontext, keine Quelle):\n{assistant_text[:800]}"
        )
        data = await self.structured_llm(EXTRACTION_PROMPT, prompt, EXTRACTION_SCHEMA)
        facts = data.get("facts", []) if isinstance(data, dict) else []
        return [
            f
            for f in facts
            if isinstance(f, dict) and float(f.get("confidence", 0)) >= 0.7 and len(str(f.get("content", ""))) >= 12
        ][:5]

    async def _extract_worker(self) -> None:
        while True:
            job = await self._extract_queue.get()
            try:
                facts = await self.extract(job["user"], job["assistant"])
                for f in facts:
                    res = await self.remember(
                        str(f["title"]),
                        str(f["content"]),
                        str(f.get("kind", "note")),
                        entities=[str(e) for e in f.get("entities", [])],
                        sensitive=bool(f.get("sensitive")),
                        source={"type": "conversation", **job["source"]},
                        actor="memory-extractor",
                    )
                    logger.info("Memory extraction: %s -> %s", f.get("title"), res.get("status"))
            except Exception:
                logger.exception("Memory extraction failed")
