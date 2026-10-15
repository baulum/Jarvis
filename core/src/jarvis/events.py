"""In-process async event bus.

Components publish typed events (``task.updated``, ``chat.delta``, ``approval.requested`` ...).
Subscribers are WebSocket clients (desktop app, voice daemon), the notification router and
internal listeners. The bus is deliberately simple and in-process; for a multi-node VPS
deployment it can be swapped for Postgres LISTEN/NOTIFY or Redis behind the same interface.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

Handler = Callable[["Event"], Awaitable[None]]


@dataclass(slots=True)
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "ts": self.ts, "data": self.data}


class EventBus:
    def __init__(self) -> None:
        self._handlers: list[tuple[str, Handler]] = []
        self._queues: set[asyncio.Queue[Event]] = set()

    def subscribe(self, pattern: str, handler: Handler) -> None:
        self._handlers.append((pattern, handler))

    def open_queue(self, maxsize: int = 1000) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self._queues.add(q)
        return q

    def close_queue(self, q: asyncio.Queue[Event]) -> None:
        self._queues.discard(q)

    async def publish(self, type_: str, **data: Any) -> Event:
        event = Event(type=type_, data=data)
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

    def publish_nowait(self, type_: str, **data: Any) -> None:
        """Fire-and-forget publish usable from sync code running inside the event loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.publish(type_, **data))


bus = EventBus()
