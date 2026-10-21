"""Agent monitor: log -> snapshot, pause/resume/message on the coding runner, slash-command list, mobile allowlist."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import jarvis.coding.runner as runner_mod
from jarvis.coding.monitor import build_monitor, compact_events, result_summary, tool_detail
from jarvis.coding.runner import CodingError, CodingRunner
from jarvis.commands import COMMANDS, command_list
from jarvis.mobile.gateway import mobile_path_allowed

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def at(sec: int) -> str:
    return datetime(2026, 1, 1, 12, 0, sec, tzinfo=UTC).isoformat(timespec="seconds")


def _job(status: str = "running", **kw: Any) -> dict[str, Any]:
    return {"id": "j1", "title": "T", "status": status, "created_at": at(0), "finished_at": None, "result": {}, **kw}


def _tool(i: str, name: str, detail: str, sec: int, usage: bool = True) -> dict[str, Any]:
    e: dict[str, Any] = {"type": "assistant", "tool": name, "tool_id": i, "detail": detail, "ts": at(sec)}
    if usage:
        e |= {"msg_id": f"m-{i}", "usage": {"input": 10, "output": 5, "cache_read": 100, "cache_creation": 0}}
    return e


def _res(i: str, sec: int, text: str = "ok", err: bool = False) -> dict[str, Any]:
    return {"type": "tool_result", "tool_id": i, "result": text, "is_error": err, "ts": at(sec)}


def test_compact_events_splits_blocks_and_keeps_usage_once() -> None:
    ev = {
        "type": "assistant",
        "message": {
            "id": "m1",
            "usage": {"input_tokens": 3, "output_tokens": 7, "cache_read_input_tokens": 11},
            "content": [
                {"type": "text", "text": "Ich lese die Datei."},
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/a/b.py"}},
                {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "ls -la\nfoo"}},
            ],
        },
    }
    out = compact_events(ev, T0)
    assert [(e.get("tool"), e.get("text")) for e in out] == [(None, "Ich lese die Datei."), ("Read", None), ("Bash", None)]
    assert out[0]["usage"] == {"input": 3, "output": 7, "cache_read": 11, "cache_creation": 0}
    assert all("usage" not in e for e in out[1:])
    assert out[1]["detail"] == "/a/b.py" and out[2]["detail"] == "ls -la" and out[2]["tool_id"] == "t2"


def test_compact_events_tool_result_and_final_result() -> None:
    user = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "a\n  b"}], "is_error": True}]}}
    (r,) = compact_events(user, T0)
    assert r == {"type": "tool_result", "ts": at(0), "tool_id": "t1", "result": "a b", "is_error": True}
    (fin,) = compact_events({"type": "result", "result": "fertig", "total_cost_usd": 0.42, "num_turns": 3}, T0)
    assert fin["cost_usd"] == 0.42 and fin["text"] == "fertig"
    assert tool_detail("X", {"url": "https://x.y"}) == "https://x.y" and tool_detail("X", {}) == ""
    assert len(result_summary("x" * 1000)) == 300


def test_build_monitor_running_tool_steps_tokens_and_runtime() -> None:
    entries = [
        _tool("a", "Read", "src/x.py", 1),
        _res("a", 2, "1234 Zeilen"),
        _tool("b", "Bash", "pytest -q", 3),
        {"type": "assistant", "text": "Teste jetzt", "ts": at(3)},
    ]
    m = build_monitor(_job(), entries, now=datetime(2026, 1, 1, 12, 0, 10, tzinfo=UTC))
    assert m["current"]["kind"] == "tool" and m["current"]["tool"] == "Bash" and "pytest" in m["current"]["label"]
    assert [s["status"] for s in m["steps"]] == ["ok", "running"]
    assert m["steps"][0]["result"] == "1234 Zeilen" and m["steps"][0]["duration_s"] == 1.0
    assert [s["tool"] for s in m["running_tools"]] == ["Bash"]
    assert m["usage"]["input_tokens"] == 20 and m["usage"]["cache_read_tokens"] == 200 and m["usage"]["total_tokens"] == 230
    assert m["usage"]["cost_usd"] is None
    assert m["elapsed_s"] == 9.0 and m["last_text"] == "Teste jetzt"
    assert m["can_pause"] and not m["can_resume"] and m["can_cancel"] and m["can_message"]


def test_build_monitor_dedupes_usage_of_streamed_message() -> None:
    a = {**_tool("a", "Read", "x", 1), "msg_id": "same"}
    b = {**_tool("b", "Read", "y", 1), "msg_id": "same"}
    m = build_monitor(_job(), [a, b], now=T0)
    assert m["usage"]["input_tokens"] == 10


def test_build_monitor_pause_excluded_from_runtime_and_state() -> None:
    entries = [
        _tool("a", "Bash", "sleep 1", 0),
        {"type": "control", "action": "paused", "ts": at(10)},
        {"type": "control", "action": "resumed", "ts": at(30)},
        {"type": "control", "action": "paused", "ts": at(40)},
    ]
    m = build_monitor(_job("paused"), entries, now=datetime(2026, 1, 1, 12, 0, 50, tzinfo=UTC))
    assert m["elapsed_s"] == 20.0  # 50 s wall clock - 20 s paused - 10 s currently paused
    assert m["current"]["kind"] == "paused" and m["steps"][0]["status"] == "paused"
    assert m["can_resume"] and not m["can_pause"]


def test_build_monitor_finished_job_closes_open_steps_and_reports_cost() -> None:
    entries = [_tool("a", "Bash", "x", 1), {"type": "result", "cost_usd": 0.1234, "ts": at(5)}, {"type": "result", "cost_usd": 0.1, "ts": at(9)}]
    job = _job("failed", finished_at=at(20), result={"error": "kaputt"})
    m = build_monitor(job, entries, now=datetime(2026, 1, 1, 13, 0, 0, tzinfo=UTC))
    assert m["steps"][0]["status"] == "aborted" and m["current"]["kind"] == "done"
    assert m["usage"]["cost_usd"] == 0.2234 and m["usage"]["cost_final"] is True
    assert m["elapsed_s"] == 19.0 and m["error"] == "kaputt"
    assert not (m["can_pause"] or m["can_cancel"] or m["can_message"])


def test_build_monitor_messages_and_thinking() -> None:
    entries = [_tool("a", "Read", "x", 1), _res("a", 2), {"type": "user_message", "text": "Bitte Tests ergänzen", "ts": at(3), "immediate": False}]
    m = build_monitor(_job(), entries, now=datetime(2026, 1, 1, 12, 0, 5, tzinfo=UTC), pending_messages=1)
    assert m["current"]["kind"] == "thinking" and m["pending_messages"] == 1
    assert m["messages"][0]["text"] == "Bitte Tests ergänzen"


# ------------------------------------------------------------------ runner control
class FakeSession:
    def __init__(self, store: dict[uuid.UUID, Any]) -> None:
        self.store = store

    async def get(self, model: Any, key: uuid.UUID) -> Any:
        return self.store.get(key)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    store: dict[uuid.UUID, Any] = {}
    events: list[tuple[str, dict[str, Any]]] = []
    signals: list[tuple[int, int]] = []

    @asynccontextmanager
    async def scope():  # type: ignore[no-untyped-def]
        yield FakeSession(store)

    async def audit(*a: Any, **k: Any) -> None:
        return None

    async def publish(type_: str, **data: Any) -> None:
        events.append((type_, data))

    monkeypatch.setattr(runner_mod, "session_scope", scope)
    monkeypatch.setattr(runner_mod, "audit", audit)
    monkeypatch.setattr(runner_mod.bus, "publish", publish)
    monkeypatch.setattr(os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    settings = SimpleNamespace(
        coding=SimpleNamespace(max_parallel_jobs=1),
        paths=SimpleNamespace(workspaces_dir=tmp_path / "ws", data_dir=tmp_path / "data"),
    )
    runner = CodingRunner(SimpleNamespace(settings=settings))  # type: ignore[arg-type]
    jid = uuid.uuid4()
    job = SimpleNamespace(id=jid, status="running", title="T", model=None, effort=None, branch="b", result={}, created_at=T0, finished_at=None, task_id=None)
    store[jid] = job
    runner._procs[jid] = SimpleNamespace(pid=4242, returncode=None)  # type: ignore[assignment]
    return SimpleNamespace(runner=runner, job=job, jid=jid, events=events, signals=signals)


async def test_pause_and_resume_signal_process_group_and_log(env: SimpleNamespace) -> None:
    r = env.runner
    await r.pause(env.jid, "sir")
    assert env.job.status == "paused" and (4242, signal.SIGSTOP) in env.signals and env.jid in r._paused
    with pytest.raises(CodingError):
        await r.pause(env.jid, "sir")  # not running any more
    await r.resume(env.jid, "sir")
    assert env.job.status == "running" and (4242, signal.SIGCONT) in env.signals
    actions = [json.loads(line)["action"] for line in r._log_path(env.jid).read_text().splitlines()]
    assert actions == ["paused", "resumed"]
    with pytest.raises(CodingError):
        await r.resume(env.jid, "sir")


async def test_cancel_of_paused_job_continues_before_terminating(env: SimpleNamespace) -> None:
    await env.runner.pause(env.jid, "sir")
    env.signals.clear()
    await env.runner.cancel(env.jid, "sir")
    assert [s for _, s in env.signals] == [signal.SIGCONT, signal.SIGTERM]
    assert env.job.status == "cancelled"


async def test_cancel_reports_finished_so_waiting_steps_wake(env: SimpleNamespace) -> None:
    tid = uuid.uuid4()
    env.job.task_id = tid
    await env.runner.cancel(env.jid, "sir")
    finished = [d for t, d in env.events if t == "coding.finished"]
    assert finished == [
        {"job_id": str(env.jid), "status": "cancelled", "title": "T", "summary": "Abgebrochen", "task_id": str(tid)}
    ]
    assert await env.runner.cancel(env.jid, "sir") is False  # already cancelled: no second event
    assert len([t for t, _ in env.events if t == "coding.finished"]) == 1


async def test_pause_stops_the_time_budget_and_resume_restores_it(env: SimpleNamespace) -> None:
    r = env.runner
    loop = asyncio.get_running_loop()
    async with asyncio.timeout(600) as budget:
        r._timeouts[env.jid] = budget
        left_before = budget.when() - loop.time()  # type: ignore[operator]
        await r.pause(env.jid, "sir")
        assert budget.when() is None  # cannot fire while the process is stopped
        await asyncio.sleep(0.05)
        await r.resume(env.jid, "sir")
        left_after = budget.when() - loop.time()  # type: ignore[operator]
        assert left_before - 1 < left_after <= left_before  # the pause did not count
    assert env.jid not in r._budget_left


async def test_send_message_queues_and_interrupts_on_request(env: SimpleNamespace) -> None:
    r = env.runner
    res = await r.send_message(env.jid, "  Nimm Port 8080  ", "sir")
    assert res == {"queued": True, "interrupted": False, "pending": 1} and not env.signals
    res = await r.send_message(env.jid, "Sofort bitte", "sir", immediate=True)
    assert res["interrupted"] and env.signals == [(4242, signal.SIGTERM)] and env.jid in r._interrupted
    assert r._inbox[env.jid] == ["Nimm Port 8080", "Sofort bitte"]
    with pytest.raises(CodingError):
        await r.send_message(env.jid, "   ", "sir")
    r._closing.add(env.jid)
    with pytest.raises(CodingError, match="nimmt keine Nachrichten"):
        await r.send_message(env.jid, "zu spät", "sir")


async def test_drain_inbox_resumes_session_with_message(env: SimpleNamespace, tmp_path: Path) -> None:
    r, calls = env.runner, []

    async def fake(job_id: uuid.UUID, cwd: Path, prompt: str, log_path: Path, resume_session: str | None = None, **kw: Any):  # type: ignore[no-untyped-def]
        calls.append((prompt, resume_session))
        return {"is_error": False, "result": "ok", "session_id": "s2"}

    r._claude_retrying = fake  # type: ignore[method-assign]
    r._inbox[env.jid] = ["A", "B"]
    res = await r._drain_inbox(env.jid, tmp_path, tmp_path / "l", {"session_id": "s1"}, model=None, effort=None)
    assert res["session_id"] == "s2" and len(calls) == 1 and calls[0][1] == "s1"
    assert "A" in calls[0][0] and "B" in calls[0][0] and not r._inbox.get(env.jid)


async def test_monitor_reads_log_of_runner(env: SimpleNamespace) -> None:
    r = env.runner
    r.logs_dir.mkdir(parents=True)
    r._log_path(env.jid).write_text(json.dumps(_tool("a", "Read", "x.py", 1)) + "\n" + json.dumps(_res("a", 2)) + "\n")
    m = await r.monitor(env.jid)
    assert m["step_count"] == 1 and m["steps"][0]["status"] == "ok" and m["job_id"] == str(env.jid)


# ------------------------------------------------------------------ commands / gateway
def test_command_list_is_complete_and_consistent() -> None:
    data = command_list()
    names = [c["name"] for c in data["commands"]]
    assert len(names) == len(set(names)) and {"neu", "stop", "modell", "agenten", "notaus"} <= set(names)
    for c in data["commands"]:
        assert c["description"] and c["usage"].startswith("/") and c["action"] in {"prompt", "model", "new", "stop", "navigate", "killswitch"}
        if c["action"] == "prompt":
            assert c["prompt"]
        if c["action"] == "navigate":
            assert c["target"]
    assert next(c for c in data["commands"] if c["name"] == "notaus")["confirm"] is True
    assert len(COMMANDS) == len(names)


@pytest.mark.parametrize(
    ("method", "path", "allowed"),
    [
        ("GET", "/api/commands", True),
        ("GET", f"/api/coding/jobs/{uuid.uuid4()}/monitor", True),
        ("POST", f"/api/coding/jobs/{uuid.uuid4()}/pause", True),
        ("POST", f"/api/coding/jobs/{uuid.uuid4()}/resume", True),
        ("POST", f"/api/coding/jobs/{uuid.uuid4()}/cancel", True),
        ("POST", f"/api/coding/jobs/{uuid.uuid4()}/message", True),
        ("POST", f"/api/coding/jobs/{uuid.uuid4()}/retry", False),
        ("POST", f"/api/tasks/{uuid.uuid4()}/pause", True),
        ("POST", f"/api/tasks/{uuid.uuid4()}/message", True),
        ("DELETE", f"/api/coding/jobs/{uuid.uuid4()}", False),
    ],
)
def test_mobile_gateway_allowlist(method: str, path: str, allowed: bool) -> None:
    assert mobile_path_allowed(method, path) is allowed
