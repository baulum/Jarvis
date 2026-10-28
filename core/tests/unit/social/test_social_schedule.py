"""The daily run: a normal schedule, one automatic video per day, missed runs only made up on the same day."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select

from jarvis.db import session_scope
from jarvis.db.models import Schedule, Task
from jarvis.jobs.engine import StepContext, StepSkip, TaskEngine
from jarvis.jobs.scheduler import Scheduler
from jarvis.social.service import DAILY_KEY, PLAYBOOK, SCHEDULE_NAME, cron_for, playbook, time_of

from .conftest import SYSTEM, FakeTasks

ACTION = {"type": "playbook", "playbook": PLAYBOOK, "params": {"trigger": "schedule"}, "daily_key": DAILY_KEY, "catch_up": "same_day"}


async def make_schedule(next_run_at: datetime) -> uuid.UUID:
    async with session_scope() as s:
        row = Schedule(name=SCHEDULE_NAME, cron="0 8 * * *", timezone="Europe/Berlin", action=ACTION, enabled=True, next_run_at=next_run_at)
        s.add(row)
        await s.flush()
        return row.id


async def last_status(sid: uuid.UUID) -> str | None:
    async with session_scope() as s:
        row = await s.get(Schedule, sid)
        return row.last_status if row else None


def test_cron_helpers() -> None:
    assert cron_for("08:00") == "0 8 * * *" and cron_for("17:45") == "45 17 * * *"
    assert time_of("5 7 * * *") == "07:05"


async def test_ensure_schedule_creates_one_daily_job(social: SimpleNamespace) -> None:
    first = await social.svc.ensure_schedule()
    again = await social.svc.ensure_schedule()
    assert first.id == again.id and first.cron == "0 8 * * *" and first.timezone == "Europe/Berlin"
    info = await social.svc.schedule_info()
    assert info["enabled"] and info["time"] == "08:00" and info["next_run_at"]
    changed = await social.svc.set_production("test", time="07:30", enabled=False)
    assert changed["time"] == "07:30" and changed["enabled"] is False and changed["next_run_at"] is None


async def test_run_from_yesterday_is_not_made_up(social: SimpleNamespace) -> None:
    sched: Scheduler = social.state.scheduler
    due = datetime.now(UTC) - timedelta(days=1)
    sid = await make_schedule(due)
    await sched._execute(sid, SCHEDULE_NAME, dict(ACTION), due, "Europe/Berlin")
    assert social.state.tasks.created == []
    assert await last_status(sid) == "skipped: verpasst"


async def test_missed_run_today_is_made_up_with_a_daily_key(social: SimpleNamespace) -> None:
    sched: Scheduler = social.state.scheduler
    due = datetime.now(UTC) - timedelta(minutes=1)
    sid = await make_schedule(due)
    await sched._execute(sid, SCHEDULE_NAME, dict(ACTION), due, "Europe/Berlin")
    [created] = social.state.tasks.created
    assert created["playbook"] == PLAYBOOK
    assert created["idempotency_key"].startswith(f"{DAILY_KEY}:") and len(created["idempotency_key"]) == len(DAILY_KEY) + 11


async def test_after_restart_the_loop_catches_up_the_same_day(social: SimpleNamespace) -> None:
    """Core was off at 08:00 and starts later the same day: the due schedule runs once."""
    sched: Scheduler = social.state.scheduler
    await make_schedule(datetime.now(UTC) - timedelta(minutes=5))
    task = asyncio.create_task(sched._loop())
    for _ in range(50):
        await asyncio.sleep(0.05)
        if social.state.tasks.created:
            break
    task.cancel()
    assert len(social.state.tasks.created) == 1


async def test_task_engine_creates_one_task_per_daily_key(social: SimpleNamespace) -> None:
    engine = TaskEngine(social.state)
    engine.register(playbook(social.svc))
    key = f"{DAILY_KEY}:2026-10-28"
    a, b = await asyncio.gather(
        engine.create(PLAYBOOK, {"trigger": "schedule"}, SYSTEM, idempotency_key=key),
        engine.create(PLAYBOOK, {"trigger": "schedule"}, SYSTEM, idempotency_key=key),
    )
    assert a.id == b.id
    async with session_scope() as s:
        assert (await s.execute(select(func.count()).select_from(Task))).scalar_one() == 1
        task = (await s.execute(select(Task))).scalar_one()
    assert task.title == "PJE Systems Daily Social Video"


def ctx_for(social: SimpleNamespace, params: dict[str, Any], context: dict[str, Any] | None = None) -> StepContext:
    return StepContext(
        engine=SimpleNamespace(state=social.state),  # type: ignore[arg-type]
        task_id=uuid.uuid4(), step_id=uuid.uuid4(), step_idx=0, params=params, context=context if context is not None else {},
        principal=SYSTEM, session_id=None,
    )  # fmt: skip


async def test_second_automatic_run_on_the_same_day_produces_nothing(social: SimpleNamespace) -> None:
    """Even two separate tasks (e.g. two Core processes) cannot both produce today: auto_date is unique."""
    first = ctx_for(social, {"trigger": "schedule"})
    out = await social.svc.step_start(first)
    assert out["video_id"]
    second = ctx_for(social, {"trigger": "schedule"})
    with pytest.raises(StepSkip, match="schon ein automatisches Video"):
        await social.svc.step_start(second)
    # the later steps of the duplicate task skip as well instead of producing
    with pytest.raises(StepSkip):
        await social.svc.step_concept(second)
    # manual productions are not limited
    manual = await social.svc.step_start(ctx_for(social, {"trigger": "manual"}))
    assert manual["video_id"] != out["video_id"]


async def test_manual_production_and_regenerate_start_tasks(social: SimpleNamespace) -> None:
    tasks: FakeTasks = social.state.tasks
    await social.svc.produce(SYSTEM, hint="Etwas mit Countdown")
    parent = uuid.uuid4()
    await social.svc.produce(SYSTEM, parent_id=parent)
    assert tasks.created[0]["params"] == {"trigger": "manual", "hint": "Etwas mit Countdown"}
    assert tasks.created[1]["params"]["trigger"] == "regenerate" and tasks.created[1]["params"]["parent_id"] == str(parent)
