"""Coding jobs for customer sites work in the customer folder; other projects keep their own worktree."""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.coding import runner as runner_mod
from jarvis.coding.runner import CodingRunner, is_customer_site


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True, capture_output=True
    )


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-b", "main")
    (path / "index.html").write_text("hi")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "init")
    return path.resolve()


@pytest.fixture
def sites(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "Kunden-Seiten"
    root.mkdir()
    monkeypatch.setattr(runner_mod, "CUSTOMER_SITES_DIR", root)
    return root


def _runner(tmp_path: Path) -> CodingRunner:
    settings = SimpleNamespace(
        coding=SimpleNamespace(max_parallel_jobs=1),
        paths=SimpleNamespace(
            workspaces_dir=tmp_path / "ws", data_dir=tmp_path / "data", projects_dir=tmp_path / "projects"
        ),
    )
    return CodingRunner(SimpleNamespace(settings=settings))  # type: ignore[arg-type]


def test_is_customer_site(sites: Path, tmp_path: Path) -> None:
    assert is_customer_site(sites / "Metzger & Mpolo GbR")
    assert not is_customer_site(sites)
    assert not is_customer_site(tmp_path / "other" / "proj")


async def test_customer_site_job_works_in_the_customer_folder(sites: Path, tmp_path: Path) -> None:
    repo = _repo(sites / "Metzger & Mpolo GbR")
    worktree, branch = await _runner(tmp_path)._new_worktree(uuid.uuid4(), repo, "Fix")
    assert Path(worktree) == repo
    assert branch == "main"
    assert not (tmp_path / "ws").exists()


async def test_normal_project_gets_its_own_worktree(sites: Path, tmp_path: Path) -> None:
    repo = _repo(tmp_path / "projects" / "demo")
    job_id = uuid.uuid4()
    runner = _runner(tmp_path)
    worktree, branch = await runner._new_worktree(job_id, repo, "Fix")
    assert Path(worktree).parent == tmp_path / "ws"
    assert Path(worktree) != repo
    assert branch == f"jarvis/fix-{str(job_id)[:8]}"
    assert Path(worktree).is_dir()
