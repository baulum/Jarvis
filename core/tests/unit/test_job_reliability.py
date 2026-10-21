"""Jobs and notifications do not get stuck: unique push keys, cancelled jobs, watches, process shutdown."""

from __future__ import annotations

import asyncio
import os
import signal
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jarvis.coding.runner import CodingRunner
from jarvis.events import Event
from jarvis.jobs.notify import Notifier, _alert_for
from jarvis.mobile.alerts import alert_target
from jarvis.mobile.calls import CallService


def test_push_key_is_unique_per_notification() -> None:
    # a retried job that finishes again must not be dropped by the 24 h push dedupe
    data = {"job_id": "j1", "status": "failed"}
    first = _alert_for("coding.finished", "t", "b", "warning", data, uuid.uuid4())
    second = _alert_for("coding.finished", "t", "b", "warning", data, uuid.uuid4())
    assert first.event_key != second.event_key
    assert first.event_key.startswith("coding.finished:j1:")


def test_interrupted_job_alert_opens_the_job() -> None:
    a = _alert_for("coding.interrupted", "t", "b", "warning", {"job_id": "j1", "status": "interrupted"}, "n1")
    assert a.category == "error" and a.deep_link == "jarvis://job/j1" and alert_target(a) == ("job", "j1")


async def test_cancelled_job_is_not_announced() -> None:
    sent: list[str] = []
    notifier = Notifier(SimpleNamespace(settings=SimpleNamespace(notifications=None)))  # type: ignore[arg-type]

    async def notify(event: str, title: str, body: str = "", **kw: Any) -> list[str]:
        sent.append(title)
        return []

    notifier.notify = notify  # type: ignore[method-assign]
    await notifier._on_event(Event("coding.finished", {"job_id": "j", "status": "cancelled", "title": "X"}))
    await notifier._on_event(Event("coding.finished", {"job_id": "j", "status": "completed", "title": "X"}))
    await notifier._on_event(Event("coding.finished", {"job_id": "j", "status": "failed", "title": "Y"}))
    assert sent == ["Coding-Job fertig: X", "Coding-Job fehlgeschlagen: Y"]


def _calls(tmp_path: Path) -> tuple[CallService, list[Any]]:
    dispatched: list[Any] = []

    async def dispatch(alert: Any) -> dict[str, Any]:
        dispatched.append(alert)
        return {}

    state = SimpleNamespace(
        settings=SimpleNamespace(paths=SimpleNamespace(data_dir=tmp_path), mobile=SimpleNamespace(calls=None)),
        alerts=SimpleNamespace(dispatch=dispatch),
    )
    return CallService(state), dispatched  # type: ignore[arg-type]


async def test_watch_on_cancelled_job_resolves_without_a_call(tmp_path: Path) -> None:
    svc, dispatched = _calls(tmp_path)
    svc._save_watches([{"id": "w1", "type": "coding_job", "target": "j1", "message": "Fertig, Sir."}])
    await svc._on_finished_event(Event("coding.finished", {"job_id": "j1", "status": "cancelled", "title": "Site"}))
    assert svc.watches() == []
    (alert,) = dispatched
    assert not alert.call_requested and not alert.allow_call and alert.title == "Abgebrochen: Site"


async def test_watch_on_cancelled_task_resolves_without_a_call(tmp_path: Path) -> None:
    svc, dispatched = _calls(tmp_path)
    svc._save_watches([{"id": "w2", "type": "task", "target": "t1", "message": "x"}])
    await svc._on_finished_event(Event("task.cancelled", {"task_id": "t1", "status": "cancelled", "title": "T"}))
    assert svc.watches() == [] and not dispatched[0].call_requested


async def test_watch_on_completed_job_still_calls(tmp_path: Path) -> None:
    svc, dispatched = _calls(tmp_path)
    svc._save_watches([{"id": "w3", "type": "coding_job", "target": "j1", "message": "Fertig, Sir."}])
    await svc._on_finished_event(Event("coding.finished", {"job_id": "j1", "status": "completed", "title": "Site"}))
    assert dispatched[0].call_requested and dispatched[0].call_message == "Fertig, Sir."


async def test_stop_process_terminates_and_escalates() -> None:
    polite = await asyncio.create_subprocess_exec("sleep", "30", start_new_session=True)
    await CodingRunner._stop_process(polite, grace_s=5)
    assert polite.returncode == -signal.SIGTERM
    stubborn = await asyncio.create_subprocess_exec(
        "sh", "-c", "trap '' TERM; sleep 30", start_new_session=True
    )
    await asyncio.sleep(0.2)  # let the shell install its trap
    await CodingRunner._stop_process(stubborn, grace_s=0.3)
    await asyncio.wait_for(stubborn.wait(), 5)
    assert stubborn.returncode == -signal.SIGKILL


async def test_shutdown_ends_agent_processes(tmp_path: Path, monkeypatch: Any) -> None:
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    settings = SimpleNamespace(
        coding=SimpleNamespace(max_parallel_jobs=1),
        paths=SimpleNamespace(workspaces_dir=tmp_path / "ws", data_dir=tmp_path / "data"),
    )
    runner = CodingRunner(SimpleNamespace(settings=settings))  # type: ignore[arg-type]
    running, paused, done = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    runner._procs[running] = SimpleNamespace(pid=1, returncode=None)  # type: ignore[assignment]
    runner._procs[paused] = SimpleNamespace(pid=2, returncode=None)  # type: ignore[assignment]
    runner._procs[done] = SimpleNamespace(pid=3, returncode=0)  # type: ignore[assignment]
    runner._paused[paused] = 0.0
    job_task = asyncio.create_task(asyncio.sleep(30))
    runner._tasks[running] = job_task
    await runner.shutdown()
    assert signals == [(1, signal.SIGTERM), (2, signal.SIGCONT), (2, signal.SIGTERM)]
    await asyncio.sleep(0)
    assert job_task.cancelled()
