"""VoIP calls between Sir's phone and Jarvis (LiveKit), signalled by encrypted FCM pushes.

Jarvis → Sir (``to_owner``): a call session + room are created, Jarvis' voice agent joins and waits, every
paired phone gets a high-priority ``call`` push; the phone registers the call with Android Telecom (ringing,
lock screen, Bluetooth, Android Auto). Answer → the phone fetches a room token (``/answer``), the others get
``call_cancel``. Nobody answers within ``ring_timeout_s`` → *missed* (+ notification with the message).
Sir → Jarvis (``to_jarvis``): only offered when the agent can actually answer (``availability``).

"Call me when X is done" is a *watch*: the agent registers it on Sir's explicit instruction; when the
coding job / task finishes (or the time is reached) the alert engine rings once (``event_key`` = watch id).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from jarvis.agent.persona import TZ, time_of_day_greeting
from jarvis.db import session_scope
from jarvis.db.models import CallSession, CodingJob, Task
from jarvis.events import Event, bus, spawn
from jarvis.mobile.alerts import Alert
from jarvis.mobile.voice_agent import CallVoiceAgent
from jarvis.security.audit import audit
from jarvis.security.auth import Principal

if TYPE_CHECKING:
    from jarvis.app_state import AppState

logger = logging.getLogger(__name__)
ACTIVE = ("ringing", "connecting", "active")
ORPHAN_GRACE = timedelta(seconds=60)  # a fresh row may briefly exist before its voice agent is registered


class CallService:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self.cfg = state.settings.mobile.calls
        self.agents: dict[uuid.UUID, CallVoiceAgent] = {}
        self._ring_timers: dict[uuid.UUID, asyncio.Task[None]] = {}
        self.watches_path: Path = state.settings.paths.data_dir / "call-watches.json"

    def start(self) -> None:
        bus.subscribe("coding.finished", self._on_finished_event)
        bus.subscribe("task.completed", self._on_finished_event)
        bus.subscribe("task.failed", self._on_finished_event)
        spawn(self._recover(), name="calls-recover")

    async def _recover(self) -> None:
        """Calls that were ringing/active when the core stopped are over."""
        async with session_scope() as s:
            rows = (await s.execute(select(CallSession).where(CallSession.status.in_(ACTIVE)))).scalars().all()
            for c in rows:
                c.status = "failed" if c.status != "ringing" else "missed"
                c.end_reason = "core_restart"
                c.ended_at = datetime.now(UTC)

    # ------------------------------------------------------------------ availability (honest)
    async def availability(self) -> dict[str, Any]:
        problems: list[str] = []
        if not self.state.settings.mobile.enabled:
            problems.append("Mobile-Funktionen sind deaktiviert")
        if not await self.state.livekit.healthy():
            problems.append("LiveKit-Server läuft nicht")
        agent = self.state.agent
        if agent is None or not agent.available():
            problems.append("Agent (Claude Code) nicht verfügbar")
        if self.state.kill_switch.engaged:
            problems.append("Not-Aus ist aktiv")
        if not await self.state.stt.healthy():
            problems.append("Spracherkennung (whisper) nicht erreichbar")
        ring_problems = list(problems)
        if not self.state.push.fcm.configured():
            ring_problems.append("Push (Firebase) nicht eingerichtet")
        elif not await self.state.push.targets():
            ring_problems.append("kein gekoppeltes Smartphone mit Push-Registrierung")
        busy = await self.active_call()
        if busy is not None:
            problems.append("es läuft bereits ein Anruf")
            ring_problems.append("es läuft bereits ein Anruf")
        return {
            "can_call_jarvis": not problems and busy is None,
            "can_ring_owner": not ring_problems and busy is None,
            "problems": problems,
            "ring_problems": ring_problems,
            "busy": busy is not None,
            "tts": await self.state.tts.status(),
        }

    async def active_call(self) -> CallSession | None:
        """The call occupying the line. A ringing/active row without a live voice agent in this process is a
        leftover (agent crashed, cleanup failed) – it is closed instead of blocking every later call."""
        now = datetime.now(UTC)
        live: CallSession | None = None
        async with session_scope() as s:
            rows = (await s.execute(select(CallSession).where(CallSession.status.in_(ACTIVE)))).scalars().all()
            for c in rows:
                if c.id in self.agents or now - c.created_at < ORPHAN_GRACE:
                    live = live or c
                    continue
                logger.warning("Closing orphaned call %s (status %s, no voice agent)", c.id, c.status)
                c.status = "missed" if c.status == "ringing" else "failed"
                c.end_reason = "orphaned"
                c.ended_at = now
                timer = self._ring_timers.pop(c.id, None)
                if timer:
                    timer.cancel()
        return live

    async def end_active_calls(self, reason: str = "superseded") -> int:
        """Hang up every ringing/connecting/active call (Sir explicitly asks for a fresh one)."""
        async with session_scope() as s:
            ids = (await s.execute(select(CallSession.id).where(CallSession.status.in_(ACTIVE)))).scalars().all()
        for call_id in ids:
            try:
                await self.hangup(call_id, reason)
            except Exception:
                logger.exception("could not hang up call %s", call_id)
            async with session_scope() as s:  # a stuck row (dead agent) must not block the new call
                c = await s.get(CallSession, call_id)
                still = c is not None and c.status in ACTIVE
            if still:
                await self._end(call_id, "ended", reason)
        if ids:
            await self.state.push.send_all(
                "call_cancel", {"call_id": str(ids[0]), "status": reason}, high_priority=True, ttl_s=60
            )
        return len(ids)

    async def call_blocker(self, event_key: str | None, *, explicit: bool = False) -> str | None:
        """Why Jarvis must NOT ring right now (None = may ring).

        ``explicit``: Sir asked for the call himself – a running call will be superseded and the spacing/duplicate
        rules do not apply; technical problems and the hourly cap (runaway protection) still do."""
        av = await self.availability()
        if av["busy"] and not explicit:
            return "es läuft bereits ein Anruf"
        if av["ring_problems"]:
            return ", ".join(av["ring_problems"])
        now = datetime.now(UTC)
        async with session_scope() as s:
            if event_key and not explicit:
                dup = (
                    await s.execute(select(CallSession.id).where(CallSession.event_key == event_key).limit(1))
                ).first()
                if dup is not None:
                    return "wegen dieses Ereignisses wurde bereits angerufen"
            recent = (
                await s.execute(
                    select(func.count())
                    .select_from(CallSession)
                    .where(
                        CallSession.direction == "to_owner",
                        CallSession.reason != "test",  # test calls never use up Sir's real call budget
                        CallSession.created_at > now - timedelta(hours=1),
                    )
                )
            ).scalar_one()
            last = (
                await s.execute(
                    select(func.max(CallSession.created_at)).where(
                        CallSession.direction == "to_owner", CallSession.reason != "test"
                    )
                )
            ).scalar_one()
        if recent >= self.cfg.max_calls_per_hour:
            return f"Anruflimit erreicht ({self.cfg.max_calls_per_hour}/Stunde)"
        if not explicit and last is not None and now - last < timedelta(minutes=self.cfg.min_minutes_between_calls):
            return f"letzter Anruf vor weniger als {self.cfg.min_minutes_between_calls} Minuten"
        return None

    # ------------------------------------------------------------------ Jarvis → Sir
    async def ring_owner(
        self,
        *,
        message: str,
        reason: str,
        event_key: str | None = None,
        alert_id: uuid.UUID | None = None,
        skip_push: bool = False,
        explicit: bool = False,
    ) -> dict[str, Any]:
        """``skip_push`` only for the owner's local device test (the phone is triggered over adb instead of FCM)."""
        blocker = None if skip_push else await self.call_blocker(event_key, explicit=explicit)
        if blocker:
            raise RuntimeError(blocker)
        call_id = uuid.uuid4()
        room = f"jarvis-{call_id.hex[:12]}"
        async with session_scope() as s:
            s.add(
                CallSession(
                    id=call_id,
                    direction="to_owner",
                    room=room,
                    status="ringing",
                    reason=reason[:60],
                    message=message[:2000],
                    event_key=event_key,
                )
            )
        greeting = f"{time_of_day_greeting(self.state.personality)} {message}".strip()
        self._start_agent(call_id, room, greeting, join_timeout=self.cfg.ring_timeout_s + 30)
        if skip_push:
            self._ring_timers[call_id] = spawn(self._ring_timeout(call_id), name=f"ring-{call_id}")
            await bus.publish("call.ringing", call_id=str(call_id), direction="to_owner", message=message[:300])
            return {"call_id": str(call_id), "room": room, "devices": [], "push": "skipped (local test)"}
        results = await self.state.push.send_all(
            "call",
            {
                "call_id": str(call_id),
                "caller": "Jarvis",  # read aloud by car/Bluetooth head units: no dotted acronym
                "subject": message[:140],
                "ring_timeout_s": self.cfg.ring_timeout_s,
            },
            high_priority=True,
            ttl_s=self.cfg.ring_timeout_s,
        )
        if not any(r.ok for r in results):
            await self._end(call_id, "failed", "push_failed")
            raise RuntimeError("Push an das Smartphone fehlgeschlagen: " + "; ".join(r.error or "?" for r in results))
        self._ring_timers[call_id] = spawn(self._ring_timeout(call_id), name=f"ring-{call_id}")
        await audit("calls", "call.ring_owner", target=str(call_id), details={"reason": reason, "alert": str(alert_id)})
        await bus.publish("call.ringing", call_id=str(call_id), direction="to_owner", message=message[:300])
        return {"call_id": str(call_id), "room": room, "devices": [r.device_id for r in results if r.ok]}

    async def _ring_timeout(self, call_id: uuid.UUID) -> None:
        await asyncio.sleep(self.cfg.ring_timeout_s)
        async with session_scope() as s:
            c = await s.get(CallSession, call_id)
            ringing = c is not None and c.status == "ringing"
        if ringing:
            await self._end(call_id, "missed", "timeout")

    # ------------------------------------------------------------------ phone actions
    async def answer(self, call_id: uuid.UUID, principal: Principal) -> dict[str, Any]:
        async with session_scope() as s:
            c = await s.get(CallSession, call_id, with_for_update=True)
            if c is None:
                raise LookupError("Anruf nicht gefunden")
            if c.status != "ringing":
                raise PermissionError(f"Anruf ist nicht mehr verfügbar ({c.status})")
            c.status = "connecting"
            c.device_id = principal.device_id
            room = c.room
        timer = self._ring_timers.pop(call_id, None)
        if timer:
            timer.cancel()
        agent = self.agents.get(call_id)
        if agent is None:  # the voice agent died meanwhile – do not pretend
            await self._end(call_id, "failed", "agent_gone")
            raise RuntimeError("Jarvis ist für dieses Gespräch nicht mehr verfügbar")
        agent.set_owner(principal)
        await self.state.push.send_all(
            "call_cancel", {"call_id": str(call_id), "status": "answered_elsewhere"}, high_priority=True, ttl_s=60
        )  # the answering phone ignores it (call already active there)
        return self._join_info(call_id, room, principal)

    async def decline(self, call_id: uuid.UUID, principal: Principal) -> None:
        async with session_scope() as s:
            c = await s.get(CallSession, call_id)
            if c is None:
                raise LookupError("Anruf nicht gefunden")
            if c.status != "ringing":
                return
        await self._end(call_id, "declined", "declined", device_id=principal.device_id)

    async def start_from_phone(self, principal: Principal) -> dict[str, Any]:
        av = await self.availability()
        if not av["can_call_jarvis"]:
            raise RuntimeError(", ".join(av["problems"]) or "es läuft bereits ein Anruf")
        call_id = uuid.uuid4()
        room = f"jarvis-{call_id.hex[:12]}"
        async with session_scope() as s:
            s.add(
                CallSession(
                    id=call_id,
                    direction="to_jarvis",
                    room=room,
                    status="connecting",
                    reason="user",
                    device_id=principal.device_id,
                )
            )
        agent = self._start_agent(call_id, room, time_of_day_greeting(self.state.personality), join_timeout=40)
        agent.set_owner(principal)
        await bus.publish("call.started", call_id=str(call_id), direction="to_jarvis")
        return self._join_info(call_id, room, principal)

    async def hangup(self, call_id: uuid.UUID, reason: str = "owner_hangup") -> None:
        agent = self.agents.get(call_id)
        if agent is not None:
            await agent.hangup(reason)
        else:
            await self._end(call_id, "ended", reason)

    def _join_info(self, call_id: uuid.UUID, room: str, principal: Principal) -> dict[str, Any]:
        identity = f"owner-{str(principal.device_id)[:8]}"
        token = self.state.livekit.mint_token(room, identity, "Sir", ttl_s=3600)
        public = self.state.settings.mobile.livekit.public_url
        return {"call_id": str(call_id), "room": room, "token": token, "url": public or None, "via_gateway": not public}

    # ------------------------------------------------------------------ agent lifecycle
    def _start_agent(self, call_id: uuid.UUID, room: str, greeting: str, join_timeout: float) -> CallVoiceAgent:
        async def joined(identity: str, call_id: uuid.UUID = call_id) -> None:
            async with session_scope() as s:
                c = await s.get(CallSession, call_id)
                if c is not None and c.status in ("connecting", "ringing"):
                    c.status = "active"
                    c.answered_at = datetime.now(UTC)
            await bus.publish("call.active", call_id=str(call_id))

        async def finished(reason: str, info: dict[str, Any], call_id: uuid.UUID = call_id) -> None:
            self.agents.pop(call_id, None)
            async with session_scope() as s:
                c = await s.get(CallSession, call_id)
                status = c.status if c else "ended"
            if status in ACTIVE:
                final = "missed" if status == "ringing" else "failed" if reason == "agent_error" else "ended"
                await self._end(call_id, final, reason, info=info)
            else:
                await self._store_info(call_id, info)

        agent = CallVoiceAgent(
            self.state,
            call_id=call_id,
            room_name=room,
            greeting=greeting,
            on_owner_joined=joined,
            on_finished=finished,
            join_timeout_s=join_timeout,
        )
        self.agents[call_id] = agent
        agent.start()
        return agent

    async def _store_info(self, call_id: uuid.UUID, info: dict[str, Any]) -> None:
        agent_metrics = info.get("metrics") or {}
        async with session_scope() as s:
            c = await s.get(CallSession, call_id)
            if c is None:
                return
            if info.get("transcript"):
                c.transcript = info["transcript"]
            if info.get("session_id"):
                c.session_id = info["session_id"]
            if agent_metrics:
                c.metrics = agent_metrics

    async def _end(
        self,
        call_id: uuid.UUID,
        status: str,
        reason: str,
        *,
        device_id: uuid.UUID | None = None,
        info: dict[str, Any] | None = None,
    ) -> None:
        timer = self._ring_timers.pop(call_id, None)
        if timer:
            timer.cancel()
        async with session_scope() as s:
            c = await s.get(CallSession, call_id)
            if c is None:
                return
            if c.status not in ACTIVE:
                return
            c.status = status
            c.end_reason = reason[:60]
            c.ended_at = datetime.now(UTC)
            if device_id:
                c.device_id = device_id
            direction, message, room = c.direction, c.message, c.room
        if info:
            await self._store_info(call_id, info)
        agent = self.agents.get(call_id)
        if agent is not None:
            await agent.hangup(reason)
        with contextlib.suppress(Exception):
            await self._delete_room(room)
        if status in ("missed", "declined", "failed") and direction == "to_owner":
            await self.state.push.send_all(
                "call_cancel", {"call_id": str(call_id), "status": status}, high_priority=True, ttl_s=120
            )
        await audit("calls", "call.end", target=str(call_id), details={"status": status, "reason": reason})
        await bus.publish(
            "call.ended", call_id=str(call_id), status=status, reason=reason, direction=direction, message=message
        )

    async def _delete_room(self, room: str) -> None:
        from livekit import api

        key, secret = self.state.livekit.credentials()
        url = self.state.settings.mobile.livekit.internal_url.replace("ws://", "http://")
        lk = api.LiveKitAPI(url, key, secret)
        try:
            await lk.room.delete_room(api.DeleteRoomRequest(room=room))
        finally:
            await lk.aclose()

    async def history(self, limit: int = 50) -> list[dict[str, Any]]:
        async with session_scope() as s:
            rows = (
                await s.execute(select(CallSession).order_by(CallSession.created_at.desc()).limit(min(limit, 200)))
            ).scalars()
            return [call_dict(c) for c in rows]

    # ------------------------------------------------------------------ watches ("call me when …")
    def _load_watches(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.watches_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []

    def _save_watches(self, watches: list[dict[str, Any]]) -> None:
        self.watches_path.write_text(json.dumps(watches, ensure_ascii=False, indent=2), encoding="utf-8")

    async def add_watch(self, trigger: dict[str, Any], message: str, session_id: str | None) -> dict[str, Any]:
        kind = trigger.get("type")
        target = str(trigger.get("id") or "")
        if kind in ("coding_job", "task") and target in ("", "latest"):
            target = await self._latest(kind, session_id)
            if not target:
                raise ValueError("Kein laufender Auftrag gefunden, auf den ich warten könnte")
        if kind == "time":
            at = datetime.fromisoformat(str(trigger.get("at")))
            if at.tzinfo is None:
                at = at.replace(tzinfo=TZ)
            if at < datetime.now(UTC):
                raise ValueError("Der Zeitpunkt liegt in der Vergangenheit")
            target = at.isoformat()
        elif kind not in ("coding_job", "task"):
            raise ValueError("trigger.type muss coding_job, task oder time sein")
        watch = {
            "id": uuid.uuid4().hex,
            "type": kind,
            "target": target,
            "message": message[:500],
            "created": datetime.now(UTC).isoformat(),
            "session_id": session_id,
        }
        watches = self._load_watches()
        watches.append(watch)
        self._save_watches(watches)
        await bus.publish("mobile.watch", **watch)
        return watch

    async def _latest(self, kind: str, session_id: str | None) -> str:
        async with session_scope() as s:
            if kind == "coding_job":
                q = select(CodingJob.id).where(CodingJob.status.in_(["queued", "running"]))
                row = (await s.execute(q.order_by(CodingJob.created_at.desc()).limit(1))).first()
            else:
                q2 = select(Task.id).where(Task.status.not_in(["completed", "failed", "cancelled"]))
                row = (await s.execute(q2.order_by(Task.created_at.desc()).limit(1))).first()
        return str(row[0]) if row else ""

    def watches(self) -> list[dict[str, Any]]:
        return self._load_watches()

    def remove_watch(self, watch_id: str) -> bool:
        watches = self._load_watches()
        rest = [w for w in watches if w["id"] != watch_id]
        self._save_watches(rest)
        return len(rest) != len(watches)

    async def _on_finished_event(self, ev: Event) -> None:
        d = ev.data
        kind = "coding_job" if ev.type == "coding.finished" else "task"
        target = str(d.get("job_id") if kind == "coding_job" else (d.get("task_id") or ev.task_id or d.get("id")))
        hits = [w for w in self._load_watches() if w["type"] == kind and w["target"] == target]
        if not hits:
            return
        for w in hits:
            self.remove_watch(w["id"])
            ok = d.get("status", "completed") == "completed" and ev.type != "task.failed"
            title = str(d.get("title") or "Auftrag")
            detail = w["message"] if ok else f"Leider ist „{title}“ fehlgeschlagen. {str(d.get('error') or '')[:200]}"
            await self.state.alerts.dispatch(
                Alert(
                    "task" if ok else "error",
                    f"{'Fertig' if ok else 'Fehlgeschlagen'}: {title}",
                    detail,
                    "info" if ok else "warning",
                    event_key=f"watch:{w['id']}",
                    deep_link=f"jarvis://task/{target}" if kind == "task" else "jarvis://activity",
                    call_requested=True,
                    call_message=detail,
                    data={"task_id": target} if kind == "task" else {"job_id": target},
                    source="watch",
                )
            )

    async def check_watches_time(self) -> None:
        now = datetime.now(UTC)
        for w in self._load_watches():
            if w["type"] == "time" and datetime.fromisoformat(w["target"]) <= now:
                self.remove_watch(w["id"])
                await self.state.alerts.dispatch(
                    Alert(
                        "reminder",
                        "Erinnerung",
                        w["message"],
                        event_key=f"watch:{w['id']}",
                        call_requested=True,
                        call_message=w["message"],
                        source="watch",
                    )
                )


def call_dict(c: CallSession) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "direction": c.direction,
        "status": c.status,
        "reason": c.reason,
        "message": c.message,
        "created_at": c.created_at.isoformat(),
        "answered_at": c.answered_at.isoformat() if c.answered_at else None,
        "ended_at": c.ended_at.isoformat() if c.ended_at else None,
        "end_reason": c.end_reason,
        "duration_s": round((c.ended_at - c.answered_at).total_seconds()) if c.ended_at and c.answered_at else None,
        "transcript": c.transcript,
        "session_id": str(c.session_id) if c.session_id else None,
    }
