"""Browser sessions for the Core's UpTik connector (action ``browser.session``): a real browser on this computer that
stays open across several commands – open, go to, click, fill, insert text, press a key, choose a file, read text,
storage state, close.

Like ``browser.automate``: declarative steps only, no JavaScript evaluation, http(s) addresses only, files only from
the agent's allowed folders. The browser is the installed Google Chrome (else Edge on Windows, else Playwright's
Chromium) with its defaults – no stealth switches, no changed user agent, no proxy. The login state arrives from the
Core with ``open`` and goes back with ``storage_state``; the context is not persistent, nothing of it is written to
disk here. Idle sessions close after 15 minutes, at most two run at once.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis_device.actions.browser import available

OPS = (
    "open",
    "goto",
    "url",
    "text",
    "exists",
    "wait_for",
    "click",
    "fill",
    "insert_text",
    "press",
    "upload",
    "statuses",
    "storage_state",
    "close",
)
MAX_SESSIONS = 2
IDLE_S = 900
MAX_TEXT = 20_000


class SessionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def chrome_path() -> str | None:
    if sys.platform == "darwin":
        app = "Google Chrome.app/Contents/MacOS/Google Chrome"
        cands = [Path("/Applications") / app, Path.home() / "Applications" / app]
    elif sys.platform == "win32":
        rel = Path("Google/Chrome/Application/chrome.exe")
        cands = [
            Path(r) / rel
            for r in (os.environ.get(k) for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"))
            if r
        ]
    else:
        cands = [Path(p) for p in (shutil.which(n) for n in ("google-chrome", "google-chrome-stable", "chromium")) if p]
    return next((str(p) for p in cands if p.is_file()), None)


@dataclass
class _Session:
    pw: Any
    browser: Any
    context: Any
    page: Any
    statuses: list[int] = field(default_factory=list)
    used: float = field(default_factory=time.monotonic)

    async def close(self) -> None:
        for closer in (self.context.close, self.browser.close, self.pw.stop):
            try:
                await closer()
            except Exception:  # noqa: S112 - best effort, the window may be gone already
                continue


def _sel(a: dict[str, Any]) -> str:
    v = a.get("selector")
    if not isinstance(v, str) or not v or len(v) > 500:
        raise SessionError("invalid", "selector fehlt oder ist zu lang")
    return v


class BrowserSessions:
    def __init__(self) -> None:
        self._sessions: dict[str, _Session] = {}
        self._lock = asyncio.Lock()

    async def _reap(self) -> None:
        now = time.monotonic()
        for sid, s in list(self._sessions.items()):
            if now - s.used > IDLE_S:
                self._sessions.pop(sid, None)
                await s.close()

    async def close_all(self) -> None:
        for sid in list(self._sessions):
            s = self._sessions.pop(sid)
            await s.close()

    async def handle(self, a: dict[str, Any], check_file: Any) -> Any:
        op = a.get("op")
        if op not in OPS:
            raise SessionError("invalid", f"Unbekannte Operation {op!r}")
        async with self._lock:
            await self._reap()
            if op == "open":
                return await self._open(a)
            sid = str(a.get("session") or "")
            s = self._sessions.get(sid)
            if s is None:
                raise SessionError("invalid", "Browser-Sitzung nicht (mehr) offen")
            s.used = time.monotonic()
        return await self._op(op, s, sid, a, check_file)

    async def _open(self, a: dict[str, Any]) -> dict[str, Any]:
        ok, reason = available()
        if not ok:
            raise SessionError("unavailable", reason)
        if len(self._sessions) >= MAX_SESSIONS:
            raise SessionError("failed", "Zu viele offene Browser-Sitzungen")
        from playwright.async_api import async_playwright

        headless = bool(a.get("headless", False))
        state = a.get("state") if isinstance(a.get("state"), dict) else None
        pw = await async_playwright().start()
        try:
            exe = chrome_path()
            if exe:
                browser = await pw.chromium.launch(executable_path=exe, headless=headless)
            elif sys.platform == "win32":
                browser = await pw.chromium.launch(channel="msedge", headless=headless)
            else:
                browser = await pw.chromium.launch(headless=headless)
            context = await browser.new_context(storage_state=state, viewport={"width": 1440, "height": 900})
            page = await context.new_page()
        except Exception as e:
            await pw.stop()
            raise SessionError("failed", f"Browser konnte nicht starten ({type(e).__name__})") from None
        sess = _Session(pw, browser, context, page)
        page.on("response", lambda r: sess.statuses.append(r.status) if r.status == 429 else None)
        sid = secrets.token_hex(8)
        self._sessions[sid] = sess
        return {"session": sid}

    async def _op(self, op: str, s: _Session, sid: str, a: dict[str, Any], check_file: Any) -> Any:
        page = s.page
        try:
            if op == "goto":
                url = str(a.get("url") or "")
                if not url.startswith(("https://", "http://")):
                    raise SessionError("invalid", "Nur http(s)-Adressen")
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                return {"ok": True}
            if op == "url":
                return {"url": page.url}
            if op == "text":
                try:
                    txt = await page.locator(_sel(a)).first.inner_text(timeout=5_000)
                except SessionError:
                    raise
                except Exception:
                    txt = ""
                return {"text": str(txt)[:MAX_TEXT]}
            if op == "exists":
                return {"exists": bool(await page.locator(_sel(a)).count())}
            if op == "wait_for":
                ms = int(min(max(int(a.get("timeout_ms") or 15_000), 0), 600_000))
                try:
                    await page.locator(_sel(a)).first.wait_for(state="attached", timeout=ms)
                    return {"found": True}
                except Exception:
                    return {"found": False}
            if op == "click":
                await page.locator(_sel(a)).first.click(timeout=15_000)
                return {"ok": True}
            if op in ("fill", "insert_text"):
                value = a.get("value")
                if not isinstance(value, str) or len(value) > 10_000:
                    raise SessionError("invalid", "value fehlt oder ist zu lang")
                loc = page.locator(_sel(a)).first
                if op == "fill":
                    await loc.fill(value, timeout=15_000)
                else:
                    await loc.click(timeout=15_000)
                    await page.keyboard.insert_text(value)
                return {"ok": True}
            if op == "press":
                key = str(a.get("key") or "")
                if not key or len(key) > 40:
                    raise SessionError("invalid", "key fehlt")
                await page.keyboard.press(key)
                return {"ok": True}
            if op == "upload":
                path = check_file(str(a.get("path") or ""))
                await page.locator(_sel(a)).first.set_input_files(str(path), timeout=30_000)
                return {"ok": True}
            if op == "statuses":
                out, s.statuses = s.statuses, []
                return {"statuses": out}
            if op == "storage_state":
                return {"state": await s.context.storage_state()}
            if op == "close":
                self._sessions.pop(sid, None)
                await s.close()
                return {"ok": True}
        except SessionError:
            raise
        except Exception as e:
            raise SessionError("failed", f"{op}: {type(e).__name__}") from None
        raise SessionError("invalid", op)
