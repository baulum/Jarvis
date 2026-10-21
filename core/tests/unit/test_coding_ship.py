"""Standard flow after a coding job: green → push, PR, merge, Supabase deploy; red / error → nothing leaves the branch.

Real git (a bare "origin" whose path contains github.com/acme/app), a fake GitHub connector that merges into that
origin, and a fake ``supabase`` executable that logs its arguments.
"""

import subprocess
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import jarvis.coding.runner as runner_mod
from jarvis.coding.runner import CodingRunner, parse_github_repo, supabase_projects
from jarvis.connectors.coding import CodingConnector
from jarvis.tools.executor import ToolUserError

BRANCH = "jarvis/job"


def _git(cwd: Path, *args: str) -> str:
    cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args]
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class FakeSession:
    def __init__(self, store: dict[uuid.UUID, Any]) -> None:
        self.store = store

    async def get(self, model: Any, key: uuid.UUID) -> Any:
        return self.store.get(key)


class FakeGitHub:
    active = True

    def __init__(self, origin: Path) -> None:
        self.origin = origin
        self.created: list[dict[str, Any]] = []
        self.merged: list[dict[str, Any]] = []
        self.merge_error: Exception | None = None

    async def create_pr(
        self, repo: str, head: str, base: str, title: str, body: str, draft: bool = True
    ) -> dict[str, Any]:
        self.created.append({"repo": repo, "head": head, "base": base, "title": title, "draft": draft})
        return {"number": 7, "html_url": f"https://github.com/{repo}/pull/7"}

    async def merge_pr(self, repo: str, number: int, *, sha: str | None = None, method: str = "squash") -> Any:
        if self.merge_error is not None:
            raise self.merge_error
        self.merged.append({"repo": repo, "number": number, "sha": sha, "method": method})
        _git(self.origin, "update-ref", "refs/heads/main", sha or "")  # what the merge does on GitHub
        return {"merged": True, "sha": sha}


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    store: dict[uuid.UUID, Any] = {}
    events: list[tuple[str, dict[str, Any]]] = []

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

    origin = tmp_path / "github.com" / "acme" / "app.git"
    origin.mkdir(parents=True)
    _git(origin, "init", "-q", "--bare", "-b", "main")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("app\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "-u", "origin", "main")
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    wt = workspaces / "job"
    _git(repo, "worktree", "add", "-q", "-b", BRANCH, str(wt), "HEAD")

    gh = FakeGitHub(origin)
    supabase_log = tmp_path / "supabase.log"
    cfg = SimpleNamespace(
        max_parallel_jobs=1,
        auto_ship=True,
        ship_base_branch="main",
        ship_merge_method="squash",
        supabase_command=str(tmp_path / "bin" / "supabase"),
        supabase_timeout_minutes=1,
        job_timeout_minutes=1,
    )
    state = SimpleNamespace(
        settings=SimpleNamespace(coding=cfg, paths=SimpleNamespace(workspaces_dir=workspaces, data_dir=tmp_path / "d")),
        connectors=SimpleNamespace(connectors={"github": gh}),
        kill_switch=SimpleNamespace(engaged=False),
    )
    return SimpleNamespace(
        runner=CodingRunner(state),  # type: ignore[arg-type]
        gh=gh,
        cfg=cfg,
        store=store,
        events=events,
        origin=origin,
        repo=repo,
        wt=wt,
        tmp=tmp_path,
        supabase_log=supabase_log,
    )


def _commit(env: SimpleNamespace, files: dict[str, str]) -> str:
    for name, text in files.items():
        path = env.wt / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(env.wt, "add", "-A")
    _git(env.wt, "commit", "-q", "-m", "work")
    return _git(env.wt, "rev-parse", "HEAD")


def _fake_supabase(env: SimpleNamespace, exit_code: int = 0) -> None:
    script = env.tmp / "bin" / "supabase"
    script.parent.mkdir(exist_ok=True)
    script.write_text(f'#!/bin/sh\necho "$(pwd -P) $@" >> "{env.supabase_log}"\necho boom\nexit {exit_code}\n')
    script.chmod(0o755)


SUPABASE_FILES = {
    "supabase/functions/hello/index.ts": "export {}\n",
    "supabase/functions/_shared/util.ts": "export {}\n",
    "supabase/migrations/001_init.sql": "select 1;\n",
}


async def _flow(env: SimpleNamespace, *, green: bool = True, **kw: Any) -> dict[str, Any]:
    return await env.runner._ship_flow(
        uuid.uuid4(),
        repo=env.repo,
        worktree=env.wt,
        branch=BRANCH,
        title="Feature",
        body="Zusammenfassung",
        green=green,
        reason="Die Tests sind rot",
        **kw,
    )


def _origin_main(env: SimpleNamespace) -> str:
    return _git(env.origin, "rev-parse", "main")


def _origin_branches(env: SimpleNamespace) -> list[str]:
    return _git(env.origin, "branch", "--format=%(refname:short)").split()


# ---------------------------------------------------------------- helpers
def test_parse_github_repo() -> None:
    assert parse_github_repo("https://github.com/acme/app.git") == "acme/app"
    assert parse_github_repo("git@github.com:acme/app.git") == "acme/app"
    assert parse_github_repo("ssh://git@github.com/acme/my.app/") == "acme/my.app"
    assert parse_github_repo("https://gitlab.com/acme/app.git") is None


def test_supabase_projects() -> None:
    paths = [
        "README.md",
        "supabase/config.toml",
        "supabase/functions/hello/index.ts",
        "supabase/functions/_shared/cors.ts",
        "supabase/functions/import_map.json",
        "supabase/migrations/001_init.sql",
        "supabase/migrations/notes.md",
        "backend/supabase/migrations/002_more.sql",
        "docs/supabase/readme.md",
    ]
    assert supabase_projects(paths) == {
        "": {"functions": ["hello"], "migrations": 1},
        "backend": {"functions": [], "migrations": 1},
    }
    assert supabase_projects(["supabase/config.toml", "src/a.py"]) == {}


# ---------------------------------------------------------------- green
async def test_green_pushes_creates_pr_merges_and_deploys(env: SimpleNamespace) -> None:
    head = _commit(env, SUPABASE_FILES)
    _fake_supabase(env)
    ship = await _flow(env)

    assert ship["state"] == "shipped", ship
    assert env.gh.created == [
        {"repo": "acme/app", "head": BRANCH, "base": "main", "title": "Jarvis: Feature", "draft": False}
    ]
    assert env.gh.merged == [{"repo": "acme/app", "number": 7, "sha": head, "method": "squash"}]
    assert BRANCH in _origin_branches(env)
    assert _origin_main(env) == head
    assert ship["pr"] == {"number": 7, "url": "https://github.com/acme/app/pull/7"}
    # migrations first, then functions, both from the local checkout brought to the merged state
    lines = env.supabase_log.read_text().splitlines()
    assert [ln.split(" ", 1)[1] for ln in lines] == ["db push --yes", "functions deploy"]
    assert all(ln.startswith(str(env.repo.resolve())) for ln in lines)
    assert (env.repo / "supabase/migrations/001_init.sql").exists()
    assert ship["supabase"]["deployed"] == ["db push", "functions deploy"]
    assert "PR #7" in ship["message"] and "Supabase deployt" in ship["message"]


async def test_green_without_supabase_skips_deploy_and_says_so(env: SimpleNamespace) -> None:
    head = _commit(env, {"app.py": "print(1)\n"})
    ship = await _flow(env)

    assert ship["state"] == "shipped"
    assert _origin_main(env) == head
    assert ship["supabase"] == {"present": False, "note": "Supabase nicht im Projekt – übersprungen"}
    assert "Supabase nicht im Projekt" in ship["message"]
    assert not env.supabase_log.exists()


async def test_green_without_changes_ships_nothing(env: SimpleNamespace) -> None:
    before = _origin_main(env)
    ship = await _flow(env)

    assert ship["state"] == "not_shipped" and "keine Änderungen" in ship["message"]
    assert env.gh.created == [] and env.gh.merged == []
    assert _origin_main(env) == before


# ---------------------------------------------------------------- red / errors: nothing leaves the branch
async def test_red_pushes_nothing(env: SimpleNamespace) -> None:
    _commit(env, SUPABASE_FILES)
    _fake_supabase(env)
    before = _origin_main(env)
    ship = await _flow(env, green=False)

    assert ship["state"] == "not_shipped" and ship["merged"] is False
    assert "Tests sind rot" in ship["message"] and BRANCH in ship["message"]
    assert env.gh.created == [] and env.gh.merged == []
    assert _origin_branches(env) == ["main"]
    assert _origin_main(env) == before
    assert not env.supabase_log.exists()
    assert BRANCH in _git(env.repo, "branch", "--list", BRANCH)  # job branch stays


async def test_missing_merge_permission_merges_and_deploys_nothing(env: SimpleNamespace) -> None:
    _commit(env, SUPABASE_FILES)
    _fake_supabase(env)
    before = _origin_main(env)
    env.gh.merge_error = ToolUserError("GitHub-Fehler 403: Resource not accessible by personal access token")
    ship = await _flow(env)

    assert ship["state"] == "failed" and ship["stage"] == "merge" and ship["merged"] is False
    assert "403" in ship["message"] and "https://github.com/acme/app/pull/7" in ship["message"]
    assert _origin_main(env) == before
    assert not env.supabase_log.exists()


async def test_push_failure_creates_no_pr(env: SimpleNamespace) -> None:
    _commit(env, {"app.py": "print(1)\n"})
    _git(env.wt, "remote", "set-url", "origin", str(env.tmp / "github.com" / "acme" / "gone.git"))
    ship = await _flow(env)

    assert ship["state"] == "failed" and ship["stage"] == "push"
    assert env.gh.created == [] and env.gh.merged == []


async def test_origin_not_github_is_refused(env: SimpleNamespace) -> None:
    _commit(env, {"app.py": "print(1)\n"})
    _git(env.wt, "remote", "set-url", "origin", "https://gitlab.com/acme/app.git")
    ship = await _flow(env)

    assert ship["state"] == "failed" and "kein GitHub-Repository" in ship["message"]
    assert env.gh.created == []


async def test_github_connector_missing_blocks_before_push(env: SimpleNamespace) -> None:
    _commit(env, {"app.py": "print(1)\n"})
    env.gh.active = False
    ship = await _flow(env)

    assert ship["state"] == "failed" and "GitHub-Connector" in ship["message"]
    assert _origin_branches(env) == ["main"]


async def test_kill_switch_blocks_everything(env: SimpleNamespace) -> None:
    _commit(env, {"app.py": "print(1)\n"})
    env.runner.state.kill_switch.engaged = True
    ship = await _flow(env)

    assert ship["state"] == "not_shipped" and "Not-Aus" in ship["message"]
    assert _origin_branches(env) == ["main"] and env.gh.created == []


async def test_deploy_failure_is_reported_and_merge_stays_recorded(env: SimpleNamespace) -> None:
    head = _commit(env, SUPABASE_FILES)
    _fake_supabase(env, exit_code=1)
    ship = await _flow(env)

    assert ship["state"] == "failed" and ship["stage"] == "deploy" and ship["merged"] is True
    assert _origin_main(env) == head
    assert "boom" in ship["message"] and "Merge ist bereits erfolgt" in ship["message"]
    assert [ln.split(" ", 1)[1] for ln in env.supabase_log.read_text().splitlines()] == ["db push --yes"]


async def test_supabase_cli_missing_is_reported(env: SimpleNamespace) -> None:
    _commit(env, SUPABASE_FILES)  # no fake executable written
    ship = await _flow(env)

    assert ship["state"] == "failed" and ship["stage"] == "deploy" and "Supabase CLI" in ship["message"]


async def test_deploy_refused_when_local_repo_is_dirty(env: SimpleNamespace) -> None:
    _commit(env, SUPABASE_FILES)
    _fake_supabase(env)
    (env.repo / "README.md").write_text("local edit\n")
    ship = await _flow(env)

    assert ship["state"] == "failed" and "nicht committete Änderungen" in ship["message"]
    assert not env.supabase_log.exists()


# ---------------------------------------------------------------- retry via ship()
def _store_job(env: SimpleNamespace, ship: dict[str, Any] | None) -> uuid.UUID:
    jid = uuid.uuid4()
    result: dict[str, Any] = {"summary": "ok", "tests": None}
    if ship is not None:
        result["ship"] = ship
    env.store[jid] = SimpleNamespace(
        id=jid,
        status="completed",
        title="Feature",
        repo_path=str(env.repo),
        worktree_path=str(env.wt),
        branch=BRANCH,
        result=result,
    )
    return jid


async def test_ship_retry_reuses_pr_after_denied_merge(env: SimpleNamespace) -> None:
    head = _commit(env, {"app.py": "print(1)\n"})
    env.gh.merge_error = ToolUserError("GitHub-Fehler 405: Pull Request is not mergeable")
    jid = _store_job(env, await _flow(env))
    assert env.store[jid].result["ship"]["state"] == "failed"

    env.gh.merge_error = None
    ship = await env.runner.ship(jid, "test")

    assert ship["state"] == "shipped" and len(env.gh.created) == 1 and len(env.gh.merged) == 1
    assert _origin_main(env) == head
    assert env.store[jid].result["ship"]["state"] == "shipped"


async def test_ship_retry_after_failed_deploy_does_not_merge_again(env: SimpleNamespace) -> None:
    _commit(env, SUPABASE_FILES)
    _fake_supabase(env, exit_code=1)
    jid = _store_job(env, await _flow(env))
    _fake_supabase(env)
    ship = await env.runner.ship(jid, "test")

    assert ship["state"] == "shipped" and len(env.gh.created) == 1 and len(env.gh.merged) == 1


async def test_ship_refuses_shipped_and_unfinished_jobs(env: SimpleNamespace) -> None:
    jid = _store_job(env, {"state": "shipped"})
    with pytest.raises(runner_mod.CodingError, match="bereits ausgeliefert"):
        await env.runner.ship(jid, "test")
    env.store[jid].status = "failed"
    with pytest.raises(runner_mod.CodingError, match="grüne"):
        await env.runner.ship(jid, "test")


async def test_ship_tool_registered_with_high_risk(env: SimpleNamespace) -> None:
    conn = CodingConnector.__new__(CodingConnector)
    conn.state = SimpleNamespace(coding=env.runner)  # type: ignore[assignment]
    specs = {t.name: t for t in conn.tools()}
    assert specs["coding_ship_job"].risk == "high"
    assert "Nur auf ausdrücklichen Wunsch" in specs["coding_prepare_pr"].description
    ctx = SimpleNamespace(principal=SimpleNamespace(label="test"))
    jid = _store_job(env, {"state": "shipped"})
    with pytest.raises(ToolUserError, match="bereits ausgeliefert"):
        await specs["coding_ship_job"].handler(ctx, {"job_id": str(jid)})  # type: ignore[arg-type]


# ---------------------------------------------------------------- through _run (job status → ship)
async def _run_job(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, *, tests_pass: bool) -> uuid.UUID:
    async def auth() -> dict[str, Any]:
        return {"ok": True}

    async def agent(*a: Any, **k: Any) -> dict[str, Any]:
        (env.wt / "app.py").write_text("print(1)\n")
        return {"is_error": False, "result": "Fertig", "session_id": "s"}

    async def tests(cwd: Path, command: str) -> dict[str, Any]:
        return {"passed": tests_pass, "exit_code": 0 if tests_pass else 1, "output": "out"}

    monkeypatch.setattr(env.runner, "auth_status", auth)
    monkeypatch.setattr(env.runner, "_claude_retrying", agent)
    monkeypatch.setattr(env.runner, "_run_tests", tests)
    jid = uuid.uuid4()
    env.store[jid] = SimpleNamespace(
        id=jid,
        status="queued",
        title="Feature",
        prompt="Mach etwas",
        repo_path=str(env.repo),
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
    await env.runner._run(jid)
    return jid


async def test_run_green_job_is_shipped(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    jid = await _run_job(env, monkeypatch, tests_pass=True)
    job = env.store[jid]

    assert job.status == "completed"
    assert job.result["ship"]["state"] == "shipped"
    assert _origin_main(env) == _git(env.wt, "rev-parse", "HEAD")
    finished = next(d for t, d in env.events if t == "coding.finished")
    assert "gemergt" in finished["summary"]


async def test_run_red_job_pushes_nothing(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    before = _origin_main(env)
    jid = await _run_job(env, monkeypatch, tests_pass=False)
    job = env.store[jid]

    assert job.status == "failed"
    assert job.result["ship"]["state"] == "not_shipped"
    assert env.gh.created == [] and env.gh.merged == []
    assert _origin_branches(env) == ["main"] and _origin_main(env) == before
    finished = next(d for t, d in env.events if t == "coding.finished")
    assert "Nichts gepusht, gemergt oder deployt" in finished["summary"]
    assert BRANCH in _git(env.repo, "branch", "--list", BRANCH)


async def test_run_with_auto_ship_off_does_not_touch_remote(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    env.cfg.auto_ship = False
    jid = await _run_job(env, monkeypatch, tests_pass=True)

    assert env.store[jid].status == "completed" and "ship" not in env.store[jid].result
    assert env.gh.created == [] and _origin_branches(env) == ["main"]
