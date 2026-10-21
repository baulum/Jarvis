"""Coding worker directory scope: worktree only by default, exactly two configured extras otherwise."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.coding.runner import CodingRunner


@pytest.fixture
def repos(tmp_path: Path) -> Path:
    # read dirs that don't exist are skipped, so the fixture must create a real directory
    d = tmp_path / "repos"
    d.mkdir()
    return d.resolve()


@pytest.fixture
def motion(tmp_path: Path) -> Path:
    d = tmp_path / "night-motion-graphics"
    d.mkdir()
    return d.resolve()


def _argv(tmp_path: Path, **cfg: object) -> list[str]:
    defaults: dict[str, object] = {
        "max_parallel_jobs": 1,
        "claude_command": "sh",  # any resolvable program; only the flags after it matter here
        "sandbox": False,
        "allowed_tools": [],
        "extra_read_dirs": [],
        "extra_write_dirs": [],
    }
    coding = SimpleNamespace(**{**defaults, **cfg})
    settings = SimpleNamespace(
        coding=coding, paths=SimpleNamespace(workspaces_dir=tmp_path, data_dir=tmp_path / "data")
    )
    state = SimpleNamespace(settings=settings, skills=SimpleNamespace(plugin_dir=tmp_path))
    return CodingRunner(state)._claude_argv("x", None)  # type: ignore[arg-type]


def _added(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv) if a == "--add-dir"]


def _settings(argv: list[str]) -> dict:
    return json.loads(argv[argv.index("--settings") + 1])


def test_default_grants_no_extra_directories(tmp_path: Path) -> None:
    argv = _argv(tmp_path)
    assert _added(argv) == []
    assert "permissions" not in _settings(argv)


def test_only_the_two_paths_are_added(tmp_path: Path, repos: Path, motion: Path) -> None:
    argv = _argv(tmp_path, extra_read_dirs=[repos], extra_write_dirs=[motion])
    assert _added(argv) == [str(repos), str(motion)]
    # no home dir, root or wildcard grants
    assert all("*" not in d and d not in ("/", str(Path.home())) for d in _added(argv))


def test_missing_read_dir_is_skipped(tmp_path: Path) -> None:
    argv = _argv(tmp_path, extra_read_dirs=[tmp_path / "missing"])
    assert _added(argv) == []
    assert "permissions" not in _settings(argv)


def test_read_dir_is_write_protected_but_write_dir_is_not(tmp_path: Path, repos: Path, motion: Path) -> None:
    deny = _settings(_argv(tmp_path, extra_read_dirs=[repos], extra_write_dirs=[motion]))["permissions"]["deny"]
    assert f"Edit(/{repos}/**)" in deny and f"Write(/{repos}/**)" in deny
    assert not any(str(motion) in d for d in deny)


def test_sandbox_allows_writes_only_to_write_dir(tmp_path: Path, repos: Path, motion: Path) -> None:
    fs = _settings(_argv(tmp_path, sandbox=True, extra_read_dirs=[repos], extra_write_dirs=[motion]))["sandbox"][
        "filesystem"
    ]
    assert str(motion) in fs["allowWrite"] and str(repos) not in fs["allowWrite"]
