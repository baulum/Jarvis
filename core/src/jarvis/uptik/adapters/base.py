"""Port of a platform: log-in check and the upload flow on the platform's own web page.

Rules every adapter follows:

* ``guard()`` before and after every significant step: a roadblock (``circuit.detect``) raises ``ManualReviewError``
  – the adapter never clicks, closes or removes anything on such a page.
* Selectors come as fallback lists (``first``); a missing element is ``RetryableError`` before the upload started
  and ``PermanentError`` for settings Sir chose (never publish with a setting silently ignored).
* ``on_stage(SUBMITTING)`` right before the final click. Success must be confirmed on the page; otherwise the
  service marks the post ``needs_review`` instead of trying again.
"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence

from jarvis.uptik.circuit import LOGIN_REQUIRED, detect
from jarvis.uptik.model import SUBMITTING, PermanentError, PublishJob, PublishResult, RetryableError
from jarvis.uptik.runners import BrowserSession

StageFn = Callable[[str], Awaitable[None]]


class PlatformAdapter(ABC):
    platform: str = ""
    label: str = ""
    login_url: str = ""
    upload_url: str = ""
    profile_url: str = ""  # with {handle}
    logged_out_url_parts: tuple[str, ...] = ()
    logged_in_selectors: tuple[str, ...] = ()
    poll_ms: int = 2_000

    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self.now = now  # monotonic seconds; tests pass the fake browser's clock

    # ------------------------------------------------------------------ helpers
    async def guard(self, s: BrowserSession) -> None:
        signal = detect(await s.url(), await s.text("body"), await s.statuses())
        if signal is not None:
            raise signal.error()

    async def require_login(self, s: BrowserSession) -> None:
        url = (await s.url()).lower()
        if any(p in url for p in self.logged_out_url_parts):
            raise LOGIN_REQUIRED.error()

    async def first(self, s: BrowserSession, selectors: Sequence[str], timeout_ms: int = 15_000) -> str | None:
        """The first selector of the list that appears within ``timeout_ms`` (all are tried in every round)."""
        deadline = self.now() + timeout_ms / 1000
        while True:
            for sel in selectors:
                if await s.exists(sel):
                    return sel
            if self.now() >= deadline:
                return None
            await s.wait(min(self.poll_ms, max(int((deadline - self.now()) * 1000), 1)))

    async def click_first(
        self, s: BrowserSession, selectors: Sequence[str], what: str, *, timeout_ms: int = 15_000, setting: bool = False
    ) -> None:
        sel = await self.first(s, selectors, timeout_ms)
        if sel is None:
            if setting:
                raise PermanentError(f"{self.label}: Einstellung „{what}“ nicht gefunden – nichts veröffentlicht")
            raise RetryableError(f"{self.label}: „{what}“ nicht gefunden", code="selector")
        await s.click(sel)

    async def replace_text(self, s: BrowserSession, selectors: Sequence[str], value: str, what: str) -> None:
        sel = await self.first(s, selectors, 30_000)
        if sel is None:
            raise RetryableError(f"{self.label}: Feld „{what}“ nicht gefunden", code="selector")
        await s.click(sel)
        await s.press("ControlOrMeta+a")
        await s.press("Backspace")
        if value:
            await s.insert_text(sel, value)

    async def set_checkbox(self, s: BrowserSession, labels: Sequence[str], want: bool, what: str) -> None:
        """Set a labelled checkbox/switch to ``want`` and verify it – a setting Sir chose is never left as it was."""

        async def state() -> tuple[bool, str] | None:
            """(checked, selector to click) of the first matching control."""
            for t in labels:
                box = f'label:has-text("{t}") input[type="checkbox"]'
                if await s.exists(box):
                    return await s.exists(box + ":checked"), f'label:has-text("{t}")'
                switch = f'[role="switch"][aria-label*="{t}" i]'
                if await s.exists(switch):
                    return await s.exists(switch + '[aria-checked="true"]'), switch
            return None

        cur = await state()
        if cur is None:
            raise PermanentError(f"{self.label}: Einstellung „{what}“ nicht gefunden – nichts veröffentlicht")
        if cur[0] == want:
            return
        await s.click(cur[1])
        await s.wait(500)
        after = await state()
        if after is None or after[0] != want:
            raise PermanentError(f"{self.label}: Einstellung „{what}“ ließ sich nicht setzen – nichts veröffentlicht")

    async def wait_confirmed(
        self, s: BrowserSession, selectors: Sequence[str], url_parts: Sequence[str], timeout_ms: int
    ) -> bool:
        """Poll until a success marker shows (guarding on every round)."""
        deadline = self.now() + timeout_ms / 1000
        while self.now() < deadline:
            await self.guard(s)
            url = await s.url()
            if any(p in url for p in url_parts):
                return True
            for sel in selectors:
                if await s.exists(sel):
                    return True
            await s.wait(self.poll_ms)
        return False

    # ------------------------------------------------------------------ login
    async def is_logged_in(self, s: BrowserSession) -> bool:
        """Only looks at the current page (Sir may be typing): not on a login address and a logged-in marker."""
        url = (await s.url()).lower()
        if not url.startswith("http") or any(p in url for p in self.logged_out_url_parts):
            return False
        for sel in self.logged_in_selectors:
            if await s.exists(sel):
                return True
        return False

    async def confirm_login(self, s: BrowserSession) -> bool:
        """After the login: open the upload page once and check that it is usable."""
        await s.goto(self.upload_url)
        await s.wait(2_000)
        url = (await s.url()).lower()
        if any(p in url for p in self.logged_out_url_parts):
            return False
        return await self.first(s, self.logged_in_selectors, 20_000) is not None

    async def handle(self, s: BrowserSession) -> str:
        """Best effort: the account's @name from the page, else ""."""
        return ""

    def post_url(self, handle: str) -> str | None:
        return self.profile_url.format(handle=handle.lstrip("@")) if self.profile_url and handle else None

    # ------------------------------------------------------------------ publish
    @abstractmethod
    async def publish(self, s: BrowserSession, job: PublishJob, on_stage: StageFn) -> PublishResult: ...

    async def submit(self, s: BrowserSession, on_stage: StageFn, selector: str) -> None:
        await self.guard(s)
        await on_stage(SUBMITTING)
        await s.click(selector)


def find_url(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text or "")
    return m.group(0) if m else None
