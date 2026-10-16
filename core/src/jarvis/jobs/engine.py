"""Persistent task engine for multi-step, resumable workflows (playbooks).

* Tasks and steps live in Postgres; a worker claims tasks with a lease
  (``FOR UPDATE SKIP LOCKED``), so a crash or restart simply lets the lease expire and the
  task resumes at the first unfinished step.
* External writes inside a step go through the ToolExecutor with deterministic idempotency
  keys (``task:<id>:<step>:<n>``) — re-running a step never duplicates a Lexware contact,
  an invoice draft or an e-mail.
* Steps can pause for an approval, for missing input from Sir or for an external event
  (e.g. a coding job finishing) and are resumed by the corresponding event.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import jsonschema
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from jarvis.db import session_scope
from jarvis.db.models import Task, TaskStep
from jarvis.events import Event, activity, bus
from jarvis.security.audit import audit
from jarvis.security.auth import Principal
from jarvis.tools.registry import ToolContext

if TYPE_CHECKING:
    from jarvis.app_state import AppState

logger = logging.getLogger(__name__)
LEASE = timedelta(seconds=90)
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


class StepWait(Exception):
    def __init__(self, status: str, info: dict[str, Any]) -> None:
        super().__init__(status)
        self.status = status  # waiting_approval | waiting_input | waiting_external
        self.info = info


class StepSkip(Exception):
    pass


class StepFail(Exception):
    pass


@dataclass
class StepContext:
    engine: TaskEngine
    task_id: uuid.UUID
    step_id: uuid.UUID
    step_idx: int
    params: dict[str, Any]
    context: dict[str, Any]
    principal: Principal
    session_id: uuid.UUID | None
    previous_output: dict[str, Any] = field(default_factory=dict)
    _calls: int = 0

    async def tool(self, name: str, args: dict[str, Any], key: str | None = None) -> Any:
        """Execute a tool idempotently; pauses the task when an approval is required.

        The idempotency key is derived from the call position inside the step (deterministic
        on re-runs) unless an explicit ``key`` is given.
        """
        self._calls += 1
        key = f"task:{self.task_id}:{self.step_idx}:{key or self._calls}:{name}"
        ctx = ToolContext(
            principal=self.principal, session_id=self.session_id, task_step_id=self.step_id, idempotency_key=key
        )
        res = await self.engine.state.executor.execute(name, args, ctx)
        if res.status in ("ok", "duplicate"):
            return res.data
        if res.status == "approval_required":
            raise StepWait(
                "waiting_approval", {"approval_id": str(res.approval_id), "tool": name, "message": res.message}
            )
        if res.status == "in_progress":
            raise StepWait("waiting_external", {"reason": "tool_in_progress", "tool": name})
        if res.status == "denied" and "nicht eingerichtet" in res.message:
            raise StepSkip(res.message)
        raise StepFail(f"{name}: {res.message}")

    def ask(self, question: str, fields: list[str]) -> None:
        raise StepWait("waiting_input", {"question": question, "fields": fields})

    def wait_for(self, event_type: str, match: dict[str, Any], note: str) -> None:
        raise StepWait("waiting_external", {"event": event_type, "match": match, "note": note})


StepFn = Callable[[StepContext], Awaitable[dict[str, Any] | None]]


@dataclass(slots=True)
class StepDef:
    name: str
    fn: StepFn


@dataclass(slots=True)
class Playbook:
    name: str
    title: str
    description: str
    params_schema: dict[str, Any]
    steps: list[StepDef]
    title_template: str = "{name}"


class TaskEngine:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self.playbooks: dict[str, Playbook] = {}
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._running: dict[uuid.UUID, asyncio.Task[None]] = {}

    def register(self, pb: Playbook) -> None:
        self.playbooks[pb.name] = pb

    async def start(self) -> None:
        bus.subscribe("approval.decided", self._on_approval)
        bus.subscribe("coding.finished", self._on_external)
        bus.subscribe("task.external", self._on_external)
        self._worker = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._worker:
            self._worker.cancel()
        for t in self._running.values():
            t.cancel()

    # ------------------------------------------------------------------ API
    async def create(
        self,
        playbook: str,
        params: dict[str, Any],
        principal: Principal,
        *,
        session_id: uuid.UUID | None = None,
        idempotency_key: str | None = None,
    ) -> Task:
        pb = self.playbooks.get(playbook)
        if pb is None:
            raise ValueError(f"Unbekanntes Playbook: {playbook}")
        params = _with_defaults(params, pb.params_schema)
        jsonschema.validate(params, pb.params_schema)
        async with session_scope() as s:
            if idempotency_key:
                existing = (
                    await s.execute(select(Task).where(Task.idempotency_key == idempotency_key))
                ).scalar_one_or_none()
                if existing is not None:
                    return existing
            fields = {k: v for k, v in params.items() if isinstance(v, str | int | float)}
            title = pb.title_template.format_map(_DefaultDict(fields, name=pb.title))
            task = Task(
                title=title[:300],
                kind=playbook,
                status="queued",
                params=params,
                context={},
                actor_role=principal.role,
                actor_id=principal.label,
                session_id=session_id,
                idempotency_key=idempotency_key,
            )
            s.add(task)
            await s.flush()
            for i, sd in enumerate(pb.steps):
                s.add(TaskStep(task_id=task.id, idx=i, name=sd.name, status="pending"))
            task_id = task.id
        await audit(
            principal.label, "task.create", target=str(task_id), details={"playbook": playbook, "params": params}
        )
        await bus.publish("task.created", task_id=str(task_id), title=title, playbook=playbook)
        self._wake.set()
        return await self.get_task(task_id)  # type: ignore[return-value]

    async def get_task(self, task_id: uuid.UUID) -> Task | None:
        async with session_scope() as s:
            return (
                await s.execute(select(Task).options(selectinload(Task.steps)).where(Task.id == task_id))
            ).scalar_one_or_none()

    async def list_tasks(self, status: str | None = None, limit: int = 50) -> list[Task]:
        q = select(Task).options(selectinload(Task.steps)).order_by(Task.created_at.desc()).limit(limit)
        if status:
            q = q.where(Task.status == status)
        async with session_scope() as s:
            return list((await s.execute(q)).scalars())

    async def provide_input(self, task_id: uuid.UUID, values: dict[str, Any], actor: str) -> Task | None:
        async with session_scope() as s:
            task = await s.get(Task, task_id, with_for_update=True)
            if task is None or task.status != "waiting_input":
                raise ValueError("Aufgabe wartet nicht auf Eingaben")
            task.params = {**task.params, **values}
            task.pending_question = None
            task.status = "queued"
            step = (
                await s.execute(select(TaskStep).where(TaskStep.task_id == task_id, TaskStep.status == "waiting_input"))
            ).scalar_one_or_none()
            if step:
                step.status = "pending"
        await audit(actor, "task.input", target=str(task_id), details={"keys": sorted(values)})
        self._wake.set()
        return await self.get_task(task_id)

    async def cancel(self, task_id: uuid.UUID, actor: str) -> bool:
        async with session_scope() as s:
            task = await s.get(Task, task_id, with_for_update=True)
            if task is None or task.status in ("completed", "failed", "cancelled"):
                return False
            task.status = "cancelled"
            task.lease_until = None
        running = self._running.get(task_id)
        if running:
            running.cancel()
        await audit(actor, "task.cancel", target=str(task_id))
        await bus.publish("task.updated", task_id=str(task_id), status="cancelled")
        return True

    async def retry(self, task_id: uuid.UUID, actor: str) -> bool:
        async with session_scope() as s:
            task = await s.get(Task, task_id, with_for_update=True)
            if task is None or task.status not in ("failed", "cancelled"):
                return False
            task.status = "queued"
            task.error = None
            for step in (await s.execute(select(TaskStep).where(TaskStep.task_id == task_id))).scalars():
                if step.status in ("failed", "running", "waiting_approval", "waiting_external"):
                    step.status = "pending"
                    step.error = None
        await audit(actor, "task.retry", target=str(task_id))
        self._wake.set()
        return True

    # ------------------------------------------------------------------ worker
    async def _loop(self) -> None:
        while True:
            try:
                claimed = await self._claim()
                if claimed is None:
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=15)
                    except TimeoutError:
                        pass
                    continue
                t = asyncio.create_task(self._execute(claimed))
                self._running[claimed] = t
                t.add_done_callback(self._forget_running(claimed))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Task loop error")
                await asyncio.sleep(5)

    def _forget_running(self, tid: uuid.UUID) -> Callable[[asyncio.Task[None]], None]:
        def done(_t: asyncio.Task[None]) -> None:
            self._running.pop(tid, None)

        return done

    async def _claim(self) -> uuid.UUID | None:
        if self.state.kill_switch.engaged:
            return None
        now = datetime.now(UTC)
        async with session_scope() as s:
            task = (
                await s.execute(
                    select(Task)
                    .where(or_(Task.status == "queued", (Task.status == "running") & (Task.lease_until < now)))
                    .where(Task.id.not_in(list(self._running) or [uuid.uuid4()]))
                    .order_by(Task.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if task is None:
                return None
            task.status = "running"
            task.lease_owner = WORKER_ID
            task.lease_until = now + LEASE
            task.attempts += 1
            return task.id

    async def _heartbeat(self, task_id: uuid.UUID) -> None:
        while True:
            await asyncio.sleep(LEASE.total_seconds() / 3)
            async with session_scope() as s:
                task = await s.get(Task, task_id)
                if task is None or task.status != "running":
                    return
                task.lease_until = datetime.now(UTC) + LEASE

    async def _execute(self, task_id: uuid.UUID) -> None:
        hb = asyncio.create_task(self._heartbeat(task_id))
        try:
            await self._execute_steps(task_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Task %s crashed", task_id)
            await self._finish(task_id, "failed", error=f"{type(e).__name__}: {e}")
        finally:
            hb.cancel()

    async def _execute_steps(self, task_id: uuid.UUID) -> None:
        task = await self.get_task(task_id)
        if task is None:
            return
        pb = self.playbooks.get(task.kind)
        if pb is None:
            await self._finish(task_id, "failed", error=f"Playbook {task.kind} nicht registriert")
            return
        await bus.publish("task.updated", task_id=str(task_id), status="running", title=task.title)
        principal = Principal(
            id=task.actor_id.split(":", 1)[-1],
            role=task.actor_role,
            channel=task.actor_id.split(":", 1)[0] if ":" in task.actor_id else "task",
        )
        context = dict(task.context or {})
        previous: dict[str, Any] = {}
        warnings: list[str] = list(context.get("_warnings", []))
        for step in task.steps:
            if step.status in ("completed", "skipped"):
                previous = step.output or {}
                continue
            if self.state.kill_switch.engaged:
                await self._set_task(task_id, status="queued", lease_until=None)
                return
            sd = pb.steps[step.idx]
            await self._set_step(step.id, status="running", started_at=datetime.now(UTC), error=None)
            await bus.publish("task.step", task_id=str(task_id), step=step.idx, name=step.name, status="running")
            sctx = StepContext(
                self, task_id, step.id, step.idx, dict(task.params), context, principal, task.session_id, previous
            )
            try:
                with activity(task_id=str(task_id), session_id=str(task.session_id) if task.session_id else None):
                    out = await sd.fn(sctx) or {}
            except StepWait as w:
                await self._set_step(
                    step.id,
                    status=w.status,
                    output={"wait": w.info},
                    approval_id=uuid.UUID(w.info["approval_id"]) if "approval_id" in w.info else None,
                )
                context.update({"_warnings": warnings})
                await self._set_task(
                    task_id,
                    status=w.status,
                    context=_jsonable(context),
                    lease_until=None,
                    pending_question=w.info.get("question"),
                )
                await bus.publish(
                    "task.updated", task_id=str(task_id), status=w.status, step=step.idx, title=task.title, info=w.info
                )
                if w.status == "waiting_input":
                    await bus.publish(
                        "task.input_required",
                        task_id=str(task_id),
                        title=task.title,
                        question=w.info.get("question"),
                        fields=w.info.get("fields"),
                    )
                return
            except StepSkip as sk:
                warnings.append(f"{step.name}: {sk}")
                await self._set_step(step.id, status="skipped", error=str(sk), finished_at=datetime.now(UTC))
                await bus.publish(
                    "task.step", task_id=str(task_id), step=step.idx, name=step.name, status="skipped", note=str(sk)
                )
                continue
            except StepFail as f:
                await self._set_step(step.id, status="failed", error=str(f), finished_at=datetime.now(UTC))
                await self._finish(task_id, "failed", error=f"{step.name}: {f}", context=context)
                return
            context[f"step_{step.idx}"] = out
            context["_warnings"] = warnings
            previous = out
            await self._set_step(step.id, status="completed", output=_jsonable(out), finished_at=datetime.now(UTC))
            await self._set_task(task_id, context=_jsonable(context))
            await bus.publish("task.step", task_id=str(task_id), step=step.idx, name=step.name, status="completed")
        await self._finish(
            task_id, "completed", context=context, result={"summary": context.get("summary", ""), "warnings": warnings}
        )

    async def _finish(
        self,
        task_id: uuid.UUID,
        status: str,
        *,
        error: str | None = None,
        context: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        fields: dict[str, Any] = {"status": status, "lease_until": None}
        if error is not None:
            fields["error"] = error
        if context is not None:
            fields["context"] = _jsonable(context)
        if result is not None:
            fields["result"] = _jsonable(result)
        await self._set_task(task_id, **fields)
        task = await self.get_task(task_id)
        title = task.title if task else str(task_id)
        await audit("task-engine", f"task.{status}", target=str(task_id), details={"error": error})
        await bus.publish(
            f"task.{status}", task_id=str(task_id), title=title, error=error, result=_jsonable(result or {})
        )
        await bus.publish("task.updated", task_id=str(task_id), status=status, title=title)

    async def _set_task(self, task_id: uuid.UUID, **fields: Any) -> None:
        async with session_scope() as s:
            task = await s.get(Task, task_id)
            if task is None:
                return
            if task.status == "cancelled" and fields.get("status") not in (None, "cancelled"):
                fields.pop("status")
            for k, v in fields.items():
                setattr(task, k, v)

    async def _set_step(self, step_id: uuid.UUID, **fields: Any) -> None:
        async with session_scope() as s:
            step = await s.get(TaskStep, step_id)
            if step is not None:
                for k, v in fields.items():
                    setattr(step, k, v)

    # ------------------------------------------------------------------ resume triggers
    async def _on_approval(self, ev: Event) -> None:
        step_id = ev.data.get("task_step_id")
        if not step_id:
            return
        async with session_scope() as s:
            step = await s.get(TaskStep, uuid.UUID(step_id))
            if step is None or step.status != "waiting_approval":
                return
            task = await s.get(Task, step.task_id, with_for_update=True)
            if task is None or task.status in ("cancelled", "completed", "failed"):
                return
            if ev.data.get("approved") and ev.data.get("status") == "executed":
                # The approved tool already ran (exactly once); re-running the step returns the
                # ledger result for that call and continues.
                step.status = "pending"
                task.status = "queued"
            elif ev.data.get("approved"):
                step.status = "failed"
                step.error = f"Ausführung nach Freigabe fehlgeschlagen: {ev.data.get('result')}"
                task.status = "failed"
                task.error = step.error
            else:
                step.status = "failed"
                step.error = "Von Sir abgelehnt"
                task.status = "cancelled"
                task.error = f"Schritt '{step.name}' wurde abgelehnt"
        await bus.publish("task.updated", task_id=str(step.task_id), status=task.status)
        self._wake.set()

    async def _on_external(self, ev: Event) -> None:
        async with session_scope() as s:
            steps = (await s.execute(select(TaskStep).where(TaskStep.status == "waiting_external"))).scalars().all()
            woke = False
            for step in steps:
                wait = (step.output or {}).get("wait", {})
                if wait.get("event") != ev.type:
                    continue
                if all(str(ev.data.get(k)) == str(v) for k, v in (wait.get("match") or {}).items()):
                    task = await s.get(Task, step.task_id, with_for_update=True)
                    if task is None or task.status != "waiting_external":
                        continue
                    ctx = dict(task.context or {})
                    ctx[f"event_{step.idx}"] = _jsonable(ev.data)
                    task.context = ctx
                    step.status = "pending"
                    task.status = "queued"
                    woke = True
        if woke:
            self._wake.set()


class _DefaultDict(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "?"


def _with_defaults(params: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Apply JSON-schema ``default`` values for missing top-level parameters."""
    out = dict(params)
    for key, prop in (schema.get("properties") or {}).items():
        if key not in out and isinstance(prop, dict) and "default" in prop:
            out[key] = prop["default"]
    return out


def _jsonable(value: Any) -> Any:
    import json

    return json.loads(json.dumps(value, default=str))
