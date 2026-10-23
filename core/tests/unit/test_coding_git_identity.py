"""Commits of coding jobs carry Sir's global git identity, never a made-up address like jarvis@localhost (Vercel blocks
deployments whose commit author email is not a real address)."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.coding.runner import DEFAULT_GIT_EMAIL, CodingRunner, git_identity, valid_git_email


@pytest.fixture
def global_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated global git config (``git config --global`` reads $GIT_CONFIG_GLOBAL)."""
    path = tmp_path / "gitconfig"
    path.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(path))
    monkeypatch.delenv("JARVIS_GIT_EMAIL", raising=False)
    monkeypatch.delenv("JARVIS_GIT_NAME", raising=False)
    return path


def _set_global(path: Path, key: str, value: str) -> None:
    subprocess.run(["git", "config", "--file", str(path), key, value], check=True)


@pytest.mark.parametrize("email", ["paulhoflich@gmail.com", "a.b+c@mail.example.de"])
def test_valid_email(email: str) -> None:
    assert valid_git_email(email)


@pytest.mark.parametrize("email", ["jarvis@localhost", "t@t", "x@host.local1", "no-at.com", "a b@c.de", "", None])
def test_invalid_email(email: str | None) -> None:
    assert not valid_git_email(email)


def test_global_identity_wins(global_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_global(global_config, "user.name", "Paul")
    _set_global(global_config, "user.email", "paul@example.com")
    monkeypatch.setenv("JARVIS_GIT_EMAIL", "other@example.com")
    assert git_identity() == ("Paul", "paul@example.com")


def test_env_fallback_without_global_identity(global_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_GIT_EMAIL", "jarvis@example.com")
    monkeypatch.setenv("JARVIS_GIT_NAME", "Jarvis")
    assert git_identity() == ("Jarvis", "jarvis@example.com")


def test_invalid_addresses_fall_back_to_the_default(global_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_global(global_config, "user.email", "jarvis@localhost")
    monkeypatch.setenv("JARVIS_GIT_EMAIL", "jarvis@localhost")
    name, email = git_identity()
    assert email == DEFAULT_GIT_EMAIL and name


def test_runner_env_carries_the_global_identity(global_config: Path, tmp_path: Path) -> None:
    """Runner's own commits (initial, WIP, final) and the agent's commits all get the identity via the git env."""
    _set_global(global_config, "user.name", "Paul")
    _set_global(global_config, "user.email", "paulhoflich@gmail.com")
    settings = SimpleNamespace(
        coding=SimpleNamespace(max_parallel_jobs=1),
        paths=SimpleNamespace(workspaces_dir=tmp_path / "ws", data_dir=tmp_path / "data"),
    )
    runner = CodingRunner(SimpleNamespace(settings=settings))  # type: ignore[arg-type]
    env = runner._env()
    assert env["GIT_AUTHOR_NAME"] == env["GIT_COMMITTER_NAME"] == "Paul"
    assert env["GIT_AUTHOR_EMAIL"] == env["GIT_COMMITTER_EMAIL"] == "paulhoflich@gmail.com"


async def test_initial_commit_author_is_a_real_address(global_config: Path, tmp_path: Path) -> None:
    settings = SimpleNamespace(
        coding=SimpleNamespace(max_parallel_jobs=1),
        paths=SimpleNamespace(workspaces_dir=tmp_path / "ws", data_dir=tmp_path / "data"),
    )
    runner = CodingRunner(SimpleNamespace(settings=settings))  # type: ignore[arg-type]
    repo = tmp_path / "proj"
    await runner.ensure_repo(repo)
    author, committer = (await runner._git(repo, "log", "-1", "--format=%ae%n%ce")).splitlines()
    assert author == committer == DEFAULT_GIT_EMAIL  # empty global config → default
    assert valid_git_email(author) and "localhost" not in author
