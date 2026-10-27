"""Automations: the device's counterpart to Apple Shortcuts.

Windows and Linux have no system-wide "Kurzbefehle" a program could list and run. The agent therefore runs the
scripts the user put into its ``automations`` folder (``jarvis-device automations`` shows where): ``.ps1``, ``.cmd``,
``.bat``, ``.py`` and ``.exe`` on Windows; executable files, ``.sh`` and ``.py`` on Linux and macOS. On a Mac the
user's Apple Shortcuts come on top (through the JarvisBridge, see the backend).

The folder lies inside the agent's configuration directory, which the path policy protects: the Core can run what
the user placed there, but it can never write a script into it.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

WINDOWS_KINDS = (".ps1", ".cmd", ".bat", ".py", ".exe")
POSIX_KINDS = (".sh", ".py")
OUTPUT_TAIL = 8000


def folder(config_dir: Path) -> Path:
    return config_dir / "automations"


def _is_windows(platform: str) -> bool:
    return platform.startswith("win")


def list_scripts(root: Path, platform: str = sys.platform) -> list[dict[str, Any]]:
    """Runnable scripts in ``root`` (not recursive, hidden files skipped), sorted by name."""
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for f in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if f.name.startswith(".") or not f.is_file():
            continue
        suffix = f.suffix.lower()
        if _is_windows(platform):
            ok = suffix in WINDOWS_KINDS
        else:
            ok = suffix in POSIX_KINDS or (not suffix and os.access(f, os.X_OK))
        if ok:
            out.append({"name": f.stem, "file": f.name, "kind": "script", "type": suffix.lstrip(".") or "exe"})
    return out


def find_script(root: Path, name: str, platform: str = sys.platform) -> Path | None:
    """The script called ``name`` (stem or full file name, case-insensitive). Ambiguous stems need the file name."""
    want = name.strip().casefold()
    if not want or any(sep in want for sep in ("/", "\\", "\x00")) or want in (".", ".."):
        return None
    scripts = list_scripts(root, platform)
    exact = [s for s in scripts if s["file"].casefold() == want]
    if exact:
        return root / exact[0]["file"]
    by_stem = [s for s in scripts if s["name"].casefold() == want]
    if len(by_stem) > 1:
        files = ", ".join(s["file"] for s in by_stem)
        raise ValueError(f"Mehrere Automationen heißen „{name}“ – Dateinamen angeben ({files})")
    return root / by_stem[0]["file"] if by_stem else None


def argv_for(script: Path, platform: str = sys.platform, which: Callable[[str], str | None] = shutil.which) -> list[str]:
    suffix = script.suffix.lower()
    if _is_windows(platform):
        if suffix == ".ps1":
            ps = which("powershell.exe") or which("powershell") or which("pwsh")
            if ps is None:
                raise RuntimeError("PowerShell nicht gefunden")
            return [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)]
        if suffix in (".cmd", ".bat"):
            return [which("cmd.exe") or "cmd.exe", "/d", "/c", str(script)]
        if suffix == ".py":
            py = which("py") or which("python") or which("python3")
            if py is None:
                raise RuntimeError("Python nicht gefunden")
            return [py, str(script)]
        return [str(script)]
    if suffix == ".sh":
        return [which("bash") or "/bin/sh", str(script)]
    if suffix == ".py":
        py = which("python3")
        if py is None:
            raise RuntimeError("python3 nicht gefunden")
        return [py, str(script)]
    return [str(script)]


async def run_script(
    script: Path,
    input: str | None,
    *,
    timeout_s: float = 300,
    platform: str = sys.platform,
    which: Callable[[str], str | None] = shutil.which,
) -> dict[str, Any]:
    """Runs a script with ``input`` on stdin and in ``JARVIS_INPUT``; returns exit code and the end of its output."""
    argv = argv_for(script, platform, which)
    env = {**os.environ, "JARVIS_INPUT": input or "", "JARVIS_AUTOMATION": script.stem}
    kwargs: dict[str, Any] = {}
    if _is_windows(platform):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(Path.home()),
        env=env,
        **kwargs,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate((input or "").encode("utf-8")), timeout_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError from None
    return {
        "name": script.stem,
        "kind": "script",
        "exit_code": proc.returncode,
        "ok": proc.returncode == 0,
        "output": out.decode("utf-8", errors="replace")[-OUTPUT_TAIL:],
        "stderr": err.decode("utf-8", errors="replace")[-OUTPUT_TAIL // 2 :],
    }
