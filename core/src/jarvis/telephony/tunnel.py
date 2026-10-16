"""Free public HTTPS address for the Twilio webhooks via a Cloudflare Quick Tunnel.

``cloudflared tunnel --url`` needs no Cloudflare account and no domain; it prints a random
``https://<words>.trycloudflare.com`` address that changes on every start. Jarvis therefore
re-registers the address with Twilio whenever it changes (see the telephony connector).
Only the telephony paths are reachable through it (``jarvis.api.exposure``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shutil
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)
_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
CANDIDATES = ("cloudflared", "/opt/homebrew/bin/cloudflared", "/usr/local/bin/cloudflared")


def cloudflared_binary() -> str | None:
    for c in CANDIDATES:
        found = shutil.which(c)
        if found:
            return found
    return None


class QuickTunnel:
    def __init__(self, port: int, on_url: Callable[[str], Awaitable[None]]) -> None:
        self.port = port
        self.on_url = on_url
        self.url: str | None = None
        self.error: str | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if not self.running:
            self._task = asyncio.create_task(self._supervise(), name="quick-tunnel")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._kill()
        self.url = None

    async def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except TimeoutError:
                proc.kill()

    async def _supervise(self) -> None:
        backoff = 5.0
        while True:
            binary = cloudflared_binary()
            if binary is None:
                self.error = "cloudflared ist nicht installiert (brew install cloudflared)"
                logger.warning(self.error)
                return
            try:
                await self._run_once(binary)
                backoff = 5.0
            except asyncio.CancelledError:
                raise
            except Exception as e:  # keep trying: the tunnel is the only way Twilio reaches us
                self.error = f"Tunnel-Fehler: {e}"
                logger.warning("Quick tunnel failed: %s", e)
            self.url = None
            await self._kill()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)

    async def _run_once(self, binary: str) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            binary,
            "tunnel",
            "--no-autoupdate",
            "--url",
            f"http://127.0.0.1:{self.port}",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        assert self._proc.stderr is not None
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                break
            m = _URL.search(line.decode("utf-8", "replace"))
            if m and m.group(0) != self.url:
                self.url = m.group(0)
                self.error = None
                logger.info("Quick tunnel ready: %s", self.url)
                try:
                    await self.on_url(self.url)
                except Exception:
                    logger.exception("Tunnel URL callback failed")
        code = await self._proc.wait()
        raise RuntimeError(f"cloudflared beendet (Code {code})")
