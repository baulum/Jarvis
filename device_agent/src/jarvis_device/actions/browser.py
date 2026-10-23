"""Browser automation on the device with Playwright (optional extra: ``pip install jarvis-device[browser]``,
then ``playwright install chromium``). Edge is used where installed (Windows), otherwise Chromium.

Only declarative steps – no JavaScript evaluation: goto, click, fill, press, wait, text, screenshot.
"""

from __future__ import annotations

import base64
import importlib.util
import sys
from typing import Any

ALLOWED_SCHEMES = ("http://", "https://")
STEP_KINDS = ("goto", "click", "fill", "press", "wait", "text", "screenshot")
MAX_TEXT = 12_000


def available() -> tuple[bool, str]:
    if importlib.util.find_spec("playwright") is None:
        return False, "Playwright fehlt (pip install 'jarvis-device[browser]' und playwright install chromium)"
    return True, ""


def validate_steps(steps: Any) -> list[dict[str, Any]]:
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps muss eine nicht leere Liste sein")
    out = []
    for i, step in enumerate(steps[:40]):
        if not isinstance(step, dict):
            raise ValueError(f"Schritt {i + 1} ist kein Objekt")
        kinds = [k for k in STEP_KINDS if k in step]
        if len(kinds) != 1:
            raise ValueError(f"Schritt {i + 1}: genau eine Aktion aus {', '.join(STEP_KINDS)} angeben")
        kind = kinds[0]
        if kind == "goto" and not str(step["goto"]).startswith(ALLOWED_SCHEMES):
            raise ValueError(f"Schritt {i + 1}: nur http(s)-Adressen")
        if kind == "fill" and "value" not in step:
            raise ValueError(f"Schritt {i + 1}: fill braucht value")
        out.append({"kind": kind, **step})
    return out


async def automate(steps: Any, headless: bool = False) -> dict[str, Any]:
    ok, reason = available()
    if not ok:
        raise RuntimeError(reason)
    from playwright.async_api import async_playwright

    plan = validate_steps(steps)
    texts: list[str] = []
    shots: list[str] = []
    async with async_playwright() as pw:
        browser = None
        if sys.platform.startswith("win"):
            try:
                browser = await pw.chromium.launch(channel="msedge", headless=headless)
            except Exception:
                browser = None
        if browser is None:
            browser = await pw.chromium.launch(headless=headless)
        try:
            page = await browser.new_page()
            page.set_default_timeout(20_000)
            for step in plan:
                kind = step["kind"]
                if kind == "goto":
                    await page.goto(str(step["goto"]), wait_until="domcontentloaded")
                elif kind == "click":
                    await page.click(str(step["click"]))
                elif kind == "fill":
                    await page.fill(str(step["fill"]), str(step["value"]))
                elif kind == "press":
                    if step.get("selector"):
                        await page.press(str(step["selector"]), str(step["press"]))
                    else:
                        await page.keyboard.press(str(step["press"]))
                elif kind == "wait":
                    target = step["wait"]
                    if isinstance(target, int | float):
                        await page.wait_for_timeout(min(float(target), 15_000))
                    else:
                        await page.wait_for_selector(str(target))
                elif kind == "text":
                    sel = str(step["text"] or "body")
                    texts.append((await page.inner_text(sel))[:MAX_TEXT])
                elif kind == "screenshot":
                    png = await page.screenshot(full_page=bool(step.get("full_page")))
                    shots.append(base64.b64encode(png).decode())
            title = await page.title()
            url = page.url
        finally:
            await browser.close()
    return {
        "url": url,
        "title": title,
        "text": "\n\n".join(texts)[:MAX_TEXT],
        "screenshots": len(shots),
        "screenshot_png_base64": shots[-1] if shots else None,
    }
