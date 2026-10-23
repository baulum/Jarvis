"""Executes the Core's commands on this computer.

Every action of ``protocol.ACTIONS`` has one handler here. Before it runs: the capability must be usable on this
machine (else ``unavailable`` with the reason), the kill switch must allow it (only read-only actions while it is
engaged), and the arguments are checked. Errors become ``CommandError(code, German message)`` – the agent never
crashes on a bad command.

Coordinates: a screenshot is scaled down to ``max_width`` before it is sent. The Core (and the model) then think in
screenshot pixels, so click/move/scroll positions are mapped back with the last capture's scale and origin.
"""

from __future__ import annotations

import asyncio
import base64
import io
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from jarvis_device.actions import browser, files, shell
from jarvis_device.actions.system import system_info
from jarvis_device.backends.base import BackendError, Capture, DesktopBackend, Unavailable
from jarvis_device.backends.keys import KeyError_
from jarvis_device.config import AgentConfig
from jarvis_device.protocol import ACTIONS, CAPABILITY_LABELS, READ_ONLY_ACTIONS
from jarvis_device.security import KillSwitch, PathPolicy, PolicyError

Handler = Callable[[dict[str, Any]], Awaitable[Any]]


class CommandError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class CaptureFrame:
    scale: float = 1.0  # screen pixels per screenshot pixel
    origin_x: int = 0
    origin_y: int = 0


def _str(a: dict[str, Any], key: str, *, required: bool = True, max_len: int = 100_000) -> str | None:
    v = a.get(key)
    if v is None or v == "":
        if required:
            raise CommandError("invalid", f"{key} fehlt")
        return None
    if not isinstance(v, str):
        raise CommandError("invalid", f"{key} muss Text sein")
    if len(v) > max_len:
        raise CommandError("invalid", f"{key} ist zu lang")
    return v


def _num(a: dict[str, Any], key: str, *, required: bool = True, default: float = 0) -> float:
    v = a.get(key)
    if v is None:
        if required:
            raise CommandError("invalid", f"{key} fehlt")
        return default
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise CommandError("invalid", f"{key} muss eine Zahl sein")
    return float(v)


def _int_opt(a: dict[str, Any], key: str) -> int | None:
    v = a.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise CommandError("invalid", f"{key} muss eine ganze Zahl sein")
    return v


def scale_png(png: bytes, max_width: int) -> tuple[bytes, int, int, float]:
    """PNG scaled to ``max_width`` (Pillow); returns (png, width, height, screen-px-per-image-px)."""
    from PIL import Image

    with Image.open(io.BytesIO(png)) as img:
        w, h = img.size
        if w <= max_width:
            return png, w, h, 1.0
        ratio = max_width / w
        small = img.convert("RGB").resize((max_width, max(1, round(h * ratio))), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        small.save(buf, "PNG", optimize=True)
        return buf.getvalue(), small.size[0], small.size[1], w / max_width


class Dispatcher:
    def __init__(self, backend: DesktopBackend, cfg: AgentConfig, policy: PathPolicy, kill: KillSwitch) -> None:
        self.backend = backend
        self.cfg = cfg
        self.policy = policy
        self.kill = kill
        self.frame = CaptureFrame()
        self.handlers: dict[str, Handler] = {
            "system.info": self._system_info,
            "apps.list": lambda a: self.backend.list_apps(),
            "apps.open": self._apps_open,
            "apps.quit": self._apps_quit,
            "windows.list": lambda a: self.backend.list_windows(),
            "windows.focus": self._windows_focus,
            "input.type": self._type,
            "input.key": self._key,
            "input.click": self._click,
            "input.move": self._move,
            "input.scroll": self._scroll,
            "screen.capture": self._screenshot,
            "clipboard.get": self._clipboard_get,
            "clipboard.set": self._clipboard_set,
            "files.list": lambda a: files.run_sync(files.list_dir, self.policy, _str(a, "path")),
            "files.read": lambda a: files.run_sync(files.read_file, self.policy, _str(a, "path"), a.get("max_bytes")),
            "files.search": self._files_search,
            "files.write": self._files_write,
            "files.move": lambda a: files.run_sync(files.move, self.policy, _str(a, "from_path"), _str(a, "to_path")),
            "files.mkdir": lambda a: files.run_sync(files.mkdir, self.policy, _str(a, "path")),
            "files.reveal": self._reveal,
            "shell.run": self._shell,
            "claude.run": self._claude,
            "browser.open": self._open_url,
            "browser.automate": self._browser,
            "notify": self._notify,
            "audio.volume": self._volume,
            "audio.speak": self._speak,
            "audio.play": self._play,
        }
        missing = set(ACTIONS) - set(self.handlers)
        if missing:  # a protocol action without handler is a programming error, caught by the tests
            raise RuntimeError(f"Aktionen ohne Handler: {sorted(missing)}")

    # ------------------------------------------------------------------ capabilities
    def capabilities(self) -> dict[str, dict[str, str]]:
        caps: dict[str, tuple[str, str]] = {
            "system.info": ("available", ""),
            "files.read": ("available", ""),
            "files.write": ("available", ""),
            "terminal": ("available", "")
            if self.cfg.terminal == "on"
            else ("unavailable", "Terminal ist am Gerät abgeschaltet (jarvis-device config terminal off)"),
            "claude_code": ("available", "")
            if shutil.which(self.cfg.claude_binary)
            else ("unavailable", f"Claude Code ({self.cfg.claude_binary}) ist nicht installiert"),
        }
        ok, reason = browser.available()
        if not self.cfg.browser_automation:
            caps["browser.automation"] = ("unavailable", "Am Gerät abgeschaltet")
        else:
            caps["browser.automation"] = ("available", "") if ok else ("unavailable", reason)
        try:
            caps.update(self.backend.probe())
        except Exception as e:  # a broken probe must not take the agent down
            for cap in set(ACTIONS.values()) - set(caps):
                caps[cap] = ("unavailable", f"Prüfung fehlgeschlagen: {type(e).__name__}")
        for cap in set(ACTIONS.values()) - set(caps):
            caps[cap] = ("unavailable", "Auf diesem System nicht umgesetzt")
        return {cap: {"status": st, "reason": why} for cap, (st, why) in sorted(caps.items())}

    # ------------------------------------------------------------------ entry point
    async def run(self, action: str, args: dict[str, Any], caps: dict[str, dict[str, str]] | None = None) -> Any:
        cap = ACTIONS.get(action)
        handler = self.handlers.get(action)
        if cap is None or handler is None:
            raise CommandError("unknown_action", f"Unbekannte Aktion {action}")
        if not isinstance(args, dict):
            raise CommandError("invalid", "args muss ein Objekt sein")
        state = (caps or self.capabilities()).get(cap, {"status": "unavailable", "reason": ""})
        if state["status"] == "unavailable":
            label = CAPABILITY_LABELS.get(cap, cap)
            raise CommandError(
                "unavailable", f"{label} ist hier nicht verfügbar" + (f": {state['reason']}" if state["reason"] else "")
            )
        if self.kill.engaged and action not in READ_ONLY_ACTIONS:
            raise CommandError("kill_switch", "Not-Aus ist aktiv – nur lesende Aktionen sind erlaubt")
        try:
            return await handler(args)
        except CommandError:
            raise
        except Unavailable as e:
            raise CommandError("unavailable", str(e)) from e
        except (PolicyError, PermissionError) as e:
            raise CommandError("denied", str(e) or "Zugriff verweigert") from e
        except (FileNotFoundError, FileExistsError, NotADirectoryError, IsADirectoryError) as e:
            raise CommandError("invalid", str(e)) from e
        except (ValueError, KeyError_) as e:
            raise CommandError("invalid", str(e)) from e
        except BackendError as e:
            raise CommandError("failed", str(e)) from e
        except TimeoutError as e:
            raise CommandError("timeout", "Zeitüberschreitung") from e
        except Exception as e:
            raise CommandError("failed", f"{type(e).__name__}: {e}"[:300]) from e

    # ------------------------------------------------------------------ helpers
    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        f = self.frame
        return round(f.origin_x + x * f.scale), round(f.origin_y + y * f.scale)

    async def ensure_front(self, app: str | None) -> None:
        """Bring ``app`` to the front and verify it before input – input never lands in the wrong window."""
        if not app:
            return
        await self.backend.open_app(app)
        want = app.casefold()
        for _ in range(12):
            await asyncio.sleep(0.15)
            try:
                fm = await self.backend.frontmost()
            except Unavailable:
                return  # this session cannot tell (Wayland): the activation above is all we can do
            names = {str(fm.get(k, "")).casefold() for k in ("app", "process", "title", "bundle_id")}
            if any(want in n or n.startswith(want) for n in names if n):
                return
        raise CommandError("failed", f"„{app}“ konnte nicht in den Vordergrund geholt werden – Eingabe abgebrochen")

    # ------------------------------------------------------------------ handlers
    async def _system_info(self, a: dict[str, Any]) -> Any:
        info = await asyncio.to_thread(system_info, self.policy.roots)
        info["platform"] = self.backend.platform
        info["session"] = self.backend.session()
        info["kill_switch"] = self.kill.engaged
        return info

    async def _apps_open(self, a: dict[str, Any]) -> Any:
        return await self.backend.open_app(_str(a, "name", max_len=300) or "")

    async def _apps_quit(self, a: dict[str, Any]) -> Any:
        name, pid = _str(a, "name", required=False, max_len=300), _int_opt(a, "pid")
        if not name and not pid:
            raise CommandError("invalid", "name oder pid angeben")
        return await self.backend.quit_app(name, pid)

    async def _windows_focus(self, a: dict[str, Any]) -> Any:
        wid, title, app = _int_opt(a, "window_id"), _str(a, "title", required=False), _str(a, "app", required=False)
        if not (wid or title or app):
            raise CommandError("invalid", "window_id, title oder app angeben")
        return await self.backend.focus_window(wid, title, app)

    async def _type(self, a: dict[str, Any]) -> Any:
        text = _str(a, "text", max_len=5000) or ""
        await self.ensure_front(_str(a, "app", required=False))
        await self.backend.type_text(text, lambda: self.kill.engaged)
        return {"typed": len(text)}

    async def _key(self, a: dict[str, Any]) -> Any:
        key = _str(a, "key", max_len=40) or ""
        mods = a.get("modifiers") or []
        if not isinstance(mods, list):
            raise CommandError("invalid", "modifiers muss eine Liste sein")
        await self.ensure_front(_str(a, "app", required=False))
        await self.backend.key(key, [str(m) for m in mods])
        return {"key": key, "modifiers": mods}

    async def _click(self, a: dict[str, Any]) -> Any:
        x, y = self.to_screen(_num(a, "x"), _num(a, "y"))
        button = _str(a, "button", required=False) or "left"
        if button not in ("left", "right", "middle"):
            raise CommandError("invalid", "button: left, right oder middle")
        count = int(_num(a, "count", required=False, default=1))
        await self.ensure_front(_str(a, "app", required=False))
        await self.backend.click(x, y, button, max(1, min(3, count)))
        return {"x": x, "y": y, "button": button, "count": count}

    async def _move(self, a: dict[str, Any]) -> Any:
        x, y = self.to_screen(_num(a, "x"), _num(a, "y"))
        await self.backend.move(x, y)
        return {"x": x, "y": y}

    async def _scroll(self, a: dict[str, Any]) -> Any:
        if a.get("x") is not None and a.get("y") is not None:
            await self.backend.move(*self.to_screen(_num(a, "x"), _num(a, "y")))
        dx, dy = int(_num(a, "dx", required=False)), int(_num(a, "dy", required=False))
        if not dx and not dy:
            raise CommandError("invalid", "dx oder dy angeben")
        await self.backend.scroll(max(-50, min(50, dx)), max(-50, min(50, dy)))
        return {"dx": dx, "dy": dy}

    async def _screenshot(self, a: dict[str, Any]) -> Any:
        shot: Capture = await self.backend.screenshot(_int_opt(a, "window_id"), _int_opt(a, "display"))
        max_width = int(_num(a, "max_width", required=False, default=1600))
        try:
            png, w, h, scale = await asyncio.to_thread(scale_png, shot.png, max(320, min(3840, max_width)))
        except ImportError:
            png, w, h, scale = shot.png, shot.width, shot.height, 1.0  # Pillow missing: full size
        self.frame = CaptureFrame(scale, shot.origin_x, shot.origin_y)
        return {
            "png_base64": base64.b64encode(png).decode(),
            "width": w,
            "height": h,
            "scale": round(scale, 4),
            "screen": {"width": shot.width, "height": shot.height, "x": shot.origin_x, "y": shot.origin_y},
        }

    async def _clipboard_get(self, a: dict[str, Any]) -> Any:
        return {"text": (await self.backend.clipboard_get())[:100_000]}

    async def _clipboard_set(self, a: dict[str, Any]) -> Any:
        text = _str(a, "text", required=False, max_len=1_000_000) or ""
        await self.backend.clipboard_set(text)
        return {"ok": True, "chars": len(text)}

    async def _files_search(self, a: dict[str, Any]) -> Any:
        q = _str(a, "query", max_len=300) or ""
        root = _str(a, "root", required=False)
        limit = max(1, min(200, int(_num(a, "limit", required=False, default=50))))
        return await files.run_sync(files.search, self.policy, q, root, limit)

    async def _files_write(self, a: dict[str, Any]) -> Any:
        content = a.get("content")
        if not isinstance(content, str):
            raise CommandError("invalid", "content muss Text sein")
        return await files.run_sync(files.write_file, self.policy, _str(a, "path"), content, bool(a.get("overwrite")))

    async def _reveal(self, a: dict[str, Any]) -> Any:
        path = self.policy.check(_str(a, "path") or "", must_exist=True)
        await self.backend.reveal(str(path))
        return {"path": str(path)}

    async def _shell(self, a: dict[str, Any]) -> Any:
        argv = a.get("argv")
        if argv is not None and (not isinstance(argv, list) or not argv):
            raise CommandError("invalid", "argv muss eine nicht leere Liste sein")
        timeout = max(1.0, min(600.0, _num(a, "timeout_s", required=False, default=60)))
        return await shell.run_command(
            self.policy,
            argv=argv,
            command=_str(a, "command", required=False, max_len=4000),
            cwd=_str(a, "cwd", required=False),
            timeout_s=timeout,
        )

    async def _claude(self, a: dict[str, Any]) -> Any:
        model = _str(a, "model", required=False)
        if model and model not in ("haiku", "sonnet", "opus"):
            raise CommandError("invalid", "model: haiku, sonnet oder opus")
        return await shell.run_claude(
            self.policy,
            binary=self.cfg.claude_binary,
            extra_args=list(self.cfg.claude_args),
            prompt=_str(a, "prompt", max_len=20_000) or "",
            cwd=_str(a, "cwd") or "",
            model=model,
            timeout_s=max(30.0, min(840.0, _num(a, "timeout_s", required=False, default=600))),
        )

    async def _open_url(self, a: dict[str, Any]) -> Any:
        url = _str(a, "url", max_len=4000) or ""
        if not url.startswith(("http://", "https://", "mailto:")):
            raise CommandError("invalid", "Nur http(s)- und mailto-Adressen")
        await self.backend.open_url(url)
        return {"opened": url}

    async def _browser(self, a: dict[str, Any]) -> Any:
        try:
            return await browser.automate(a.get("steps"), headless=bool(a.get("headless", False)))
        except RuntimeError as e:
            raise CommandError("unavailable", str(e)) from e

    async def _notify(self, a: dict[str, Any]) -> Any:
        title = _str(a, "title", max_len=120) or ""
        message = _str(a, "message", max_len=500) or ""
        await self.backend.notify(title, message)
        return {"ok": True}

    async def _volume(self, a: dict[str, Any]) -> Any:
        op = _str(a, "op", required=False) or "get"
        if op not in ("get", "set", "mute", "unmute"):
            raise CommandError("invalid", "op: get, set, mute oder unmute")
        level = a.get("level")
        if op == "set" and (not isinstance(level, int) or isinstance(level, bool) or not 0 <= level <= 100):
            raise CommandError("invalid", "level 0–100 angeben")
        return await self.backend.volume(op, level if isinstance(level, int) else None)

    async def _speak(self, a: dict[str, Any]) -> Any:
        text = _str(a, "text", max_len=2000) or ""
        await self.backend.speak(text)
        return {"spoken": len(text)}

    async def _play(self, a: dict[str, Any]) -> Any:
        path = self.policy.check(_str(a, "path") or "", must_exist=True)
        await self.backend.play(str(path))
        return {"played": str(path)}
