"""Cron scheduler persisted in Postgres (survives restarts, catches up one missed run).

Playbook actions may carry two options for daily jobs (used by the social video, ``jarvis.social``):
``daily_key`` – the task gets the idempotency key ``<daily_key>:<local date of the run>``, so two Core processes or
a manual "Jetzt ausführen" never start the same day twice; ``catch_up: "same_day"`` – a run missed while the Core
was down is made up only on the day it was due (a run from yesterday is skipped, not started today)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import select, update

from jarvis.db import session_scope
from jarvis.db.models import Approval, Schedule
from jarvis.events import bus, spawn
from jarvis.security.audit import audit
from jarvis.security.auth import Principal
from jarvis.tools.registry import ToolContext

if TYPE_CHECKING:
    from jarvis.app_state import AppState

logger = logging.getLogger(__name__)
SCHEDULER = Principal(id="scheduler", role="system", channel="scheduler")


def next_run(cron: str, tz: str, after: datetime | None = None) -> datetime:
    base = (after or datetime.now(UTC)).astimezone(ZoneInfo(tz))
    nxt: datetime = croniter(cron, base).get_next(datetime)
    return nxt.astimezone(UTC)


MIN_INTERVAL_S = 300  # a prompt every minute would burn the quota (and is what a prompt injection would try)


def check_min_interval(cron: str, tz: str = "Europe/Berlin") -> None:
    base = datetime.now(ZoneInfo(tz))
    it = croniter(cron, base)
    first, second, third = (it.get_next(datetime) for _ in range(3))
    if min((second - first).total_seconds(), (third - second).total_seconds()) < MIN_INTERVAL_S:
        raise ValueError(f"Zeitpläne dürfen höchstens alle {MIN_INTERVAL_S // 60} Minuten laufen")


class Scheduler:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self.state.settings.scheduler.enabled:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def create(
        self, name: str, cron: str, action: dict[str, Any], actor: str, tz: str = "Europe/Berlin"
    ) -> Schedule:
        if not croniter.is_valid(cron):
            raise ValueError("Ungültiger Cron-Ausdruck")
        check_min_interval(cron, tz)
        if action.get("type") not in ("prompt", "tool", "playbook"):
            raise ValueError("action.type muss prompt, tool oder playbook sein")
        async with session_scope() as s:
            sched = Schedule(
                name=name, cron=cron, timezone=tz, action=action, enabled=True, next_run_at=next_run(cron, tz)
            )
            s.add(sched)
            await s.flush()
        await audit(actor, "schedule.create", target=str(sched.id), details={"name": name, "cron": cron})
        await bus.publish("schedule.updated", id=str(sched.id))
        return sched

    async def list(self) -> list[Schedule]:
        async with session_scope() as s:
            return list((await s.execute(select(Schedule).order_by(Schedule.created_at))).scalars())

    async def set_enabled(self, sid: uuid.UUID, enabled: bool, actor: str) -> bool:
        async with session_scope() as s:
            sched = await s.get(Schedule, sid)
            if sched is None:
                return False
            sched.enabled = enabled
            if enabled:
                sched.next_run_at = next_run(sched.cron, sched.timezone)
        await audit(actor, "schedule.enable" if enabled else "schedule.disable", target=str(sid))
        await bus.publish("schedule.updated", id=str(sid))
        return True

    async def delete(self, sid: uuid.UUID, actor: str) -> bool:
        async with session_scope() as s:
            sched = await s.get(Schedule, sid)
            if sched is None:
                return False
            await s.delete(sched)
        await audit(actor, "schedule.delete", target=str(sid))
        await bus.publish("schedule.updated", id=str(sid))
        return True

    async def run_now(self, sid: uuid.UUID) -> None:
        async with session_scope() as s:
            sched = await s.get(Schedule, sid)
            if sched is None:
                raise LookupError("Zeitplan nicht gefunden")
            action, name, tz = dict(sched.action), sched.name, sched.timezone
        await self._execute(sid, name, action, None, tz)

    async def _sweep_approvals(self) -> None:
        """Expire pending approvals past their TTL and fail ones that were approved but never finished (crash)."""
        now = datetime.now(UTC)
        async with session_scope() as s:
            await s.execute(
                update(Approval).where(Approval.status == "pending", Approval.expires_at < now).values(status="expired")
            )
            await s.execute(
                update(Approval)
                .where(Approval.status == "approved", Approval.decided_at < now - timedelta(minutes=10))
                .values(status="failed", result={"status": "error", "message": "Ausführung unterbrochen"})
            )

    async def _loop(self) -> None:
        tick = self.state.settings.scheduler.tick_seconds
        while True:
            try:
                await self._sweep_approvals()
                if not self.state.kill_switch.engaged:
                    now = datetime.now(UTC)
                    async with session_scope() as s:
                        due = (
                            (
                                await s.execute(
                                    select(Schedule)
                                    .where(Schedule.enabled.is_(True), Schedule.next_run_at <= now)
                                    .with_for_update(skip_locked=True)
                                )
                            )
                            .scalars()
                            .all()
                        )
                        jobs = []
                        for sched in due:
                            due_at = sched.next_run_at
                            sched.last_run_at = now
                            sched.next_run_at = next_run(sched.cron, sched.timezone, now)
                            jobs.append((sched.id, sched.name, dict(sched.action), due_at, sched.timezone))
                    for sid, name, action, due_at, tz in jobs:
                        spawn(self._execute(sid, name, action, due_at, tz), name=f"schedule-{name}")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduler tick failed")
            await asyncio.sleep(tick)

    async def _execute(
        self,
        sid: uuid.UUID,
        name: str,
        action: dict[str, Any],
        due_at: datetime | None = None,
        tz: str = "Europe/Berlin",
    ) -> None:
        status = "ok"
        try:
            kind = action["type"]
            today = datetime.now(ZoneInfo(tz)).date()
            if (
                action.get("catch_up") == "same_day"
                and due_at is not None
                and due_at.astimezone(ZoneInfo(tz)).date() != today
            ):
                status = "skipped: verpasst"
                logger.info("Schedule %s: run due %s was missed and is not made up today", name, due_at.isoformat())
                kind = "skip"
            if kind == "prompt":
                result = await self.state.orchestrator.handle(SCHEDULER, "scheduler", action["prompt"])
                if action.get("notify", True) and result.text:
                    await self.state.notifier.notify("schedule.result", name, result.text)
                status = "error" if result.error else "ok"
            elif kind == "tool":
                res = await self.state.executor.execute(
                    action["tool"], action.get("args", {}), ToolContext(principal=SCHEDULER)
                )
                status = res.status
            elif kind == "playbook":
                key = f"{action['daily_key']}:{today.isoformat()}" if action.get("daily_key") else None
                await self.state.tasks.create(
                    action["playbook"], action.get("params", {}), SCHEDULER, idempotency_key=key
                )
        except Exception as e:
            logger.exception("Scheduled job %s failed", name)
            status = f"error: {e}"[:30]
        async with session_scope() as s:
            sched = await s.get(Schedule, sid)
            if sched is not None:
                sched.last_status = status
        await audit("scheduler", "schedule.run", target=str(sid), outcome=status, details={"name": name})
        await bus.publish("schedule.ran", id=str(sid), name=name, status=status)
