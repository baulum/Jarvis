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

    eng.register(
        Playbook(
            "demo",
            "Demo",
            "d",
            {"type": "object", "properties": {"city": {"type": "string"}}},
            [StepDef("low", s1), StepDef("medium", s2), StepDef("input", s3)],
        )
    )
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
        await stack.executor.decide(
            approval_id,
            True,
            Principal(id="desk", role="owner", channel="device", device_id=__import__("uuid").uuid4()),
        )
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


async def test_step_parked_on_running_tool_call_continues(stack, monkeypatch) -> None:
    """A write left 'running' in the ledger (core crashed mid-call) used to park the step for good."""
    import jarvis.jobs.engine as engine_mod
    from jarvis.db.models import ToolExecution

    monkeypatch.setattr(engine_mod, "IN_PROGRESS_RECHECK", timedelta(0))
    bus._handlers.clear()
    eng = make_engine(stack)
    task = await eng.create("demo", {"city": "Hamburg"}, OWNER)
    key = f"task:{task.id}:0:1:t_low"
    async with session_scope() as s:
        s.add(ToolExecution(idempotency_key=key, tool="t_low", args_hash="x", status="running", actor="dead-worker"))
    await eng.start()
    try:
        t = await wait_status(eng, task.id, {"waiting_external"})
        assert (t.steps[0].output or {})["wait"]["reason"] == "tool_in_progress"
        async with session_scope() as s:  # the interrupted call turns out to have gone through
            row = await s.get(ToolExecution, key)
            row.status = "succeeded"
            row.result = {"data": {"n": 1}, "message": ""}
        t = await wait_status(eng, task.id, {"waiting_approval"})
        assert t.steps[0].status == "completed" and t.steps[0].output == {"low": {"n": 1}}
        assert stack.counters["t_low"].calls == []  # taken from the ledger, not executed a second time
    finally:
        await eng.stop()


async def test_failed_execution_after_approval_reports_task_failed(stack) -> None:
    from jarvis.tools.registry import ToolSpec

    async def broken(ctx, a):  # type: ignore[no-untyped-def]
        raise RuntimeError("Lexware antwortet nicht")

    stack.registry.register(
        ToolSpec("t_broken", "t_broken", {"type": "object"}, broken, "medium", "test", writes=True)
    )
    bus._handlers.clear()
    failed: list[dict] = []

    async def on_failed(ev) -> None:  # type: ignore[no-untyped-def]
        failed.append(ev.data)

    bus.subscribe("task.failed", on_failed)
    eng = TaskEngine(SimpleNamespace(executor=stack.executor, kill_switch=stack.kill_switch))  # type: ignore[arg-type]

    async def step(ctx: StepContext) -> dict:
        return {"r": await ctx.tool("t_broken", {})}

    eng.register(Playbook("broken", "Kaputt", "d", {"type": "object"}, [StepDef("write", step)]))
    await eng.start()
    try:
        task = await eng.create("broken", {}, OWNER)
        t = await wait_status(eng, task.id, {"waiting_approval"})
        owner = Principal(id="desk", role="owner", channel="device", device_id=__import__("uuid").uuid4())
        await stack.executor.decide(t.steps[0].approval_id, True, owner)
        t = await wait_status(eng, task.id, {"failed"})
        assert "Lexware antwortet nicht" in (t.error or "")
        for _ in range(20):
            if failed:
                break
            await asyncio.sleep(0.05)
        assert failed and failed[0]["task_id"] == str(task.id) and failed[0]["title"] == "Kaputt"
    finally:
        await eng.stop()


async def test_schema_defaults_applied_for_title(stack) -> None:
    from jarvis.jobs.playbooks import NEW_CUSTOMER_PROJECT

    eng = TaskEngine(SimpleNamespace(executor=stack.executor, kill_switch=stack.kill_switch))  # type: ignore[arg-type]
    eng.register(NEW_CUSTOMER_PROJECT)
    task = await eng.create(
        "new_customer_project", {"customer_name": "Müller Gebäudereinigung", "price_eur": 499}, OWNER
    )
    assert task.title == "Neukunde Müller Gebäudereinigung: Website (499 EUR)"
    assert task.params["project_type"] == "Website"
