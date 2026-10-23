"""Terminal commands and Claude Code on the device.

``argv`` runs a program directly (no shell). ``command`` is a command line for the device's own shell –
PowerShell on Windows, ``/bin/sh`` elsewhere – because that is what "run this in the terminal" means. Either way
the working directory must lie below the allowed roots, the run is bounded by a timeout, and on timeout the whole
process tree is ended. The Core has already asked Sir (risk ``high``) before a command arrives here.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import psutil

from jarvis_device.security import PathPolicy

MAX_OUT = 64_000
SECRET_ENV_PREFIXES = ("JARVIS_DEVICE_", "JARVIS_SECRET")


def shell_argv(command: str, platform: str | None = None) -> list[str]:
    plat = sys.platform if platform is None else platform
    if plat.startswith("win"):
        ps = shutil.which("powershell.exe") or shutil.which("pwsh") or "powershell.exe"
        return [ps, "-NoProfile", "-NonInteractive", "-Command", command]
    return ["/bin/sh", "-c", command]


def child_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith(SECRET_ENV_PREFIXES)}


def kill_tree(pid: int) -> None:
    try:
        root = psutil.Process(pid)
        procs = [*root.children(recursive=True), root]
    except psutil.Error:
        return
    for p in procs:
        with contextlib.suppress(psutil.Error):
            p.kill()


async def run_command(
    policy: PathPolicy,
    *,
    argv: list[str] | None,
    command: str | None,
    cwd: str | None,
    timeout_s: float,
    input: bytes | None = None,
) -> dict[str, Any]:
    if argv:
        if not all(isinstance(a, str) for a in argv):
            raise ValueError("argv muss eine Liste von Zeichenketten sein")
        exe = shutil.which(argv[0]) or argv[0]
        full = [exe, *argv[1:]]
    elif command:
        full = shell_argv(command)
    else:
        raise ValueError("argv oder command angeben")
    workdir = policy.check(cwd or str(Path.home()), must_exist=True)
    kwargs: dict[str, Any] = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = 0x00000200  # CREATE_NEW_PROCESS_GROUP
    try:
        proc = await asyncio.create_subprocess_exec(
            *full,
            cwd=str(workdir),
            env=child_env(),
            stdin=asyncio.subprocess.PIPE if input is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs,
        )
    except FileNotFoundError as e:
        raise FileNotFoundError(f"Programm nicht gefunden: {full[0]}") from e
    try:
        out, err = await asyncio.wait_for(proc.communicate(input), timeout_s)
        timed_out = False
    except TimeoutError:
        kill_tree(proc.pid)
        out, err = await proc.communicate()
        timed_out = True
    return {
        "exit_code": proc.returncode,
        "timed_out": timed_out,
        "cwd": str(workdir),
        "stdout": out.decode("utf-8", errors="replace")[-MAX_OUT:],
        "stderr": err.decode("utf-8", errors="replace")[-MAX_OUT // 4 :],
    }


def claude_argv(binary: str, prompt: str, model: str | None, extra: list[str]) -> list[str]:
    argv = [binary, "-p", prompt, "--output-format", "json", *extra]
    if model:
        argv += ["--model", model]
    return argv


async def run_claude(
    policy: PathPolicy,
    *,
    binary: str,
    extra_args: list[str],
    prompt: str,
    cwd: str,
    model: str | None,
    timeout_s: float,
) -> dict[str, Any]:
    exe = shutil.which(binary)
    if exe is None:
        raise FileNotFoundError(f"Claude Code ({binary}) ist auf diesem Gerät nicht installiert")
    res = await run_command(
        policy, argv=claude_argv(exe, prompt, model, extra_args), command=None, cwd=cwd, timeout_s=timeout_s
    )
    out: dict[str, Any] = {"exit_code": res["exit_code"], "timed_out": res["timed_out"], "cwd": res["cwd"]}
    try:
        parsed = json.loads(res["stdout"])
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        out["text"] = str(parsed.get("result") or "")
        out["is_error"] = bool(parsed.get("is_error"))
        out["session_id"] = parsed.get("session_id")
        out["cost_usd"] = parsed.get("total_cost_usd")
        out["turns"] = parsed.get("num_turns")
    else:
        out["text"] = res["stdout"][-20000:]
        out["is_error"] = res["exit_code"] != 0
    if res["stderr"].strip():
        out["stderr"] = res["stderr"][-4000:]
    return out
