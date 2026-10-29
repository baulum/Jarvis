"""Where the browser runs: ``LocalRunner`` (Playwright on the Core host) or ``DeviceRunner`` (Playwright on Sir's
computer through the Jarvis Device Agent, action ``browser.session``).

Both hand the platform adapters the same small ``BrowserSession`` interface – declarative steps only (go to, click,
fill, insert text, press a key, choose a file, wait, read visible text), no script evaluation, no DOM edits. The
browser is the installed Google Chrome where there is one (else Playwright's Chromium/Edge) with its defaults: no
stealth plugins, no changed user agent or fingerprint, no proxies, the automation flag stays visible.

Every session is a fresh, non-persistent browser context: the login state comes in decrypted from the
``SessionVault`` and goes back out through ``storage_state()`` – no profile directory with plain cookies remains
on disk, neither on the Core nor on the device.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Protocol

from jarvis.uptik.model import PermanentError, RetryableError


class BrowserSession(Protocol):
    async def goto(self, url: str) -> None: ...
    async def url(self) -> str: ...
    async def text(self, selector: str = "body") -> str: ...
    async def exists(self, selector: str) -> bool: ...
    async def wait_for(self, selector: str, timeout_ms: int = 15_000) -> bool: ...
    async def click(self, selector: str) -> None: ...
    async def mouse_click(self, selector: str) -> None:
        """Real mouse input: scroll the element into view, move to the centre of its box, press and release."""
        ...

    async def press_on(self, selector: str, key: str) -> None:
        """Focus the element, then send ``key`` to it (keyboard fallback for an element that only took focus)."""
        ...

    async def fill(self, selector: str, value: str) -> None: ...
    async def insert_text(self, selector: str, value: str) -> None: ...
    async def press(self, key: str) -> None: ...
    async def upload(self, selector: str, path: str) -> None: ...
    async def wait(self, ms: int) -> None: ...
    async def statuses(self) -> list[int]: ...
    async def storage_state(self) -> dict[str, Any]: ...
    async def close(self) -> None: ...


class Runner(ABC):
    kind: str = "local"

    @abstractmethod
    async def available(self) -> tuple[bool, str]: ...

    @abstractmethod
    async def open(self, storage_state: dict[str, Any] | None, *, headless: bool) -> BrowserSession: ...

    async def stage_video(self, path: Path) -> str:
        """The path under which the runner's browser can read the video."""
        return str(path)

    async def release_video(self, staged: str) -> None:
        return None


# --------------------------------------------------------------------------- OS specifics
def chrome_candidates(platform: str | None = None, env: dict[str, str] | None = None) -> list[Path]:
    """Where Google Chrome usually lives on macOS, Windows and Linux (first existing one wins)."""
    plat = platform or sys.platform
    e = env if env is not None else dict(os.environ)
    if plat == "darwin":
        app = "Google Chrome.app/Contents/MacOS/Google Chrome"
        return [Path("/Applications") / app, Path.home() / "Applications" / app]
    if plat == "win32":
        rel = Path("Google/Chrome/Application/chrome.exe")
        roots = [e.get("PROGRAMFILES"), e.get("PROGRAMFILES(X86)"), e.get("LOCALAPPDATA")]
        return [Path(r) / rel for r in roots if r]
    names = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
    return [Path(p) for p in (shutil.which(n) for n in names) if p]


def find_chrome(configured: str = "") -> str | None:
    if configured:
        return configured if Path(configured).expanduser().is_file() else None
    return next((str(p) for p in chrome_candidates() if p.is_file()), None)


# --------------------------------------------------------------------------- local (Playwright on the Core host)
class _PlaywrightSession:
    def __init__(self, pw: Any, browser: Any, context: Any, page: Any) -> None:
        self._pw, self._browser, self._context, self.page = pw, browser, context, page
        self._statuses: list[int] = []
        page.on("response", lambda r: self._statuses.append(r.status) if r.status == 429 else None)

    async def goto(self, url: str) -> None:
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as e:  # network, DNS, timeout
            raise RetryableError(f"Seite nicht erreichbar ({type(e).__name__})", code="navigation") from None

    async def url(self) -> str:
        return str(self.page.url)

    async def text(self, selector: str = "body") -> str:
        try:
            return str(await self.page.locator(selector).first.inner_text(timeout=5_000))
        except Exception:
            return ""

    async def exists(self, selector: str) -> bool:
        try:
            return bool(await self.page.locator(selector).count())
        except Exception:
            return False

    async def wait_for(self, selector: str, timeout_ms: int = 15_000) -> bool:
        try:
            await self.page.locator(selector).first.wait_for(state="attached", timeout=timeout_ms)
            return True
        except Exception:
            return False

    async def click(self, selector: str) -> None:
        await self.page.locator(selector).first.click(timeout=15_000)

    async def mouse_click(self, selector: str) -> None:
        loc = self.page.locator(selector).first
        try:
            await loc.scroll_into_view_if_needed(timeout=10_000)
            box = await loc.bounding_box(timeout=5_000)
        except Exception as e:
            raise RetryableError(f"nicht klickbar ({type(e).__name__})", code="selector") from None
        if not box or box["width"] < 1 or box["height"] < 1:
            raise RetryableError("nicht klickbar (keine sichtbare Fläche)", code="selector")
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        await self.page.mouse.move(x, y, steps=4)
        await asyncio.sleep(0.08)
        await self.page.mouse.down()
        await asyncio.sleep(0.06)
        await self.page.mouse.up()

    async def press_on(self, selector: str, key: str) -> None:
        loc = self.page.locator(selector).first
        await loc.focus(timeout=10_000)
        await self.page.keyboard.press(key)

    async def fill(self, selector: str, value: str) -> None:
        await self.page.locator(selector).first.fill(value, timeout=15_000)

    async def insert_text(self, selector: str, value: str) -> None:
        await self.page.locator(selector).first.click(timeout=15_000)
        await self.page.keyboard.insert_text(value)

    async def press(self, key: str) -> None:
        await self.page.keyboard.press(key)

    async def upload(self, selector: str, path: str) -> None:
        await self.page.locator(selector).first.set_input_files(path, timeout=30_000)

    async def wait(self, ms: int) -> None:
        await asyncio.sleep(ms / 1000)

    async def statuses(self) -> list[int]:
        out, self._statuses = self._statuses, []
        return out

    async def storage_state(self) -> dict[str, Any]:
        return dict(await self._context.storage_state())

    async def close(self) -> None:
        for closer in (self._context.close, self._browser.close, self._pw.stop):
            try:
                await closer()
            except Exception:  # noqa: S112 - closing is best effort (window may already be gone)
                continue


class LocalRunner(Runner):
    kind = "local"

    def __init__(self, browser_path: str = "") -> None:
        self.browser_path = browser_path

    async def available(self) -> tuple[bool, str]:
        try:
            import playwright  # noqa: F401
        except ImportError:
            return False, "Playwright fehlt im Core (uv sync)"
        if self.browser_path and not find_chrome(self.browser_path):
            return False, f"Browser nicht gefunden: {self.browser_path}"
        return True, ""

    async def open(self, storage_state: dict[str, Any] | None, *, headless: bool) -> BrowserSession:
        from playwright.async_api import async_playwright

        pw = await async_playwright().start()
        try:
            exe = find_chrome(self.browser_path)
            if exe:
                browser = await pw.chromium.launch(executable_path=exe, headless=headless)
            elif sys.platform == "win32":
                browser = await pw.chromium.launch(channel="msedge", headless=headless)
            else:
                browser = await pw.chromium.launch(headless=headless)
            context = await browser.new_context(
                storage_state=storage_state,  # type: ignore[arg-type]
                viewport={"width": 1440, "height": 900},
            )
            page = await context.new_page()
        except Exception as e:
            await pw.stop()
            raise PermanentError(f"Browser konnte nicht starten ({type(e).__name__})", code="browser") from None
        return _PlaywrightSession(pw, browser, context, page)


# --------------------------------------------------------------------------- Sir's computer (Device Agent)
class _DeviceSession:
    def __init__(self, hub: Any, device: str, sid: str) -> None:
        self.hub, self.device, self.sid = hub, device, sid

    async def _op(self, op: str, timeout_s: float = 90, **args: Any) -> Any:
        from jarvis.devices.hub import DeviceError

        try:
            return await self.hub.call(
                self.device, "browser.session", {"op": op, "session": self.sid, **args}, timeout_s
            )
        except DeviceError as e:
            if e.code in ("offline", "timeout"):
                raise RetryableError(f"Gerät {self.device}: {e.message}", code="device") from None
            raise PermanentError(f"Gerät {self.device}: {e.message}", code="device") from None

    async def goto(self, url: str) -> None:
        await self._op("goto", url=url)

    async def url(self) -> str:
        return str((await self._op("url") or {}).get("url", ""))

    async def text(self, selector: str = "body") -> str:
        return str((await self._op("text", selector=selector) or {}).get("text", ""))

    async def exists(self, selector: str) -> bool:
        return bool((await self._op("exists", selector=selector) or {}).get("exists"))

    async def wait_for(self, selector: str, timeout_ms: int = 15_000) -> bool:
        res = await self._op("wait_for", timeout_s=timeout_ms / 1000 + 30, selector=selector, timeout_ms=timeout_ms)
        return bool((res or {}).get("found"))

    async def click(self, selector: str) -> None:
        await self._op("click", selector=selector)

    async def mouse_click(self, selector: str) -> None:
        res = await self._op("mouse_click", selector=selector) or {}
        if not res.get("ok", True):
            raise RetryableError(f"nicht klickbar ({res.get('reason') or 'unbekannt'})", code="selector")

    async def press_on(self, selector: str, key: str) -> None:
        await self._op("press_on", selector=selector, key=key)

    async def fill(self, selector: str, value: str) -> None:
        await self._op("fill", selector=selector, value=value)

    async def insert_text(self, selector: str, value: str) -> None:
        await self._op("insert_text", selector=selector, value=value)

    async def press(self, key: str) -> None:
        await self._op("press", key=key)

    async def upload(self, selector: str, path: str) -> None:
        await self._op("upload", timeout_s=300, selector=selector, path=path)

    async def wait(self, ms: int) -> None:
        await asyncio.sleep(ms / 1000)

    async def statuses(self) -> list[int]:
        return [int(x) for x in (await self._op("statuses") or {}).get("statuses", [])]

    async def storage_state(self) -> dict[str, Any]:
        return dict((await self._op("storage_state") or {}).get("state") or {})

    async def close(self) -> None:
        try:
            await self._op("close", timeout_s=30)
        except Exception:  # noqa: S110 - the device closes idle sessions itself
            pass


class DeviceRunner(Runner):
    """Browser on Sir's computer: the video is pushed over the agent's WebSocket (SHA-256 checked) into
    ``staging_dir`` on the device, the session state travels only inside the TLS connection."""

    kind = "device"

    def __init__(self, hub: Any, device: str, staging_dir: str = "~/Downloads/Jarvis-UpTik") -> None:
        self.hub, self.device, self.staging_dir = hub, device, staging_dir.rstrip("/\\")

    async def available(self) -> tuple[bool, str]:
        from jarvis.devices.hub import DeviceError

        try:
            conn = self.hub.resolve(self.device or None)
        except DeviceError as e:
            return False, e.message
        ok, reason = conn.capability("browser.automation")
        return (True, "") if ok else (False, f"Browser-Automatisierung auf {conn.name} nicht verfügbar: {reason}")

    async def open(self, storage_state: dict[str, Any] | None, *, headless: bool) -> BrowserSession:
        from jarvis.devices.hub import DeviceError

        try:
            res = await self.hub.call(
                self.device or None,
                "browser.session",
                {"op": "open", "headless": headless, "state": storage_state},
                120,
            )
        except DeviceError as e:
            cls = RetryableError if e.code in ("offline", "timeout") else PermanentError
            raise cls(f"Gerät {self.device or '?'}: {e.message}", code="device") from None
        return _DeviceSession(self.hub, self.device or str(res.get("device") or ""), str(res["session"]))

    async def stage_video(self, path: Path) -> str:
        from jarvis.whatsapp.transfer import TransferError, push_file

        try:
            res = await push_file(self.hub, self.device or None, path, f"{self.staging_dir}/{path.name}")
        except TransferError as e:
            raise RetryableError(f"Video konnte nicht aufs Gerät übertragen werden: {e}", code="transfer") from None
        return str(res["path"])
