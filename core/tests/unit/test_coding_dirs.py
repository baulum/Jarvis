"""Coding worker directory scope: worktree only by default, exactly two configured extras otherwise."""

import json
from pathlib import Path
from types import SimpleNamespace

from jarvis.coding.runner import CodingRunner

REPOS = Path("/Users/paul/Developer/Jarvis-Workspaces/repos")
MOTION = Path("/Users/paul/Developer/Projects/night-motion-graphics")


def _argv(tmp_path: Path, **cfg: object) -> list[str]:
    defaults: dict[str, object] = {
        "max_parallel_jobs": 1,
        "claude_binary": "claude",
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


def test_only_the_two_paths_are_added(tmp_path: Path) -> None:
    argv = _argv(tmp_path, extra_read_dirs=[REPOS], extra_write_dirs=[MOTION])
    assert _added(argv) == [str(REPOS), str(MOTION)]
    # no home dir, root or wildcard grants
    assert all("*" not in d and d not in ("/", str(Path.home())) for d in _added(argv))


def test_read_dir_is_write_protected_but_write_dir_is_not(tmp_path: Path) -> None:
    deny = _settings(_argv(tmp_path, extra_read_dirs=[REPOS], extra_write_dirs=[MOTION]))["permissions"]["deny"]
    assert f"Edit(/{REPOS}/**)" in deny and f"Write(/{REPOS}/**)" in deny
    assert not any(str(MOTION) in d for d in deny)


def test_sandbox_allows_writes_only_to_write_dir(tmp_path: Path) -> None:
    fs = _settings(_argv(tmp_path, sandbox=True, extra_read_dirs=[REPOS], extra_write_dirs=[MOTION]))["sandbox"][
        "filesystem"
    ]
    assert str(MOTION) in fs["allowWrite"] and str(REPOS) not in fs["allowWrite"]
