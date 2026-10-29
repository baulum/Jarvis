"""Instagram Reels: finding the upload field of the create dialog – against a mocked browser page only.

``DomPage`` stands in for the browser. A selector "exists" from a given moment on (seconds of the page's own clock,
advanced only by ``wait``); a click can reveal further elements or change the address. Hidden inputs count as
existing, like Playwright's ``count()``. Nothing is opened, typed or posted for real: the tests check which selectors
the adapter asked for and which actions it sent.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from jarvis.uptik.adapters import instagram
from jarvis.uptik.adapters.instagram import InstagramReelsAdapter
from jarvis.uptik.model import ManualReviewError, PostContent, PublishJob, RetryableError

VIDEO = "/videos/pje-reel.mp4"
PROFILE = "https://www.instagram.com/pje_systems/reels/"

# the usual ways the upload field shows up in the create dialog
FIELD_VARIANTS = {
    "im Dialog, ausgeblendet": 'div[role="dialog"] input[type="file"]',
    "im Formular, Video-Accept": 'form input[type="file"][accept*="video"]',
    "frei im Dokument, Video-Accept": 'input[type="file"][accept*="video"]',
    "frei im Dokument, ohne accept": 'input[type="file"]',
}
# the "Post" entry of the Create menu, English and German, as link, span or menu item
MENU_ITEMS = ('a:has-text("Post")', 'span:has-text("Beitrag")', 'span:text-is("Post")')


class DomPage:
    def __init__(self, appears: dict[str, float], *, on_click: dict[str, Callable[[DomPage], None]]) -> None:
        self.appears = appears  # selector → second of the page clock from which it exists
        self.on_click = on_click  # selector → effect of a click
        self.address = "https://www.instagram.com/"
        self.clock = 0.0
        self.actions: list[tuple[Any, ...]] = []

    def show(self, selector: str) -> None:
        self.appears[selector] = self.clock

    async def goto(self, url: str) -> None:
        self.actions.append(("goto", url))
        self.address = url

    async def url(self) -> str:
        return self.address

    async def text(self, selector: str = "body") -> str:
        return ""

    async def exists(self, selector: str) -> bool:
        at = self.appears.get(selector)
        return at is not None and self.clock >= at

    async def wait_for(self, selector: str, timeout_ms: int = 15_000) -> bool:
        return await self.exists(selector)

    async def click(self, selector: str) -> None:
        assert await self.exists(selector), f"click on a missing element: {selector}"
        self.actions.append(("click", selector))
        if selector in self.on_click:
            self.on_click[selector](self)

    async def fill(self, selector: str, value: str) -> None:
        self.actions.append(("fill", selector, value))

    async def insert_text(self, selector: str, value: str) -> None:
        assert await self.exists(selector), f"text into a missing element: {selector}"
        self.actions.append(("insert_text", selector, value))

    async def press(self, key: str) -> None:
        self.actions.append(("press", key))

    async def upload(self, selector: str, path: str) -> None:
        assert await self.exists(selector), f"upload into a missing element: {selector}"
        self.actions.append(("upload", selector, path))

    async def wait(self, ms: int) -> None:
        self.clock += ms / 1000

    async def statuses(self) -> list[int]:
        return []

    async def storage_state(self) -> dict[str, Any]:
        return {}

    async def close(self) -> None:
        return None


def instagram_page(
    *,
    field: str | None,
    field_at: float = 0.0,
    menu: str | None = MENU_ITEMS[0],
    menu_at: float = 0.0,
    field_after_menu: bool = False,
    on_create: Callable[[DomPage], None] | None = None,
) -> DomPage:
    """instagram.com up to and including the upload field, with the rest of the flow present from the start."""
    appears = {
        instagram.CREATE[0]: 0.0,
        instagram.NEXT[0]: 0.0,
        instagram.CAPTION[0]: 0.0,
        instagram.SHARE[0]: 0.0,
        instagram.DONE[0]: 0.0,
    }
    on_click: dict[str, Callable[[DomPage], None]] = {}
    if on_create is not None:
        on_click[instagram.CREATE[0]] = on_create
    if menu is not None:
        appears[menu] = menu_at
    if field is not None and field_after_menu:
        assert menu is not None
        on_click[menu] = lambda p: p.show(field)  # the dialog's field exists only after "Post" was chosen
    elif field is not None:
        appears[field] = field_at
    return DomPage(appears, on_click=on_click)


def job() -> PublishJob:
    return PublishJob(
        post_id="post-1",
        account_id="acct-1",
        platform="instagram_reels",
        video_path=VIDEO,
        content=PostContent(caption="Website in 7 Tagen", hashtags=["#webdesign"]),
        handle="pje_systems",
    )


async def publish(page: DomPage) -> tuple[Any, list[str]]:
    stages: list[str] = []

    async def on_stage(stage: str) -> None:
        stages.append(stage)

    result = await InstagramReelsAdapter(now=lambda: page.clock).publish(page, job(), on_stage)
    return result, stages


@pytest.mark.parametrize("field", list(FIELD_VARIANTS.values()), ids=list(FIELD_VARIANTS.keys()))
async def test_upload_field_is_found_in_each_usual_variant(field: str) -> None:
    page = instagram_page(field=field)

    result, stages = await publish(page)

    assert result.url == PROFILE
    assert ("upload", field, VIDEO) in page.actions
    assert stages == ["uploading", "details", "submitting"]


@pytest.mark.parametrize("menu", MENU_ITEMS)
async def test_menu_item_that_renders_late_is_clicked_and_opens_the_field(menu: str) -> None:
    field = FIELD_VARIANTS["im Dialog, ausgeblendet"]
    page = instagram_page(field=field, menu=menu, menu_at=9.0, field_after_menu=True)

    result, _ = await publish(page)

    assert ("click", menu) in page.actions
    assert ("upload", field, VIDEO) in page.actions
    assert result.url == PROFILE


async def test_field_that_appears_late_is_waited_for() -> None:
    field = FIELD_VARIANTS["frei im Dokument, Video-Accept"]
    page = instagram_page(field=field, field_at=25.0)

    result, _ = await publish(page)

    assert ("upload", field, VIDEO) in page.actions
    assert result.url == PROFILE


async def test_dialog_that_opens_without_menu_still_gets_its_field() -> None:
    field = FIELD_VARIANTS["im Dialog, ausgeblendet"]
    page = instagram_page(field=field, menu=None)

    result, _ = await publish(page)

    assert ("upload", field, VIDEO) in page.actions
    assert result.url == PROFILE


async def test_missing_field_is_a_retryable_selector_error_and_nothing_is_uploaded() -> None:
    page = instagram_page(field=None)

    with pytest.raises(RetryableError, match="Upload-Feld nicht gefunden") as exc:
        await publish(page)

    assert exc.value.code == "selector"
    assert not any(a[0] in ("upload", "insert_text") for a in page.actions)
    assert ("click", instagram.SHARE[0]) not in page.actions


async def test_checkpoint_instead_of_the_dialog_is_a_manual_review_not_a_retry() -> None:
    def checkpoint(p: DomPage) -> None:
        p.address = "https://www.instagram.com/challenge/xyz/"

    page = instagram_page(field=None, on_create=checkpoint)

    with pytest.raises(ManualReviewError) as exc:
        await publish(page)

    assert exc.value.code == "checkpoint"
    assert not any(a[0] == "upload" for a in page.actions)
