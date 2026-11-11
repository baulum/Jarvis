"""Lasting behaviour rules – the part of the memory that is *loaded*, not searched.

A rule is an ordinary vault note (folder ``Regeln/``, frontmatter ``kind: rule``) with a few extra frontmatter
fields, so it stays a plain Obsidian note and needs no extra table or index column:

``scope``       ``global`` (every agent run) or ``project`` (only when the run concerns that project)
``project``     project name for ``scope: project`` (matched against task/job/path hints, see ``match_projects``)
``active``      ``false`` switches the rule off without deleting it
``topic``       optional key: of several active rules with the same topic only the newest counts
``supersedes``  id of the rule this one replaced (``memory_correct``); the old note is ``status: superseded``
``migrated_from`` vault path of the preference note a rule was derived from (``plan_migration``)

Why this exists (B1): until now a stated rule was an ordinary memory – it reached the model only when the semantic
search happened to hit it (``MemoryService.context_for``: score threshold, 1800 characters shared with conversation
notes, none at all for queries under 8 characters, a 250 ms budget on calls) and landed in the per-turn context block
of the conversation, which Claude Code compacts away. Background tasks, scheduled runs and resumed sessions never saw
it. Rules are read from the vault directly (no embedding, no database) and put into the system prompt by
``agent.prompting`` on every run.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis.memory.vault import Note, Vault, parse_note

logger = logging.getLogger(__name__)

RULE_KIND = "rule"
RULE_FOLDER = "Regeln"
SCOPES = ("global", "project")
# Character budget of the rules block in the system prompt. Global rules are never dropped for it (they are
# shortened instead and a warning is logged); project rules fill what is left, newest first.
DEFAULT_BUDGET = 6000
_TAIL_RE = re.compile(r"\n+(?:Ersetzt:[^\n]*|Bezug:[^\n]*)", re.S)
_HISTORY_RE = re.compile(r"\n*## Verlauf\n.*$", re.S)


@dataclass(slots=True)
class Rule:
    id: str
    title: str
    text: str
    scope: str = "global"
    project: str = ""
    active: bool = True
    topic: str = ""
    path: str = ""
    created: str = ""
    updated: str = ""
    supersedes: str = ""
    superseded: bool = False  # replaced by a newer rule (``status: superseded``)
    migrated_from: str = ""
    note: str = ""  # why a rule is inactive (migration: replaced by a newer, contradicting rule)
    shadowed_by: str = field(default="", compare=False)  # set by ``effective``: the newer rule with the same topic

    @property
    def in_force(self) -> bool:
        return self.active and not self.superseded

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "text": self.text,
            "scope": self.scope,
            "project": self.project,
            "active": self.active,
            "topic": self.topic,
            "path": self.path,
            "created_at": self.created,
            "updated_at": self.updated,
            "supersedes": self.supersedes or None,
            "superseded": self.superseded,
            "in_force": self.in_force and not self.shadowed_by,
            "shadowed_by": self.shadowed_by or None,
            "migrated_from": self.migrated_from or None,
            "note": self.note or None,
        }


def rule_text(body: str) -> str:
    """The instruction itself: the note body without the 'Ersetzt'/'Bezug' lines and the history section."""
    text = _HISTORY_RE.sub("", body)
    text = _TAIL_RE.sub("", text)
    return text.strip()


def _truthy(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "no", "nein", "0", "off", "inaktiv")
    return default if value is None else bool(value)


def rule_from_note(note: Note) -> Rule:
    scope = str(note.extra.get("scope") or "global").lower()
    if scope not in SCOPES:
        scope = "global"
    project = str(note.extra.get("project") or "").strip()
    if scope == "project" and not project:
        scope = "global"  # a project rule without a project cannot be matched – better global than never
    return Rule(
        id=note.id,
        title=note.title,
        text=rule_text(note.body),
        scope=scope,
        project=project if scope == "project" else "",
        active=_truthy(note.extra.get("active"), True),
        topic=str(note.extra.get("topic") or "").strip().lower(),
        path=note.path,
        created=note.created,
        updated=note.updated or note.created,
        supersedes=str(note.extra.get("supersedes") or ""),
        superseded=note.status == "superseded" or bool(note.extra.get("superseded_by")),
        migrated_from=str(note.extra.get("migrated_from") or ""),
        note=str(note.extra.get("note") or ""),
    )


class RuleBook:
    """Reads the rule notes straight from the vault. Cached by (mtime, size) per file, so a run costs one
    ``stat`` per rule file – no database, no embedding, and Obsidian edits are picked up immediately."""

    def __init__(self, vault: Vault) -> None:
        self.vault = vault
        self._cache: dict[str, tuple[tuple[int, int], Rule]] = {}

    def _paths(self) -> list[Path]:
        folder = self.vault.root / RULE_FOLDER
        if not folder.is_dir():
            return []
        return sorted(p for p in folder.glob("*.md") if not p.name.startswith("."))

    def all(self) -> list[Rule]:
        """Every rule note, active or not (the settings list shows them all)."""
        out: list[Rule] = []
        seen: set[str] = set()
        for p in self._paths():
            rel = self.vault.relpath(p)
            seen.add(rel)
            try:
                st = p.stat()
                key = (st.st_mtime_ns, st.st_size)
                hit = self._cache.get(rel)
                if hit is not None and hit[0] == key:
                    out.append(hit[1])
                    continue
                note = parse_note(p.read_text(encoding="utf-8"), rel)
                if note.kind != RULE_KIND:
                    continue
                rule = rule_from_note(note)
            except (OSError, ValueError):
                logger.warning("Regel %s nicht lesbar", rel, exc_info=True)
                continue
            self._cache[rel] = (key, rule)
            out.append(rule)
        for gone in set(self._cache) - seen:
            self._cache.pop(gone, None)
        return out

    def get(self, rule_id: str) -> Rule | None:
        return next((r for r in self.all() if r.id == rule_id), None)

    def effective(self) -> list[Rule]:
        """Rules in force: active, not superseded, and – per ``topic`` – only the newest one."""
        return effective(self.all())


def effective(rules: list[Rule]) -> list[Rule]:
    live = [r for r in rules if r.in_force]
    newest: dict[tuple[str, str, str], Rule] = {}
    for r in live:
        if not r.topic:
            continue
        key = (r.scope, r.project.lower(), r.topic)
        cur = newest.get(key)
        if cur is None or (r.updated, r.created) > (cur.updated, cur.created):
            newest[key] = r
    out: list[Rule] = []
    for r in live:
        if r.topic:
            winner = newest[(r.scope, r.project.lower(), r.topic)]
            if winner is not r:
                r.shadowed_by = winner.id
                continue
        r.shadowed_by = ""
        out.append(r)
    out.sort(key=lambda r: (r.created, r.title))
    return out


def match_projects(
    rules: list[Rule], explicit: list[str], text: list[str] | None = None, ignore: tuple[str, ...] = ()
) -> list[Rule]:
    """Project rules whose project is named in the hints.

    ``explicit``: task parameters, coding-job path, title – a name found there always counts. ``text``: free text
    (the chat message) – names in ``ignore`` (the persona's own name, which is in nearly every message) don't count
    there. A name only counts as a whole word or path segment: 'Jarvis' matches '/…/Programmieren/Jarvis', not
    'Jarvisbot'."""
    hard = "\n".join(h for h in explicit if h).lower()
    soft = "\n".join(h for h in (text or []) if h).lower()
    if not hard and not soft:
        return []
    skip = {i.lower() for i in ignore}
    out: list[Rule] = []
    for r in rules:
        if r.scope != "project" or len(r.project) < 3:
            continue
        name = r.project.lower()
        rx = re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])")
        if rx.search(hard) or (name not in skip and rx.search(soft)):
            out.append(r)
    return out


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: max(limit - 1, 1)].rstrip() + "…"


def render_rules(
    global_rules: list[Rule], project_rules: list[Rule] | None = None, budget: int = DEFAULT_BUDGET
) -> tuple[str, list[str]]:
    """The prompt block and the warnings (shortened/dropped rules). Empty string when there are no rules.

    Global rules are never dropped: if they exceed the budget they are shortened (400 → 240 → 140 characters
    each) and, as a last resort, still all listed. Project rules use what is left, newest first."""
    project_rules = project_rules or []
    warnings: list[str] = []
    if not global_rules and not project_rules:
        return "", warnings

    def lines(rules: list[Rule], limit: int) -> list[str]:
        return [f"- {r.title}: {_clip(r.text, limit)}" for r in rules]

    glob: list[str] = []
    for limit in (600, 400, 240, 140):
        glob = lines(global_rules, limit)
        if sum(len(x) + 1 for x in glob) <= budget:
            break
    else:
        warnings.append(
            f"Globale Regeln überschreiten das Budget ({budget} Zeichen) auch gekürzt – alle {len(glob)} bleiben "
            "trotzdem im Prompt"
        )
    shortened = [r.title for r, g in zip(global_rules, glob, strict=True) if g.endswith("…")]
    if shortened:
        warnings.append("Gekürzte globale Regeln: " + ", ".join(shortened))
    left = budget - sum(len(x) + 1 for x in glob)
    proj: list[str] = []
    dropped: list[str] = []
    for r in sorted(project_rules, key=lambda r: (r.updated, r.created), reverse=True):
        line = f"- [{r.project}] {r.title}: {_clip(r.text, 400)}"
        if len(line) + 1 <= left:
            proj.append(line)
            left -= len(line) + 1
        else:
            dropped.append(r.title)
    if dropped:
        warnings.append("Projektregeln ohne Platz im Budget: " + ", ".join(dropped))
    parts = [
        "Diese Regeln hat Sir ausdrücklich als dauerhaft gültig festgelegt. Sie gelten in jeder Antwort, jedem "
        "Hintergrundauftrag und nach jeder Fortsetzung, ohne dass Sir sie wiederholt. Ältere, widersprechende "
        "Hinweise aus Gesprächen oder dem Gedächtnis sind überholt. Eine ausdrückliche Anweisung im aktuellen "
        "Gespräch hat Vorrang; die Kernanweisungen (Sicherheit, Freigaben) heben sie nie auf."
    ]
    if glob:
        parts.append("### Globale Regeln\n" + "\n".join(glob))
    if proj:
        parts.append("### Regeln für das aktuelle Projekt\n" + "\n".join(proj))
    return "\n\n".join(parts), warnings


def render_project_rules(rules: list[Rule], budget: int = 2400) -> str:
    """Per-turn block with the project rules that match this run (empty when none)."""
    lines: list[str] = []
    left = budget
    for r in sorted(rules, key=lambda r: (r.updated, r.created), reverse=True):
        line = f"- [{r.project}] {r.title}: {_clip(r.text, 500)}"
        if len(line) + 1 > left:
            logger.warning("Projektregel %r passt nicht mehr ins Budget (%d Zeichen)", r.title, budget)
            continue
        lines.append(line)
        left -= len(line) + 1
    if not lines:
        return ""
    return (
        "Projektregeln, die Sir für dieses Projekt dauerhaft festgelegt hat (gelten zusätzlich zu den globalen "
        "Regeln aus dem Systemprompt und gehen früheren widersprechenden Hinweisen vor):\n" + "\n".join(lines)
    )


# --------------------------------------------------------------------------- migration of old preference notes
_PERMANENT_RE = re.compile(
    r"\b(immer|stets|grundsätzlich|dauerhaft|fest(?:e|er|en|es)?|nie|niemals|ab sofort|standard\w*|regel\w*|"
    r"jedes ?mal|auf keinen fall|unbedingt|ausnahmslos)\b",
    re.IGNORECASE,
)
_TOPICS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("modellwahl", re.compile(r"\b(opus|sonnet|haiku|modell\w*|effort|medium|xhigh)\b", re.IGNORECASE)),
    ("anrede", re.compile(r"\b(anrede|ansprechen|duzen|siezen|\"sir\")\b", re.IGNORECASE)),
)


def topic_of(text: str) -> str:
    for name, rx in _TOPICS:
        if rx.search(text):
            return name
    return ""


@dataclass(slots=True)
class MigrationItem:
    source_path: str
    title: str
    text: str
    topic: str
    created: str
    updated: str
    active: bool = True
    note: str = ""


def plan_migration(vault: Vault, book: RuleBook | None = None) -> list[MigrationItem]:
    """Preference notes that read like lasting rules and are not migrated yet. Per topic only the newest stays
    active; older contradicting ones become inactive rules with a note (nothing is deleted, the source notes are
    not touched)."""
    book = book or RuleBook(vault)
    done = {r.migrated_from for r in book.all() if r.migrated_from}
    folder = vault.root / "Präferenzen"
    cands: list[MigrationItem] = []
    if not folder.is_dir():
        return cands
    for p in sorted(folder.glob("*.md")):
        rel = vault.relpath(p)
        if rel in done:
            continue
        try:
            note = parse_note(p.read_text(encoding="utf-8"), rel)
        except (OSError, ValueError):
            continue
        if note.kind != "preference" or note.status != "active" or note.sensitivity == "sensitive":
            continue
        text = rule_text(note.body)
        if len(text) < 12 or not _PERMANENT_RE.search(f"{note.title} {text}"):
            continue
        cands.append(
            MigrationItem(
                source_path=rel,
                title=note.title,
                text=text,
                topic=topic_of(f"{note.title} {text}"),
                created=note.created,
                updated=note.updated or note.created,
            )
        )
    newest: dict[str, MigrationItem] = {}
    for c in cands:
        if c.topic and (
            c.topic not in newest or (c.updated, c.created) > (newest[c.topic].updated, newest[c.topic].created)
        ):
            newest[c.topic] = c
    for c in cands:
        if c.topic and newest[c.topic] is not c:
            c.active = False
            c.note = f"überholt durch die neuere Regel „{newest[c.topic].title}“ (Thema {c.topic})"
    return cands
