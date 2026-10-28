"""A coding job that delivered changes to Jarvis itself restarts Jarvis: first the delivery and the job result, then
(once no other coding job runs) the core and – only if it is running – the desktop app. Jobs on other projects never
restart anything, and restart problems never undo the delivery.

Real git like test_coding_ship (a bare "origin" whose path contains github.com/acme/jarvis, direct push to main);
the restart helper itself is replaced by a recorder.
"""

import asyncio
import os
import signal
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import jarvis.coding.runner as runner_mod
from jarvis.coding import self_restart
from jarvis.coding.runner import CodingRunner
from jarvis.coding.self_restart import Restarter, is_jarvis_repo, needs_restart, plan

BRANCH = "jarvis/job"


def _git(cwd: Path, *args: str) -> str:
    cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args]
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class FakeSession:
    def __init__(self, store: dict[uuid.UUID, Any]) -> None:
        self.store = store

    async def get(self, model: Any, key: uuid.UUID) -> Any:
        return self.store.get(key)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    store: dict[uuid.UUID, Any] = {}
    events: list[tuple[str, dict[str, Any]]] = []
    launches: list[dict[str, Any]] = []

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

    origin = tmp_path / "github.com" / "acme" / "jarvis.git"
    origin.mkdir(parents=True)
    _git(origin, "init", "-q", "--bare", "-b", "main")
    repo = tmp_path / "Jarvis"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("jarvis\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "-u", "origin", "main")
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    wt = workspaces / "job"
    _git(repo, "worktree", "add", "-q", "-b", BRANCH, str(wt), "HEAD")
    monkeypatch.setattr(self_restart, "REPO_ROOT", repo)  # this repository is "Jarvis itself"

    cfg = SimpleNamespace(
        max_parallel_jobs=3,
        auto_ship=True,
        ship_base_branch="main",
        ship_merge_method="squash",
        ship_via_pr=False,
        supabase_command="supabase",
        supabase_timeout_minutes=1,
        job_timeout_minutes=1,
    )
    data = tmp_path / "d"
    state = SimpleNamespace(
        settings=SimpleNamespace(
            coding=cfg,
            paths=SimpleNamespace(workspaces_dir=workspaces, data_dir=data, log_dir=tmp_path / "logs"),
            server=SimpleNamespace(host="127.0.0.1", port=8765),
        ),
        connectors=SimpleNamespace(connectors={"github": SimpleNamespace(active=True)}),
        kill_switch=SimpleNamespace(engaged=False),
    )
    runner = CodingRunner(state)  # type: ignore[arg-type]
    runner.restart_poll_s = 0
    runner.restart_status_poll_s = 0

    def launch(status: Path, **kw: Any) -> int:
        launches.append(
            {
                "status": self_restart.read_status(status),
                "jobs": {jid: (job.status, job.result) for jid, job in store.items()},
                "finished": [d["job_id"] for t, d in events if t == "coding.finished"],
                "busy": [jid for jid, t in runner._tasks.items() if not t.done()],
                **kw,
            }
        )
        return 4242

    monkeypatch.setattr(self_restart, "launch", launch)
    return SimpleNamespace(
        runner=runner, cfg=cfg, store=store, events=events, launches=launches, repo=repo, wt=wt, tmp=tmp_path, data=data
    )


def _job(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, files: dict[str, str], repo: Path | None = None) -> uuid.UUID:
    async def auth() -> dict[str, Any]:
        return {"ok": True}

    async def agent(*a: Any, **k: Any) -> dict[str, Any]:
        for name, text in files.items():
            path = env.wt / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return {"is_error": False, "result": "Fertig", "session_id": "s"}

    async def tests(cwd: Path, command: str) -> dict[str, Any]:
        return {"passed": True, "exit_code": 0, "output": "ok"}

    monkeypatch.setattr(env.runner, "auth_status", auth)
    monkeypatch.setattr(env.runner, "_claude_retrying", agent)
    monkeypatch.setattr(env.runner, "_run_tests", tests)
    jid = uuid.uuid4()
    env.store[jid] = SimpleNamespace(
        id=jid,
        status="queued",
        title="Selbstverbesserung",
        prompt="Mach etwas",
        repo_path=str(repo or env.repo),
        test_command="pytest",
        worktree_path=str(env.wt),
        branch=BRANCH,
        claude_session_id=None,
        model="m",
        effort=None,
        task_id=None,
        result=None,
        finished_at=None,
    )
    return jid


async def _run_to_end(env: SimpleNamespace, jid: uuid.UUID) -> None:
    env.runner._start(jid)
    await env.runner._tasks[jid]
    if env.runner._restart_task is not None:
        await asyncio.wait_for(env.runner._restart_task, 10)


# ---------------------------------------------------------------- detection: does the job concern Jarvis itself?
def test_only_the_repository_jarvis_runs_from_counts(tmp_path: Path) -> None:
    jarvis = tmp_path / "Programmieren" / "Jarvis"
    (jarvis / "core").mkdir(parents=True)
    lead_tool = tmp_path / "Programmieren" / "Lead-Tool"
    lead_tool.mkdir()

    assert is_jarvis_repo(jarvis, jarvis)
    assert is_jarvis_repo(jarvis / "core" / "..", jarvis)
    assert not is_jarvis_repo(lead_tool, jarvis)
    assert not is_jarvis_repo(jarvis / "core", jarvis)  # a sub folder is a different project path
    assert not is_jarvis_repo(tmp_path / "Programmieren" / "Jarvis-Kopie", jarvis)


def test_default_root_is_the_checkout_of_this_core() -> None:
    from jarvis.config import REPO_ROOT

    assert self_restart.REPO_ROOT == REPO_ROOT
    assert is_jarvis_repo(REPO_ROOT)
    assert (REPO_ROOT / "core" / "src" / "jarvis" / "coding" / "self_restart.py").is_file()


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (["core/src/jarvis/coding/runner.py"], True),
        (["core/src/jarvis/mcp/server.py"], True),  # MCP tools
        (["config/mcp_servers.yaml"], True),
        (["desktop/src/App.tsx"], True),
        (["core/pyproject.toml", "core/uv.lock"], True),
        (["core/migrations/versions/x.py"], True),
        (["scripts/local-tts/voices/rene_redo.wav"], True),  # the voice server reloads its voices
        (["README.md", "docs/DISTRIBUTED.md"], False),
        (["core/tests/unit/test_x.py"], False),
        (["apps/jarvis_mobile/lib/main.dart", "device_agent/src/jarvis_device/cli.py"], False),
        ([], False),
        (None, True),  # unknown: restart to be safe
    ],
)
def test_which_changes_need_a_restart(files: list[str] | None, expected: bool) -> None:
    assert needs_restart(files) is expected


def _ship(**kw: Any) -> dict[str, Any]:
    return {"merged": True, "message": "Ausgeliefert.", "local_sync": {"state": "synced"}, **kw}


def test_plan_after_a_merge_with_synced_checkout() -> None:
    ship = _ship()
    assert plan(ship, ["core/src/jarvis/x.py"]) is True
    assert ship["self_restart"]["state"] == "pending"
    assert "Jarvis wird nach Abschluss des Jobs neu gestartet" in ship["message"]


@pytest.mark.parametrize(
    ("ship", "files", "enabled", "reason"),
    [
        (_ship(local_sync={"state": "skipped"}), ["core/src/x.py"], True, "lokale Projekt wurde nicht aktualisiert"),
        (_ship(), ["README.md"], True, "nur Dateien geändert"),
        (_ship(), ["core/src/x.py"], False, "ausgeschaltet"),
    ],
)
def test_plan_skips_with_a_reason(ship: dict[str, Any], files: list[str], enabled: bool, reason: str) -> None:
    assert plan(ship, files, enabled=enabled) is False
    assert ship["self_restart"]["state"] == "skipped" and reason in ship["self_restart"]["message"]
    assert "Kein Neustart von Jarvis" in ship["message"]


def test_plan_without_merge_records_nothing() -> None:
    ship = {"merged": False, "message": "Nicht ausgeliefert."}
    assert plan(ship, ["core/src/x.py"]) is False
    assert "self_restart" not in ship and ship["message"] == "Nicht ausgeliefert."


# ---------------------------------------------------------------- order inside the runner
@pytest.mark.coding_runner
async def test_jarvis_job_restarts_after_delivery_and_result(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    jid = _job(env, monkeypatch, {"core/src/jarvis/neu.py": "x = 1\n"})
    await _run_to_end(env, jid)

    assert len(env.launches) == 1
    at_launch = env.launches[0]
    status, result = at_launch["jobs"][jid]
    # delivery done, result written and "fertig" published before the restart was handed over
    assert status == "completed" and result["ship"]["state"] == "shipped" and result["ship"]["merged"]
    assert result["ship"]["local_sync"]["state"] == "synced"
    assert str(jid) in at_launch["finished"]
    assert at_launch["busy"] == []  # the job's own task had ended
    assert at_launch["status"]["phase"] == "started" and at_launch["status"]["jobs"] == [str(jid)]
    assert at_launch["health_url"] == "http://127.0.0.1:8765/api/health"
    assert at_launch["run_dir"] == env.data / "run"
    # after the hand-over the job says so; the delivery itself is unchanged
    job = env.store[jid]
    assert job.result["ship"]["self_restart"]["state"] == "started"
    assert job.result["ship"]["state"] == "shipped" and job.result["delivery_error"] is None
    assert "neu gestartet" in next(d for t, d in env.events if t == "coding.finished")["summary"]


@pytest.mark.coding_runner
async def test_unrelated_open_changes_in_the_checkout_do_not_block_the_restart(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cause of the restarts that never came: Sir's uncommitted work on other files in the main checkout made the
    local sync skip, and without a synced checkout no restart was planned."""
    (env.repo / "README.md").write_text("lokale Änderung\n")
    (env.repo / "core" / "src").mkdir(parents=True)
    (env.repo / "core" / "src" / "wip.py").write_text("# Entwurf\n")
    jid = _job(env, monkeypatch, {"core/src/jarvis/neu.py": "x = 1\n"})
    await _run_to_end(env, jid)

    ship = env.store[jid].result["ship"]
    assert ship["local_sync"]["state"] == "synced" and ship["local_sync"]["kept_changes"] == 2
    assert (env.repo / "core" / "src" / "jarvis" / "neu.py").is_file()  # the new core loads the delivered code
    assert (env.repo / "README.md").read_text() == "lokale Änderung\n"
    assert (env.repo / "core" / "src" / "wip.py").read_text() == "# Entwurf\n"
    assert len(env.launches) == 1 and ship["self_restart"]["state"] == "started"


@pytest.mark.coding_runner
async def test_open_changes_to_delivered_files_skip_the_restart(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    (env.repo / "core" / "src" / "jarvis").mkdir(parents=True)
    (env.repo / "core" / "src" / "jarvis" / "neu.py").write_text("# lokal\n")  # the job delivers this file too
    jid = _job(env, monkeypatch, {"core/src/jarvis/neu.py": "x = 1\n"})
    await _run_to_end(env, jid)

    ship = env.store[jid].result["ship"]
    assert ship["state"] == "shipped" and ship["local_sync"]["state"] == "skipped"
    assert ship["self_restart"]["state"] == "skipped" and env.launches == []
    assert (env.repo / "core" / "src" / "jarvis" / "neu.py").read_text() == "# lokal\n"


@pytest.mark.coding_runner
async def test_job_on_another_project_never_restarts(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(self_restart, "REPO_ROOT", tmp_path / "Programmieren" / "Jarvis")  # Lead-Tool job below
    jid = _job(env, monkeypatch, {"core/src/jarvis/neu.py": "x = 1\n"})
    await _run_to_end(env, jid)

    job = env.store[jid]
    assert job.status == "completed" and job.result["ship"]["state"] == "shipped"
    assert "self_restart" not in job.result["ship"]
    assert env.launches == [] and env.runner._restart_task is None
    assert not (env.data / self_restart.STATUS_FILE).exists()


@pytest.mark.coding_runner
async def test_jarvis_job_with_docs_only_does_not_restart(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    jid = _job(env, monkeypatch, {"docs/NEU.md": "# Neu\n"})
    await _run_to_end(env, jid)

    assert env.store[jid].result["ship"]["self_restart"]["state"] == "skipped"
    assert env.launches == []


async def test_red_jarvis_job_does_not_restart(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    jid = _job(env, monkeypatch, {"core/src/jarvis/neu.py": "x = 1\n"})

    async def red(cwd: Path, command: str) -> dict[str, Any]:
        return {"passed": False, "exit_code": 1, "output": "1 failed"}

    monkeypatch.setattr(env.runner, "_run_tests", red)
    await _run_to_end(env, jid)

    assert env.store[jid].status == "failed" and "self_restart" not in env.store[jid].result["ship"]
    assert env.launches == []


@pytest.mark.coding_runner
async def test_restart_waits_for_other_running_jobs(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    other_id, release = uuid.uuid4(), asyncio.Event()
    env.runner._tasks[other_id] = asyncio.create_task(release.wait())  # another job still at work
    jid = _job(env, monkeypatch, {"core/src/jarvis/neu.py": "x = 1\n"})
    env.runner._start(jid)
    await env.runner._tasks[jid]
    for _ in range(50):
        await asyncio.sleep(0)

    assert env.launches == []  # never while another job runs
    waiting = env.store[jid].result["ship"]["self_restart"]
    assert waiting["state"] == "waiting" and waiting["waiting_for"] == [str(other_id)]
    assert str(other_id)[:8] in waiting["message"]
    assert env.store[jid].result["ship"]["state"] == "shipped"

    release.set()
    await asyncio.wait_for(env.runner._restart_task, 10)  # type: ignore[arg-type]
    assert len(env.launches) == 1 and env.launches[0]["busy"] == []


@pytest.mark.coding_runner
async def test_failed_hand_over_is_a_warning_not_a_delivery_error(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(status: Path, **kw: Any) -> int:
        raise OSError("kein Python")

    monkeypatch.setattr(self_restart, "launch", broken)
    jid = _job(env, monkeypatch, {"core/src/jarvis/neu.py": "x = 1\n"})
    await _run_to_end(env, jid)

    job = env.store[jid]
    assert job.status == "completed" and job.result["ship"]["state"] == "shipped"
    assert job.result["delivery_error"] is None
    assert job.result["ship"]["self_restart"]["state"] == "failed"
    assert job.result["warnings"] == ["Jarvis-Neustart: Neustart konnte nicht gestartet werden: kein Python"]
    assert not (env.data / self_restart.STATUS_FILE).exists()


@pytest.mark.coding_runner
async def test_kill_switch_skips_the_restart(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    other_id, release = uuid.uuid4(), asyncio.Event()
    env.runner._tasks[other_id] = asyncio.create_task(release.wait())  # keeps the restart waiting
    jid = _job(env, monkeypatch, {"core/src/jarvis/neu.py": "x = 1\n"})
    env.runner._start(jid)
    await env.runner._tasks[jid]
    env.runner.state.kill_switch.engaged = True  # engaged while the restart waits
    release.set()
    await asyncio.wait_for(env.runner._restart_task, 10)  # type: ignore[arg-type]

    assert env.launches == [] and env.store[jid].result["ship"]["self_restart"]["state"] == "skipped"


# ---------------------------------------------------------------- the new core records the outcome
def _shipped_job(env: SimpleNamespace, state: str = "started") -> uuid.UUID:
    jid = uuid.uuid4()
    env.store[jid] = SimpleNamespace(
        id=jid,
        status="completed",
        title="Selbstverbesserung",
        result={"ship": {"state": "shipped", "merged": True, "self_restart": {"state": state}}, "delivery_error": None},
    )
    return jid


async def test_new_core_records_a_failed_restart_as_warning(env: SimpleNamespace) -> None:
    jid = _shipped_job(env)
    notes: list[tuple[str, str, str, str]] = []

    class Notifier:
        async def notify(self, event: str, title: str, body: str = "", *, level: str = "info", **k: Any) -> list[str]:
            notes.append((event, title, body, level))
            return ["desktop"]

    env.runner.state.notifier = Notifier()
    outcome = {
        "state": "failed",
        "message": "Core neu gestartet und antwortet wieder (launchd); Desktop-App: Wieder öffnen fehlgeschlagen.",
        "core": {"state": "ok"},
        "desktop": {"state": "failed"},
    }
    path = env.data / self_restart.STATUS_FILE
    self_restart.write_status(path, {"phase": "finished", "jobs": [str(jid)], "outcome": outcome})
    await env.runner.finish_self_restart()

    job = env.store[jid]
    assert job.result["ship"]["self_restart"]["state"] == "failed"
    assert job.result["ship"]["self_restart"]["desktop"] == {"state": "failed"}
    assert job.result["warnings"] == [f"Jarvis-Neustart: {outcome['message']}"]
    assert job.result["ship"]["state"] == "shipped" and job.result["delivery_error"] is None  # delivery untouched
    assert notes and notes[0][0] == "coding.self_restart" and notes[0][3] == "warning"
    assert not path.exists()


async def test_new_core_records_a_successful_restart(env: SimpleNamespace) -> None:
    jid = _shipped_job(env)
    outcome = {"state": "done", "message": "Core neu gestartet und antwortet wieder.", "core": {"state": "ok"}}
    self_restart.write_status(
        env.data / self_restart.STATUS_FILE, {"phase": "finished", "jobs": [str(jid)], "outcome": outcome}
    )
    await env.runner.finish_self_restart()

    assert env.store[jid].result["ship"]["self_restart"]["state"] == "done"
    assert "warnings" not in env.store[jid].result


async def test_new_core_waits_for_the_helper_to_finish(env: SimpleNamespace) -> None:
    jid = _shipped_job(env)
    path = env.data / self_restart.STATUS_FILE
    self_restart.write_status(path, {"phase": "running", "jobs": [str(jid)], "helper_pid": __import__("os").getpid()})
    waiting = asyncio.create_task(env.runner.finish_self_restart())
    for _ in range(20):
        await asyncio.sleep(0)
    assert not waiting.done()  # the helper is still restarting the desktop app

    outcome = {"state": "done", "message": "fertig", "core": {"state": "ok"}, "desktop": {"state": "restarted"}}
    self_restart.write_status(path, {"phase": "finished", "jobs": [str(jid)], "outcome": outcome})
    await asyncio.wait_for(waiting, 5)
    assert env.store[jid].result["ship"]["self_restart"]["desktop"] == {"state": "restarted"}


async def test_planned_restart_overtaken_by_another_restart(env: SimpleNamespace) -> None:
    jid = _shipped_job(env, state="waiting")
    self_restart.write_status(env.data / self_restart.STATUS_FILE, {"phase": "pending", "jobs": [str(jid)]})
    await env.runner.finish_self_restart()

    info = env.store[jid].result["ship"]["self_restart"]
    assert info["state"] == "done" and "anderweitig neu gestartet" in info["message"]


async def test_no_status_file_nothing_happens(env: SimpleNamespace) -> None:
    await env.runner.finish_self_restart()
    assert env.store == {}


# ---------------------------------------------------------------- helper: core first, desktop app only if running
class RecordingRestarter(Restarter):
    def __init__(self, *, desktop: dict[int, str], core_error: Exception | None = None, quit_error: Exception | None = None) -> None:
        super().__init__(root=Path("/repo"), core_pid=1, health_url="http://127.0.0.1:8765/api/health", run_dir=Path("/run"))
        self.desktop, self.core_error, self.quit_error = desktop, core_error, quit_error
        self.calls: list[str] = []

    def desktop_pids(self) -> dict[int, str]:
        self.calls.append("desktop_pids")
        return self.desktop

    def quit_desktop(self, pids: list[int]) -> str:
        self.calls.append(f"quit_desktop {pids}")
        if self.quit_error:
            raise self.quit_error
        return "per SIGTERM"

    def restart_core(self) -> str:
        self.calls.append("restart_core")
        if self.core_error:
            raise self.core_error
        return "launchd com.jarvis.core"

    def wait_healthy(self, timeout: float = 0) -> dict[str, Any]:
        self.calls.append("wait_healthy")
        return {"status": "ok", "version": "1"}

    def open_desktop(self, bundle: str) -> None:
        self.calls.append(f"open_desktop {bundle}")


APP = "/Applications/J.A.R.V.I.S..app"


def test_running_desktop_app_is_quit_before_and_reopened_after_the_core() -> None:
    r = RecordingRestarter(desktop={501: APP})
    out = r.restart()

    assert r.calls == ["desktop_pids", "quit_desktop [501]", "restart_core", "wait_healthy", f"open_desktop {APP}"]
    assert out["state"] == "done"
    assert out["core"] == {"state": "ok", "method": "launchd com.jarvis.core", "health": {"status": "ok", "version": "1"}}
    assert out["desktop"] == {"state": "restarted", "bundle": APP, "quit": "per SIGTERM"}
    assert "Desktop-App beendet (per SIGTERM) und wieder geöffnet" in out["message"]


def test_desktop_app_not_running_is_left_alone() -> None:
    r = RecordingRestarter(desktop={})
    out = r.restart()

    assert r.calls == ["desktop_pids", "restart_core", "wait_healthy"]
    assert out["state"] == "done" and out["desktop"] == {"state": "not_running"}
    assert "Desktop-App lief nicht" in out["message"]


def test_core_failure_still_reopens_the_app_and_is_reported() -> None:
    r = RecordingRestarter(desktop={501: APP}, core_error=self_restart.RestartError("start.sh fehlgeschlagen (Exit 1)"))
    out = r.restart()

    assert r.calls == ["desktop_pids", "quit_desktop [501]", "restart_core", f"open_desktop {APP}"]
    assert out["state"] == "failed" and out["core"]["state"] == "failed"
    assert out["desktop"]["state"] == "restarted"
    assert "Core-Neustart fehlgeschlagen: start.sh fehlgeschlagen (Exit 1)" in out["message"]


def test_app_that_cannot_be_quit_is_not_opened_twice() -> None:
    r = RecordingRestarter(desktop={501: APP}, quit_error=self_restart.RestartError("läuft weiter"))
    out = r.restart()

    assert r.calls == ["desktop_pids", "quit_desktop [501]", "restart_core", "wait_healthy"]
    assert out["state"] == "failed" and out["core"]["state"] == "ok"
    assert out["desktop"]["state"] == "failed" and "läuft weiter" in out["desktop"]["error"]


class FakeRun:
    def __init__(self, answers: dict[str, tuple[int | None, str]]) -> None:
        self.answers = answers
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], timeout: float) -> tuple[int | None, str]:
        self.calls.append(argv)
        for prefix, answer in self.answers.items():
            if " ".join(argv).startswith(prefix):
                return answer
        return 0, ""


def _restarter(run: FakeRun, tmp_path: Path, platform: str = "darwin") -> Restarter:
    dead = subprocess.Popen(["true"])
    dead.wait()  # a pid that no longer runs: stop_core never signals a real process here
    return Restarter(
        root=tmp_path, core_pid=dead.pid, health_url="http://x/api/health", run_dir=tmp_path / "run",
        platform=platform, run=run, sleep=lambda s: None,
    )  # fmt: skip


def test_desktop_app_is_found_by_its_executable(tmp_path: Path) -> None:
    ps = (
        "  101 /usr/sbin/cfprefsd agent\n"
        f"  501 {APP}/Contents/MacOS/jarvis-desktop\n"
        "  777 /Users/paul/Documents/Programmieren/Jarvis/desktop/src-tauri/target/release/bundle/macos/"
        "J.A.R.V.I.S..app/Contents/MacOS/jarvis-desktop --flag\n"
        "  888 grep jarvis\n"
        f"  999 grep {APP}/Contents/MacOS/jarvis-desktop\n"  # only names the app: never signalled
    )
    r = _restarter(FakeRun({"ps -axo": (0, ps)}), tmp_path)
    found = r.desktop_pids()

    assert found[501] == APP
    assert found[777].endswith("bundle/macos/J.A.R.V.I.S..app") and set(found) == {501, 777}
    assert _restarter(FakeRun({"ps -axo": (0, ps)}), tmp_path, platform="linux").desktop_pids() == {}


# ---------------------------------------------------------------- helper end to end (real decisions, fake commands)
class Ticker:
    """Fake clock for ``_wait_gone``; every tick reaps the test's own child processes (a dead, unreaped child still
    counts as alive for ``kill(pid, 0)``)."""

    def __init__(self, *procs: subprocess.Popen[Any]) -> None:
        self.now, self.procs = 0.0, procs

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        time.sleep(0.02)
        for proc in self.procs:
            proc.poll()


def _helper(run: FakeRun, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *procs: subprocess.Popen[Any]) -> Restarter:
    ticker = Ticker(*procs)
    r = _restarter(run, tmp_path)
    r.sleep, r.clock = ticker.sleep, ticker.clock
    monkeypatch.setattr(r, "wait_healthy", lambda timeout=0: {"status": "ok", "version": "2"})
    return r


LAUNCHD = {"launchctl print": (0, "state = running")}


@pytest.mark.coding_runner
def test_restart_without_desktop_app_only_restarts_the_core(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No desktop app running (or not even installed): the core is restarted, the app is neither quit nor opened."""
    run = FakeRun({"ps -axo": (0, "  101 /usr/sbin/cfprefsd agent\n"), **LAUNCHD})
    out = _helper(run, tmp_path, monkeypatch).restart()

    assert out["state"] == "done" and out["core"]["state"] == "ok" and out["desktop"] == {"state": "not_running"}
    assert [c[:2] for c in run.calls] == [["ps", "-axo"], ["launchctl", "print"], ["launchctl", "kickstart"]]
    assert not any(c[0] in ("open", "osascript") for c in run.calls)
    assert out["message"] == (
        "Core neu gestartet und antwortet wieder (launchd com.jarvis.core); Desktop-App lief nicht, nicht angefasst."
    )


@pytest.mark.coding_runner
def test_restart_with_running_desktop_app_restarts_both(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = subprocess.Popen(["sleep", "60"])  # stands in for the running desktop app
    try:
        ps = f"  {app.pid} {APP}/Contents/MacOS/jarvis-desktop\n"
        run = FakeRun({"ps -axo": (0, ps), **LAUNCHD})
        out = _helper(run, tmp_path, monkeypatch, app).restart()
    finally:
        app.kill()
        app.wait()

    assert app.returncode == -signal.SIGTERM  # ended by the helper, no AppleScript quit
    assert out["state"] == "done" and out["core"]["state"] == "ok"
    assert out["desktop"] == {"state": "restarted", "bundle": APP, "quit": "per SIGTERM"}
    names = [c[1] if c[0] == "launchctl" else c[0] for c in run.calls]
    assert names == ["ps", "print", "kickstart", "open", "ps"]  # core restarted before the app is opened again
    assert ["open", APP] in run.calls and not any(c[0] == "osascript" for c in run.calls)


@pytest.mark.coding_runner
def test_desktop_app_ignoring_sigterm_is_killed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = subprocess.Popen(["bash", "-c", "trap '' TERM; echo ready; exec sleep 60"], stdout=subprocess.PIPE, text=True)
    try:
        assert app.stdout is not None and app.stdout.readline().strip() == "ready"
        how = _helper(FakeRun({}), tmp_path, monkeypatch, app).quit_desktop([app.pid])
    finally:
        app.kill()
        app.wait()
        app.stdout.close()  # type: ignore[union-attr]

    assert how == "per SIGKILL" and app.returncode == -signal.SIGKILL


@pytest.mark.coding_runner
def test_failed_desktop_check_still_restarts_the_core(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = FakeRun({"ps -axo": (127, "No such file or directory: 'ps'"), **LAUNCHD})
    out = _helper(run, tmp_path, monkeypatch).restart()

    assert out["core"]["state"] == "ok"
    assert out["desktop"]["state"] == "failed" and "ps fehlgeschlagen" in out["desktop"]["error"]
    assert not any(c[0] == "open" for c in run.calls)  # an app Jarvis did not quit is never opened
    assert out["state"] == "failed"  # reported to Sir, the core restart itself went through


@pytest.mark.coding_runner
def test_core_under_launchd_is_kickstarted(tmp_path: Path) -> None:
    run = FakeRun({"launchctl print": (0, "state = running")})
    method = _restarter(run, tmp_path).restart_core()

    assert method == "launchd com.jarvis.core"
    assert [c[:3] for c in run.calls] == [["launchctl", "print", run.calls[0][2]], ["launchctl", "kickstart", "-k"]]
    assert run.calls[1][3].endswith("/com.jarvis.core")


@pytest.mark.coding_runner
def test_core_from_the_start_script_is_stopped_and_started_again(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "start.sh").write_text("#!/bin/bash\n")
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "core.pid").write_text("999999\n")
    run = FakeRun({"launchctl print": (113, "Could not find service")})
    method = _restarter(run, tmp_path).restart_core()

    assert method == "scripts/start.sh --no-app"
    assert run.calls[-1] == ["/bin/bash", str(tmp_path / "scripts" / "start.sh"), "--no-app"]  # voice daemon included
    assert not (tmp_path / "run" / "core.pid").exists()  # the start script would otherwise think the core still runs


def _tts_server(tmp_path: Path) -> subprocess.Popen[Any]:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "scripts/local-tts/server.py"])
    (tmp_path / "run").mkdir(exist_ok=True)
    (tmp_path / "run" / "tts.pid").write_text(f"{proc.pid}\n")
    voices = tmp_path / "scripts" / "local-tts" / "voices"
    voices.mkdir(parents=True)
    (voices / "jarvis.txt").write_text("Hallo.")
    return proc


def _age(path: Path, seconds: float) -> None:
    for f in path.rglob("*"):
        os.utime(f, (time.time() + seconds, time.time() + seconds))


@pytest.mark.coding_runner
def test_changed_voice_server_is_restarted_with_the_core(tmp_path: Path) -> None:
    proc = _tts_server(tmp_path)
    try:
        _age(tmp_path / "scripts", 120)  # a delivery wrote new voices after the server started
        r = _restarter(FakeRun({}), tmp_path)
        r.sleep, r.clock = Ticker(proc).sleep, Ticker(proc).clock
        assert r.stop_stale_tts() is True
        assert proc.wait(5) is not None and not (tmp_path / "run" / "tts.pid").exists()
    finally:
        proc.kill()


@pytest.mark.coding_runner
def test_unchanged_voice_server_keeps_running(tmp_path: Path) -> None:
    proc = _tts_server(tmp_path)
    try:
        _age(tmp_path / "scripts", -3600)
        assert _restarter(FakeRun({}), tmp_path).stop_stale_tts() is False
        assert proc.poll() is None and (tmp_path / "run" / "tts.pid").exists()
        (tmp_path / "run" / "tts.pid").write_text(f"{os.getpid()}\n")  # pid of something else: never signalled
        _age(tmp_path / "scripts", 120)
        assert _restarter(FakeRun({}), tmp_path).stop_stale_tts() is False
    finally:
        proc.kill()


@pytest.mark.coding_runner
def test_failing_start_script_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "start.sh").write_text("#!/bin/bash\n")
    run = FakeRun({"launchctl print": (113, ""), "/bin/bash": (1, "✗ Postgres fehlt")})
    with pytest.raises(self_restart.RestartError, match=r"start\.sh fehlgeschlagen"):
        _restarter(run, tmp_path).restart_core()


def test_helper_writes_its_outcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / self_restart.STATUS_FILE
    self_restart.write_status(path, {"phase": "started", "jobs": ["j1"]})
    monkeypatch.setattr(
        Restarter, "restart", lambda self: {"state": "done", "message": "ok", "core": {"state": "ok"}}
    )
    code = self_restart.main(
        ["--status", str(path), "--core-pid", "1", "--health-url", "http://x", "--root", str(tmp_path),
         "--run-dir", str(tmp_path), "--delay", "0"]
    )  # fmt: skip

    status = self_restart.read_status(path)
    assert code == 0 and status is not None
    assert status["phase"] == "finished" and status["jobs"] == ["j1"] and status["outcome"]["state"] == "done"
