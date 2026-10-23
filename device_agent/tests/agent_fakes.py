"""Test doubles for the device agent (no OS access)."""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image

from jarvis_device.backends.base import BackendError, CapStatus, Capture, DesktopBackend, ProcResult


def png_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


class FakeBackend(DesktopBackend):
    """Records every call; no OS involved."""

    platform = "fake"

    def __init__(self, caps: dict[str, CapStatus] | None = None) -> None:
        super().__init__(which=lambda name: None)
        self.calls: list[tuple[Any, ...]] = []
        self.caps = caps
        self.front = {"app": "", "title": ""}
        self.capture = Capture(png_bytes(3200, 1800), 3200, 1800, origin_x=100, origin_y=50)

    def probe(self) -> dict[str, CapStatus]:
        if self.caps is not None:
            return self.caps
        return {
            cap: ("available", "")
            for cap in (
                "apps.list", "apps.launch", "apps.quit", "windows.list", "windows.focus", "input.keyboard",
                "input.mouse", "screen.capture", "clipboard", "files.reveal", "browser.open", "notifications",
                "audio.volume", "audio.speak", "audio.play",
            )
        }  # fmt: skip

    async def list_apps(self) -> list[dict[str, Any]]:
        self.calls.append(("list_apps",))
        return [{"name": "Rechner", "pid": 1}]

    async def open_app(self, name: str) -> dict[str, Any]:
        self.calls.append(("open_app", name))
        self.front = {"app": name.lower(), "title": f"{name} – Fenster"}
        return {"activated": name}

    async def frontmost(self) -> dict[str, Any]:
        return self.front

    async def type_text(self, text: str, should_stop: Callable[[], bool]) -> None:
        if should_stop():
            raise BackendError("stop")
        self.calls.append(("type", text))

    async def key(self, key: str, modifiers: list[str]) -> None:
        self.calls.append(("key", key, tuple(modifiers)))

    async def click(self, x: int, y: int, button: str, count: int) -> None:
        self.calls.append(("click", x, y, button, count))

    async def move(self, x: int, y: int) -> None:
        self.calls.append(("move", x, y))

    async def scroll(self, dx: int, dy: int) -> None:
        self.calls.append(("scroll", dx, dy))

    async def screenshot(self, window_id: int | None, display: int | None) -> Capture:
        self.calls.append(("screenshot", window_id, display))
        return self.capture

    async def clipboard_get(self) -> str:
        return "aus der Zwischenablage"

    async def clipboard_set(self, text: str) -> None:
        self.calls.append(("clipboard_set", text))

    async def notify(self, title: str, message: str) -> None:
        self.calls.append(("notify", title, message))

    async def volume(self, op: str, level: int | None) -> dict[str, Any]:
        self.calls.append(("volume", op, level))
        return {"level": level or 30, "muted": op == "mute"}


class FakeRun:
    """Stands in for ``run_process``: records argv and answers from a table (program name → ProcResult)."""

    def __init__(self, answers: dict[str, ProcResult] | None = None) -> None:
        self.calls: list[tuple[list[str], bytes | None]] = []
        self.answers = answers or {}

    async def __call__(
        self, argv: list[str], *, input: bytes | None = None, timeout: float = 30, env: Any = None
    ) -> ProcResult:
        self.calls.append((list(argv), input))
        name = Path(argv[0]).name
        key = " ".join([name, *argv[1:2]])
        return self.answers.get(key) or self.answers.get(name) or ProcResult(0, b"", b"")
