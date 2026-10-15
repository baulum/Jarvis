import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from jarvis.db import session_scope
from jarvis.db.models import Task
from jarvis.events import bus
from jarvis.jobs.engine import Playbook, StepContext, StepDef, TaskEngine
from jarvis.security.auth import Principal

pytestmark = pytest.mark.integration
OWNER = Principal(id="desk", role="owner", channel="device")


def make_engine(stack) -> TaskEngine:
    eng = TaskEngine(SimpleNamespace(executor=stack.executor, kill_switch=stack.kill_switch))  # type: ignore[arg-type]

    async def s1(ctx: StepContext) -> dict:
        return {"low": await ctx.tool("t_low", {"x": 1})}

    async def s2(ctx: StepContext) -> dict:
        return {"medium": await ctx.tool("t_medium", {"x": 2})}

    async def s3(ctx: StepContext) -> dict:
        if not ctx.params.get("city"):
            ctx.ask("Ort fehlt", ["city"])
        return {"city": ctx.params["city"]}

    eng.register(Playbook("demo", "Demo", "d", {"type": "object", "properties": {"city": {"type": "string"}}},
                          [StepDef("low", s1), StepDef("medium", s2), StepDef("input", s3)]))
    return eng


async def wait_status(eng: TaskEngine, tid, statuses, timeout=15.0) -> Task:
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        t = await eng.get_task(tid)
        if t and t.status in statuses:
            return t
        eng._wake.set()
        await asyncio.sleep(0.2)
    raise AssertionError(f"task stuck in {t.status if t else None}")


async def test_playbook_with_approval_and_input(stack) -> None:
    bus._handlers.clear()
    eng = make_engine(stack)
    await eng.start()
    try:
        task = await eng.create("demo", {}, OWNER)
        t = await wait_status(eng, task.id, {"waiting_approval"})
        approval_id = t.steps[1].approval_id
        await stack.executor.decide(approval_id, True, Principal(id="desk", role="owner", channel="device",
                                                                 device_id=__import__("uuid").uuid4()))
        t = await wait_status(eng, task.id, {"waiting_input"})
        assert t.pending_question == "Ort fehlt"
        await eng.provide_input(task.id, {"city": "Soltau"}, "pytest")
        t = await wait_status(eng, task.id, {"completed"})
        assert [s.status for s in t.steps] == ["completed"] * 3
        # every external write ran exactly once despite three re-runs of the playbook
        assert len(stack.counters["t_low"].calls) == 1 and len(stack.counters["t_medium"].calls) == 1
    finally:
        await eng.stop()


async def test_resume_after_crash(stack) -> None:
    """A task left 'running' with an expired lease (crashed worker) is picked up again."""
    bus._handlers.clear()
    eng = make_engine(stack)
    task = await eng.create("demo", {"city": "Hamburg"}, OWNER)
    async with session_scope() as s:
        row = await s.get(Task, task.id)
        row.status = "running"
        row.lease_owner = "dead-worker"
        row.lease_until = datetime.now(UTC) - timedelta(seconds=5)
    await eng.start()
    try:
        t = await wait_status(eng, task.id, {"waiting_approval"})
        assert t.attempts >= 1 and t.steps[0].status == "completed"
    finally:
        await eng.stop()
