"""Obsidian vault access.

The vault is the source of truth for long-term memory and stays a plain Obsidian vault:
one Markdown note per memory with YAML frontmatter and ``[[wiki links]]``. Postgres only
holds a rebuildable index (embeddings + link graph).
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

KIND_FOLDERS: dict[str, str] = {
    "preference": "Präferenzen",
    "project": "Projekte",
    "customer": "Kunden",
    "company": "Unternehmen",
    "person": "Personen",
    "process": "Abläufe",  # procedural memory
    "recurring": "Wiederkehrend",
    "decision": "Entscheidungen",
    "commitment": "Aufgaben",  # tasks and obligations Sir mentioned
    "appointment": "Termine",
    "email": "E-Mails",
    "document": "Dokumente",
    "technical": "Technik",  # semantic memory: facts and knowledge
    "conversation": "Gespräche",  # episodic memory: one note per conversation
    "note": "Notizen",
}
# Memory layers (for retrieval weighting and the UI); short-term memory is the live conversation itself.
LAYERS: dict[str, str] = {
    "conversation": "episodic",
    "appointment": "episodic",
    "email": "episodic",
    "technical": "semantic",
    "note": "semantic",
    "document": "semantic",
    "project": "project",
    "customer": "customer",
    "company": "customer",
    "person": "customer",
    "preference": "preference",
    "decision": "decision",
    "process": "procedural",
    "recurring": "procedural",
    "commitment": "procedural",
}
# Kinds that describe one real-world entity: a second note about the same name is a duplicate.
ENTITY_KINDS = ("customer", "company", "person", "project")
FOLDER_KINDS = {v: k for k, v in KIND_FOLDERS.items()}
_FORBIDDEN = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]')
_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
_FM_RE = re.compile(r"^---\n(.*?)\n---\n?", re.S)


def slugify_title(title: str) -> str:
    title = unicodedata.normalize("NFC", title).strip()
    title = _FORBIDDEN.sub("-", title)
    title = re.sub(r"\s+", " ", title).strip(" .-")
    return title[:120] or "Unbenannt"


@dataclass(slots=True)
class Note:
    title: str
    body: str
    kind: str = "note"
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    updated: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    sensitivity: str = "normal"
    status: str = "active"
    source: dict[str, Any] = field(default_factory=dict)
    entities: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    confidence: float = 1.0
    aliases: list[str] = field(default_factory=list)
    # typed relations: {"relation": "arbeitet bei", "target": "Müller GmbH", "kind": "company"}
    relations: list[dict[str, str]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    path: str = ""  # vault-relative

    @property
    def links(self) -> list[str]:
        found = {m.group(1).strip() for m in _LINK_RE.finditer(self.body)}
        found.update(e for e in self.entities if e)
        found.update(r["target"] for r in self.relations if r.get("target"))
        found.discard(self.title)
        return sorted(found)

    def frontmatter(self) -> dict[str, Any]:
        fm: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "created": self.created,
            "updated": self.updated,
            "status": self.status,
            "sensitivity": self.sensitivity,
        }
        if self.source:
            fm["source"] = self.source
        if self.entities:
            fm["entities"] = self.entities
        if self.tags:
            fm["tags"] = self.tags
        if self.confidence < 1.0:
            fm["confidence"] = round(self.confidence, 2)
        if self.aliases:
            fm["aliases"] = self.aliases  # Obsidian understands `aliases` natively
        if self.relations:
            fm["relations"] = self.relations
        fm.update(self.extra)
        return fm

    def render(self) -> str:
        fm = yaml.safe_dump(self.frontmatter(), allow_unicode=True, sort_keys=False).strip()
        return f"---\n{fm}\n---\n# {self.title}\n\n{self.body.strip()}\n"


class Vault:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for folder in KIND_FOLDERS.values():
            (self.root / folder).mkdir(exist_ok=True)
        readme = self.root / "README.md"
        if not readme.exists():
            readme.write_text(
                "# Jarvis Vault\n\nDies ist das Langzeitgedächtnis von J.A.R.V.I.S. als normaler "
                "Obsidian-Vault. Jede Notiz ist eine Erinnerung mit YAML-Frontmatter. Du kannst Notizen "
                "in Obsidian bearbeiten, verschieben oder löschen – Jarvis übernimmt die Änderungen "
                "automatisch in seinen Suchindex.\n\nOrdner: " + ", ".join(KIND_FOLDERS.values()) + "\n",
                encoding="utf-8",
            )

    def resolve(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError("Pfad liegt außerhalb des Vaults")
        return p

    def relpath(self, p: Path) -> str:
        return p.resolve().relative_to(self.root).as_posix()

    def path_for(self, title: str, kind: str) -> str:
        folder = KIND_FOLDERS.get(kind, KIND_FOLDERS["note"])
        base = slugify_title(title)
        rel = f"{folder}/{base}.md"
        n = 2
        while (self.root / rel).exists():
            rel = f"{folder}/{base} ({n}).md"
            n += 1
        return rel

    def read(self, rel: str) -> Note:
        p = self.resolve(rel)
        return parse_note(p.read_text(encoding="utf-8"), rel)

    def write(self, note: Note) -> str:
        if not note.path:
            note.path = self.path_for(note.title, note.kind)
        p = self.resolve(note.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".md.tmp")
        tmp.write_text(note.render(), encoding="utf-8")
        tmp.replace(p)
        return note.path

    def delete(self, rel: str) -> bool:
        p = self.resolve(rel)
        if p.exists():
            p.unlink()
            return True
        return False

    def iter_notes(self) -> list[str]:
        out = []
        for p in self.root.rglob("*.md"):
            rel = self.relpath(p)
            if any(part.startswith(".") for part in Path(rel).parts) or rel == "README.md":
                continue
            out.append(rel)
        return sorted(out)


def parse_note(text: str, rel: str) -> Note:
    fm: dict[str, Any] = {}
    body = text
    m = _FM_RE.match(text)
    if m:
        try:
            loaded = yaml.safe_load(m.group(1)) or {}
            fm = loaded if isinstance(loaded, dict) else {}
        except yaml.YAMLError:
            fm = {}
        body = text[m.end() :]
    title = Path(rel).stem
    hm = re.match(r"^\s*#\s+(.+)\n", body)
    if hm:
        title = hm.group(1).strip()
        body = body[hm.end() :]
    folder = Path(rel).parts[0] if len(Path(rel).parts) > 1 else ""
    known = {
        "id",
        "kind",
        "created",
        "updated",
        "status",
        "sensitivity",
        "source",
        "entities",
        "tags",
        "confidence",
        "aliases",
        "relations",
    }
    try:
        confidence = max(0.0, min(1.0, float(fm.get("confidence", 1.0))))
    except (TypeError, ValueError):
        confidence = 1.0
    relations = [
        {"relation": str(r.get("relation") or "bezug"), "target": str(r["target"]), "kind": str(r.get("kind") or "")}
        for r in (fm.get("relations") or [])
        if isinstance(r, dict) and r.get("target")
    ]
    return Note(
        title=title,
        body=body.strip(),
        kind=str(fm.get("kind") or FOLDER_KINDS.get(folder, "note")),
        id=str(fm.get("id") or uuid.uuid5(uuid.NAMESPACE_URL, rel)),
        created=str(fm.get("created") or ""),
        updated=str(fm.get("updated") or ""),
        sensitivity=str(fm.get("sensitivity") or "normal"),
        status=str(fm.get("status") or "active"),
        source=dict(fm["source"]) if isinstance(fm.get("source"), dict) else {},
        entities=[str(e) for e in (fm.get("entities") or []) if e],
        tags=[str(t) for t in (fm.get("tags") or []) if t],
        confidence=confidence,
        aliases=[str(a) for a in (fm.get("aliases") or []) if a] if isinstance(fm.get("aliases"), list) else [],
        relations=relations,
        extra={k: v for k, v in fm.items() if k not in known},
        path=rel,
    )
