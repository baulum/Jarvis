"""Claude-Code-Prozesse im Chat: Absturz → Exit-Code/stderr + ein automatischer zweiter Versuch, Stopp/Barge-in →
"Abgebrochen" statt Fehler, keine sterbenden Prozesse aus dem Pool, keine verwaisten Abbruch-Wünsche.

Die Agent-Tests starten echte Prozesse: ein kleines Python-Skript spielt die Claude-Code-CLI (stream-json)."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.agent.claude_agent import ClaudeCodeAgent, exit_detail
from jarvis.agent.llm import AgentEvent
from jarvis.agent.orchestrator import Orchestrator
from jarvis.security.auth import Principal

OWNER = Principal(id="desk", role="owner", channel="device", device_id=uuid.uuid4())

# Behaviour per spawned process (in spawn order, the last one repeats), set in the file "modes".
FAKE_CLI = r"""
import json, os, sys, time
from pathlib import Path

state = Path(os.environ["FAKE_CLAUDE_DIR"])
modes = (state / "modes").read_text().split(",")
counter = state / "count"
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
mode = modes[min(n, len(modes) - 1)]
argv = sys.argv[1:]
resume = argv[argv.index("--resume") + 1] if "--resume" in argv else None
with (state / "spawns.jsonl").open("a") as f:
    f.write(json.dumps({"mode": mode, "resume": resume}) + "\n")


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def err(text):
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


if mode == "noresume" and resume:
    err(f"No conversation found with session ID: {resume}")
    sys.exit(1)
sid = resume or f"sess-{n}"
while True:
    line = sys.stdin.readline()
    if not line:
        break
    msg = json.loads(line)
    if msg.get("type") != "user":
        continue
    with (state / "prompts.jsonl").open("a") as f:
        f.write(json.dumps(msg["message"]["content"]) + "\n")
    out({"type": "system", "subtype": "init", "session_id": sid})
    if mode == "crash":
        err("TypeError: boom")
        sys.exit(3)
    if mode == "quota":
        err("Claude usage limit reached, resets 5pm")
        sys.exit(1)
    if mode == "tool_then_crash":
        tool = {"type": "tool_use", "id": "t1", "name": "mcp__jarvis__coding_retry", "input": {}}
        out({"type": "assistant", "message": {"content": [tool]}})
        out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}})
        sys.exit(2)
    if mode == "hang":
        time.sleep(60)
    if mode == "slow":
        time.sleep(0.5)
    delta = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Erledigt"}}
    out({"type": "stream_event", "event": delta})
    out({"type": "result", "session_id": sid, "result": "Erledigt, Sir.", "usage": {}})
"""


class Fake:
    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def spawns(self) -> list[dict[str, Any]]:
        path = self.root / "spawns.jsonl"
        return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []

    @property
    def prompts(self) -> list[Any]:
        path = self.root / "prompts.jsonl"
        return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


@pytest.fixture
async def make_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    agents: list[ClaudeCodeAgent] = []

    def make(*modes: str) -> tuple[ClaudeCodeAgent, Fake]:
        script = tmp_path / "fake-claude"
        script.write_text(f"#!{sys.executable}\n{FAKE_CLI}", encoding="utf-8")
        script.chmod(0o755)
        (tmp_path / "modes").write_text(",".join(modes), encoding="utf-8")
        monkeypatch.setenv("FAKE_CLAUDE_DIR", str(tmp_path))
        state = SimpleNamespace(
            settings=SimpleNamespace(
                agent=SimpleNamespace(
                    claude_binary=str(script), model="sonnet", idle_timeout_minutes=15, turn_timeout_s=30
                ),
                paths=SimpleNamespace(data_dir=tmp_path),
                server=SimpleNamespace(port=1),
            ),
            skills=SimpleNamespace(plugin_dir=tmp_path),
            usage=SimpleNamespace(on_result=lambda *a: None, on_rate_limit=lambda *a: None),
            model_router=None,
        )
        agent = ClaudeCodeAgent(state)  # type: ignore[arg-type]
        agent.home.mkdir(parents=True, exist_ok=True)
        agent.system_prompt = lambda: "Test"  # type: ignore[method-assign]

        async def no_standby() -> None:  # keeps the spawn order of the fake deterministic
            return None

        agent._ensure_standby = no_standby  # type: ignore[method-assign]
        agents.append(agent)
        return agent, Fake(tmp_path)

    yield make
    for agent in agents:
        await agent.stop()


async def _turn(agent: ClaudeCodeAgent, sid: uuid.UUID, resume: str | None = None) -> list[AgentEvent]:
    return [ev async for ev in agent.chat_stream(sid, resume, "Starte den Job neu", "")]


def _types(events: list[AgentEvent]) -> list[str]:
    return [e.type for e in events if e.type not in ("delta", "assistant_completed")]


# ---------------------------------------------------------------------------------------------- agent


async def test_crash_shows_exit_code_and_stderr_and_is_retried_once(make_agent: Any) -> None:
    agent, fake = make_agent("crash", "ok")
    events = await _turn(agent, uuid.uuid4())
    assert _types(events) == ["run_started", "retry", "run_started", "completed"]
    retry = next(e for e in events if e.type == "retry")
    assert "Exit-Code 3" in retry.text and "TypeError: boom" in retry.text
    assert [s["resume"] for s in fake.spawns] == [None, "sess-0"]  # the retry continues the conversation


async def test_second_crash_fails_with_details_and_no_third_try(make_agent: Any) -> None:
    agent, fake = make_agent("crash")
    events = await _turn(agent, uuid.uuid4())
    assert _types(events)[-1] == "failed"
    assert "Exit-Code 3" in events[-1].text and "boom" in events[-1].text
    assert events[-1].text != "Claude Code wurde beendet"
    assert len(fake.spawns) == 2
    assert agent.last_error and "Exit-Code 3" in agent.last_error


async def test_failed_resume_is_retried_as_fresh_conversation(make_agent: Any) -> None:
    agent, fake = make_agent("noresume", "ok")
    events = await _turn(agent, uuid.uuid4(), resume="weg")
    assert _types(events) == ["retry", "run_started", "completed"]
    assert "No conversation found" in next(e for e in events if e.type == "retry").text
    assert [s["resume"] for s in fake.spawns] == ["weg", None]


async def test_retry_after_tools_tells_claude_not_to_repeat_them(make_agent: Any) -> None:
    agent, fake = make_agent("tool_then_crash", "ok")
    events = await _turn(agent, uuid.uuid4())
    assert _types(events)[-1] == "completed"
    first, second = fake.prompts
    assert first == "Starte den Job neu"
    assert "wiederhole keine Aktion" in second and "coding_retry" in second and second.endswith("Starte den Job neu")


async def test_quota_error_is_not_retried(make_agent: Any) -> None:
    agent, fake = make_agent("quota", "ok")
    events = await _turn(agent, uuid.uuid4())
    assert _types(events)[-1] == "failed" and "Kontingent" in events[-1].text
    assert len(fake.spawns) == 1


async def test_interrupt_ends_turn_as_cancelled_without_retry(make_agent: Any) -> None:
    agent, fake = make_agent("hang", "ok")
    sid = uuid.uuid4()
    events: list[AgentEvent] = []
    started = asyncio.Event()

    async def run() -> None:
        async for ev in agent.chat_stream(sid, None, "Erzähl was Langes", ""):
            events.append(ev)
            if ev.type == "run_started":
                started.set()

    task = asyncio.create_task(run())
    await asyncio.wait_for(started.wait(), 10)
    await agent.interrupt(sid)  # Stopp button / voice barge-in
    await asyncio.wait_for(task, 10)
    assert _types(events) == ["run_started", "cancelled"]
    assert len(fake.spawns) == 1


async def test_shutdown_mid_turn_is_reported_as_such(make_agent: Any) -> None:
    agent, fake = make_agent("hang")
    sid = uuid.uuid4()
    events: list[AgentEvent] = []
    started = asyncio.Event()

    async def run() -> None:
        async for ev in agent.chat_stream(sid, None, "Hallo", ""):
            events.append(ev)
            if ev.type == "run_started":
                started.set()

    task = asyncio.create_task(run())
    await asyncio.wait_for(started.wait(), 10)
    await agent.stop()
    await asyncio.wait_for(task, 10)
    assert _types(events)[-1] == "failed" and "neu gestartet" in events[-1].text
    assert len(fake.spawns) == 1


async def test_process_marked_for_termination_is_never_reused(make_agent: Any) -> None:
    """Reaper/reload used to kill first and unlist afterwards: a turn in between got the dying process."""
    agent, fake = make_agent("ok")
    sid = uuid.uuid4()
    assert _types(await _turn(agent, sid))[-1] == "completed"
    p = agent._procs[sid]
    p.stop_reason = "idle"  # being stopped, not yet taken out of the pool
    assert _types(await _turn(agent, sid, resume=p.claude_session_id))[-1] == "completed"
    assert agent._procs[sid] is not p
    assert [s["resume"] for s in fake.spawns] == [None, "sess-0"]


async def test_follow_up_turn_reuses_the_process(make_agent: Any) -> None:
    agent, fake = make_agent("ok")
    sid = uuid.uuid4()
    await _turn(agent, sid)
    await _turn(agent, sid)
    assert len(fake.spawns) == 1 and not agent._procs[sid].busy


def test_exit_detail() -> None:
    assert exit_detail(3, ["a", "b"]) == "Claude Code wurde unerwartet beendet (Exit-Code 3): a | b"
    assert exit_detail(-9, []) == "Claude Code wurde unerwartet beendet (Signal SIGKILL)"
    assert "Exit-Code unbekannt" in exit_detail(None, [])


# ---------------------------------------------------------------------------------------------- orchestrator


class ScriptedAgent:
    last_error = None

    def __init__(self, events: list[AgentEvent], orch: Orchestrator | None = None) -> None:
        self.events = events
        self.orch = orch
        self.calls = 0
        self.interrupted: list[uuid.UUID] = []

    def available(self) -> bool:
        return True

    async def interrupt(self, session_id: uuid.UUID) -> None:
        self.interrupted.append(session_id)

    async def chat_stream(self, *a: Any, **k: Any) -> AsyncIterator[AgentEvent]:
        self.calls += 1
        for ev in self.events:
            if ev.type == "_stop" and self.orch is not None:
                await self.orch.interrupt()  # Sir presses Stopp mid-turn
                continue
            yield ev


def _orch(agent: ScriptedAgent) -> Orchestrator:
    state = SimpleNamespace(
        agent=agent,
        hermes=None,
        personality=SimpleNamespace(error_phrases=["Das hat leider nicht funktioniert, Sir."]),
    )
    orch = Orchestrator(state)  # type: ignore[arg-type]
    agent.orch = orch
    return orch


async def _run(orch: Orchestrator, sess: Any) -> Any:
    emitted: list[AgentEvent] = []

    async def emit(ev: AgentEvent) -> None:
        emitted.append(ev)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("jarvis.agent.orchestrator.bus.publish", _no_publish)
        return await orch._run_claude(sess, OWNER, "Hallo", "", emit, "sonnet"), emitted


async def _no_publish(*a: Any, **k: Any) -> None:
    return None


def _sess() -> Any:
    return SimpleNamespace(id=uuid.uuid4(), channel="desktop", hermes_session_id=None)


async def test_cancelled_turn_is_not_an_error() -> None:
    orch = _orch(ScriptedAgent([AgentEvent("delta", "Zuerst"), AgentEvent("cancelled")]))
    res, emitted = await _run(orch, _sess())
    assert res.error == "cancelled" and res.text == "Zuerst"
    assert not any("nicht funktioniert" in e.text for e in emitted)


async def test_failure_after_stopp_counts_as_cancelled() -> None:
    agent = ScriptedAgent(
        [AgentEvent("delta", "Teil"), AgentEvent("_stop"), AgentEvent("failed", "Claude Code wurde beendet")]
    )
    orch = _orch(agent)
    sess = _sess()
    res, emitted = await _run(orch, sess)
    assert res.error == "cancelled" and agent.interrupted == [sess.id]
    assert not any("nicht funktioniert" in e.text for e in emitted)


async def test_retry_discards_text_of_the_failed_attempt() -> None:
    orch = _orch(
        ScriptedAgent(
            [
                AgentEvent("delta", "Halb"),
                AgentEvent("retry", "Claude Code wurde unerwartet beendet (Exit-Code 1)"),
                AgentEvent("delta", "Fertig, Sir."),
                AgentEvent("completed", data={}),
            ]
        )
    )
    res, emitted = await _run(orch, _sess())
    assert res.error is None and res.text == "Fertig, Sir."
    assert [e.type for e in emitted] == ["delta", "retry", "delta"]


async def test_crash_message_reaches_sir_with_details() -> None:
    detail = "Claude Code wurde unerwartet beendet (Exit-Code 3): TypeError: boom"
    orch = _orch(ScriptedAgent([AgentEvent("failed", detail)]))
    res, _ = await _run(orch, _sess())
    assert res.error == detail and "Exit-Code 3" in res.text and "boom" in res.text


async def test_late_barge_in_does_not_cancel_the_next_turn() -> None:
    """Barge-in while the finished answer is still being spoken: no turn runs or waits for that session."""
    agent = ScriptedAgent([AgentEvent("delta", "Antwort"), AgentEvent("completed", data={})])
    orch = _orch(agent)
    sess = _sess()
    assert await orch.interrupt(sess.id) is False
    res, _ = await _run(orch, sess)
    assert res.error is None and res.text == "Antwort" and agent.calls == 1


async def test_interrupt_of_a_waiting_turn_skips_it() -> None:
    agent = ScriptedAgent([AgentEvent("delta", "Antwort"), AgentEvent("completed", data={})])
    orch = _orch(agent)
    sess = _sess()
    await orch._run_lock.acquire()  # another channel's turn is running
    task = asyncio.create_task(_run(orch, sess))
    await asyncio.sleep(0.05)
    assert await orch.interrupt(sess.id) is False
    orch._run_lock.release()
    res, _ = await asyncio.wait_for(task, 5)
    assert res.error == "cancelled" and agent.calls == 0
    assert not orch._cancel_pending and not orch._waiting


# ---------------------------------------------------------------------------------------------- orchestrator + agent


def _real_orch(agent: ClaudeCodeAgent, monkeypatch: pytest.MonkeyPatch) -> Orchestrator:
    """Orchestrator over the real agent (fake CLI processes); the agent session id is kept in memory, not the DB."""
    monkeypatch.setattr("jarvis.agent.orchestrator.bus.publish", _no_publish)
    state = SimpleNamespace(
        agent=agent, hermes=None, personality=SimpleNamespace(error_phrases=["Das hat leider nicht funktioniert, Sir."])
    )
    orch = Orchestrator(state)  # type: ignore[arg-type]

    async def remember(sess: Any, agent_session_id: str | None) -> None:
        if agent_session_id:
            sess.hermes_session_id = agent_session_id

    orch._remember_agent_session = remember  # type: ignore[method-assign]
    return orch


async def _turn_via(orch: Orchestrator, sess: Any, text: str = "Hallo") -> tuple[Any, list[AgentEvent]]:
    emitted: list[AgentEvent] = []

    async def emit(ev: AgentEvent) -> None:
        emitted.append(ev)

    return await orch._run_claude(sess, OWNER, text, "", emit, "sonnet"), emitted


async def _until_running(orch: Orchestrator) -> None:
    for _ in range(500):
        if orch.active_run is not None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the turn never started")


async def test_text_input_during_a_running_answer_waits_and_keeps_the_process(
    make_agent: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sir writes in the chat while Jarvis is still answering: the new turn queues up, nothing is stopped."""
    agent, fake = make_agent("slow")
    orch = _real_orch(agent, monkeypatch)
    sess = _sess()
    first = asyncio.create_task(_turn_via(orch, sess))
    await _until_running(orch)
    second = asyncio.create_task(_turn_via(orch, sess, "Und noch etwas"))
    await asyncio.sleep(0.05)
    assert sess.id in orch._waiting  # queued behind the running answer
    (r1, e1), (r2, e2) = await asyncio.wait_for(asyncio.gather(first, second), 20)
    assert r1.error is None and r2.error is None and r1.text == r2.text == "Erledigt, Sir."
    assert len(fake.spawns) == 1  # both turns on the same process: it was never ended
    p = agent._procs[sess.id]
    assert p.alive and p.stop_reason is None and not p.busy
    assert not any("beendet" in ev.text for ev in [*e1, *e2])


async def test_stopp_during_an_answer_cancels_cleanly_and_the_session_goes_on(
    make_agent: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real interruption (Stopp button, Sir talking over Jarvis): "Abgebrochen", no error message, and the next
    turn continues the same Claude Code conversation."""
    agent, fake = make_agent("hang", "ok")
    orch = _real_orch(agent, monkeypatch)
    sess = _sess()
    first = asyncio.create_task(_turn_via(orch, sess))
    await _until_running(orch)
    for _ in range(500):  # the CLI has started the turn
        if sess.hermes_session_id:
            break
        await asyncio.sleep(0.01)
    assert await orch.interrupt(sess.id) is True
    res, emitted = await asyncio.wait_for(first, 10)
    assert res.error == "cancelled" and res.text == "Abgebrochen, Sir."
    assert not any("nicht funktioniert" in ev.text or "beendet" in ev.text for ev in emitted)

    res, _ = await _turn_via(orch, sess)
    assert res.error is None and res.text == "Erledigt, Sir."
    assert [s["resume"] for s in fake.spawns] == [None, "sess-0"]  # same conversation, continued via --resume


async def test_real_process_end_still_reaches_sir(make_agent: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    agent, fake = make_agent("crash")
    orch = _real_orch(agent, monkeypatch)
    res, emitted = await _turn_via(orch, _sess())
    assert res.error and "Claude Code wurde unerwartet beendet (Exit-Code 3)" in res.error
    shown = [ev.text for ev in emitted if ev.type == "delta"]
    assert any("nicht funktioniert" in t and "unerwartet beendet" in t for t in shown)
    assert len(fake.spawns) == 2  # tried once more before giving up
