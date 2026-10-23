"""Standard flow after a coding job: green → push, PR, merge, Supabase deploy, then the job branch (remote and local)
and the worktree are removed; red / error / cancelled → open work is committed (WIP) and the job branch pushed, but
nothing reaches main or Supabase and the worktree stays.

Real git (a bare "origin" whose path contains github.com/acme/app), a fake GitHub connector that merges into that
origin, and a fake ``supabase`` executable that logs its arguments.
"""

import asyncio
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
        self.merge_attempts = 0
        self.merge_error: Exception | None = None  # every attempt fails with it
        self.merge_errors: list[Exception] = []  # the next attempts fail with these, one each
        self.create_error: Exception | None = None
        self.delete_branch_on_merge = False  # GitHub's "Automatically delete head branches"

    async def create_pr(
        self, repo: str, head: str, base: str, title: str, body: str, draft: bool = True
    ) -> dict[str, Any]:
        if self.create_error is not None:
            raise self.create_error
        self.created.append({"repo": repo, "head": head, "base": base, "title": title, "draft": draft})
        return {"number": 7, "html_url": f"https://github.com/{repo}/pull/7"}

    async def merge_pr(self, repo: str, number: int, *, sha: str | None = None, method: str = "squash") -> Any:
        self.merge_attempts += 1
        if self.merge_errors:
            raise self.merge_errors.pop(0)
        if self.merge_error is not None:
            raise self.merge_error
        self.merged.append({"repo": repo, "number": number, "sha": sha, "method": method})
        _git(self.origin, "update-ref", "refs/heads/main", sha or "")  # what the merge does on GitHub
        if self.delete_branch_on_merge:
            _git(self.origin, "update-ref", "-d", f"refs/heads/{BRANCH}")
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
        ship_via_pr=True,
        supabase_command=str(tmp_path / "bin" / "supabase"),
        supabase_timeout_minutes=1,
        job_timeout_minutes=1,
    )
    state = SimpleNamespace(
        settings=SimpleNamespace(coding=cfg, paths=SimpleNamespace(workspaces_dir=workspaces, data_dir=tmp_path / "d")),
        connectors=SimpleNamespace(connectors={"github": gh}),
        kill_switch=SimpleNamespace(engaged=False),
    )
    runner = CodingRunner(state)  # type: ignore[arg-type]
    runner.merge_retry_delays = (0, 0)
    return SimpleNamespace(
        runner=runner,
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


def _local_branches(env: SimpleNamespace) -> list[str]:
    return _git(env.repo, "branch", "--format=%(refname:short)").split()


def _assert_cleaned_up(env: SimpleNamespace, ship: dict[str, Any]) -> None:
    """After the merge nothing of the job is left: remote branch, local branch and worktree are gone."""
    assert ship["cleanup"] == {"remote_branch": True, "worktree": True, "local_branch": True, "errors": []}
    assert BRANCH not in _origin_branches(env)
    assert BRANCH not in _local_branches(env)
    assert not env.wt.exists()
    assert str(env.wt) not in _git(env.repo, "worktree", "list")
    assert "Job-Branch (lokal und auf origin) und Worktree entfernt" in ship["message"]


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
    assert ship["pushed"] is True
    assert _origin_main(env) == head
    _assert_cleaned_up(env, ship)
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
    _assert_cleaned_up(env, ship)


async def test_green_cleanup_when_github_already_deleted_the_branch(env: SimpleNamespace) -> None:
    head = _commit(env, {"app.py": "print(1)\n"})
    env.gh.delete_branch_on_merge = True
    ship = await _flow(env)

    assert ship["state"] == "shipped" and _origin_main(env) == head
    _assert_cleaned_up(env, ship)


async def test_green_cleanup_keeps_worktree_with_unmerged_changes(env: SimpleNamespace) -> None:
    _commit(env, {"app.py": "print(1)\n"})
    real_deploy = env.runner._ship_deploy

    async def deploy_then_edit(ship: dict[str, Any], repo: Path) -> None:
        await real_deploy(ship, repo)
        (env.wt / "late.txt").write_text("nach dem Merge\n")  # must not be thrown away

    env.runner._ship_deploy = deploy_then_edit
    ship = await _flow(env)

    assert ship["state"] == "shipped"
    assert ship["cleanup"]["worktree"] is False and ship["cleanup"]["local_branch"] is False
    assert (env.wt / "late.txt").exists() and BRANCH in _local_branches(env)
    assert "Aufräumen unvollständig" in ship["message"]


async def test_green_without_changes_ships_nothing(env: SimpleNamespace) -> None:
    before = _origin_main(env)
    ship = await _flow(env)

    assert ship["state"] == "not_shipped" and "keine Änderungen" in ship["message"]
    assert env.gh.created == [] and env.gh.merged == []
    assert _origin_main(env) == before
    _assert_cleaned_up(env, ship)  # main already has everything: nothing can get lost


async def test_merge_is_retried_while_github_is_not_ready(env: SimpleNamespace) -> None:
    head = _commit(env, {"app.py": "print(1)\n"})
    env.gh.merge_errors = [
        ToolUserError("GitHub-Fehler 405: Pull Request is not mergeable"),
        ToolUserError('GitHub-Fehler 405: Required status check "Core (ruff, mypy, unit tests)" is expected.'),
    ]
    ship = await _flow(env)

    assert env.gh.merge_attempts == 3
    assert ship["state"] == "shipped" and _origin_main(env) == head
    _assert_cleaned_up(env, ship)


async def test_merge_is_not_retried_when_a_review_is_required(env: SimpleNamespace) -> None:
    _commit(env, {"app.py": "print(1)\n"})
    env.gh.merge_error = ToolUserError("GitHub-Fehler 405: At least 1 approving review is required")
    ship = await _flow(env)

    assert env.gh.merge_attempts == 1
    assert ship["state"] == "failed" and ship["stage"] == "merge" and "review" in ship["message"]
    assert env.wt.exists() and BRANCH in _local_branches(env)


async def test_unexpected_github_error_is_reported_not_raised(env: SimpleNamespace) -> None:
    _commit(env, {"app.py": "print(1)\n"})
    env.gh.create_error = OSError("Network is unreachable")
    ship = await _flow(env)

    assert ship["state"] == "failed" and ship["stage"] == "pr"
    assert "Network is unreachable" in ship["message"]
    assert env.wt.exists() and BRANCH in _origin_branches(env)  # pushed before the PR: nothing lost


# ---------------------------------------------------------------- red / errors: branch backed up, never main
async def test_red_backs_up_the_branch_but_never_main(env: SimpleNamespace) -> None:
    head = _commit(env, SUPABASE_FILES)
    _fake_supabase(env)
    before = _origin_main(env)
    ship = await _flow(env, green=False)

    assert ship["state"] == "not_shipped" and ship["merged"] is False and ship["pushed"] is True
    assert "Tests sind rot" in ship["message"] and "Nichts in main gemergt oder deployt" in ship["message"]
    assert f"Job-Branch {BRANCH} auf origin gesichert" in ship["message"]
    assert env.gh.created == [] and env.gh.merged == []
    assert sorted(_origin_branches(env)) == sorted([BRANCH, "main"])
    assert _git(env.origin, "rev-parse", BRANCH) == head
    assert _origin_main(env) == before
    assert not env.supabase_log.exists()
    assert BRANCH in _local_branches(env) and not env.wt.exists()  # branch stays, worktree removed after backup
    assert "cleanup" not in ship


async def test_red_without_changes_pushes_nothing(env: SimpleNamespace) -> None:
    ship = await _flow(env, green=False)

    assert ship["pushed"] is False and "nichts zu sichern" in ship["message"]
    assert _origin_branches(env) == ["main"]


async def test_red_backup_failure_is_named(env: SimpleNamespace) -> None:
    _commit(env, {"app.py": "print(1)\n"})
    _git(env.wt, "remote", "remove", "origin")
    ship = await _flow(env, green=False)

    assert ship["pushed"] is False and "kein 'origin'-Remote" in ship["backup_error"]
    assert "Sichern des Job-Branch fehlgeschlagen" in ship["message"] and env.wt.exists()


async def test_red_with_kill_switch_stays_local(env: SimpleNamespace) -> None:
    _commit(env, {"app.py": "print(1)\n"})
    env.runner.state.kill_switch.engaged = True
    ship = await _flow(env, green=False)

    assert ship["pushed"] is False and "Not-Aus" in ship["message"]
    assert _origin_branches(env) == ["main"]


async def test_missing_merge_permission_merges_and_deploys_nothing(env: SimpleNamespace) -> None:
    _commit(env, SUPABASE_FILES)
    _fake_supabase(env)
    before = _origin_main(env)
    env.gh.merge_error = ToolUserError("GitHub-Fehler 403: Resource not accessible by personal access token")
    ship = await _flow(env)

    assert ship["state"] == "failed" and ship["stage"] == "merge" and ship["merged"] is False
    assert "403" in ship["message"] and "https://github.com/acme/app/pull/7" in ship["message"]
    assert env.gh.merge_attempts == 1  # missing permission is not waited for
    assert _origin_main(env) == before
    assert not env.supabase_log.exists()
    assert "cleanup" not in ship and env.wt.exists() and BRANCH in _origin_branches(env)


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
async def _write_app(env: SimpleNamespace) -> dict[str, Any]:
    (env.wt / "app.py").write_text("print(1)\n")
    return {"is_error": False, "result": "Fertig", "session_id": "s"}


def _prepare_job(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, *, tests_pass: bool, agent: Any = None
) -> uuid.UUID:
    async def auth() -> dict[str, Any]:
        return {"ok": True}

    async def run_agent(*a: Any, **k: Any) -> dict[str, Any]:
        return await (agent or _write_app)(env)

    async def tests(cwd: Path, command: str) -> dict[str, Any]:
        return {"passed": tests_pass, "exit_code": 0 if tests_pass else 1, "output": "433 passed" if tests_pass else "1 failed"}

    monkeypatch.setattr(env.runner, "auth_status", auth)
    monkeypatch.setattr(env.runner, "_claude_retrying", run_agent)
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
    return jid


async def _run_job(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, *, tests_pass: bool, agent: Any = None
) -> uuid.UUID:
    jid = _prepare_job(env, monkeypatch, tests_pass=tests_pass, agent=agent)
    await env.runner._run(jid)
    return jid


def _finished(env: SimpleNamespace) -> dict[str, Any]:
    return next(d for t, d in env.events if t == "coding.finished")


async def test_run_green_job_is_shipped(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    jid = await _run_job(env, monkeypatch, tests_pass=True)
    job = env.store[jid]

    assert job.status == "completed"
    assert job.result["ship"]["state"] == "shipped"
    assert job.result["commit"] and job.result["commits"] == 1 and "app.py" in job.result["diffstat"]
    assert _git(env.origin, "rev-parse", "--short", "main") == job.result["commit"]
    assert job.result["delivery_error"] is None
    _assert_cleaned_up(env, job.result["ship"])
    assert "gemergt" in _finished(env)["summary"]


async def test_run_green_job_committed_by_the_agent_is_detected_and_shipped(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Job dd167cb9: the agent committed itself, the runner had nothing left to commit → ``commit`` was null."""

    async def agent_commits(env: SimpleNamespace) -> dict[str, Any]:
        _commit(env, {"tests/test_dirs.py": "def test_ok(): pass\n"})
        return {"is_error": False, "result": "Fertig. Nichts gepusht oder gemergt.", "session_id": "s"}

    jid = await _run_job(env, monkeypatch, tests_pass=True, agent=agent_commits)
    job = env.store[jid]

    assert job.status == "completed"
    assert job.result["commit"] is not None and job.result["commits"] == 1
    assert "tests/test_dirs.py" in job.result["diffstat"]
    assert job.result["ship"]["state"] == "shipped" and env.gh.merged
    assert _git(env.origin, "rev-parse", "--short", "main") == job.result["commit"]
    _assert_cleaned_up(env, job.result["ship"])


async def test_run_red_job_commits_wip_and_pushes_branch_not_main(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _origin_main(env)
    jid = await _run_job(env, monkeypatch, tests_pass=False)
    job = env.store[jid]

    assert job.status == "failed"
    assert job.result["ship"]["state"] == "not_shipped" and job.result["ship"]["pushed"] is True
    assert env.gh.created == [] and env.gh.merged == []
    assert _origin_main(env) == before
    assert _git(env.origin, "rev-parse", BRANCH) == _git(env.repo, "rev-parse", BRANCH)
    assert _git(env.repo, "log", "-1", "--format=%s", BRANCH).startswith("Jarvis (WIP): Feature")
    assert job.result["commit"] == _git(env.repo, "rev-parse", "--short", BRANCH)
    summary = _finished(env)["summary"]
    assert "Nichts in main gemergt oder deployt" in summary and "auf origin gesichert" in summary
    assert BRANCH in _local_branches(env) and not env.wt.exists()


async def test_run_aborted_job_commits_and_pushes_its_work(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Job 341f33f5: the run ended with an exception – the changes were never committed and got lost."""

    async def agent_times_out(env: SimpleNamespace) -> dict[str, Any]:
        (env.wt / "half.py").write_text("x = 1\n")
        raise runner_mod.CodingError("Zeitlimit von 1 Minuten überschritten")

    before = _origin_main(env)
    jid = await _run_job(env, monkeypatch, tests_pass=True, agent=agent_times_out)
    job = env.store[jid]

    assert job.status == "failed" and "Zeitlimit" in job.result["error"]
    assert job.result["ship"]["pushed"] is True and "Der Job ist abgebrochen" in job.result["ship"]["message"]
    assert _git(env.origin, "show", f"{BRANCH}:half.py") == "x = 1"
    assert _origin_main(env) == before and env.gh.created == []
    assert not env.wt.exists()


async def test_run_red_job_names_a_failed_backup(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    _git(env.repo, "remote", "set-url", "origin", str(env.tmp / "github.com" / "acme" / "gone.git"))
    jid = await _run_job(env, monkeypatch, tests_pass=False)
    job = env.store[jid]

    assert job.status == "failed"
    assert "Sichern des Job-Branch fehlgeschlagen" in job.result["delivery_error"]
    assert _git(env.wt, "log", "-1", "--format=%s").startswith("Jarvis (WIP)")  # committed locally at least


async def test_run_green_job_with_ship_error_keeps_the_result(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.gh.create_error = OSError("Network is unreachable")
    jid = await _run_job(env, monkeypatch, tests_pass=True)
    job = env.store[jid]

    assert job.status == "completed" and job.result["summary"] == "Fertig" and job.result["tests"]["passed"]
    assert job.result["ship"]["state"] == "failed"
    assert "Network is unreachable" in job.result["delivery_error"]


async def test_cancelled_job_commits_and_pushes_its_work(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    started = asyncio.Event()

    async def agent_hangs(env: SimpleNamespace) -> dict[str, Any]:
        (env.wt / "wip.py").write_text("y = 2\n")
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("not reached")

    jid = _prepare_job(env, monkeypatch, tests_pass=True, agent=agent_hangs)
    before = _origin_main(env)
    env.runner._start(jid)
    task = env.runner._tasks[jid]
    await asyncio.wait_for(started.wait(), 10)
    assert await env.runner.cancel(jid, "test") is True
    with pytest.raises(asyncio.CancelledError):
        await task
    job = env.store[jid]

    assert job.status == "cancelled"
    assert job.result["ship"]["pushed"] is True and "abgebrochen" in job.result["ship"]["message"]
    assert _git(env.origin, "show", f"{BRANCH}:wip.py") == "y = 2"
    assert _origin_main(env) == before and env.gh.created == []
    assert not env.wt.exists()


async def test_run_with_auto_ship_off_does_not_touch_remote(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    env.cfg.auto_ship = False
    jid = await _run_job(env, monkeypatch, tests_pass=True)

    assert env.store[jid].status == "completed" and "ship" not in env.store[jid].result
    assert env.gh.created == [] and _origin_branches(env) == ["main"]
    assert env.store[jid].result["commit"] == _git(env.wt, "rev-parse", "--short", "HEAD")


# ---------------------------------------------------------------- Vercel after the merge
class FakeVercelConn:
    def __init__(self, active: bool = True, error: Exception | None = None) -> None:
        self.active = active
        self.settings: dict[str, Any] = {}
        self.error = error
        self.calls: list[tuple[Path, str, str]] = []

    async def deploy_after_merge(self, repo: Path, repo_name: str, base: str) -> dict[str, Any]:
        self.calls.append((repo, repo_name, base))
        if self.error is not None:
            raise self.error
        return {
            "state": "READY",
            "project": "app",
            "deployment_id": "dpl_1",
            "production_url": "https://app.vercel.app",
            "message": "Vercel app deployt: https://app.vercel.app",
        }


async def test_green_with_vercel_json_deploys_to_vercel_after_merge(env: SimpleNamespace) -> None:
    head = _commit(env, {"vercel.json": "{}\n", "index.html": "<h1>hi</h1>\n"})
    vc = FakeVercelConn()
    env.runner.state.connectors.connectors["vercel"] = vc
    ship = await _flow(env)

    assert ship["state"] == "shipped" and _origin_main(env) == head
    assert vc.calls == [(env.repo, "acme/app", "main")]
    assert ship["vercel"]["state"] == "READY" and ship["vercel"]["production_url"] == "https://app.vercel.app"
    assert "Vercel app deployt: https://app.vercel.app" in ship["message"]
    _assert_cleaned_up(env, ship)


async def test_vercel_failure_does_not_undo_the_delivery(env: SimpleNamespace) -> None:
    _commit(env, {"vercel.json": "{}\n"})
    vc = FakeVercelConn(error=ToolUserError("Vercel-Fehler 500: boom"))
    env.runner.state.connectors.connectors["vercel"] = vc
    ship = await _flow(env)

    assert ship["state"] == "shipped" and ship["vercel"]["state"] == "failed"
    assert "Vercel-Deploy fehlgeschlagen: Vercel-Fehler 500: boom" in ship["message"]
    _assert_cleaned_up(env, ship)


async def test_vercel_skipped_without_marker_or_inactive_connector(env: SimpleNamespace) -> None:
    _commit(env, {"app.py": "print(1)\n"})
    vc = FakeVercelConn()
    env.runner.state.connectors.connectors["vercel"] = vc
    ship = await _flow(env)
    assert ship["state"] == "shipped" and "vercel" not in ship and vc.calls == []

    (env.repo / ".vercel").mkdir()
    (env.repo / ".vercel" / "project.json").write_text('{"projectId": "prj_1"}')  # local link, gitignored
    vc.active = False
    ship = {"base": "main", "pr": None, "merged": True}
    await env.runner._ship_deploy(ship, env.repo)
    assert vc.calls == [] and ship["state"] == "shipped" and ship["vercel"]["state"] == "skipped"
    assert "Vercel-Connector ist nicht aktiv" in ship["message"]


def test_coding_jobs_never_see_the_vercel_token(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERCEL_TOKEN", "vcp_x")
    monkeypatch.setenv("JARVIS_SECRET_VERCEL_TOKEN", "vcp_y")
    job_env = env.runner._env()
    assert "VERCEL_TOKEN" not in job_env and "JARVIS_SECRET_VERCEL_TOKEN" not in job_env


async def test_green_ships_directly_to_main_without_pr(env: SimpleNamespace) -> None:
    head = _commit(env, {"a.py": "x = 1\n"})
    env.runner.cfg.ship_via_pr = False
    ship = await _flow(env)

    assert ship["state"] != "failed" and ship["merged"] is True and ship["direct"] is True
    assert env.gh.created == [] and env.gh.merged == []
    assert _origin_main(env) == head
    assert not env.wt.exists() and BRANCH not in _local_branches(env)


# ---------------------------------------------------------------- several jobs at once
def _job_record(env: SimpleNamespace, wt: Path, branch: str, repo: Path | None = None) -> uuid.UUID:
    jid = uuid.uuid4()
    env.store[jid] = SimpleNamespace(
        id=jid,
        status="queued",
        title="Feature",
        prompt="Mach etwas",
        repo_path=str(repo or env.repo),
        test_command="pytest",
        worktree_path=str(wt),
        branch=branch,
        claude_session_id=None,
        model="m",
        effort=None,
        task_id=None,
        result=None,
        finished_at=None,
    )
    return jid


def _second_job(env: SimpleNamespace) -> tuple[uuid.UUID, Path]:
    wt = env.wt.parent / "job2"
    _git(env.repo, "worktree", "add", "-q", "-b", "jarvis/job2", str(wt), "HEAD")
    return _job_record(env, wt, "jarvis/job2"), wt


def _runner_with(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, agent: Any) -> CodingRunner:
    """A fresh runner (the semaphore takes ``max_parallel_jobs`` on creation) with a fake agent and green tests."""
    runner = CodingRunner(env.runner.state)

    async def auth() -> dict[str, Any]:
        return {"ok": True}

    async def tests(cwd: Path, command: str) -> dict[str, Any]:
        return {"passed": True, "exit_code": 0, "output": "ok"}

    monkeypatch.setattr(runner, "auth_status", auth)
    monkeypatch.setattr(runner, "_claude_retrying", agent)
    monkeypatch.setattr(runner, "_run_tests", tests)
    return runner


async def test_jobs_run_at_the_same_time_and_ship_one_after_another(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.cfg.max_parallel_jobs = 2
    env.cfg.ship_via_pr = False
    inside: set[Path] = set()
    both = asyncio.Event()

    async def agent(job_id: uuid.UUID, cwd: Path, *a: Any, **k: Any) -> dict[str, Any]:
        (cwd / f"{cwd.name}.py").write_text("x = 1\n")
        inside.add(cwd)
        if len(inside) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 10)  # only returns if the other job runs at the same time
        return {"is_error": False, "result": "Fertig", "session_id": str(job_id)}

    runner = _runner_with(env, monkeypatch, agent)
    first = _job_record(env, env.wt, BRANCH)
    second, wt2 = _second_job(env)
    await asyncio.gather(runner._run(first), runner._run(second))

    assert inside == {env.wt, wt2}
    for jid in (first, second):
        job = env.store[jid]
        assert job.status == "completed" and job.result["ship"]["state"] == "shipped", job.result
    # the second delivery was rebased onto the first one: main holds the work of both jobs
    assert _git(env.origin, "show", "main:job.py") == "x = 1"
    assert _git(env.origin, "show", "main:job2.py") == "x = 1"
    assert not env.wt.exists() and not wt2.exists()
    assert _local_branches(env) == ["main"]


async def test_jobs_beyond_the_limit_wait_queued(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    env.cfg.auto_ship = False
    started, release = asyncio.Event(), asyncio.Event()
    order: list[Path] = []

    async def agent(job_id: uuid.UUID, cwd: Path, *a: Any, **k: Any) -> dict[str, Any]:
        order.append(cwd)
        if len(order) == 1:
            started.set()
            await asyncio.wait_for(release.wait(), 10)
        return {"is_error": False, "result": "Fertig", "session_id": str(job_id)}

    runner = _runner_with(env, monkeypatch, agent)  # max_parallel_jobs=1
    first = _job_record(env, env.wt, BRANCH)
    second, wt2 = _second_job(env)
    runner._start(first)
    await asyncio.wait_for(started.wait(), 10)
    runner._start(second)
    tasks = list(runner._tasks.values())
    await asyncio.sleep(0.05)

    assert order == [env.wt] and env.store[second].status == "queued"
    assert any(
        t == "coding.progress"
        and d["job_id"] == str(second)
        and d.get("text", "").startswith("Wartet auf einen freien")
        for t, d in env.events
    )
    release.set()
    await asyncio.gather(*tasks)
    assert order == [env.wt, wt2]
    assert env.store[first].status == "completed" and env.store[second].status == "completed"


async def test_jobs_for_the_same_customer_site_wait_for_each_other(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Customer sites are edited in place (no own worktree): two jobs there would trample each other's files."""
    sites = env.tmp / "Kunden-Seiten"
    site = sites / "Acme"
    site.mkdir(parents=True)
    monkeypatch.setattr(runner_mod, "CUSTOMER_SITES_DIR", sites)
    env.cfg.max_parallel_jobs = 3
    runner = CodingRunner(env.runner.state)
    a = _job_record(env, site, "main", repo=site)
    b = _job_record(env, site, "main", repo=site)
    other = _job_record(env, env.wt, BRANCH)

    async def enter(jid: uuid.UUID) -> None:
        async with runner._slot(jid):
            pass

    async with runner._slot(a):
        waiting = asyncio.create_task(enter(b))
        await asyncio.wait_for(enter(other), 5)  # another repository is not held up
        await asyncio.sleep(0.05)
        assert not waiting.done()
    await asyncio.wait_for(waiting, 5)
    assert any(t == "coding.progress" and d.get("job_id") == str(b) for t, d in env.events)
