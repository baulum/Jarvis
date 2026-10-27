"""macOS backend: uses the existing JarvisBridge (Swift, 127.0.0.1:8766) for apps, windows, input, screenshots,
clipboard and Finder – the same code that already drives the Mac for the local Core – plus ``osascript`` for
notifications/volume, ``say`` and ``afplay``.

Optional: on a Mac that runs the Core itself nothing changes (the Core talks to the bridge directly, mac_* tools).
The agent is for a second Mac that a Core elsewhere should control.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from jarvis_device.backends.base import (
    BackendError,
    CapStatus,
    Capture,
    DesktopBackend,
    Runner,
    UiTarget,
    Unavailable,
    fuzzy_pick,
    run_process,
)
from jarvis_device.backends.keys import bridge_key

BRIDGE_URL = "http://127.0.0.1:8766"
TOKEN_FILE = Path.home() / "Library" / "Application Support" / "Jarvis" / "bridge.token"

# AppleScript receives its values as argv (``on run argv``) – never spliced into the source
NOTIFY_SCRIPT = "on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run"
VOLUME_GET = 'output volume of (get volume settings) & "," & output muted of (get volume settings)'


class MacBackend(DesktopBackend):
    platform = "mac"

    def __init__(
        self,
        run: Runner = run_process,
        which: Callable[[str], str | None] = shutil.which,
        token_file: Path = TOKEN_FILE,
        bridge_url: str = BRIDGE_URL,
    ) -> None:
        super().__init__(run, which)
        self.token_file = token_file
        self.bridge_url = bridge_url

    def session(self) -> str:
        return "aqua"

    # ------------------------------------------------------------------ bridge access
    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        try:
            token = self.token_file.read_text().strip()
        except FileNotFoundError as e:
            raise Unavailable("JarvisBridge ist nicht installiert (Token-Datei fehlt)") from e
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(  # noqa: S310 - fixed loopback URL
            self.bridge_url + path,
            data=data,
            method=method,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read() or b"{}")
            except ValueError:
                err = {}
            code = err.get("error", "error")
            if code == "permission_denied":
                raise Unavailable(f"macOS-Berechtigung fehlt: {err.get('permission')}") from e
            if code == "kill_switch_engaged":
                raise BackendError("Not-Aus der Bridge ist aktiv") from e
            raise BackendError(f"Bridge-Fehler {e.code}: {code} {err.get('message', '')}".strip()) from e
        except urllib.error.URLError as e:
            raise Unavailable("JarvisBridge läuft nicht (JarvisBridge.app starten)") from e

    async def bridge(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        return await asyncio.to_thread(self._request, method, path, body)

    def _health(self) -> dict[str, Any] | None:
        try:
            req = urllib.request.Request(self.bridge_url + "/health")  # noqa: S310
            with urllib.request.urlopen(req, timeout=2) as r:  # noqa: S310
                return dict(json.loads(r.read()))
        except (OSError, ValueError):
            return None

    def probe(self) -> dict[str, CapStatus]:
        h = self._health()
        osa = self.have("osascript")
        audio = {
            "notifications": ("available", "") if osa else ("unavailable", "osascript fehlt"),
            "audio.volume": ("available", "") if osa else ("unavailable", "osascript fehlt"),
            "audio.speak": ("available", "") if self.have("say") else ("unavailable", "say fehlt"),
            "audio.play": ("available", "") if self.have("afplay") else ("unavailable", "afplay fehlt"),
            "browser.open": ("available", "") if self.have("open") else ("unavailable", "open fehlt"),
        }
        no_osa = ("unavailable", "osascript fehlt")
        if h is None:
            down = ("unavailable", "JarvisBridge läuft nicht (bridge/scripts/build-app.sh, JarvisBridge.app starten)")
            return {cap: down for cap in _BRIDGE_CAPS} | audio | {"automation.applescript": down if osa else no_osa}
        perms = h.get("permissions") or {}
        ax = (
            ("available", "") if perms.get("accessibility", True) else ("unavailable", "Recht „Bedienungshilfen“ fehlt")
        )
        rec = (
            ("available", "")
            if perms.get("screenRecording", True)
            else ("unavailable", "Recht „Bildschirmaufnahme“ fehlt")
        )
        no_move = ("degraded", "Klicken ja; Bewegen/Scrollen bietet die JarvisBridge nicht an")
        return {
            "apps.list": ("available", ""),
            "apps.launch": ("available", ""),
            "apps.quit": ("available", ""),
            "windows.list": ("available", ""),
            "windows.focus": ("available", ""),
            "input.keyboard": ax,
            "input.mouse": no_move if ax[0] == "available" else ax,
            "screen.capture": rec,
            "clipboard": ("available", ""),
            "files.reveal": ("available", ""),
            "windows.frontmost": ("available", ""),
            "ui.elements": ax,
            "automation": ("available", "") if self.have("shortcuts") else ("unavailable", "shortcuts fehlt"),
            "automation.applescript": ("degraded", "Nur wenn die Richtlinie der JarvisBridge AppleScript erlaubt"),
        } | audio

    # ------------------------------------------------------------------ programs and windows
    async def list_apps(self) -> list[dict[str, Any]]:
        apps = await self.bridge("GET", "/apps")
        return [
            {"name": a.get("name"), "bundle_id": a.get("bundleId"), "pid": a.get("pid"), "active": a.get("active")}
            for a in apps
        ]

    async def open_app(self, name: str) -> dict[str, Any]:
        apps = await self.bridge("GET", "/apps")
        running = fuzzy_pick(name, [(str(a.get("name", "")), a) for a in apps])
        if running is not None:
            await self.bridge("POST", "/apps/activate", {"pid": running["pid"]})
            return {"activated": running.get("name")}
        await self.bridge("POST", "/apps/launch", {"name": name})
        return {"launched": name}

    async def quit_app(self, name: str | None, pid: int | None) -> dict[str, Any]:
        body: dict[str, Any] = {"pid": pid} if pid else {"name": name}
        return dict(await self.bridge("POST", "/apps/quit", body) or {})

    async def list_windows(self) -> list[dict[str, Any]]:
        ws = await self.bridge("GET", "/windows")
        return [
            {
                "window_id": w.get("windowId"),
                "title": w.get("title"),
                "app": w.get("ownerName"),
                "pid": w.get("ownerPid"),
                "bounds": w.get("bounds"),
            }
            for w in ws
        ]

    async def focus_window(self, window_id: int | None, title: str | None, app: str | None) -> dict[str, Any]:
        wins = await self.list_windows()
        target = next((w for w in wins if w["window_id"] == window_id), None) if window_id else None
        if target is None and (app or title):
            target = fuzzy_pick(
                app or title or "", [(str(w["app"]), w) for w in wins] + [(str(w["title"]), w) for w in wins]
            )
        if target is None:
            raise BackendError("Kein passendes Fenster gefunden")
        if target.get("pid"):
            await self.bridge("POST", "/apps/activate", {"pid": target["pid"]})
        else:
            await self.bridge("POST", "/apps/activate", {"name": target["app"]})
        return {"window_id": target["window_id"], "title": target["title"], "app": target["app"]}

    async def frontmost(self) -> dict[str, Any]:
        fm = await self.bridge("GET", "/frontmost")
        return {"app": fm.get("name", ""), "bundle_id": fm.get("bundleId"), "title": fm.get("focusedWindowTitle") or ""}

    # ------------------------------------------------------------------ input, screen, clipboard
    async def type_text(self, text: str, should_stop: Callable[[], bool]) -> None:
        if should_stop():
            raise BackendError("Not-Aus – Tippen abgebrochen")
        await self.bridge("POST", "/input/type", {"text": text})

    async def key(self, key: str, modifiers: list[str]) -> None:
        await self.bridge("POST", "/input/key", bridge_key(key, modifiers))

    async def click(self, x: int, y: int, button: str, count: int) -> None:
        await self.bridge(
            "POST",
            "/input/click",
            {"x": x, "y": y, "button": "right" if button == "right" else "left", "count": min(2, max(1, count))},
        )

    async def screenshot(self, window_id: int | None, display: int | None) -> Capture:
        import base64

        body: dict[str, Any] = {}
        if window_id:
            body["windowId"] = window_id
        if display is not None:
            body["displayId"] = display
        shot = await self.bridge("POST", "/screenshot", body)
        png = base64.b64decode(shot["png_base64"])
        return Capture(png, int(shot.get("width") or 0), int(shot.get("height") or 0))

    async def clipboard_get(self) -> str:
        return str((await self.bridge("GET", "/clipboard")).get("text") or "")

    async def clipboard_set(self, text: str) -> None:
        await self.bridge("POST", "/clipboard", {"text": text})

    async def reveal(self, path: str) -> None:
        await self.bridge("POST", "/files/reveal", {"path": path})

    async def open_url(self, url: str) -> None:
        exe = self.have("open")
        if exe is None:
            raise Unavailable("open fehlt")
        res = await self.run([exe, url], timeout=10)
        if res.returncode != 0:
            raise BackendError("URL konnte nicht geöffnet werden")

    # ------------------------------------------------------------------ controls (Accessibility)
    async def _selector(self, target: UiTarget) -> dict[str, Any]:
        if target.pid:
            return {"pid": target.pid}
        if target.app:
            return {"name": target.app}
        if target.window_id:
            win = next((w for w in await self.list_windows() if w["window_id"] == target.window_id), None)
            if win is None:
                raise BackendError("Fenster nicht gefunden")
            return {"pid": win["pid"]} if win.get("pid") else {"name": win["app"]}
        front = await self.frontmost()
        if not front.get("app"):
            raise BackendError("Kein Programm im Vordergrund – app angeben")
        return {"name": front["app"]}

    async def ui_tree(self, target: UiTarget, max_depth: int, max_nodes: int) -> dict[str, Any]:
        body = await self._selector(target) | {"maxDepth": max_depth, "maxNodes": max_nodes}
        return dict(await self.bridge("POST", "/ui/tree", body) or {})

    async def ui_press(
        self, target: UiTarget, path: str | None, role: str | None, title: str | None
    ) -> dict[str, Any]:
        body = await self._selector(target)
        body |= {k: v for k, v in (("path", path), ("role", role), ("title", title)) if v}
        return dict(await self.bridge("POST", "/ui/press", body) or {})

    # ------------------------------------------------------------------ automation
    async def shortcuts_list(self) -> list[str]:
        return [str(n) for n in await self.bridge("GET", "/shortcuts") or []]

    async def shortcut_run(self, name: str, input: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name}
        if input is not None:
            body["input"] = input
        return {"kind": "shortcut", **dict(await self.bridge("POST", "/shortcuts/run", body) or {})}

    async def applescript(self, source: str) -> dict[str, Any]:
        return dict(await self.bridge("POST", "/applescript", {"source": source}) or {})

    # ------------------------------------------------------------------ Spotlight
    async def index_search(
        self, query: str, roots: Sequence[str], limit: int, names_only: bool
    ) -> tuple[str, list[str]] | None:
        mdfind = self.have("mdfind")
        if mdfind is None:
            return None
        paths: list[str] = []
        for root in roots:
            argv = [mdfind, "-onlyin", root] + (["-name", query] if names_only else [query])
            res = await self.run(argv, timeout=30)
            if res.returncode != 0:
                return None
            paths += [line for line in res.text.splitlines() if line.strip()]
            if len(paths) >= limit:
                break
        return "spotlight", paths[:limit]

    # ------------------------------------------------------------------ notifications, audio
    async def _osa(self, script: str, *args: str) -> str:
        osa = self.have("osascript")
        if osa is None:
            raise Unavailable("osascript fehlt")
        res = await self.run([osa, "-e", script, *args], timeout=15)
        if res.returncode != 0:
            raise BackendError("osascript: " + res.stderr.decode(errors="replace").strip()[:200])
        return res.text.strip()

    async def notify(self, title: str, message: str) -> None:
        await self._osa(NOTIFY_SCRIPT, title, message)

    async def volume(self, op: str, level: int | None) -> dict[str, Any]:
        if op == "set":
            await self._osa(f"set volume output volume {max(0, min(100, int(level or 0)))}")
        elif op in ("mute", "unmute"):
            await self._osa(f"set volume output muted {'true' if op == 'mute' else 'false'}")
        out = await self._osa(VOLUME_GET)
        vol, _, muted = out.partition(",")
        try:
            lvl = int(vol)
        except ValueError:
            lvl = 0
        return {"level": lvl, "muted": muted.strip() == "true"}

    async def speak(self, text: str) -> None:
        say = self.have("say")
        if say is None:
            raise Unavailable("say fehlt")
        res = await self.run([say, "--", text], timeout=120)
        if res.returncode != 0:
            raise BackendError("say ist fehlgeschlagen")

    async def play(self, path: str) -> None:
        af = self.have("afplay")
        if af is None:
            raise Unavailable("afplay fehlt")
        res = await self.run([af, path], timeout=600)
        if res.returncode != 0:
            raise BackendError("afplay konnte die Datei nicht abspielen")


_BRIDGE_CAPS = (
    "apps.list",
    "apps.launch",
    "apps.quit",
    "windows.list",
    "windows.focus",
    "input.keyboard",
    "input.mouse",
    "screen.capture",
    "clipboard",
    "files.reveal",
    "windows.frontmost",
    "ui.elements",
    "automation",
)
