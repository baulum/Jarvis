"""Coding runner: claude command/env resolution, model aliases, argv and automatic continuation."""

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import jarvis.coding.runner as runner_mod
from jarvis.agent.model_router import MODELS, model_id, resolve_alias
from jarvis.coding.runner import (
    CodingError,
    CodingRunner,
    TransientCodingError,
    is_transient_error,
    resolve_command,
)


def _exe(directory: Path, name: str) -> Path:
    p = directory / name
    p.write_text("#!/bin/sh\n")
    p.chmod(0o755)
    return p


def _runner(tmp_path: Path, **cfg: Any) -> CodingRunner:
    base = {
        "claude_command": "claudeb",
        "claude_aliases": {"claudeb": ["claude"]},
        "claude_env": {},
        "default_model": "",
        "max_parallel_jobs": 1,
        "job_timeout_minutes": 5,
        "allowed_tools": ["Read", "Bash(git log:*)"],
        "extra_read_dirs": [],
        "extra_write_dirs": [],
        "sandbox": False,
        "restricted": True,
    }
    settings = SimpleNamespace(
        coding=SimpleNamespace(**{**base, **cfg}),
        paths=SimpleNamespace(workspaces_dir=tmp_path / "ws", data_dir=tmp_path / "data"),
    )
    state = SimpleNamespace(
        settings=settings,
        skills=SimpleNamespace(plugin_dir=tmp_path / "plugin"),
        model_router=SimpleNamespace(default="opus", effort="high"),
        kill_switch=SimpleNamespace(engaged=False),
    )
    return CodingRunner(state)  # type: ignore[arg-type]


# ------------------------------------------------------------------ command / env
def test_real_program_wins_over_alias(tmp_path: Path) -> None:
    b = _exe(tmp_path, "claudeb")
    _exe(tmp_path, "claude")
    assert resolve_command("claudeb", {"claudeb": ["claude"]}, path=str(tmp_path)) == [str(b)]


def test_alias_expands_to_target_with_args(tmp_path: Path) -> None:
    c = _exe(tmp_path, "claude")
    assert resolve_command("claudeb", {"claudeb": ["claude", "--x"]}, path=str(tmp_path)) == [str(c), "--x"]


def test_missing_command_has_no_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _exe(tmp_path, "claude")
    monkeypatch.setattr(runner_mod, "_CLI_DIRS", ())
    assert resolve_command("claudeb", {}, path=str(tmp_path)) is None  # plain "claude" must not be used
    assert resolve_command("", {}, path=str(tmp_path)) is None


def test_missing_command_gives_clear_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_mod, "_CLI_DIRS", ())
    monkeypatch.setenv("PATH", str(tmp_path))
    r = _runner(tmp_path, claude_command="claudeb-gibts-nicht", claude_aliases={})
    with pytest.raises(CodingError, match="claudeb-gibts-nicht nicht gefunden"):
        r._claude_argv("p", None)


async def test_auth_status_reports_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_mod, "_CLI_DIRS", ())
    monkeypatch.setenv("PATH", str(tmp_path))
    st = await _runner(tmp_path, claude_aliases={}).auth_status()
    assert st["ok"] is False and "claudeb nicht gefunden" in st["error"]


def test_env_applies_claude_env_and_refuses_api_keys(tmp_path: Path) -> None:
    r = _runner(tmp_path, claude_env={"CLAUDE_CONFIG_DIR": "~/.claude-b"})
    env = r._env()
    assert env["CLAUDE_CONFIG_DIR"] == str(Path("~/.claude-b").expanduser())
    bad = _runner(tmp_path, claude_env={"ANTHROPIC_API_KEY": "sk-x"})
    with pytest.raises(CodingError, match="ANTHROPIC_API_KEY"):
        bad._env()


# ------------------------------------------------------------------ models
def test_alias_resolution_uses_shared_table() -> None:
    assert resolve_alias("Opus") == "opus"
    assert resolve_alias(MODELS["haiku"]["id"]) == "haiku"
    assert resolve_alias("auto") is None and resolve_alias(None) is None and resolve_alias("gpt") is None
    assert model_id("sonnet") == "claude-sonnet-5-5"


def test_model_precedence(tmp_path: Path) -> None:
    assert _runner(tmp_path).resolve_model("haiku") == "claude-haiku-5-5"  # explicit
    assert _runner(tmp_path, default_model="sonnet").resolve_model(None) == "claude-sonnet-5-5"  # yaml default
    assert _runner(tmp_path, default_model="sonnet").resolve_model("auto") == "claude-sonnet-5-5"
    assert _runner(tmp_path).resolve_model(None) == "claude-opus-5-5"  # chat model
    with pytest.raises(CodingError, match="Unbekanntes Modell"):
        _runner(tmp_path).resolve_model("gpt-9")


def test_effort_resolution(tmp_path: Path) -> None:
    r = _runner(tmp_path)
    assert r.resolve_effort("max") == "max" and r.resolve_effort(None) == "high"
    with pytest.raises(CodingError, match="Effort"):
        r.resolve_effort("ultra")


# ------------------------------------------------------------------ argv
def test_argv_model_effort_dirs_and_rights(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = _exe(tmp_path, "claude")
    monkeypatch.setenv("PATH", str(tmp_path))
    dl = tmp_path / "Downloads"
    dl.mkdir()
    r = _runner(tmp_path, extra_read_dirs=[str(dl), str(tmp_path / "gibt-es-nicht")])
    argv = r._claude_argv("p", "sid-1", model="claude-opus-5-5", effort="xhigh")
    assert argv[0] == str(c) and argv[1:3] == ["-p", "p"]
    assert argv[argv.index("--model") + 1] == "claude-opus-5-5"
    assert argv[argv.index("--effort") + 1] == "xhigh"
    assert argv[argv.index("--resume") + 1] == "sid-1"
    assert argv.count("--add-dir") == 1 and argv[argv.index("--add-dir") + 1] == str(dl.resolve())
    deny = argv[argv.index("--disallowedTools") + 1 : argv.index("--disallowedTools") + 3]
    assert deny == [f"Edit(/{dl.resolve()}/**)", f"Write(/{dl.resolve()}/**)"]
    assert "Bash(git log:*)" in argv and "--restricted" in argv and "--continue" not in argv
    hint = argv[argv.index("--append-system-prompt") + 1]
    assert "einzeln" in hint and str(dl.resolve()) in hint


def test_argv_continue_and_unrestricted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _exe(tmp_path, "claude")
    monkeypatch.setenv("PATH", str(tmp_path))
    argv = _runner(tmp_path)._claude_argv("p", None, continue_session=True)
    assert "--continue" in argv and "--resume" not in argv and "--model" not in argv and "--add-dir" not in argv
    assert "--restricted" not in _runner(tmp_path, restricted=False)._claude_argv("p", None)


def test_bypass_flag_refused_with_restricted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _exe(tmp_path, "claude")
    monkeypatch.setenv("PATH", str(tmp_path))
    aliases = {"claudeb": ["claude", "--dangerously-skip-permissions"]}
    with pytest.raises(CodingError, match="--restricted"):
        _runner(tmp_path, claude_aliases=aliases)._claude_argv("p", None)
    argv = _runner(tmp_path, claude_aliases=aliases, restricted=False)._claude_argv("p", None)
    assert "--dangerously-skip-permissions" in argv and "--restricted" not in argv


def test_shipped_yaml_allows_read_commands_only() -> None:
    import yaml

    root = Path(__file__).resolve().parents[3]
    cfg = yaml.safe_load((root / "config" / "jarvis.yaml").read_text(encoding="utf-8"))["coding"]
    tools = set(cfg["allowed_tools"])
    for t in ("git log", "git show", "git worktree list", "head", "tail", "wc", "which", "git branch -a"):
        assert f"Bash({t}:*)" in tools, t
    assert "Bash(git branch)" in tools and "Bash(git branch:*)" not in tools  # no `git branch -D`
    assert "/Users/paul/Downloads" in cfg["extra_read_dirs"] and cfg["claude_command"] == "claudeb"
    assert "Bash(git push:*)" not in tools


# ------------------------------------------------------------------ transient errors / retry
@pytest.mark.parametrize(
    "text",
    [
        "API Error: Can't reach the API server (ENOTFOUND)",
        "read ECONNRESET",
        "connect ETIMEDOUT 1.2.3.4:443",
        "API Error: 503 Service Unavailable",
        "API Error (529): overloaded_error",
    ],
)
def test_transient_detection(text: str) -> None:
    assert is_transient_error(text)


@pytest.mark.parametrize("text", ["", "Tests failed: 500 lines changed", "API Error: 401 invalid auth", "SyntaxError"])
def test_non_transient(text: str) -> None:
    assert not is_transient_error(text)


@pytest.fixture
def retry_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    events: list[dict[str, Any]] = []

    async def publish(type_: str, **data: Any) -> None:
        events.append({"event": type_, **data})

    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    monkeypatch.setattr(runner_mod.bus, "publish", publish)
    monkeypatch.setattr(runner_mod.asyncio, "sleep", fake_sleep)
    r = _runner(tmp_path)
    log = tmp_path / "job.jsonl"
    return SimpleNamespace(runner=r, events=events, sleeps=sleeps, log=log)


def _script(r: CodingRunner, outcomes: list[Any], calls: list[dict[str, Any]]) -> None:
    async def fake(job_id: uuid.UUID, cwd: Path, prompt: str, log_path: Path, resume_session: str | None = None, **kw: Any):  # type: ignore[no-untyped-def]
        calls.append({"prompt": prompt, "resume": resume_session, **kw})
        r._sessions[job_id] = "sess-1"
        out = outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    r._claude = fake  # type: ignore[method-assign]


ENOTFOUND = {"is_error": True, "result": "API Error: Can't reach the API server (ENOTFOUND)", "session_id": "sess-1"}


async def test_retry_resumes_same_session_with_backoff(retry_env: SimpleNamespace) -> None:
    r, calls = retry_env.runner, []
    _script(r, [ENOTFOUND, TransientCodingError("ECONNRESET"), {"is_error": False, "result": "ok"}], calls)
    res = await r._claude_retrying(uuid.uuid4(), Path("."), "Auftrag", retry_env.log, model="m", effort="low")
    assert res["result"] == "ok" and res["_retries"] == 2
    assert retry_env.sleeps == [30, 90]
    assert [c["resume"] for c in calls] == [None, "sess-1", "sess-1"]
    assert calls[0]["prompt"] == "Auftrag" and calls[1]["prompt"] == runner_mod.RETRY_PROMPT
    assert all(c["model"] == "m" and c["effort"] == "low" for c in calls)
    assert any(e["event"] == "coding.progress" and e.get("type") == "retry" for e in retry_env.events)
    assert json.loads(retry_env.log.read_text().splitlines()[0])["type"] == "retry"


async def test_retry_gives_up_after_three_with_clear_message(retry_env: SimpleNamespace) -> None:
    r, calls = retry_env.runner, []
    _script(r, [ENOTFOUND] * 4, calls)
    res = await r._claude_retrying(uuid.uuid4(), Path("."), "p", retry_env.log)
    assert retry_env.sleeps == [30, 90, 180] and len(calls) == 4
    assert res["is_error"] and "nach 3 automatischen Wiederholungen" in res["errors"][0]
    assert "ENOTFOUND" in res["errors"][0]


async def test_exception_after_retries_is_codingerror(retry_env: SimpleNamespace) -> None:
    r, calls = retry_env.runner, []
    _script(r, [TransientCodingError("ETIMEDOUT")] * 4, calls)
    with pytest.raises(CodingError, match="nach 3 automatischen Wiederholungen"):
        await r._claude_retrying(uuid.uuid4(), Path("."), "p", retry_env.log)
    assert retry_env.sleeps == [30, 90, 180]


async def test_continue_when_no_session_known(retry_env: SimpleNamespace) -> None:
    r, calls = retry_env.runner, []

    async def fake(job_id: uuid.UUID, cwd: Path, prompt: str, log_path: Path, resume_session: str | None = None, **kw: Any):  # type: ignore[no-untyped-def]
        calls.append({"resume": resume_session, **kw})
        if len(calls) == 1:
            raise TransientCodingError("ENOTFOUND")
        return {"is_error": False}

    r._claude = fake  # type: ignore[method-assign]
    await r._claude_retrying(uuid.uuid4(), Path("."), "p", retry_env.log)
    assert [c["continue_session"] for c in calls] == [False, True]


async def test_permanent_errors_and_success_are_not_retried(retry_env: SimpleNamespace) -> None:
    r, calls = retry_env.runner, []
    _script(r, [{"is_error": True, "result": "API Error: 401 invalid auth"}], calls)
    res = await r._claude_retrying(uuid.uuid4(), Path("."), "p", retry_env.log)
    assert res["is_error"] and retry_env.sleeps == [] and len(calls) == 1
    _script(r, [CodingError("Zeitlimit")], calls)
    with pytest.raises(CodingError, match="Zeitlimit"):
        await r._claude_retrying(uuid.uuid4(), Path("."), "p", retry_env.log)
    assert retry_env.sleeps == []


async def test_kill_switch_stops_retrying(retry_env: SimpleNamespace) -> None:
    r, calls = retry_env.runner, []
    r.state.kill_switch.engaged = True  # type: ignore[attr-defined]
    _script(r, [ENOTFOUND, {"is_error": False}], calls)
    with pytest.raises(CodingError, match="Not-Aus"):
        await r._claude_retrying(uuid.uuid4(), Path("."), "p", retry_env.log)
