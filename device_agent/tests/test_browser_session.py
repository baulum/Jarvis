"""browser.session (UpTik on Sir's computer): argument checks without starting a browser."""

from __future__ import annotations

import pytest

from jarvis_device.actions.browser_session import BrowserSessions, SessionError, chrome_path


async def test_unknown_op_and_closed_session_are_refused() -> None:
    s = BrowserSessions()
    with pytest.raises(SessionError) as e:
        await s.handle({"op": "evaluate", "script": "alert(1)"}, lambda p: p)
    assert e.value.code == "invalid"  # no script evaluation, ever
    with pytest.raises(SessionError, match="nicht"):
        await s.handle({"op": "click", "session": "nope", "selector": "button"}, lambda p: p)


def test_chrome_path_is_a_file_or_none() -> None:
    p = chrome_path()
    assert p is None or p.endswith(("Google Chrome", "chrome.exe", "google-chrome", "google-chrome-stable", "chromium"))
