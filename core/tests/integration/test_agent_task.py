"""Persistent agent tasks (jobs/agent_task.py) on the real TaskEngine + database, with a fake model client (the "turn
runner") and a fake checker. Covers: many tool actions, running without any client, state for a reconnecting client,
restart/resume without repeating, retry with backoff, context limit, dependent steps, completion check, cancel,
approval wait + continue, input wait + continue, limits → BLOCKED."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import update

from jarvis.agent.llm import AgentEvent
from jarvis.config import AgentTaskCfg
from jarvis.db import session_scope
from jarvis.db.models import Approval, Task
from jarvis.events import bus
from jarvis.jobs.agent_task import (
    AgentState,
    AgentTaskService,
    ToolEvent,  # noqa: F401  (re-exported type used by readers of this test)
    TurnOutcome,
    Verdict,
    backoff_s,
    classify_error,
    looks_long_running,
)
from jarvis.jobs.engine import TaskEngine
from jarvis.jobs.task_status import phase
from jarvis.security.auth import Principal

pytestmark = pytest.mark.integration
OWNER = Principal(id="desk", role="owner", channel="device")


# ------------------------------------------------------------------ fakes
class FakeOrchestrator:
    def __init__(self) -> None:
        self.sessions: list[uuid.UUID] = []
        self.interrupted: list[uuid.UUID] = []
        self.said: list[str] = []

    async def get_or_create_session(self, principal: Any, channel: str, sid: Any = None, title: str = "") -> Any:
        s = SimpleNamespace(id=uuid.uuid4())
        self.sessions.append(s.id)
        return s

    async def interrupt(self, sid: uuid.UUID | None = None) -> bool:
        self.interrupted.append(sid)  # type: ignore[arg-type]
        return True

    async def record_assistant(self, sid: Any, text: str, meta: Any = None) -> None:
        self.said.append(text)


Step = Callable[[Any, str, Callable[[AgentEvent], Awaitable[None]]], Awaitable[TurnOutcome]]


class Script:
    """Scripted model client: the n-th call runs the n-th step (the last one repeats)."""

    def __init__(self, *steps: Step) -> None:
        self.steps = steps
        self.prompts: list[str] = []
        self.sessions: list[str | None] = []

    async def run(self, task: Any, prompt: str, on_event: Any) -> TurnOutcome:
        self.prompts.append(prompt)
        self.sessions.append(task.st.session_id)
        step = self.steps[min(len(self.prompts) - 1, len(self.steps) - 1)]
        return await step(task, prompt, on_event)


def acts(n: int, *, ok: bool = True, tool: str = "t_low", args: str = "x", text: str = "erledigt") -> Step:
    async def step(task: Any, prompt: str, on_event: Any) -> TurnOutcome:
        for i in range(n):
            uid = f"{tool}-{uuid.uuid4().hex[:6]}"
            a = args if args != "unique" else str(i)
            await on_event(AgentEvent("tool_started", data={"name": tool, "tool_use_id": uid, "args": a}))
            if ok:
                await on_event(AgentEvent("tool_completed", data={"name": tool, "tool_use_id": uid, "result": "ok"}))
            else:
                await on_event(AgentEvent("tool_failed", data={"name": tool, "tool_use_id": uid, "error": "kaputt"}))
        return TurnOutcome(text=text)

    return step


def fails(error: str) -> Step:
    async def step(task: Any, prompt: str, on_event: Any) -> TurnOutcome:
        return TurnOutcome(error=error)

    return step


class FakeJudge:
    def __init__(self, *verdicts: Verdict, plan: list[str] | None = None) -> None:
        self.verdicts = verdicts
        self.calls = 0
        self.prompts: list[str] = []
        self._plan = plan or ["Schritt eins", "Schritt zwei", "Prüfen"]

    async def plan(self, goal: str, hints: list[str]) -> list[str]:
        return list(self._plan)

    async def verdict(self, prompt: str) -> Verdict:
        self.prompts.append(prompt)
        v = self.verdicts[min(self.calls, len(self.verdicts) - 1)]
        self.calls += 1
        return v


def done_all(evidence: str = "Beleg: Datei erzeugt") -> Verdict:
    return Verdict(
        goal_reached=True, steps={0: ("done", "ok"), 1: ("done", "ok"), 2: ("done", "ok")}, evidence=evidence
    )


def partial(*done: int, missing: str = "Rest fehlt") -> Verdict:
    return Verdict(goal_reached=False, steps={i: ("done", "ok") for i in done}, missing=[missing])


def make(
    stack: Any, runner: Script, judge: FakeJudge, **cfg: Any
) -> tuple[TaskEngine, AgentTaskService, FakeOrchestrator]:
    bus._handlers.clear()
    c = AgentTaskCfg(**{"retry_base_s": 0.05, "retry_max_s": 0.2, **cfg})
    orch = FakeOrchestrator()
    state = SimpleNamespace(
        executor=stack.executor,
        kill_switch=stack.kill_switch,
        registry=stack.registry,
        settings=SimpleNamespace(agent=SimpleNamespace(tasks=c)),
        orchestrator=orch,
    )
    eng = TaskEngine(state)  # type: ignore[arg-type]
    svc = AgentTaskService(state, runner, judge)  # type: ignore[arg-type]
    state.tasks = eng
    state.agent_tasks = svc
    svc.register()
    return eng, svc, orch


async def wait_for(eng: TaskEngine, tid: uuid.UUID, statuses: set[str], timeout: float = 20.0) -> Task:
    end = asyncio.get_running_loop().time() + timeout
    t = None
    while asyncio.get_running_loop().time() < end:
        t = await eng.get_task(tid)
        if t and t.status in statuses:
            return t
        eng._wake.set()
        await asyncio.sleep(0.05)
    raise AssertionError(f"task stuck in {t.status if t else None}: {(t.error if t else '')}")


def state_of(t: Task) -> AgentState:
    return AgentState.from_dict((t.context or {}).get("agent"))


async def start(svc: AgentTaskService, goal: str = "Erledige A, dann B und prüfe C") -> Task:
    return await svc.start(goal, OWNER, channel="mobile", origin_session=uuid.uuid4())


# ------------------------------------------------------------------ 1 + 8: many actions, completion with verification
async def test_more_than_20_consecutive_tool_actions_then_verified_completion(stack: Any) -> None:
    runner = Script(acts(25, args="unique"))
    judge = FakeJudge(done_all())
    eng, svc, orch = make(stack, runner, judge)
    await eng.start()
    try:
        task = await start(svc)
        assert task.status == "queued"  # start returns at once with the id
        t = await wait_for(eng, task.id, {"completed"})
    finally:
        await eng.stop()
    st = state_of(t)
    assert st.tool_calls == 25 and len(st.ledger) == 25 and st.turns == 1
    assert st.verification["passed"] is True and st.verification["evidence"]
    assert all(p.status == "done" for p in st.plan)
    assert t.result["summary"] == "erledigt" and phase(t.status) == "completed"
    assert any("Aufgabe erledigt" in s for s in orch.said)  # the origin chat gets the result


async def test_judge_yes_without_evidence_or_with_open_steps_is_not_completion(stack: Any) -> None:
    runner = Script(acts(1))
    judge = FakeJudge(
        Verdict(goal_reached=True, steps={0: ("done", "")}, evidence="Beleg"),  # steps 1+2 still open
        Verdict(goal_reached=True, steps={1: ("done", ""), 2: ("done", "")}, evidence=""),  # no evidence
        done_all(),
    )
    eng, svc, _ = make(stack, runner, judge)
    await eng.start()
    try:
        task = await start(svc)
        t = await wait_for(eng, task.id, {"completed"})
    finally:
        await eng.stop()
    st = state_of(t)
    assert st.turns == 3 and st.continuations == 2  # two rejected "done"s, completed only after the third check
    assert "Plan-Schritte nicht erledigt" in runner.prompts[1] and "Kein Beleg" in runner.prompts[2]


async def test_unrecovered_tool_failure_blocks_completion(stack: Any) -> None:
    runner = Script(acts(1, ok=False, tool="t_a"), acts(1, tool="t_a"))
    judge = FakeJudge(done_all(), done_all())
    eng, svc, _ = make(stack, runner, judge)
    await eng.start()
    try:
        task = await start(svc)
        t = await wait_for(eng, task.id, {"completed"})
    finally:
        await eng.stop()
    assert state_of(t).turns == 2 and "Offene Fehler bei: t_a" in runner.prompts[1]


# ------------------------------------------------------------------ 7: dependent steps, compact state, 3: reconnect view
async def test_dependent_steps_continue_with_structured_state(stack: Any) -> None:
    runner = Script(acts(2), acts(2), acts(1))
    judge = FakeJudge(partial(0), partial(0, 1), done_all())
    eng, svc, _ = make(stack, runner, judge)
    await eng.start()
    try:
        task = await start(svc, "Erledige A, dann B und prüfe C")
        t = await wait_for(eng, task.id, {"completed"})
    finally:
        await eng.stop()
    st = state_of(t)
    assert st.turns == 3 and st.continuations == 2
    assert "ZIEL:\nErledige A, dann B und prüfe C" in runner.prompts[0] and "FORTSETZUNG" not in runner.prompts[0]
    assert "FORTSETZUNG (Lauf 2)" in runner.prompts[1] and "[x] Schritt eins" in runner.prompts[1]
    assert "[x] Schritt zwei" in runner.prompts[2] and "Rest fehlt" in runner.prompts[2]


async def test_client_gone_and_reconnect_shows_the_current_state(stack: Any) -> None:
    gate = asyncio.Event()

    async def slow(task: Any, prompt: str, on_event: Any) -> TurnOutcome:
        await acts(3)(task, prompt, on_event)
        await gate.wait()  # the model is still working while "the app" looks again
        return TurnOutcome(text="fertig")

    runner = Script(slow)
    eng, svc, _ = make(stack, runner, FakeJudge(done_all()))
    await eng.start()
    try:
        task = await start(svc)  # nobody listens to any stream: no client is attached at all
        end = asyncio.get_running_loop().time() + 10
        view = None
        while asyncio.get_running_loop().time() < end:  # a new client connects and asks
            t = await eng.get_task(task.id)
            view = AgentTaskService.view(t) if t else None
            if view and view["tool_calls"] == 3:
                break
            await asyncio.sleep(0.05)
        assert view and view["tool_calls"] == 3 and view["phase"] == "executing" and view["plan_total"] == 3
        assert view["session_id"] and view["goal"]
        assert (await eng.get_task(task.id)).status == "running"  # type: ignore[union-attr]
        gate.set()
        t = await wait_for(eng, task.id, {"completed"})
    finally:
        await eng.stop()
    assert AgentTaskService.view(t)["verification"]["passed"] is True  # type: ignore[index]
    assert AgentTaskService.log(t)["ledger"]


# ------------------------------------------------------------------ 4: restart in the middle of a turn
async def test_core_restart_resumes_from_the_checkpoint_without_repeating(stack: Any) -> None:
    started = asyncio.Event()

    async def hang(task: Any, prompt: str, on_event: Any) -> TurnOutcome:
        await acts(2, tool="t_write", args="unique")(task, prompt, on_event)
        started.set()
        await asyncio.sleep(3600)
        return TurnOutcome(text="never")

    eng1, svc1, _ = make(stack, Script(hang), FakeJudge(done_all()))
    await eng1.start()
    task = await start(svc1)
    await asyncio.wait_for(started.wait(), 10)
    # the Core process dies: the worker task vanishes, the DB still says "running" with a live lease of a dead pid
    for t in list(eng1._running.values()):
        t.cancel()
    await eng1.stop()
    async with session_scope() as s:
        await s.execute(
            update(Task)
            .where(Task.id == task.id)
            .values(lease_owner="host:1", lease_until=datetime.now(UTC) + timedelta(seconds=80))
        )
    before = state_of(await eng1.get_task(task.id))  # type: ignore[arg-type]
    assert before.in_flight == 1 and len(before.ledger) == 2

    # a new Core process: its start frees the orphaned lease (here: only if the owner is another pid of this host)
    runner2 = Script(acts(1))
    eng2, svc2, _ = make(stack, runner2, FakeJudge(done_all()))
    from jarvis.jobs import engine as engine_mod

    host = engine_mod.WORKER_ID.rsplit(":", 1)[0]
    async with session_scope() as s:
        await s.execute(update(Task).where(Task.id == task.id).values(lease_owner=f"{host}:1"))
    await eng2.start()
    try:
        t = await wait_for(eng2, task.id, {"completed"}, timeout=15)
    finally:
        await eng2.stop()
    st = state_of(t)
    assert "Lauf 1 wurde unterbrochen" in " ".join(st.notes)
    assert "t_write (ok), t_write (ok)" in runner2.prompts[0]  # told what already ran: nothing may be repeated
    assert "wiederhole keine Aktion mit Außenwirkung" in runner2.prompts[0]
    assert st.tool_calls == 3 and [e["turn"] for e in st.ledger] == [1, 1, 2]  # the ledger survived the restart
    assert t.attempts == 2


# ------------------------------------------------------------------ 5: failures → retry/backoff/correction
async def test_transient_error_retries_with_backoff_then_succeeds(stack: Any) -> None:
    runner = Script(fails("Zeitüberschreitung nach 15 Minuten"), fails("Claude Code wurde unerwartet beendet"), acts(1))
    seen: list[str] = []
    eng, svc, _ = make(stack, runner, FakeJudge(done_all()))

    async def rec(ev: Any) -> None:
        seen.append(ev.data.get("status"))

    bus.subscribe("task.updated", rec)
    await eng.start()
    try:
        task = await start(svc)
        t = await wait_for(eng, task.id, {"completed"})
    finally:
        await eng.stop()
    assert "retrying" in seen  # visible state while the backoff runs
    st = state_of(t)
    assert st.turns == 3 and st.retries == 0  # reset after the success
    assert any("Wiederholung 1" in n for n in st.notes) and any("Wiederholung 2" in n for n in st.notes)
    assert backoff_s(1, 5, 300) == 5 and backoff_s(3, 5, 300) == 20 and backoff_s(20, 5, 300) == 300


async def test_endless_transient_errors_end_blocked_with_the_reason(stack: Any) -> None:
    eng, svc, _ = make(stack, Script(fails("Verbindung getrennt")), FakeJudge(done_all()), max_retries=2)
    await eng.start()
    try:
        task = await start(svc)
        t = await wait_for(eng, task.id, {"blocked"})
    finally:
        await eng.stop()
    assert "Nach 2 Wiederholungen" in (t.error or "") and phase(t.status) == "blocked"


async def test_same_failing_action_again_and_again_is_blocked(stack: Any) -> None:
    runner = Script(acts(1, ok=False, tool="t_x", args="gleich"))
    eng, svc, _ = make(stack, runner, FakeJudge(partial()), repeat_limit=3)
    await eng.start()
    try:
        task = await start(svc)
        t = await wait_for(eng, task.id, {"blocked"})
    finally:
        await eng.stop()
    assert "t_x" in (t.error or "") and "3-mal identisch" in (t.error or "")
    assert state_of(t).turns == 3


async def test_login_missing_is_a_blocker_not_a_retry(stack: Any) -> None:
    eng, svc, _ = make(
        stack, Script(fails("Claude Code ist nicht angemeldet (im Terminal `claude` → /login).")), FakeJudge(done_all())
    )
    await eng.start()
    try:
        task = await start(svc)
        t = await wait_for(eng, task.id, {"blocked"})
    finally:
        await eng.stop()
    assert state_of(t).turns == 1 and "angemeldet" in (t.error or "")


# ------------------------------------------------------------------ 6: context limit
async def test_context_limit_starts_a_fresh_session_with_the_compact_state(stack: Any) -> None:
    runner = Script(acts(2), fails("Prompt is too long"), acts(1))
    judge = FakeJudge(partial(0), done_all())
    eng, svc, orch = make(stack, runner, judge)
    await eng.start()
    try:
        task = await start(svc)
        t = await wait_for(eng, task.id, {"completed"})
    finally:
        await eng.stop()
    assert len(set(s for s in runner.sessions if s)) >= 2  # a new agent session after the limit
    assert len(orch.sessions) >= 2
    # the retry after the limit carries goal, plan with the finished step and the notes – not the old history
    assert "ZIEL:" in runner.prompts[2] and "[x] Schritt eins" in runner.prompts[2]
    assert any("Kontextlimit" in n for n in state_of(t).notes)


async def test_session_is_rotated_after_n_turns(stack: Any) -> None:
    runner = Script(acts(1))
    judge = FakeJudge(partial(0), partial(0), partial(0), partial(0, 1), partial(0, 1), done_all())
    eng, svc, orch = make(stack, runner, judge, session_rotate_turns=2)
    await eng.start()
    try:
        task = await start(svc)
        await wait_for(eng, task.id, {"completed"})
    finally:
        await eng.stop()
    assert len(orch.sessions) >= 3  # first + a fresh one every 2 turns


# ------------------------------------------------------------------ 9: cancel
async def test_cancel_stops_the_run_and_the_agent_process(stack: Any) -> None:
    running = asyncio.Event()

    async def block(task: Any, prompt: str, on_event: Any) -> TurnOutcome:
        running.set()
        await asyncio.sleep(3600)
        return TurnOutcome(text="x")

    eng, svc, orch = make(stack, Script(block), FakeJudge(done_all()))
    await eng.start()
    try:
        task = await start(svc)
        await asyncio.wait_for(running.wait(), 10)
        assert await eng.cancel(task.id, "pytest")
        t = await wait_for(eng, task.id, {"cancelled"})
        await asyncio.sleep(0.2)
    finally:
        await eng.stop()
    assert phase(t.status) == "cancelled" and orch.interrupted  # the claude process got stopped too


# ------------------------------------------------------------------ 10: approval → waiting for user → continue
async def test_pending_approval_parks_the_task_and_the_decision_continues_it(stack: Any) -> None:
    aid = uuid.uuid4()

    async def ask_approval(task: Any, prompt: str, on_event: Any) -> TurnOutcome:
        async with session_scope() as s:
            s.add(
                Approval(
                    id=aid,
                    tool="gmail_send",
                    args={},
                    summary="E-Mail an Müller senden",
                    risk="high",
                    reason="Außenwirkung",
                    requested_by_role="owner",
                    requested_by="desk",
                    channel="device",
                    session_id=uuid.UUID(task.st.session_id),
                    idempotency_key=f"k{aid}",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
        await bus.publish("approval.requested", approval_id=str(aid), session_id=task.st.session_id, tool="gmail_send")
        return TurnOutcome(text="FREIGABE ERFORDERLICH: wartet")

    runner = Script(ask_approval, acts(1))
    eng, svc, _ = make(stack, runner, FakeJudge(partial(0), done_all()))
    await eng.start()
    try:
        task = await start(svc)
        t = await wait_for(eng, task.id, {"waiting_approval"})
        assert phase(t.status) == "waiting_for_user" and state_of(t).waiting["approval_ids"] == [str(aid)]
        assert len(runner.prompts) == 1  # parked, not spinning
        async with session_scope() as s:
            ap = await s.get(Approval, aid)
            ap.status, ap.result = "executed", {"status": "ok"}
        await bus.publish(
            "approval.decided", approval_id=str(aid), approved=True, status="executed", tool="gmail_send",
            summary="E-Mail an Müller senden", result={"status": "ok"}, session_id=None, task_step_id=None,
        )  # fmt: skip
        t = await wait_for(eng, task.id, {"completed"})
    finally:
        await eng.stop()
    assert "ENTSCHEIDUNGEN ZU FREIGABEN" in runner.prompts[1] and "freigegeben und ausgeführt" in runner.prompts[1]


async def test_rejected_approval_is_passed_on_and_the_task_goes_on(stack: Any) -> None:
    aid = uuid.uuid4()

    async def ask(task: Any, prompt: str, on_event: Any) -> TurnOutcome:
        async with session_scope() as s:
            s.add(
                Approval(
                    id=aid, tool="invoice_send", args={}, summary="Rechnung senden", risk="high", reason="x",
                    requested_by_role="owner", requested_by="desk", channel="device",
                    session_id=uuid.UUID(task.st.session_id), idempotency_key=f"k{aid}",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )  # fmt: skip
        await bus.publish("approval.requested", approval_id=str(aid), session_id=task.st.session_id)
        return TurnOutcome(text="wartet")

    runner = Script(ask, acts(1))
    eng, svc, _ = make(stack, runner, FakeJudge(partial(), done_all()))
    await eng.start()
    try:
        task = await start(svc)
        await wait_for(eng, task.id, {"waiting_approval"})
        async with session_scope() as s:
            (await s.get(Approval, aid)).status = "rejected"  # type: ignore[union-attr]
        await bus.publish("approval.decided", approval_id=str(aid), approved=False, status="rejected", tool="invoice_send", summary="Rechnung senden")  # fmt: skip
        await wait_for(eng, task.id, {"completed"})
    finally:
        await eng.stop()
    assert "von Sir abgelehnt" in runner.prompts[1]


# ------------------------------------------------------------------ input wait
async def test_missing_information_waits_for_sir_and_continues_with_the_answer(stack: Any) -> None:
    runner = Script(acts(1), acts(1))
    judge = FakeJudge(Verdict(needs_user="Welche Farbe soll die Seite haben?", missing=["Farbe"]), done_all())
    eng, svc, _ = make(stack, runner, judge)
    notified: list[Any] = []

    async def rec(ev: Any) -> None:
        notified.append(ev.data)

    bus.subscribe("task.input_required", rec)
    await eng.start()
    try:
        task = await start(svc)
        t = await wait_for(eng, task.id, {"waiting_input"})
        assert t.pending_question == "Welche Farbe soll die Seite haben?" and phase(t.status) == "waiting_for_user"
        for _ in range(40):  # bus handlers run asynchronously
            if notified:
                break
            await asyncio.sleep(0.05)
        assert notified and notified[0]["fields"] == ["answer"]  # the push/app asks the question
        await eng.provide_input(task.id, {"answer": "Dunkelblau"}, "pytest")
        t = await wait_for(eng, task.id, {"completed"})
    finally:
        await eng.stop()
    assert "ANTWORT VON SIR:\nDunkelblau" in runner.prompts[1]


# ------------------------------------------------------------------ limits → BLOCKED, retry continues
async def test_limit_reached_blocks_with_reason_and_retry_continues(stack: Any) -> None:
    runner = Script(acts(1))
    judge = FakeJudge(partial(0), partial(0), partial(0), done_all())
    eng, svc, _ = make(stack, runner, judge, max_continuations=1)
    notified: list[str] = []

    async def rec(ev: Any) -> None:
        notified.append(ev.type)

    bus.subscribe("task.blocked", rec)
    await eng.start()
    try:
        task = await start(svc)
        t = await wait_for(eng, task.id, {"blocked"})
        assert "Fortsetzungen" in (t.error or "") and "Rest fehlt" in (t.error or "")
        for _ in range(40):  # bus handlers run asynchronously
            if notified:
                break
            await asyncio.sleep(0.05)
        assert notified == ["task.blocked"]
        turns_before = state_of(t).turns
        assert await eng.retry(task.id, "pytest")  # Sir extends the run: limits count from here again
        t = await wait_for(eng, task.id, {"completed", "blocked"})
    finally:
        await eng.stop()
    assert t.status == "completed" and state_of(t).turns > turns_before


async def test_wall_clock_and_turn_limits(stack: Any) -> None:
    eng, svc, _ = make(stack, Script(acts(1)), FakeJudge(partial()), max_turns=2, max_continuations=50)
    await eng.start()
    try:
        task = await start(svc)
        t = await wait_for(eng, task.id, {"blocked"})
    finally:
        await eng.stop()
    assert "2 Agent-Läufen" in (t.error or "")


async def test_impossible_goal_fails_with_the_reason(stack: Any) -> None:
    eng, svc, _ = make(stack, Script(acts(1)), FakeJudge(Verdict(impossible="Das Konto existiert nicht")))
    await eng.start()
    try:
        task = await start(svc)
        t = await wait_for(eng, task.id, {"failed"})
    finally:
        await eng.stop()
    assert "Das Konto existiert nicht" in (t.error or "")


async def test_pause_and_kill_switch_yield_without_losing_the_checkpoint(stack: Any) -> None:
    gate = asyncio.Event()

    async def first(task: Any, prompt: str, on_event: Any) -> TurnOutcome:
        await acts(1)(task, prompt, on_event)
        await gate.wait()
        return TurnOutcome(text="x")

    runner = Script(first, acts(1))
    eng, svc, _ = make(stack, runner, FakeJudge(partial(0), done_all()))
    await eng.start()
    try:
        task = await start(svc)
        await asyncio.sleep(0.5)
        assert await eng.pause(task.id, "pytest")
        gate.set()
        t = await wait_for(eng, task.id, {"paused"})
        await asyncio.sleep(0.5)
        t = await eng.get_task(task.id)  # type: ignore[assignment]
        assert t.status == "paused" and state_of(t).turns == 1  # waits at the checkpoint
        assert await eng.resume(task.id, "pytest")
        t = await wait_for(eng, task.id, {"completed"})
    finally:
        await eng.stop()
    assert state_of(t).turns == 2


# ------------------------------------------------------------------ pure helpers
def test_error_classification() -> None:
    assert classify_error(None) == "" and classify_error("cancelled") == "cancelled"
    assert classify_error("Zeitüberschreitung nach 15 Minuten") == "transient"
    assert classify_error("Claude Code wurde unerwartet beendet (Signal SIGKILL)") == "transient"
    assert classify_error("Das Claude-Kontingent der Subscription ist ausgeschöpft") == "transient"
    assert classify_error("Claude Code ist nicht angemeldet (im Terminal `claude` → /login).") == "permission"
    assert classify_error("Prompt is too long") == "context"
    assert classify_error("irgendwas Seltsames") == "permanent"


def test_only_clear_multi_step_requests_run_in_the_background() -> None:
    long_seq = (
        "Suche zuerst alle offenen Rechnungen von Müller heraus, erstelle dann eine Übersicht als PDF und schicke sie "
        "anschließend an den Steuerberater. Prüfe danach, ob alle Beträge stimmen und fasse das Ergebnis zusammen."
    )
    assert looks_long_running(long_seq, 180)
    assert looks_long_running("Mach Folgendes:\n1. Backup\n2. Update\n3. Test\n" + "x" * 200, 180)
    assert not looks_long_running("Wie spät ist es?", 180)
    assert not looks_long_running("Erstelle dann danach etwas", 180)  # short: a normal turn
    assert not looks_long_running("direkt: " + long_seq, 180) and not looks_long_running(
        "/modell opus " + long_seq, 180
    )
    assert phase("waiting_approval") == phase("waiting_input") == "waiting_for_user" and phase("retrying") == "retrying"


def test_agent_state_round_trips_through_json() -> None:
    st = AgentState(goal="g", turns=3, fail_counts={"a:b": 2})
    st.add_note("x" * 500)
    back = AgentState.from_dict(st.to_dict())
    assert back.goal == "g" and back.turns == 3 and back.fail_counts == {"a:b": 2} and len(back.notes[0]) <= 300
    assert (
        AgentState.from_dict(None).turns == 0
        and AgentState.from_dict({"plan": [{"title": "a"}]}).plan[0].status == "open"
    )
