"""Instagram Reels via instagram.com (Create → file → Next → Next → caption → Share).

Videos uploaded in the web dialog are published as Reels. UpTik has no Instagram adapter; this one is new."""

from __future__ import annotations

from jarvis.uptik.adapters.base import PlatformAdapter, StageFn
from jarvis.uptik.model import PublishJob, PublishResult, RetryableError
from jarvis.uptik.runners import BrowserSession

CREATE = (
    'svg[aria-label="New post"]',
    'svg[aria-label="Neuer Beitrag"]',
    'svg[aria-label="Create"]',
    'svg[aria-label="Erstellen"]',
)
CREATE_POST = (
    'a:has-text("Post")',
    'span:has-text("Beitrag")',
    'div[role="menuitem"]:has-text("Post")',
    'div[role="menuitem"]:has-text("Beitrag")',
    'span:text-is("Post")',
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

    async def publish(self, s: BrowserSession, job: PublishJob, on_stage: StageFn) -> PublishResult:
        await s.goto(self.upload_url)
        await s.wait(2_500)
        await self.guard(s)
        await self.require_login(s)
        not_now = await self.first(s, NOT_NOW, 2_000)  # "Turn on notifications?" – declining changes nothing
        if not_now:
            await s.click(not_now)
        await self.click_first(s, CREATE, "Erstellen")
        # the menu can render a few seconds after "Create"; without a menu the dialog may open directly
        sub = await self.first(s, CREATE_POST, 8_000)
        if sub:
            await s.click(sub)
        file_input = await self.first(s, FILE_INPUT, 30_000)
        if file_input is None:
            await self.guard(s)  # a checkpoint instead of the dialog is a manual review, not a selector retry
            raise RetryableError("Instagram: Upload-Feld nicht gefunden", code="selector")
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
