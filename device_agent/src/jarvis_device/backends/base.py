"""``DesktopBackend``: the operating-system part of the agent (programs, windows, input, screen, clipboard, audio).

One implementation per OS (``windows``, ``linux``, ``macos``). What a backend cannot do on this machine it reports
in ``probe()`` as unavailable *with the reason*; calling it anyway raises ``Unavailable`` – never a crash.
Spawning helpers is injectable (``run``), so the command lines can be tested on any host.
"""

from __future__ import annotations

import abc
import asyncio
import shutil
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

CapStatus = tuple[str, str]  # (available | degraded | unavailable, reason)


class Unavailable(Exception):
    """This backend cannot do that here; ``str(e)`` is the German reason."""


class BackendError(Exception):
    """The action was possible but failed (program not found, helper exited with an error …)."""


@dataclass(slots=True)
class ProcResult:
    returncode: int
    stdout: bytes
    stderr: bytes

    @property
    def text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace")


Runner = Callable[..., Awaitable[ProcResult]]


async def run_process(
    argv: Sequence[str],
    *,
    input: bytes | None = None,
    timeout: float = 30.0,
    env: dict[str, str] | None = None,
) -> ProcResult:
    """Runs a helper program (argv, never a shell string) and collects its output."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if input is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(input), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise BackendError(f"{argv[0]} hat nicht rechtzeitig geantwortet") from None
    return ProcResult(proc.returncode or 0, out, err)


@dataclass(slots=True)
class UiTarget:
    """Which program's controls ``ui.tree``/``ui.press`` mean: a window, a process or a program name – or, with
    nothing set, the program in the foreground."""

    app: str | None = None
    pid: int | None = None
    window_id: int | None = None


@dataclass(slots=True)
class Capture:
    png: bytes
    width: int
    height: int
    origin_x: int = 0  # screen coordinates of the image's top-left pixel
    origin_y: int = 0


class DesktopBackend(abc.ABC):
    platform: str

    def __init__(self, run: Runner = run_process, which: Callable[[str], str | None] = shutil.which) -> None:
        self.run = run
        self.which = which

    def have(self, *programs: str) -> str | None:
        for p in programs:
            if found := self.which(p):
                return found
        return None

    def session(self) -> str:
        return "desktop"

    @abc.abstractmethod
    def probe(self) -> dict[str, CapStatus]:
        """Capability → (status, reason) for the desktop capabilities of this backend."""

    # ------------------------------------------------------------------ programs and windows
    async def list_apps(self) -> list[dict[str, Any]]:
        raise Unavailable("Programme auflisten ist hier nicht umgesetzt")

    async def open_app(self, name: str) -> dict[str, Any]:
        raise Unavailable("Programme öffnen ist hier nicht umgesetzt")

    async def quit_app(self, name: str | None, pid: int | None) -> dict[str, Any]:
        raise Unavailable("Programme beenden ist hier nicht umgesetzt")

    async def list_windows(self) -> list[dict[str, Any]]:
        raise Unavailable("Fenster auflisten ist hier nicht umgesetzt")

    async def focus_window(self, window_id: int | None, title: str | None, app: str | None) -> dict[str, Any]:
        raise Unavailable("Fenster aktivieren ist hier nicht umgesetzt")

    async def frontmost(self) -> dict[str, Any]:
        raise Unavailable("Vordergrundfenster ist hier nicht bekannt")

    # ------------------------------------------------------------------ input
    async def type_text(self, text: str, should_stop: Callable[[], bool]) -> None:
        raise Unavailable("Tippen ist hier nicht umgesetzt")

    async def key(self, key: str, modifiers: list[str]) -> None:
        raise Unavailable("Tastenkombinationen sind hier nicht umgesetzt")

    async def click(self, x: int, y: int, button: str, count: int) -> None:
        raise Unavailable("Klicken ist hier nicht umgesetzt")

    async def move(self, x: int, y: int) -> None:
        raise Unavailable("Mausbewegung ist hier nicht umgesetzt")

    async def scroll(self, dx: int, dy: int) -> None:
        raise Unavailable("Scrollen ist hier nicht umgesetzt")

    # ------------------------------------------------------------------ screen, clipboard
    async def screenshot(self, window_id: int | None, display: int | None) -> Capture:
        raise Unavailable("Screenshots sind hier nicht umgesetzt")

    async def clipboard_get(self) -> str:
        raise Unavailable("Zwischenablage ist hier nicht umgesetzt")

    async def clipboard_set(self, text: str) -> None:
        raise Unavailable("Zwischenablage ist hier nicht umgesetzt")

    # ------------------------------------------------------------------ shell integration
    async def reveal(self, path: str) -> None:
        raise Unavailable("Dateimanager ist hier nicht verfügbar")

    async def open_url(self, url: str) -> None:
        raise Unavailable("Standardbrowser ist hier nicht verfügbar")

    async def notify(self, title: str, message: str) -> None:
        raise Unavailable("Benachrichtigungen sind hier nicht umgesetzt")

    # ------------------------------------------------------------------ audio
    async def volume(self, op: str, level: int | None) -> dict[str, Any]:
        raise Unavailable("Lautstärke ist hier nicht umgesetzt")

    async def speak(self, text: str) -> None:
        raise Unavailable("Sprachausgabe ist hier nicht umgesetzt")

    async def play(self, path: str) -> None:
        raise Unavailable("Audiowiedergabe ist hier nicht umgesetzt")

    # ------------------------------------------------------------------ controls (accessibility)
    async def ui_tree(self, target: UiTarget, max_depth: int, max_nodes: int) -> dict[str, Any]:
        """The program's controls as a tree; every node has an ``id`` (path) that ``ui_press`` accepts."""
        raise Unavailable("Bedienelemente lesen ist hier nicht umgesetzt")

    async def ui_press(self, target: UiTarget, path: str | None, role: str | None, title: str | None) -> dict[str, Any]:
        raise Unavailable("Bedienelemente drücken ist hier nicht umgesetzt")

    # ------------------------------------------------------------------ automation
    async def shortcuts_list(self) -> list[str]:
        """Apple Shortcuts (macOS only); Windows and Linux use the agent's script folder instead."""
        raise Unavailable("Apple-Kurzbefehle gibt es nur auf macOS")

    async def shortcut_run(self, name: str, input: str | None) -> dict[str, Any]:
        raise Unavailable("Apple-Kurzbefehle gibt es nur auf macOS")

    async def applescript(self, source: str) -> dict[str, Any]:
        raise Unavailable(
            "AppleScript gibt es nur auf macOS – hier Automationen (Skripte) oder Terminal-Befehle verwenden"
        )

    # ------------------------------------------------------------------ search index
    async def index_search(
        self, query: str, roots: Sequence[str], limit: int, names_only: bool
    ) -> tuple[str, list[str]] | None:
        """Paths from the system's search index (Spotlight, Windows Search, Tracker/locate) – unfiltered, the
        dispatcher applies the path policy. None: no index here, the agent searches the folders itself."""
        return None


def fuzzy_pick(name: str, candidates: Sequence[tuple[str, Any]]) -> Any | None:
    """Best match for a program name: exact (case-insensitive) → prefix → substring. Candidates: (label, value)."""
    want = name.strip().casefold()
    if not want:
        return None
    for label, value in candidates:
        if label.casefold() == want:
            return value
    for label, value in candidates:
        if label.casefold().startswith(want):
            return value
    for label, value in candidates:
        if want in label.casefold():
            return value
    return None
