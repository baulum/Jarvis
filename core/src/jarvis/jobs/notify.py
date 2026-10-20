"""Notification routing: desktop (WebSocket + macOS), Telegram, voice and phone.

Rules from ``config/jarvis.yaml`` map event types to channels; quiet hours suppress
everything except desktop unless a rule sets ``bypass_quiet_hours``.
"""

from __future__ import annotations

import fnmatch
import logging
from datetime import datetime, time
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update

from jarvis.agent.persona import TZ
from jarvis.db import session_scope
from jarvis.db.models import Notification
from jarvis.events import Event, bus

if TYPE_CHECKING:
    from jarvis.app_state import AppState

logger = logging.getLogger(__name__)


_CATEGORY = {
    "task.completed": "task",
    "task.failed": "error",
    "task.input_required": "task",
    "approval.requested": "approval",
    "coding.finished": "task",
    "memory.pending": "system",
    "system.critical": "error",
}


def _alert_for(event: str, title: str, body: str, level: str, data: dict[str, Any], nid: Any) -> Any:
    from jarvis.mobile.alerts import Alert

    category = _CATEGORY.get(event, "system")
    if event == "coding.finished" and data.get("status") != "completed":
        category = "error"
    key_id = data.get("approval_id") or data.get("id") or data.get("task_id") or data.get("job_id") or nid
    link = "jarvis://activity"
    if data.get("task_id"):
        link = f"jarvis://task/{data['task_id']}"
    if data.get("job_id"):  # a coding job has its own detail view (preferred over the task it belongs to)
        link = f"jarvis://job/{data['job_id']}"
    if event == "approval.requested" and data.get("approval_id"):
        link = f"jarvis://approval/{data['approval_id']}"
    extra = {"approval_id": data["approval_id"]} if data.get("approval_id") else {}
    return Alert(
        category,
        title,
        body,
        "critical" if level == "critical" else "warning" if level == "warning" else "info",
        event_key=f"{event}:{key_id}",
        deep_link=link,
        data={**{k: data[k] for k in ("task_id", "job_id") if data.get(k)}, **extra},
        source=event,
    )


class Notifier:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self.cfg = state.settings.notifications

    def start(self) -> None:
        bus.subscribe("task.completed", self._on_event)
        bus.subscribe("task.failed", self._on_event)
        bus.subscribe("task.input_required", self._on_event)
        bus.subscribe("approval.requested", self._on_event)
        bus.subscribe("coding.finished", self._on_event)
        bus.subscribe("memory.pending", self._on_event)
        bus.subscribe("system.critical", self._on_event)

    def _channels_for(self, event: str) -> tuple[list[str], bool]:
        for rule in self.cfg.rules:
            if fnmatch.fnmatchcase(event, rule.event):
                return list(rule.channels), rule.bypass_quiet_hours
        return list(self.cfg.default_channels), False

    def _quiet(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(TZ)
        start = time.fromisoformat(self.cfg.quiet_hours.start)
        end = time.fromisoformat(self.cfg.quiet_hours.end)
        t = now.time()
        return (start <= t or t < end) if start > end else (start <= t < end)

    async def notify(
        self,
        event: str,
        title: str,
        body: str = "",
        *,
        channels: list[str] | None = None,
        level: str = "info",
        data: dict[str, Any] | None = None,
    ) -> list[str]:
        chosen, bypass = (channels, level == "critical") if channels else self._channels_for(event)
        if self._quiet() and not bypass:  # the phone applies its own quiet hours (silent instead of nothing)
            chosen = [c for c in chosen if c in ("desktop", "mobile")]
        delivered: list[str] = []
        async with session_scope() as s:
            n = Notification(event=event, level=level, title=title[:300], body=body[:4000], data=data or {})
            s.add(n)
            await s.flush()
            nid = n.id
        if "desktop" in chosen:
            await bus.publish(
                "notification", id=str(nid), event=event, level=level, title=title, body=body, data=data or {}
            )
            delivered.append("desktop")
        if "voice" in chosen:
            await bus.publish("voice.say", text=f"{title}. {body}"[:400])
            delivered.append("voice")
        if "telegram" in chosen:
            tg = self.state.connectors.connectors.get("telegram")
            if tg is not None and tg.active and event != "approval.requested":  # telegram sends its own cards
                try:
                    await tg.send(f"{title}\n{body}".strip())  # type: ignore[attr-defined]
                    delivered.append("telegram")
                except Exception as e:
                    logger.warning("Telegram notification failed: %s", e)
            elif tg is not None and tg.active:
                delivered.append("telegram")
        if "mobile" in chosen:
            try:
                res = await self.state.alerts.dispatch(_alert_for(event, title, body, level, data or {}, nid))
                if res.get("delivered") or res.get("call_id"):
                    delivered.append("mobile")
            except Exception as e:
                logger.warning("Mobile notification failed: %s", e)
        if "phone" in chosen and self.state.telephony is not None:
            try:
                await self.state.telephony.call_owner(f"{title}. {body}"[:500], reason=event)
                delivered.append("phone")
            except Exception as e:
                logger.warning("Phone notification failed: %s", e)
        async with session_scope() as s:
            await s.execute(update(Notification).where(Notification.id == nid).values(delivered=delivered))
        return delivered

    async def _on_event(self, ev: Event) -> None:
        d = ev.data
        if ev.type == "task.completed":
            warn = d.get("result", {}).get("warnings") or []
            body = "Abgeschlossen." + (f" Hinweise: {'; '.join(warn)}" if warn else "")
            await self.notify(ev.type, f"Aufgabe erledigt: {d.get('title')}", body, data=d)
        elif ev.type == "task.failed":
            await self.notify(
                ev.type, f"Aufgabe fehlgeschlagen: {d.get('title')}", str(d.get("error") or ""), level="warning", data=d
            )
        elif ev.type == "task.input_required":
            await self.notify(ev.type, f"Angaben benötigt: {d.get('title')}", str(d.get("question") or ""), data=d)
        elif ev.type == "approval.requested":
            await self.notify(ev.type, "Freigabe erforderlich", str(d.get("summary")), level="warning", data=d)
        elif ev.type == "coding.finished":
            await self.notify(
                ev.type,
                f"Coding-Job {d.get('status')}: {d.get('title')}",
                str(d.get("summary", ""))[:500],
                level="info" if d.get("status") == "completed" else "warning",
                data=d,
            )
        elif ev.type == "memory.pending":
            await self.notify(ev.type, "Sensible Erinnerung wartet auf Bestätigung", str(d.get("title")), data=d)
        elif ev.type == "system.critical":
            await self.notify(
                ev.type, str(d.get("title", "Kritisches Ereignis")), str(d.get("body", "")), level="critical", data=d
            )

    async def recent(self, unread_only: bool = False, limit: int = 100) -> list[Notification]:
        q = select(Notification).order_by(Notification.created_at.desc()).limit(limit)
        if unread_only:
            q = q.where(Notification.read.is_(False))
        async with session_scope() as s:
            return list((await s.execute(q)).scalars())

    async def mark_read(self, ids: list[str] | None = None) -> None:
        async with session_scope() as s:
            q = update(Notification).values(read=True)
            if ids:
                import uuid

                q = q.where(Notification.id.in_([uuid.UUID(i) for i in ids]))
            await s.execute(q)
