"""Chronological chat timeline: everything that happens in a conversation as one ordered list of items.

Item kinds (``TimelineItem.kind``):

* ``message``  – ``role`` user | assistant | system. Assistant text is split into *segments*: a tool call (or a
  reasoning block) closes the running segment, the next text opens a new one – so text and tool calls interleave
  exactly in event order.
* ``tool``     – one tool call (``tool_use_id``, status running | ok | failed, start/end in ms, input, result,
  optional ``display`` artifact such as a route for the map).
* ``process``  – a step of the "Gedankenprozess": ``step`` reasoning | model | memory | search | tool | plan |
  action | summary and ``source`` model (real model output, e.g. reasoning summaries), protocol (a logged action)
  or summary (built afterwards from logged data). Nothing here is invented: without model reasoning there is no
  reasoning item, and the turn records ``reasoning: false``.
* ``turn``     – status/model/timing of one exchange (not rendered as a row).

Every item has a stable ``id``, a creation time ``ts`` (ms since epoch), a per-conversation ``seq`` and the
``turn_id`` of its exchange. Items are persisted in ``chat_events`` (upsert, written in order by one writer per
turn) and published as ``chat.event`` (full item). Streaming text arrives as ``chat.delta`` / ``chat.thinking``
carrying the item id, so clients append to the right segment without re-mounting anything.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

from sqlalchemy import String, cast, func, select
from sqlalchemy.dialects.postgresql import insert

from jarvis.db import session_scope
from jarvis.db.models import ChatEvent, ChatMessage
from jarvis.tools.artifacts import ArtifactStore, artifacts

if TYPE_CHECKING:
    from jarvis.agent.llm import AgentEvent

logger = logging.getLogger(__name__)

Source = Literal["model", "protocol", "summary"]
Publish = Callable[..., Awaitable[Any]]
MAX_INPUT_CHARS = 4000
MAX_RESULT_CHARS = 4000
PREVIEW_CHARS = 280


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


@dataclass(slots=True)
class TimelineItem:
    id: str
    session_id: str
    turn_id: str | None
    seq: int
    ts: int
    kind: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "seq": self.seq,
            "ts": self.ts,
            "kind": self.kind,
            "data": self.data,
        }


# ---------------------------------------------------------------------------------------------- storage
class TimelineStore(Protocol):
    async def max_seq(self, session_id: str) -> int: ...

    async def upsert(self, item: dict[str, Any]) -> None: ...

    async def list(
        self, session_id: str, *, turns: int | None = None, before_seq: int | None = None
    ) -> tuple[list[dict[str, Any]], bool]:
        """Items sorted by ``seq``: the newest ``turns`` exchanges (before ``before_seq``) and whether older exist."""
        ...

    async def get(self, item_id: str) -> dict[str, Any] | None: ...


def _window(
    items: list[dict[str, Any]], turns: int | None, before_seq: int | None
) -> tuple[list[dict[str, Any]], bool]:
    """Pure paging rule shared by the stores: whole turns only, never a turn cut in half."""
    items = [i for i in items if before_seq is None or i["seq"] < before_seq]
    if turns is None:
        return items, False
    first_seq: dict[str, int] = {}
    for i in items:
        key = str(i.get("turn_id") or i["id"])
        first_seq[key] = min(first_seq.get(key, i["seq"]), i["seq"])
    keep = sorted(first_seq.values())[-turns:] if turns > 0 else []
    if len(keep) == len(first_seq):
        return items, False
    cutoff = keep[0] if keep else None
    return ([i for i in items if cutoff is not None and i["seq"] >= cutoff], True)


class DbTimelineStore:
    async def max_seq(self, session_id: str) -> int:
        async with session_scope() as s:
            value = (
                await s.execute(select(func.max(ChatEvent.seq)).where(ChatEvent.session_id == uuid.UUID(session_id)))
            ).scalar_one_or_none()
        return int(value or 0)

    async def upsert(self, item: dict[str, Any]) -> None:
        async with session_scope() as s:
            await s.execute(upsert_statement(item))

    async def list(
        self, session_id: str, *, turns: int | None = None, before_seq: int | None = None
    ) -> tuple[list[dict[str, Any]], bool]:
        cond = window_conditions(session_id, before_seq)
        async with session_scope() as s:
            has_more = False
            if turns is not None:
                firsts = [int(v) for v in (await s.execute(turn_starts_statement(cond, turns))).scalars().all()]
                if len(firsts) > turns >= 1:
                    has_more = True
                    cond.append(ChatEvent.seq >= firsts[turns - 1])
            rows = (await s.execute(select(ChatEvent).where(*cond).order_by(ChatEvent.seq))).scalars().all()
        return [_row_dict(r) for r in rows], has_more

    async def get(self, item_id: str) -> dict[str, Any] | None:
        async with session_scope() as s:
            row = await s.get(ChatEvent, item_id)
        return _row_dict(row) if row is not None else None


def upsert_statement(item: dict[str, Any]) -> Any:
    """Insert, or update only the payload of an existing item (id, seq and position never change)."""
    stmt = insert(ChatEvent).values(
        id=item["id"],
        session_id=uuid.UUID(item["session_id"]),
        turn_id=uuid.UUID(item["turn_id"]) if item.get("turn_id") else None,
        seq=item["seq"],
        ts=item["ts"],
        kind=item["kind"],
        data=item["data"],
    )
    return stmt.on_conflict_do_update(
        index_elements=[ChatEvent.id], set_={"data": stmt.excluded.data, "updated_at": func.now()}
    )


def window_conditions(session_id: str, before_seq: int | None) -> list[Any]:
    cond: list[Any] = [ChatEvent.session_id == uuid.UUID(session_id)]
    if before_seq is not None:
        cond.append(ChatEvent.seq < before_seq)
    return cond


def turn_starts_statement(cond: list[Any], turns: int) -> Any:
    """First ``seq`` of every turn, newest first; the ``turns``-th one is where the window starts."""
    return (
        select(func.min(ChatEvent.seq).label("s"))
        .where(*cond)
        .group_by(func.coalesce(cast(ChatEvent.turn_id, String), ChatEvent.id))
        .order_by(func.min(ChatEvent.seq).desc())
        .limit(turns + 1)
    )


def _row_dict(r: ChatEvent) -> dict[str, Any]:
    return {
        "id": r.id,
        "session_id": str(r.session_id),
        "turn_id": str(r.turn_id) if r.turn_id else None,
        "seq": r.seq,
        "ts": r.ts,
        "kind": r.kind,
        "data": dict(r.data or {}),
    }


class MemoryTimelineStore:
    """In-memory store with the same semantics (tests, and a fallback that never loses the live order)."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    async def max_seq(self, session_id: str) -> int:
        return max((i["seq"] for i in self.items.values() if i["session_id"] == session_id), default=0)

    async def upsert(self, item: dict[str, Any]) -> None:
        prev = self.items.get(item["id"])
        if prev is not None:  # like the DB: only the payload changes, position and identity never do
            self.items[item["id"]] = {**prev, "data": json.loads(json.dumps(item["data"]))}
            return
        if any(i["session_id"] == item["session_id"] and i["seq"] == item["seq"] for i in self.items.values()):
            raise ValueError(f"duplicate seq {item['seq']}")
        self.items[item["id"]] = json.loads(json.dumps(item))

    async def list(
        self, session_id: str, *, turns: int | None = None, before_seq: int | None = None
    ) -> tuple[list[dict[str, Any]], bool]:
        items = sorted((i for i in self.items.values() if i["session_id"] == session_id), key=lambda i: i["seq"])
        return _window(items, turns, before_seq)

    async def get(self, item_id: str) -> dict[str, Any] | None:
        return self.items.get(item_id)


class SeqAllocator:
    """Strictly increasing sequence number per conversation (continues after a restart from the stored maximum)."""

    def __init__(self, store: TimelineStore) -> None:
        self.store = store
        self._next: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def ensure(self, session_id: str) -> None:
        if session_id in self._next:
            return
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            if session_id not in self._next:
                self._next[session_id] = await self.store.max_seq(session_id) + 1

    def next(self, session_id: str) -> int:
        n = self._next[session_id]
        self._next[session_id] = n + 1
        return n

    async def allocate(self, session_id: str) -> int:
        await self.ensure(session_id)
        return self.next(session_id)


# ---------------------------------------------------------------------------------------------- helpers
def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _pretty(value: Any, limit: int) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return _clip(value, limit)
    try:
        return _clip(json.dumps(value, ensure_ascii=False, indent=2, default=str), limit)
    except (TypeError, ValueError):
        return _clip(str(value), limit)


def _one_line(value: Any, limit: int = 240) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return _clip(" ".join(value.split()), limit)
    if isinstance(value, dict):
        parts = [
            f"{k}: {v if isinstance(v, str | int | float | bool) else json.dumps(v, ensure_ascii=False)}"
            for k, v in value.items()
        ]
        return _clip(", ".join(parts), limit)
    return _clip(json.dumps(value, ensure_ascii=False, default=str), limit)


def tool_result_view(raw: str) -> tuple[str, str]:
    """(pretty result, one-line preview) from a Jarvis MCP payload ``{"status", "tool", "data"|"message"}``."""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return _clip(raw or "", MAX_RESULT_CHARS), _clip(" ".join((raw or "").split()), PREVIEW_CHARS)
    shown: Any = payload
    if isinstance(payload, dict) and {"status", "tool"} <= payload.keys():
        shown = payload.get("data") if payload.get("data") is not None else payload.get("message", "")
    return _pretty(shown, MAX_RESULT_CHARS), _one_line(shown, PREVIEW_CHARS)


def tool_step(name: str) -> str:
    if name.startswith("memory_"):
        return "memory"
    if any(k in name for k in ("search", "find", "lookup", "query", "geocode")):
        return "search"
    return "tool"


_STEP_TITLE = {"memory": "Gedächtnis", "search": "Suche", "tool": "Werkzeug"}


# ---------------------------------------------------------------------------------------------- recorder
class TurnRecorder:
    """Records one exchange (user message → Jarvis' answer) into the timeline of its conversation."""

    def __init__(
        self,
        store: TimelineStore,
        allocator: SeqAllocator,
        session_id: str,
        *,
        publish: Publish,
        turn_id: str | None = None,
        artifact_store: ArtifactStore = artifacts,
        clock: Callable[[], int] = now_ms,
        plan_steps: Callable[[str], Awaitable[list[str]]] | None = None,
    ) -> None:
        self.store = store
        self.allocator = allocator
        self.session_id = session_id
        self.turn_id = turn_id or str(uuid.uuid4())
        self.publish = publish
        self.artifacts = artifact_store
        self.clock = clock
        self.plan_steps = plan_steps
        self.items: dict[str, TimelineItem] = {}
        self.turn: TimelineItem | None = None
        self._text: TimelineItem | None = None  # open assistant text segment
        self._thinking: TimelineItem | None = None  # open reasoning block
        self._tools: dict[str, TimelineItem] = {}  # tool_use_id -> tool item
        self._tool_args: dict[str, dict[str, Any]] = {}  # tool item id -> its arguments (for the display)
        self._tool_process: dict[str, TimelineItem] = {}  # tool item id -> its process step
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._writer: asyncio.Task[None] | None = None
        self.finished = False

    # ------------------------------------------------------------------ plumbing
    async def _new(self, kind: str, data: dict[str, Any], prefix: str) -> TimelineItem:
        await self.allocator.ensure(self.session_id)
        item = TimelineItem(
            id=new_id(prefix),
            session_id=self.session_id,
            turn_id=self.turn_id,
            seq=self.allocator.next(self.session_id),
            ts=self.clock(),
            kind=kind,
            data=data,
        )
        self.items[item.id] = item
        return item

    async def _emit(self, item: TimelineItem, *, persist: bool = True) -> None:
        if persist:
            self._save(item)
        await self.publish("chat.event", session_id=self.session_id, event=item.to_dict())

    def _save(self, item: TimelineItem) -> None:
        if self._writer is None:
            self._writer = asyncio.get_running_loop().create_task(self._write_loop(), name="timeline-writer")
        self._queue.put_nowait(json.loads(json.dumps(item.to_dict(), default=str)))  # snapshot

    async def _write_loop(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    return
                await self.store.upsert(item)
            except Exception:
                logger.warning("Timeline item %s not persisted", item and item.get("id"), exc_info=True)
            finally:
                self._queue.task_done()

    async def flush(self) -> None:
        if self._writer is None:
            return
        self._queue.put_nowait(None)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._writer, 10)
        self._writer = None

    # ------------------------------------------------------------------ turn start
    async def start(
        self, user_text: str | None, channel: str, attachments: list[dict[str, Any]] | None = None
    ) -> TimelineItem | None:
        user = None
        if user_text is not None:
            data: dict[str, Any] = {"role": "user", "text": user_text, "status": "done", "channel": channel}
            if attachments:
                data["attachments"] = attachments
            user = await self._new("message", data, "msg")
            await self._emit(user)
        self.turn = await self._new(
            "turn", {"status": "running", "channel": channel, "started_ts": self.clock(), "reasoning": False}, "turn"
        )
        await self._emit(self.turn)
        return user

    async def set_model(self, model: str, label: str, reason: str) -> None:
        if self.turn is not None:
            self.turn.data["model"] = model
            await self._emit(self.turn)
        await self.process("model", f"Modell: {label}", text=reason)

    async def process(
        self,
        step: str,
        title: str,
        *,
        source: Source = "protocol",
        text: str = "",
        status: str | None = None,
        items: list[str] | None = None,
        ref: str | None = None,
    ) -> TimelineItem:
        data: dict[str, Any] = {"step": step, "source": source, "title": _clip(title, 200)}
        if text:
            data["text"] = text
        if status:
            data["status"] = status
        if items:
            data["items"] = [_clip(i, 160) for i in items[:12]]
        if ref:
            data["ref"] = ref
        item = await self._new("process", data, "proc")
        await self._emit(item)
        return item

    # ------------------------------------------------------------------ agent stream
    async def _close_text(self) -> None:
        seg, self._text = self._text, None
        if seg is not None:
            seg.data["status"] = "done"
            await self._emit(seg)

    async def _close_thinking(self) -> None:
        block, self._thinking = self._thinking, None
        if block is not None:
            block.data["status"] = "done"
            await self._emit(block)

    async def on_agent_event(self, ev: AgentEvent) -> dict[str, Any]:
        """Folds one agent event into the timeline; returns the item reference for the bus event."""
        if self.finished:
            return {}
        if ev.type == "delta":
            if not ev.text:
                return {}
            await self._close_thinking()
            if self._text is None:
                self._text = await self._new("message", {"role": "assistant", "text": "", "status": "streaming"}, "msg")
                await self._emit(self._text, persist=False)
            self._text.data["text"] += ev.text
            return {"event_id": self._text.id, "seq": self._text.seq, "turn_id": self.turn_id}
        if ev.type == "thinking":
            if not ev.text:
                return {}
            await self._close_text()
            if self._thinking is None:
                self._thinking = await self._new(
                    "process",
                    {
                        "step": "reasoning",
                        "source": "model",
                        "title": "Überlegung des Modells",
                        "text": "",
                        "status": "running",
                    },
                    "proc",
                )
                await self._emit(self._thinking, persist=False)
                if self.turn is not None and not self.turn.data.get("reasoning"):
                    self.turn.data["reasoning"] = True
                    await self._emit(self.turn)
            self._thinking.data["text"] += ev.text
            return {"event_id": self._thinking.id, "seq": self._thinking.seq, "turn_id": self.turn_id}
        if ev.type == "tool_started":
            await self._close_text()
            await self._close_thinking()
            return await self._tool_started(ev.data)
        if ev.type in ("tool_completed", "tool_failed"):
            return await self._tool_finished(ev.data, ok=ev.type == "tool_completed")
        return {}

    async def _tool_started(self, d: dict[str, Any]) -> dict[str, Any]:
        name = str(d.get("name") or d.get("tool") or "werkzeug")
        use_id = str(d.get("tool_use_id") or "") or new_id("use")
        started = self.clock()
        raw_input = d.get("input", d.get("args"))
        item = await self._new(
            "tool",
            {
                "tool_use_id": use_id,
                "name": name,
                "status": "running",
                "started_ts": started,
                "input": _pretty(raw_input, MAX_INPUT_CHARS),
            },
            "tool",
        )
        self._tools[use_id] = item
        if isinstance(raw_input, dict):
            self._tool_args[item.id] = raw_input
        await self._emit(item)
        step = tool_step(name)
        proc = await self.process(
            step, f"{_STEP_TITLE[step]}: {name}", text=_one_line(raw_input), status="running", ref=item.id
        )
        self._tool_process[item.id] = proc
        return {"event_id": item.id, "seq": item.seq, "turn_id": self.turn_id}

    async def _tool_finished(self, d: dict[str, Any], *, ok: bool) -> dict[str, Any]:
        name = str(d.get("name") or d.get("tool") or "werkzeug")
        use_id = str(d.get("tool_use_id") or "")
        item = self._tools.get(use_id) if use_id else None
        if item is None:  # providers without ids (Hermes): the latest running call of that name
            item = next(
                (
                    t
                    for t in reversed(self._tools.values())
                    if t.data["name"] in (name, "tool") and t.data["status"] == "running"
                ),
                None,
            )
        if item is None:
            ref = await self._tool_started({"name": name, "tool_use_id": use_id})
            item = self.items[ref["event_id"]]
        ended = self.clock()
        raw = d.get("result")
        result, preview = tool_result_view(raw) if isinstance(raw, str) else ("", "")
        item.data.update(
            {
                "status": "ok" if ok else "failed",
                "ended_ts": ended,
                "duration_ms": max(0, ended - int(item.data["started_ts"])),
            }
        )
        if result:
            item.data["result"] = result
        if not ok:
            item.data["error"] = _clip(str(d.get("error") or preview or "Fehlgeschlagen"), 600)
        if isinstance(raw, str) and ok:
            display = self.artifacts.display_for(raw, self._tool_args.get(item.id))
            if display:
                item.data["display"] = display
        await self._emit(item)
        proc = self._tool_process.get(item.id)
        if proc is not None:
            proc.data["status"] = "ok" if ok else "failed"
            if preview:
                proc.data["result"] = preview
            await self._emit(proc)
        return {"event_id": item.id, "seq": item.seq, "turn_id": self.turn_id}

    # ------------------------------------------------------------------ bus observations
    async def on_bus_event(self, ev: Any) -> None:
        """Logged actions that belong to this conversation and happen during the turn."""
        if self.finished or ev.session_id != self.session_id:
            return
        d = ev.data
        if ev.type == "memory.recalled" and d.get("purpose") != "extraction":
            hits = d.get("hits") or []
            via = f" (über {d['via']})" if d.get("via") else ""
            text = f"{len(hits)} Treffer in {d.get('duration_ms', '?')} ms" if hits else "keine passenden Einträge"
            await self.process(
                "memory",
                f"Gedächtnis durchsucht{via}",
                text=text,
                items=[f"{h.get('title')} ({h.get('kind')})" for h in hits],
            )
        elif ev.type == "approval.requested":
            await self.process(
                "action",
                "Geplante Aktion – wartet auf Freigabe",
                text=str(d.get("summary") or d.get("tool") or ""),
                status="waiting",
            )
        elif ev.type == "task.created":
            steps: list[str] = []
            if self.plan_steps is not None and d.get("task_id"):
                try:
                    steps = await self.plan_steps(str(d["task_id"]))
                except Exception:
                    logger.debug("plan steps unavailable", exc_info=True)
            await self.process(
                "plan",
                f"Plan angelegt: {d.get('title') or d.get('playbook') or 'Aufgabe'}",
                text=f"{len(steps)} Schritte" if steps else "",
                items=steps,
            )

    # ------------------------------------------------------------------ end
    async def finish(self, *, error: str | None = None, model_label: str | None = None) -> None:
        if self.finished:
            return
        cancelled = error == "cancelled"
        await self._close_text()
        await self._close_thinking()
        ended = self.clock()
        for t in self._tools.values():  # calls without result: the run ended underneath them
            if t.data["status"] == "running":
                t.data.update(
                    {
                        "status": "failed",
                        "ended_ts": ended,
                        "duration_ms": max(0, ended - int(t.data["started_ts"])),
                        "error": "Abgebrochen" if cancelled else "Kein Ergebnis erhalten",
                    }
                )
                await self._emit(t)
                proc = self._tool_process.get(t.id)
                if proc is not None:
                    proc.data["status"] = "failed"
                    await self._emit(proc)
        if cancelled:
            msg = await self._new(
                "message",
                {"role": "system", "text": "Antwort wurde unterbrochen.", "level": "warning", "status": "done"},
                "msg",
            )
            await self._emit(msg)
        elif error:
            msg = await self._new(
                "message",
                {"role": "system", "text": f"Fehler: {_clip(error, 400)}", "level": "error", "status": "done"},
                "msg",
            )
            await self._emit(msg)
        tools = list(self._tools.values())
        if self.turn is not None:
            started = int(self.turn.data["started_ts"])
            failed = sum(1 for t in tools if t.data["status"] == "failed")
            parts = [f"Dauer {(ended - started) / 1000:.1f} s".replace(".", ",")]
            if tools:
                parts.append(
                    f"{len(tools)} Werkzeug{'e' if len(tools) != 1 else ''}"
                    + (f" (davon {failed} fehlgeschlagen)" if failed else "")
                )
            recalls = sum(
                1
                for i in self.items.values()
                if i.kind == "process" and i.data.get("title", "").startswith("Gedächtnis durchsucht")
            )
            if recalls:
                parts.append(f"{recalls} Gedächtnisabruf{'e' if recalls != 1 else ''}")
            if model_label:
                parts.append(f"Modell {model_label}")
            if not self.turn.data.get("reasoning"):
                parts.append("keine Modellgedanken übermittelt")
            await self.process("summary", "Ablauf", source="summary", text="; ".join(parts))
            self.turn.data.update(
                {
                    "status": "cancelled" if cancelled else "failed" if error else "done",
                    "ended_ts": ended,
                    "duration_ms": max(0, ended - started),
                }
            )
            if error and not cancelled:
                self.turn.data["error"] = _clip(error, 400)
            await self._emit(self.turn)
        self.finished = True
        await self.flush()


# ---------------------------------------------------------------------------------------------- reading
def legacy_items(session_id: str, messages: list[ChatMessage], covered_turns: set[str]) -> list[dict[str, Any]]:
    """Messages stored before the timeline existed, as timeline items (negative seq: they precede every event)."""
    out: list[dict[str, Any]] = []
    turn: str | None = None
    for m in messages:
        meta = m.meta or {}
        if m.role not in ("user", "assistant", "system") or str(meta.get("turn_id")) in covered_turns:
            continue
        if m.role == "user" or turn is None:
            turn = f"legacy-{m.id}"
        base = {"session_id": session_id, "turn_id": turn, "ts": int(m.created_at.timestamp() * 1000)}
        if m.role == "assistant":  # the old format only knew which tools ran, not where: they precede the text
            marks = [t for t in meta.get("tools") or [] if t.get("name")]
            started = [t["name"] for t in marks if t.get("event") == "tool_started"]
            failed = {t["name"] for t in marks if t.get("event") == "tool_failed"}
            for k, name in enumerate(started):
                out.append(
                    {
                        **base,
                        "id": f"legacy-tool-{m.id}-{k}",
                        "kind": "tool",
                        "data": {"name": name, "status": "failed" if name in failed else "ok", "legacy": True},
                    }
                )
        data: dict[str, Any] = {"role": m.role, "text": m.content, "status": "done", "legacy": True}
        for key in ("checks", "error"):
            if meta.get(key):
                data[key] = meta[key]
        out.append({**base, "id": str(m.id), "kind": "message", "data": data})
    for n, r in enumerate(out):  # dense negative seq: older than every recorded event, order explicit in seq
        r["seq"] = n - len(out)
    return out


def load_timeline(session_id: str, events: list[dict[str, Any]], messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """Recorded items plus legacy messages of turns without recorded items, in one ``seq`` order."""
    covered = {str(e["turn_id"]) for e in events if e.get("turn_id")}
    return [*legacy_items(session_id, messages, covered), *sorted(events, key=lambda e: e["seq"])]


async def append_message(
    store: TimelineStore,
    allocator: SeqAllocator,
    session_id: str,
    role: str,
    text: str,
    *,
    publish: Publish,
) -> dict[str, Any]:
    """A single message outside a turn (e.g. Jarvis' greeting when a call starts) as its own exchange."""
    item = TimelineItem(
        id=new_id("msg"),
        session_id=session_id,
        turn_id=str(uuid.uuid4()),
        seq=await allocator.allocate(session_id),
        ts=now_ms(),
        kind="message",
        data={"role": role, "text": text, "status": "done"},
    ).to_dict()
    try:
        await store.upsert(item)
    except Exception:
        logger.warning("Timeline message not persisted", exc_info=True)
    await publish("chat.event", session_id=session_id, event=item)
    return item


async def append_tool_display(
    store: TimelineStore,
    allocator: SeqAllocator,
    session_id: str,
    name: str,
    display: dict[str, Any],
    *,
    publish: Publish,
    via: str = "approval",
) -> dict[str, Any]:
    """A tool result that arrives outside its turn (e.g. an image generated after Sir's approval) as its own
    exchange – otherwise it would only be visible in the approvals view, never in the conversation."""
    ts = now_ms()
    item = TimelineItem(
        id=new_id("tool"),
        session_id=session_id,
        turn_id=str(uuid.uuid4()),
        seq=await allocator.allocate(session_id),
        ts=ts,
        kind="tool",
        data={"name": name, "status": "ok", "started_ts": ts, "ended_ts": ts, "display": display, "via": via},
    ).to_dict()
    try:
        await store.upsert(item)
    except Exception:
        logger.warning("Timeline tool display not persisted", exc_info=True)
    await publish("chat.event", session_id=session_id, event=item)
    return item


def redact_for_mobile(item: dict[str, Any]) -> dict[str, Any]:
    """The phone gets no memory titles (same rule as for ``memory.recalled``)."""
    data = item.get("data") or {}
    if item.get("kind") == "process" and data.get("step") == "memory" and (data.get("items") or data.get("result")):
        return {**item, "data": {**{k: v for k, v in data.items() if k not in ("items", "result")}, "redacted": True}}
    if item.get("kind") == "tool" and str(data.get("name", "")).startswith("memory_") and data.get("result"):
        return {**item, "data": {**{k: v for k, v in data.items() if k != "result"}, "redacted": True}}
    return item
