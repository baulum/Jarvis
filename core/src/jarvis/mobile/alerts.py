"""Notification & Calling Engine.

Decides for every alert whether Sir gets **nothing**, a **silent** notification, a normal **push** or an
incoming **call** – from his policy (per category, quiet hours) and the urgency. Guarantees:

* A call happens only on an explicit instruction (``call_requested`` – Sir asked "ruf mich an, wenn …") or a
  category Sir configured to ``call``. Never because the agent "felt like it".
* At most one call per event (``event_key``), rate limits per hour and a minimum gap between calls.
* No call is attempted when it cannot be completed (agent, voice engine, LiveKit or push not available):
  the alert is delivered as a notification instead, with the reason.
* Quiet hours: push → silent, call → push, unless the alert is critical and the policy lets it through.
* Duplicates (same ``event_key`` within 24 h) are dropped; snoozed alerts come back as notifications.

The policy is stored in ``alert-policy.json`` (owner-only changes, audited).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import select

from jarvis.agent.persona import TZ
from jarvis.db import session_scope
from jarvis.db.models import AlertDispatch
from jarvis.events import Event, bus, spawn

if TYPE_CHECKING:
    from jarvis.app_state import AppState

logger = logging.getLogger(__name__)

Category = Literal["task", "appointment", "customer", "error", "approval", "reminder", "system"]
Mode = Literal["off", "silent", "push", "call"]
CATEGORIES: tuple[str, ...] = ("task", "appointment", "customer", "error", "approval", "reminder", "system")
MODES: tuple[str, ...] = ("off", "silent", "push", "call")
LEVELS = {"info": 0, "warning": 1, "critical": 2}

DEFAULT_POLICY: dict[str, Any] = {
    "categories": {
        "task": {"mode": "push"},
        "appointment": {"mode": "push", "lead_minutes": 30},
        "customer": {"mode": "push"},
        "error": {"mode": "push"},
        "approval": {"mode": "push"},
        "reminder": {"mode": "push"},
        "system": {"mode": "silent"},
    },
    "quiet_hours": {"enabled": True, "start": "22:30", "end": "07:00"},
    "critical_bypasses_quiet_hours": True,
    "calls_enabled": True,
}

# Actions the phone offers per category (rendered natively; security-relevant ones open the app).
CATEGORY_ACTIONS: dict[str, list[str]] = {
    "task": ["open", "reply", "call"],
    "appointment": ["open", "snooze"],
    "customer": ["open", "reply"],
    "error": ["open", "reply", "call"],
    "approval": ["approve", "reject", "open"],
    "reminder": ["snooze", "reply"],
    "system": ["open"],
}


@dataclass(slots=True)
class Alert:
    category: str
    title: str
    body: str = ""
    level: str = "info"  # info | warning | critical
    event_key: str = ""
    deep_link: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    call_requested: bool = False  # explicit instruction from Sir (or a call rule) → ring
    call_message: str = ""  # what Jarvis says when Sir picks up
    source: str = "system"
    allow_call: bool = True  # False for follow-ups of a call (a missed call must never ring again)


@dataclass(slots=True)
class Decision:
    mode: str
    reason: str


class AlertPolicy:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        merged: dict[str, Any] = json.loads(json.dumps(DEFAULT_POLICY))
        with contextlib.suppress(FileNotFoundError, json.JSONDecodeError, OSError):
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            merged = self._merge(merged, stored)
        return merged

    @staticmethod
    def _merge(base: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
        for cat, cfg in (new.get("categories") or {}).items():
            if cat in CATEGORIES and isinstance(cfg, dict):
                mode = cfg.get("mode")
                if mode is not None and mode not in MODES:
                    raise ValueError(f"unknown mode {mode!r} for {cat}")
                base["categories"][cat].update({k: v for k, v in cfg.items() if k in ("mode", "lead_minutes")})
        qh = new.get("quiet_hours")
        if isinstance(qh, dict):
            for k in ("start", "end"):
                if k in qh:
                    time.fromisoformat(str(qh[k]))  # validates HH:MM
            base["quiet_hours"].update({k: qh[k] for k in ("enabled", "start", "end") if k in qh})
        for k in ("critical_bypasses_quiet_hours", "calls_enabled"):
            if k in new:
                base[k] = bool(new[k])
        return base

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        self.data = self._merge(json.loads(json.dumps(self.data)), patch)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        return self.data

    def mode_for(self, category: str) -> str:
        return str(self.data["categories"].get(category, {}).get("mode", "push"))

    def quiet(self, now: datetime | None = None) -> bool:
        qh = self.data["quiet_hours"]
        if not qh.get("enabled"):
            return False
        now = now or datetime.now(TZ)
        start, end, t = time.fromisoformat(qh["start"]), time.fromisoformat(qh["end"]), now.time()
        return (start <= t or t < end) if start > end else (start <= t < end)


def decide(policy: AlertPolicy, alert: Alert, now: datetime | None = None) -> Decision:
    """Pure decision (no I/O) – unit tested."""
    mode = policy.mode_for(alert.category)
    reason = f"Regel {alert.category}={mode}"
    if alert.call_requested:
        mode, reason = "call", "ausdrücklicher Anrufwunsch"
    if mode == "off" and alert.level != "critical":
        return Decision("none", reason)
    if mode == "off":
        mode, reason = "push", "kritisch trotz Regel 'aus'"
    if mode == "call" and not alert.allow_call:
        mode, reason = "push", "Folgebenachrichtigung (kein erneuter Anruf)"
    if mode == "call" and not policy.data.get("calls_enabled", True):
        mode, reason = "push", "Anrufe sind deaktiviert"
    if policy.quiet(now):
        bypass = alert.level == "critical" and policy.data.get("critical_bypasses_quiet_hours", True)
        if not bypass:
            if mode == "call":
                return Decision("push", "Ruhezeit: Benachrichtigung statt Anruf")
            if mode == "push":
                return Decision("silent", "Ruhezeit: still")
    return Decision(mode, reason)


class AlertEngine:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self.policy = AlertPolicy(state.settings.paths.data_dir / "alert-policy.json")
        self._ticker: asyncio.Task[None] | None = None
        self._appointments_seen: set[str] = set()

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._ticker = spawn(self._tick_loop(), name="alert-ticker")
        bus.subscribe("call.ended", self._on_call_ended)

    async def stop(self) -> None:
        if self._ticker is not None:
            self._ticker.cancel()

    # ------------------------------------------------------------------ main entry
    async def dispatch(self, alert: Alert) -> dict[str, Any]:
        key = alert.event_key or f"{alert.category}:{uuid.uuid4().hex}"
        alert.event_key = key[:200]
        if await self._duplicate(alert.event_key):
            return {"decision": "duplicate", "event_key": alert.event_key}
        decision = decide(self.policy, alert)
        call_block: str | None = None
        if decision.mode == "call":
            call_block = await self.state.calls.call_blocker(alert.event_key)
            if call_block:
                decision = Decision("push", f"kein Anruf: {call_block}")
        row_id = await self._record(alert, decision)
        if decision.mode == "none":
            await self._finish(row_id, "suppressed")
            return {"decision": "none", "id": str(row_id), "reason": decision.reason}
        if decision.mode == "call":
            try:
                call = await self.state.calls.ring_owner(
                    message=alert.call_message or f"{alert.title}. {alert.body}".strip(),
                    reason=f"alert:{alert.category}",
                    event_key=alert.event_key,
                    alert_id=row_id,
                )
                await self._finish(row_id, "call_ringing", call_id=uuid.UUID(call["call_id"]))
                return {"decision": "call", "id": str(row_id), "call_id": call["call_id"], "reason": decision.reason}
            except RuntimeError as e:  # last-second failure → notification instead
                decision = Decision("push", f"Anruf fehlgeschlagen: {e}")
        delivered = await self._push(row_id, alert, silent=decision.mode == "silent")
        status = "sent" if delivered else "undeliverable"
        await self._finish(row_id, status, delivered=delivered, reason=decision.reason)
        return {"decision": decision.mode, "id": str(row_id), "delivered": delivered, "reason": decision.reason}

    async def _duplicate(self, key: str) -> bool:
        since = datetime.now(UTC) - timedelta(hours=24)
        async with session_scope() as s:
            row = (
                await s.execute(
                    select(AlertDispatch.id).where(
                        AlertDispatch.event_key == key,
                        AlertDispatch.created_at > since,
                        AlertDispatch.status.not_in(["failed"]),
                    )
                )
            ).first()
        return row is not None

    async def _record(self, alert: Alert, d: Decision) -> uuid.UUID:
        async with session_scope() as s:
            row = AlertDispatch(
                event_key=alert.event_key,
                category=alert.category,
                level=alert.level,
                title=alert.title[:300],
                body=alert.body[:4000],
                decision=d.mode,
                reason=d.reason[:200],
                deep_link=alert.deep_link[:300],
                actions=CATEGORY_ACTIONS.get(alert.category, ["open"]),
                data={k: v for k, v in alert.data.items() if isinstance(v, str | int | float | bool)},
            )
            s.add(row)
            await s.flush()
            return row.id

    async def _finish(
        self,
        row_id: uuid.UUID,
        status: str,
        *,
        delivered: list[str] | None = None,
        call_id: uuid.UUID | None = None,
        reason: str | None = None,
    ) -> None:
        async with session_scope() as s:
            row = await s.get(AlertDispatch, row_id)
            if row is None:
                return
            row.status = status
            if delivered is not None:
                row.delivered_to = delivered
            if call_id is not None:
                row.call_id = call_id
            if reason:
                row.reason = reason[:200]
        await bus.publish("mobile.alert", id=str(row_id), status=status)

    async def _push(self, row_id: uuid.UUID, alert: Alert, *, silent: bool) -> list[str]:
        payload = {
            "alert_id": str(row_id),
            "category": alert.category,
            "level": alert.level,
            "title": alert.title[:200],
            "body": alert.body[:900],
            "silent": silent,
            "link": alert.deep_link,
            "actions": CATEGORY_ACTIONS.get(alert.category, ["open"]),
            "group": alert.category,
        }
        for k in ("approval_id", "task_id", "job_id", "session_id"):
            if alert.data.get(k):
                payload[k] = str(alert.data[k])
        results = await self.state.push.send_all(
            "alert", payload, high_priority=alert.level != "info" or alert.category == "approval", ttl_s=86400
        )
        return [r.device_id for r in results if r.ok]

    # ------------------------------------------------------------------ actions from the phone
    async def snooze(self, alert_id: uuid.UUID, minutes: int) -> datetime:
        at = datetime.now(UTC) + timedelta(minutes=max(1, min(minutes, 24 * 60)))
        async with session_scope() as s:
            row = await s.get(AlertDispatch, alert_id)
            if row is None:
                raise LookupError("Benachrichtigung nicht gefunden")
            row.remind_at = at
            row.status = "snoozed"
        return at

    async def get(self, alert_id: uuid.UUID) -> AlertDispatch | None:
        async with session_scope() as s:
            return await s.get(AlertDispatch, alert_id)

    async def recent(self, limit: int = 50) -> list[AlertDispatch]:
        async with session_scope() as s:
            q = select(AlertDispatch).order_by(AlertDispatch.created_at.desc()).limit(limit)
            return list((await s.execute(q)).scalars())

    # ------------------------------------------------------------------ background
    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            try:
                await self._due_snoozes()
                await self.state.calls.check_watches_time()
                await self._appointments()
            except Exception:
                logger.exception("alert ticker failed")

    async def _due_snoozes(self) -> None:
        now = datetime.now(UTC)
        async with session_scope() as s:
            rows = list(
                (
                    await s.execute(
                        select(AlertDispatch).where(
                            AlertDispatch.remind_at.is_not(None), AlertDispatch.remind_at <= now
                        )
                    )
                ).scalars()
            )
            for r in rows:
                r.remind_at = None
                r.status = "reminded"
            items = [(r.id, r.category, r.title, r.body, r.level, r.event_key, r.deep_link, dict(r.data)) for r in rows]
        for rid, cat, title, body, level, key, link, data in items:
            await self.dispatch(
                Alert(
                    "reminder" if cat != "approval" else cat,
                    title,
                    body,
                    level,
                    event_key=f"{key}:snooze:{rid.hex[:8]}:{int(now.timestamp())}",
                    deep_link=link,
                    data=data,
                    source="snooze",
                    allow_call=False,
                )
            )

    async def _appointments(self) -> None:
        """Upcoming calendar events (Google connector) → category 'appointment'."""
        cfg = self.policy.data["categories"]["appointment"]
        if cfg.get("mode") == "off":
            return
        google = self.state.connectors.connectors.get("google")
        if google is None or not google.active:
            return
        now_min = datetime.now(TZ).minute
        if now_min % 5 != 0 and self._appointments_seen:
            return  # poll every ~5 minutes (first poll right after start)
        from jarvis.security.auth import SYSTEM
        from jarvis.tools.registry import ToolContext

        lead = int(cfg.get("lead_minutes", 30))
        res = await self.state.executor.execute(
            "calendar_list_events",
            {"from": datetime.now(TZ).isoformat(), "to": (datetime.now(TZ) + timedelta(minutes=lead)).isoformat()},
            ToolContext(principal=SYSTEM),
        )
        self._appointments_seen.add("_polled")
        if res.status != "ok" or not isinstance(res.data, list):
            return
        for ev in res.data:
            start = str(ev.get("start") or "")
            if "T" not in start:
                continue  # all-day events: no "starts in N minutes"
            key = f"appointment:{ev.get('id')}:{start}"
            if key in self._appointments_seen:
                continue
            self._appointments_seen.add(key)
            try:
                mins = max(0, round((datetime.fromisoformat(start) - datetime.now(TZ)).total_seconds() / 60))
            except ValueError:
                continue
            title = str(ev.get("title") or "Termin")
            await self.dispatch(
                Alert(
                    "appointment",
                    f"In {mins} Minuten: {title}",
                    f"Sir, Ihr Termin „{title}“ beginnt in {mins} Minuten."
                    + (f" Ort: {ev['location']}" if ev.get("location") else ""),
                    event_key=key,
                    deep_link="jarvis://home",
                    call_message=f"Ihr Termin {title} beginnt in {mins} Minuten.",
                )
            )

    async def _on_call_ended(self, ev: Event) -> None:
        """A missed/declined call must not swallow the message: it follows as a notification."""
        d = ev.data
        if d.get("status") not in ("missed", "declined", "failed") or d.get("direction") != "to_owner":
            return
        if not d.get("message"):
            return
        title = "Verpasster Anruf von J.A.R.V.I.S." if d["status"] == "missed" else "J.A.R.V.I.S. wollte Sie sprechen"
        await self.dispatch(
            Alert(
                "system" if d["status"] == "failed" else "task",
                title,
                str(d.get("message"))[:900],
                "warning",
                event_key=f"call:{d.get('call_id')}:{d['status']}",
                deep_link=f"jarvis://call/{d.get('call_id')}",
                data={"call_id": str(d.get("call_id"))},
                source="call",
                allow_call=False,
            )
        )
