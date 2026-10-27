"""Test command of a coding job: runs without a shell, ``&&`` chains steps, each step must be an allowed program."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.coding.runner import CodingError, CodingRunner, split_test_command


def _runner(tmp_path: Path) -> CodingRunner:
    runner = CodingRunner.__new__(CodingRunner)
    runner.cfg = SimpleNamespace()
    runner.workspaces = tmp_path
    return runner


def test_and_chain_is_split_into_steps_not_passed_as_argument() -> None:
    steps = split_test_command("pnpm --dir desktop install --frozen-lockfile && pnpm --dir desktop test")
    assert steps == [["pnpm", "--dir", "desktop", "install", "--frozen-lockfile"], ["pnpm", "--dir", "desktop", "test"]]
    assert split_test_command("npm ci&&npm test") == [["npm", "ci"], ["npm", "test"]]
    assert split_test_command("pytest -k 'a and b'") == [["pytest", "-k", "a and b"]]
    assert split_test_command('python3 -c "import sys; sys.exit(0)"') == [["python3", "-c", "import sys; sys.exit(0)"]]


@pytest.mark.parametrize(
    "command",
    [
        "pnpm test && rm -rf build",
        "pnpm test ; pnpm lint",
        "pnpm test || true",
        "pnpm test | tee log",
        "pnpm test &&",
        "",
    ],
)
def test_other_operators_and_disallowed_steps_are_refused(command: str) -> None:
    with pytest.raises(CodingError):
        split_test_command(command)


async def test_steps_run_in_order_and_stop_at_the_first_failure(tmp_path: Path) -> None:
    cmd = (
        "python3 -c \"print('eins')\" && python3 -c \"import sys; print('zwei'); sys.exit(3)\" "
        "&& python3 -c \"print('nie')\""
    )
    res = await _runner(tmp_path)._run_tests(tmp_path, cmd)
    assert res["passed"] is False
    assert res["exit_code"] == 3
    assert "eins" in res["output"] and "zwei" in res["output"]
    assert "nie\n" not in res["output"]


@pytest.mark.coding_runner
async def test_all_steps_green(tmp_path: Path) -> None:
    res = await _runner(tmp_path)._run_tests(tmp_path, "python3 -c \"print('a')\" && python3 -c \"print('b')\"")
    assert res == {"passed": True, "exit_code": 0, "output": res["output"]}
    assert "a\n" in res["output"] and "b\n" in res["output"]


@pytest.mark.coding_runner
async def test_worktree_src_leads_pythonpath_and_import_path_is_logged(tmp_path: Path, monkeypatch) -> None:
    pkg = tmp_path / "core" / "src" / "jarvis"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    monkeypatch.setenv("PYTHONPATH", "/somewhere/else")
    res = await _runner(tmp_path)._run_tests(
        tmp_path, "python3 -c \"import os; print(os.environ['PYTHONPATH'])\""
    )
    assert res["passed"] is True
    assert res["output"].startswith(f"{tmp_path / 'core' / 'src'}:/somewhere/else")
    assert f"[jarvis geladen aus: {pkg / '__init__.py'}]" in res["output"]


async def test_invalid_stored_command_fails_without_running(tmp_path: Path) -> None:
    res = await _runner(tmp_path)._run_tests(tmp_path, "pnpm test ; touch pwned")
    assert res["passed"] is False and res["exit_code"] is None
    assert not (tmp_path / "pwned").exists()


@pytest.mark.coding_runner
async def test_timeout_covers_the_whole_chain(tmp_path: Path) -> None:
    cmd = 'python3 -c "print(\'a\')" && python3 -c "import time; time.sleep(5)"'
    res = await _runner(tmp_path)._run_tests(tmp_path, cmd, timeout_s=1)
    assert res["passed"] is False and res["exit_code"] is None
    assert "Timeout" in res["output"]
