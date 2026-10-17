"""In-process async event bus.

Components publish typed events (``task.updated``, ``chat.delta``, ``approval.requested`` ...).
Subscribers are WebSocket clients (desktop app, voice daemon), the notification router and
internal listeners. The bus is deliberately simple and in-process; for a multi-node VPS
deployment it can be swapped for Postgres LISTEN/NOTIFY or Redis behind the same interface.

Every event carries one envelope (``Event.to_dict``): ``id``, ``seq`` (monotonic per process),
``ts``, ``type``, ``source`` (agent | memory | tool | mcp | coding | tasks | scheduler | browser |
voice | telephony | system), ``session_id``, ``task_id`` and ``nodes`` – the ids of the
knowledge-graph nodes the event touches (see ``jarvis.graph``). ``session_id``/``task_id`` are
taken from the event data or, if absent, from the activity context of the running coroutine
(``activity``), so a memory lookup inside a tool call is attributed to that call's session and task.
Only observable actions are published – never invented reasoning.
"""

from __future__ import annotations

import asyncio
import contextvars
import fnmatch
import itertools
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

Handler = Callable[["Event"], Awaitable[None]]


_SOURCES: dict[str, str] = {
    "chat": "agent",
    "session": "agent",
    "agent": "agent",
    "memory": "memory",
    "graph": "memory",
    "tool": "tool",
    "approval": "tool",
    "mcp": "mcp",
    "coding": "coding",
    "task": "tasks",
    "schedule": "scheduler",
    "voice": "voice",
    "telephony": "telephony",
}
_seq = itertools.count(1)


@dataclass(slots=True)
class Activity:
    """Who is acting right now (propagated through ``contextvars`` into nested calls)."""

    session_id: str | None = None
    task_id: str | None = None
    tool: str | None = None


_activity: contextvars.ContextVar[Activity | None] = contextvars.ContextVar("jarvis_activity", default=None)


def current_activity() -> Activity:
    return _activity.get() or Activity()


@contextmanager
def activity(**fields: str | None) -> Iterator[Activity]:
    """Attribute every event published inside this block to the given session/task/tool."""
    base = current_activity()
    act = Activity(
        session_id=fields.get("session_id", base.session_id),
        task_id=fields.get("task_id", base.task_id),
        tool=fields.get("tool", base.tool),
    )
    token = _activity.set(act)
    try:
        yield act
    finally:
        _activity.reset(token)


def source_for(type_: str, data: dict[str, Any]) -> str:
    if type_.startswith("tool.") and str(data.get("connector", "")).startswith("mcp"):
        return "mcp"
    if type_.startswith("tool.") and data.get("connector") == "browser":
        return "browser"
    return _SOURCES.get(type_.split(".", 1)[0], "system")


@dataclass(slots=True)
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    seq: int = field(default_factory=lambda: next(_seq))
    source: str = "system"
    session_id: str | None = None
    task_id: str | None = None
    nodes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "seq": self.seq,
            "type": self.type,
            "ts": self.ts,
            "source": self.source,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "nodes": self.nodes,
            "data": self.data,
        }


def derive_nodes(type_: str, data: dict[str, Any], session_id: str | None, task_id: str | None) -> list[str]:
    """Graph nodes an event touches, by convention (ids as built by ``jarvis.graph``)."""
    family = type_.split(".", 1)[0]
    out: list[str] = []
    if family in ("chat", "session"):
        out.append("agent:jarvis")
        raw = data.get("data")
        inner: dict[str, Any] = raw if isinstance(raw, dict) else {}
        name = inner.get("name")
        if type_.startswith("chat.tool_") and name and name != "tool":
            out.append(f"tool:{name}")
    if family in ("tool", "approval") and data.get("tool"):
        out.append(f"tool:{data['tool']}")
        if data.get("connector"):
            out.append(f"connector:{data['connector']}")
    if family == "task" and task_id:
        out.append(f"task:{task_id}")
    if family == "coding":
        out.append("agent:coding")
        if data.get("job_id"):
            out.append(f"coding:{data['job_id']}")
    if family == "schedule":
        out.append("agent:scheduler")
        if data.get("id"):
            out.append(f"schedule:{data['id']}")
    if family == "voice":
        out.append("agent:voice")
    if family == "telephony":
        out.append("agent:telephony")
    if family == "memory" and data.get("id") and type_ != "memory.pending":
        out.append(f"memory:{data['id']}")
    if session_id and family in ("chat", "session", "tool", "memory"):
        out.append(f"session:{session_id}")
    return out


def make_event(type_: str, data: dict[str, Any], nodes: list[str] | None = None) -> Event:
    act = current_activity()
    sid = data.get("session_id") or act.session_id
    tid = data.get("task_id") or act.task_id
    sid = str(sid) if sid else None
    tid = str(tid) if tid else None
    return Event(
        type=type_,
        data=data,
        source=source_for(type_, data),
        session_id=sid,
        task_id=tid,
        nodes=list(dict.fromkeys([*(nodes or []), *derive_nodes(type_, data, sid, tid)])),
    )


class EventBus:
    def __init__(self) -> None:
        self._handlers: list[tuple[str, Handler]] = []
        self._queues: set[asyncio.Queue[Event]] = set()

    def subscribe(self, pattern: str, handler: Handler) -> None:
        self._handlers.append((pattern, handler))

    def unsubscribe(self, pattern: str, handler: Handler) -> None:
        self._handlers = [(p, h) for p, h in self._handlers if not (p == pattern and h == handler)]

    def open_queue(self, maxsize: int = 1000) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self._queues.add(q)
        return q

    def close_queue(self, q: asyncio.Queue[Event]) -> None:
        self._queues.discard(q)

    async def publish(self, type_: str, *, nodes: list[str] | None = None, **data: Any) -> Event:
        event = make_event(type_, data, nodes)
        for q in list(self._queues):
            if q.full():
                try:
                    q.get_nowait()  # drop oldest for slow consumers
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(event)
        for pattern, handler in list(self._handlers):
            if fnmatch.fnmatchcase(type_, pattern):
                try:
                    await handler(event)
                except Exception:
                    logger.exception("Event handler failed for %s", type_)
        return event

    def publish_nowait(self, type_: str, *, nodes: list[str] | None = None, **data: Any) -> None:
        """Fire-and-forget publish usable from sync code running inside the event loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self.publish(type_, nodes=nodes, **data))
        _background.add(task)
        task.add_done_callback(_background.discard)


bus = EventBus()

_background: set[asyncio.Task[Any]] = set()


def spawn(coro: Any, name: str | None = None) -> asyncio.Task[Any]:
    """Run a fire-and-forget coroutine while keeping a strong reference (avoids GC of running tasks)."""
    task = asyncio.get_running_loop().create_task(coro, name=name)
    _background.add(task)

    def _done(t: asyncio.Task[Any]) -> None:
        _background.discard(t)
        if not t.cancelled() and t.exception() is not None:
            logger.error("Background task %s failed", t.get_name(), exc_info=t.exception())

    task.add_done_callback(_done)
    return task
