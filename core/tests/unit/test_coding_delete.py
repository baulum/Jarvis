"""coding_delete_job: only failed/cancelled jobs, branch always kept, worktree only on request."""

import subprocess
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import jarvis.coding.runner as runner_mod
from jarvis.coding.runner import CodingError, CodingRunner
from jarvis.connectors.coding import CodingConnector
from jarvis.tools.executor import ToolUserError


class FakeSession:
    def __init__(self, store: dict[uuid.UUID, Any]) -> None:
        self.store = store

    async def get(self, model: Any, key: uuid.UUID) -> Any:
        return self.store.get(key)

    async def delete(self, obj: Any) -> None:
        self.store.pop(obj.id, None)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    store: dict[uuid.UUID, Any] = {}
    events: list[str] = []

    @asynccontextmanager
    async def scope():  # type: ignore[no-untyped-def]
        yield FakeSession(store)

    async def audit(*a: Any, **k: Any) -> None:
        return None

    async def publish(type_: str, **data: Any) -> None:
        events.append(type_)

    monkeypatch.setattr(runner_mod, "session_scope", scope)
    monkeypatch.setattr(runner_mod, "audit", audit)
    monkeypatch.setattr(runner_mod.bus, "publish", publish)

    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    settings = SimpleNamespace(
        coding=SimpleNamespace(max_parallel_jobs=1),
        paths=SimpleNamespace(workspaces_dir=workspaces, data_dir=tmp_path / "data"),
    )
    runner = CodingRunner(SimpleNamespace(settings=settings))  # type: ignore[arg-type]
    return SimpleNamespace(store=store, events=events, runner=runner, workspaces=workspaces, tmp=tmp_path)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def _job(env: SimpleNamespace, status: str, worktree: str = "", branch: str = "", repo: str = "") -> uuid.UUID:
    jid = uuid.uuid4()
    env.store[jid] = SimpleNamespace(id=jid, status=status, repo_path=repo, worktree_path=worktree, branch=branch)
    return jid


def _make_worktree(env: SimpleNamespace) -> tuple[Path, Path, str]:
    repo = env.tmp / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
    wt, branch = env.workspaces / "job", "jarvis/job"
    _git(repo, "worktree", "add", "-q", "-b", branch, str(wt), "HEAD")
    return repo, wt, branch


@pytest.mark.parametrize("status", ["failed", "cancelled"])
async def test_deletes_failed_and_cancelled(env: SimpleNamespace, status: str) -> None:
    jid = _job(env, status)
    res = await env.runner.delete(jid, "test")
    assert res["deleted"] is True and res["worktree_removed"] is False
    assert jid not in env.store
    assert env.events == ["coding.deleted"]


@pytest.mark.parametrize("status", ["queued", "running", "completed"])
async def test_rejects_other_statuses(env: SimpleNamespace, status: str) -> None:
    jid = _job(env, status)
    with pytest.raises(CodingError, match="failed/cancelled"):
        await env.runner.delete(jid, "test")
    assert jid in env.store


async def test_unknown_job(env: SimpleNamespace) -> None:
    with pytest.raises(CodingError, match="nicht gefunden"):
        await env.runner.delete(uuid.uuid4(), "test")


async def test_keeps_worktree_and_branch_by_default(env: SimpleNamespace) -> None:
    repo, wt, branch = _make_worktree(env)
    jid = _job(env, "failed", str(wt), branch, str(repo))
    res = await env.runner.delete(jid, "test")
    assert res["worktree_removed"] is False
    assert wt.exists()
    assert branch in _git(repo, "branch", "--list", branch)


async def test_removes_worktree_only_on_request_branch_stays(env: SimpleNamespace) -> None:
    repo, wt, branch = _make_worktree(env)
    (wt / "dirty.txt").write_text("x")
    jid = _job(env, "cancelled", str(wt), branch, str(repo))
    res = await env.runner.delete(jid, "test", delete_worktree=True)
    assert res["worktree_removed"] is True
    assert not wt.exists()
    assert branch in _git(repo, "branch", "--list", branch)
    assert jid not in env.store


async def test_refuses_worktree_outside_workspace(env: SimpleNamespace) -> None:
    outside = env.tmp / "elsewhere"
    outside.mkdir()
    jid = _job(env, "failed", str(outside), "b", str(env.tmp))
    with pytest.raises(CodingError, match="außerhalb"):
        await env.runner.delete(jid, "test", delete_worktree=True)
    assert outside.exists() and jid in env.store


async def test_tool_registered_and_maps_errors(env: SimpleNamespace) -> None:
    conn = CodingConnector.__new__(CodingConnector)
    conn.state = SimpleNamespace(coding=env.runner)  # type: ignore[assignment]
    spec = next(t for t in conn.tools() if t.name == "coding_delete_job")
    assert spec.parameters["required"] == ["job_id"]
    assert spec.parameters["properties"]["delete_worktree"] == {"type": "boolean", "default": False}
    ctx = SimpleNamespace(principal=SimpleNamespace(label="test"))
    jid = _job(env, "completed")
    with pytest.raises(ToolUserError, match="completed"):
        await spec.handler(ctx, {"job_id": str(jid)})  # type: ignore[arg-type]
    jid = _job(env, "failed")
    assert (await spec.handler(ctx, {"job_id": str(jid)}))["deleted"] is True  # type: ignore[arg-type]
