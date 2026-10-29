"""Instagram Reels via instagram.com (Create → file → Next → Next → caption → Share).

Videos uploaded in the web dialog are published as Reels. UpTik has no Instagram adapter; this one is new."""

from __future__ import annotations

from jarvis.uptik.adapters.base import PlatformAdapter, StageFn
from jarvis.uptik.model import PublishJob, PublishResult, RetryableError
from jarvis.uptik.runners import BrowserSession

_CREATE_LABELS = ("New post", "Neuer Beitrag", "Create", "Erstellen")
# the clickable navigation entry (link / button around the icon) comes first: that is what takes the mouse click;
# the bare icon and the text label are fallbacks (the svg itself is only a child of the entry that reacts)
CREATE = (
    *(f'a:has(svg[aria-label="{t}"])' for t in _CREATE_LABELS),
    *(f'div[role="button"]:has(svg[aria-label="{t}"])' for t in _CREATE_LABELS),
    *(f'svg[aria-label="{t}"]' for t in _CREATE_LABELS),
    'a:has(span:text-is("Erstellen"))',
    'a:has(span:text-is("Create"))',
)
# exact entries first; the substring variants (``has-text`` also hits any link that merely contains "Post") only
# as fallback
CREATE_POST = (
    'a:has(span:text-is("Post"))',
    'a:has(span:text-is("Beitrag"))',
    'span:text-is("Post")',
    'span:text-is("Beitrag")',
    'div[role="menuitem"]:has-text("Post")',
    'div[role="menuitem"]:has-text("Beitrag")',
    'a:has-text("Post")',
    'span:has-text("Beitrag")',
)
# hidden inputs count too (Playwright's count()); video-accepting inputs first, so a page with several file fields
# (profile picture, stories) still gets the reel's one
FILE_INPUT = (
    'div[role="dialog"] input[type="file"]',
    'form input[type="file"][accept*="video"]',
    'input[type="file"][accept*="video"]',
    'input[type="file"]',
)
REELS_INFO_OK = ('div[role="dialog"] button:has-text("OK")',)
NEXT = (
    'div[role="dialog"] div[role="button"]:has-text("Next")',
    'div[role="dialog"] div[role="button"]:has-text("Weiter")',
    'div[role="dialog"] button:has-text("Next")',
    'div[role="dialog"] button:has-text("Weiter")',
)
CAPTION = (
    'div[role="dialog"] div[aria-label^="Write a caption"]',
    'div[role="dialog"] div[aria-label^="Bildunterschrift"]',
    'div[role="dialog"] div[contenteditable="true"]',
)
SHARE = (
    'div[role="dialog"] div[role="button"]:has-text("Share")',
    'div[role="dialog"] div[role="button"]:has-text("Teilen")',
    'div[role="dialog"] button:has-text("Share")',
    'div[role="dialog"] button:has-text("Teilen")',
)
ADVANCED = (
    'div[role="dialog"] div[role="button"]:has-text("Advanced settings")',
    'div[role="dialog"] div[role="button"]:has-text("Erweiterte Einstellungen")',
)
DONE = (
    'text="Your reel has been shared."',
    'text="Your reel has been shared"',
    'text="Dein Reel wurde geteilt."',
    'text="Dein Reel wurde geteilt"',
    'text="Reel shared"',
    'img[alt="Animated checkmark"]',
)
NOT_NOW = ('button:has-text("Not Now")', 'button:has-text("Jetzt nicht")')
DIALOG_FILE = (FILE_INPUT[0],)
# how long to wait for the Create menu after the 1st / each further click (a re-click on a menu that is merely slow
# would close it again, so the first wait is long), and how often Create / "Post" are clicked at most
MENU_WAIT_MS = (8_000, 4_000, 4_000)
POST_CLICKS = 3


class InstagramReelsAdapter(PlatformAdapter):
    platform = "instagram_reels"
    label = "Instagram Reels"
    login_url = "https://www.instagram.com/accounts/login/"
    upload_url = "https://www.instagram.com/"
    profile_url = "https://www.instagram.com/{handle}/reels/"
    logged_out_url_parts = ("/accounts/login", "/accounts/emailsignup")
    logged_in_selectors = (
        *CREATE,
        'a[href="/direct/inbox/"]',
        'svg[aria-label="Home"]',
        'svg[aria-label="Startseite"]',
    )

    async def step_error(self, s: BrowserSession, what: str, selectors: str | tuple[str, ...]) -> RetryableError:
        """Error naming the step it hung at, the selector(s) and the page address – so the cause is visible at once."""
        sel = selectors if isinstance(selectors, str) else " | ".join(selectors[:3])
        return RetryableError(f"{self.label}: {what} – Selektor: {sel}; Seite: {await s.url()}", code="selector")

    async def mouse_click(self, s: BrowserSession, selector: str, what: str) -> None:
        """A real mouse click (move, press, release at the centre of the element) – a plain ``click`` on Instagram's
        navigation can end with the entry merely focused and nothing opened."""
        try:
            await s.mouse_click(selector)
        except RetryableError as e:
            raise await self.step_error(s, f"{what} nicht klickbar ({e})", selector) from None

    async def open_create_menu(self, s: BrowserSession) -> str:
        """Click "Create" until the menu (or the dialog) shows; returns the selector that proved it open.

        Real mouse click, verified; if the menu does not open, click again (bounded), then Enter / Space on the focused
        entry. A step that never worked is reported by name."""
        opened = (*CREATE_POST, *DIALOG_FILE)
        create: str | None = None
        unclickable: RetryableError | None = None
        for attempt, wait_ms in enumerate(MENU_WAIT_MS):
            create = await self.first(s, CREATE, 15_000 if attempt == 0 else 2_000)
            if create is None:
                raise await self.step_error(s, "Erstellen nicht gefunden", CREATE)
            try:
                await self.mouse_click(s, create, "Erstellen")
                unclickable = None
            except RetryableError as e:
                unclickable = e
                await s.wait(1_000)
                continue
            await s.wait(800)
            sign = await self.first(s, opened, wait_ms)
            if sign:
                return sign
            await self.guard(s)  # a checkpoint instead of the menu is a manual review, not a selector retry
        if unclickable is not None:
            raise unclickable
        assert create is not None
        for key in ("Enter", " "):  # the click only focused the entry: activate it from the keyboard
            await s.press_on(create, key)
            await s.wait(800)
            sign = await self.first(s, opened, 4_000)
            if sign:
                return sign
            await self.guard(s)
        raise await self.step_error(s, "Erstellen-Menü nicht geöffnet (Klick, Wiederholung, Enter/Leertaste)", create)

    async def open_upload_dialog(self, s: BrowserSession) -> None:
        """Create → (menu) Post, each with a real mouse click; leaves the dialog's file field to ``publish``."""
        entry = await self.open_create_menu(s)
        if entry in DIALOG_FILE:  # no menu: the dialog opened directly
            return
        for attempt in range(POST_CLICKS):
            if attempt:  # menu gone → the click worked and the dialog is still loading
                again = await self.first(s, CREATE_POST, 500)
                if again is None:
                    return
                entry = again
            await self.mouse_click(s, entry, "Beitrag")
            if await self.first(s, FILE_INPUT, 6_000):
                return
            await self.guard(s)

    async def publish(self, s: BrowserSession, job: PublishJob, on_stage: StageFn) -> PublishResult:
        await s.goto(self.upload_url)
        await s.wait(2_500)
        await self.guard(s)
        await self.require_login(s)
        not_now = await self.first(s, NOT_NOW, 2_000)  # "Turn on notifications?" – declining changes nothing
        if not_now:
            await s.click(not_now)
        await self.open_upload_dialog(s)
        file_input = await self.first(s, FILE_INPUT, 30_000)
        if file_input is None:
            await self.guard(s)  # a checkpoint instead of the dialog is a manual review, not a selector retry
            raise await self.step_error(s, "Upload-Feld nicht gefunden (Beitrag angeklickt)", FILE_INPUT)
        await on_stage("uploading")
        await s.upload(file_input, job.video_path)
        await s.wait(3_000)
        await self.guard(s)
        ok = await self.first(s, REELS_INFO_OK, 3_000)
        if ok:
            await s.click(ok)
        for step in ("Zuschnitt", "Bearbeiten"):
            await self.click_first(s, NEXT, f"Weiter ({step})", timeout_ms=60_000)
            await s.wait(1_500)
            await self.guard(s)
        await on_stage("details")
        await self.replace_text(s, CAPTION, job.content.text(), "Bildunterschrift")
        o = job.content.options
        if o.get("allow_comments") is False or o.get("hide_like_count"):
            await self.click_first(s, ADVANCED, "Erweiterte Einstellungen", setting=True)
            if o.get("hide_like_count"):
                await self.set_checkbox(
                    s, ("Hide like", "„Gefällt mir“-Angaben", "Anzahl der „Gefällt mir“"), True, "Like-Zahl verbergen"
                )
            if o.get("allow_comments") is False:
                await self.set_checkbox(
                    s, ("Turn off commenting", "Kommentarfunktion deaktivieren"), True, "Kommentare deaktivieren"
                )
        share = await self.first(s, SHARE, 30_000)
        if share is None:
            raise RetryableError("Instagram: Teilen-Knopf nicht gefunden", code="selector")
        await self.submit(s, on_stage, share)
        if not await self.wait_confirmed(s, DONE, (), 300_000):
            raise RetryableError("Instagram hat die Veröffentlichung nicht bestätigt", code="unconfirmed")
        return PublishResult(url=self.post_url(job.handle))
