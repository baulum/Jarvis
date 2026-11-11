"""Editing a schedule: cron validation, re-planning of ``next_run_at`` and the input guards.

Runs on SQLite (aiosqlite) with only the two tables the scheduler needs – no Postgres.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import BigInteger, event
from sqlalchemy import types as sqltypes
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.sqlite import DATETIME as SQLITE_DATETIME
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

import jarvis.db as jdb
from jarvis.db.models import AuditEntry, Schedule
from jarvis.jobs.scheduler import Scheduler, validate_cron

BERLIN = ZoneInfo("Europe/Berlin")
PROMPT_ACTION = {"type": "prompt", "prompt": "Termine zusammenfassen", "notify": False}


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_: Any, compiler: Any, **kw: Any) -> str:
    return "JSON"


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_: Any, compiler: Any, **kw: Any) -> str:
    return "INTEGER"


class AwareDateTime(SQLITE_DATETIME):
    """SQLite drops the timezone; read values back as UTC so they compare with aware datetimes."""

    def result_processor(self, dialect: Any, coltype: Any) -> Any:
        base = super().result_processor(dialect, coltype)

        def process(value: Any) -> Any:
            v = base(value) if base else value
            if v is not None and v.tzinfo is None:
                v = v.replace(tzinfo=UTC)
            return v

        return process


@pytest.fixture
async def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'schedules.db'}")
    engine.sync_engine.dialect.colspecs = {**engine.sync_engine.dialect.colspecs, sqltypes.DateTime: AwareDateTime}

    @event.listens_for(engine.sync_engine, "connect")
    def _advisory_lock_stub(dbapi_conn: Any, _rec: Any) -> None:
        dbapi_conn.create_function("pg_advisory_xact_lock", 1, lambda _x: 0)  # audit() takes this lock on Postgres

    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Schedule.metadata.create_all(c, tables=[Schedule.__table__, AuditEntry.__table__])
        )
    monkeypatch.setattr(jdb, "_engine", engine)
    monkeypatch.setattr(jdb, "_sessionmaker", async_sessionmaker(engine, expire_on_commit=False))
    yield
    await engine.dispose()


def scheduler() -> Scheduler:
    return Scheduler(SimpleNamespace())  # type: ignore[arg-type]  # the update path never touches the state


def test_validate_cron_normalises_and_rejects() -> None:
    assert validate_cron("  0   8 * * 1-5 ") == "0 8 * * 1-5"
    with pytest.raises(ValueError, match="Ungültiger Cron-Ausdruck"):
        validate_cron("bad")
    with pytest.raises(ValueError, match="Ungültiger Cron-Ausdruck"):
        validate_cron("0 8 * * 1 0")  # six fields: the UI and the scheduler use five
    with pytest.raises(ValueError, match="höchstens alle 5 Minuten"):
        validate_cron("*/2 * * * *")


async def test_new_cron_replans_the_next_run(db: None) -> None:
    sched = scheduler()
    created = await sched.create("Morgen-Briefing", "0 8 * * *", dict(PROMPT_ACTION), "desk")
    updated = await sched.update(created.id, "desk", cron="30 7 * * 1-5")
    assert updated is not None and updated.cron == "30 7 * * 1-5"
    local = updated.next_run_at.astimezone(BERLIN) if updated.next_run_at else None
    assert local is not None and local.strftime("%H:%M") == "07:30" and local.weekday() < 5
    [stored] = await sched.list()
    assert stored.cron == "30 7 * * 1-5" and stored.next_run_at == updated.next_run_at


async def test_invalid_input_is_refused_and_nothing_is_written(db: None) -> None:
    sched = scheduler()
    created = await sched.create("Morgen-Briefing", "0 8 * * *", dict(PROMPT_ACTION), "desk")
    for change in [{"cron": "bad"}, {"cron": "*/2 * * * *"}, {"name": "   "}, {"prompt": "  "}]:
        with pytest.raises(ValueError):
            await sched.update(created.id, "desk", **change)
    [stored] = await sched.list()
    assert (stored.name, stored.cron, stored.action, stored.next_run_at) == (
        created.name,
        created.cron,
        created.action,
        created.next_run_at,
    )


async def test_prompt_changes_only_the_prompt_and_keeps_the_plan(db: None) -> None:
    sched = scheduler()
    created = await sched.create("Morgen-Briefing", "0 8 * * *", dict(PROMPT_ACTION), "desk")
    updated = await sched.update(created.id, "desk", prompt="  Termine und Mails zusammenfassen  ", name="Briefing")
    assert updated is not None
    assert updated.action == {"type": "prompt", "prompt": "Termine und Mails zusammenfassen", "notify": False}
    assert updated.name == "Briefing" and updated.next_run_at == created.next_run_at

    playbook = await sched.create("Social", "0 9 * * *", {"type": "playbook", "playbook": "x"}, "desk")
    with pytest.raises(ValueError, match="Prompt-Zeitplänen"):
        await sched.update(playbook.id, "desk", prompt="egal")


async def test_pausing_clears_the_plan_and_resuming_plans_again(db: None) -> None:
    sched = scheduler()
    created = await sched.create("Morgen-Briefing", "0 8 * * *", dict(PROMPT_ACTION), "desk")
    paused = await sched.update(created.id, "desk", enabled=False)
    assert paused is not None and paused.enabled is False and paused.next_run_at is None
    resumed = await sched.update(created.id, "desk", enabled=True)
    assert resumed is not None and resumed.enabled and resumed.next_run_at is not None
    assert resumed.next_run_at > datetime.now(UTC)
    assert await sched.update(uuid.uuid4(), "desk", name="gibt es nicht") is None
