"""Ist die installierte Desktop-App so neu wie der Code, mit dem der Core läuft?

Der Core startet bei jedem Neustart mit dem aktuellen Stand des Repositorys. Die Desktop-App dagegen ist ein
fertig gebautes Bundle in ``/Applications``: Neue Oberflächen-Funktionen (z. B. Anhänge im Chat) erscheinen dort
erst, nachdem ``scripts/install-app.sh`` die App neu gebaut hat. Ohne diesen Schritt sah Sir weder Büroklammer
noch Bildvorschau, obwohl der Code längst in ``main`` war.

Die App nennt beim Verbinden den Commit, aus dem sie gebaut wurde. Der Core zählt mit ``git``, wie viele Commits
seither die Oberfläche (``UI_PATHS``) geändert haben – Änderungen nur am Core lösen keine Meldung aus.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import shutil
from pathlib import Path
from typing import Any

UI_PATHS = (
    "desktop/src",
    "desktop/index.html",
    "desktop/package.json",
    "desktop/vite.config.ts",
    "desktop/src-tauri/src",
    "desktop/src-tauri/tauri.conf.json",
)
_SHA = re.compile(r"[0-9a-f]{7,40}")
GIT_TIMEOUT_S = 10


async def _git(root: Path, *args: str) -> str | None:
    """Ausgabe eines git-Befehls im Repository; ``None`` bei Fehler, Zeitüberschreitung oder ohne git."""
    exe = shutil.which("git")
    if exe is None:
        return None
    proc = await asyncio.create_subprocess_exec(
        exe, "-C", str(root), *args,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), GIT_TIMEOUT_S)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        return None
    return out.decode(errors="replace").strip() if proc.returncode == 0 else None


async def desktop_build_status(root: Path, commit: str) -> dict[str, Any]:
    """``stale``: True = die App ist älter als die Oberfläche im Repository, None = nicht feststellbar."""
    out: dict[str, Any] = {"build_commit": commit[:12], "core_commit": None, "stale": None, "changes": 0}
    if not _SHA.fullmatch(commit):
        out["build_commit"] = ""
        out["reason"] = "Die App kennt den Commit ihres Builds nicht."
        return out
    head = await _git(root, "rev-parse", "HEAD")
    if head is None:
        out["reason"] = "Der Core läuft nicht aus einem Git-Repository."
        return out
    out["core_commit"] = head[:12]
    if await _git(root, "cat-file", "-e", f"{commit}^{{commit}}") is None:
        out["reason"] = "Der Commit des App-Builds ist in diesem Repository unbekannt."
        return out
    count = await _git(root, "rev-list", "--count", f"{commit}..HEAD", "--", *UI_PATHS)
    if count is None or not count.isdigit():
        out["reason"] = "Die Änderungen seit dem App-Build konnten nicht gezählt werden."
        return out
    out["changes"] = int(count)
    out["stale"] = out["changes"] > 0
    return out
