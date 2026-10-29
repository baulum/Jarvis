"""TikTok via TikTok Studio's web upload page (www.tiktok.com/tiktokstudio/upload)."""

from __future__ import annotations

from jarvis.uptik.adapters.base import PlatformAdapter, StageFn
from jarvis.uptik.model import PublishJob, PublishResult, RetryableError
from jarvis.uptik.runners import BrowserSession

FILE_INPUT = ('input[type="file"][accept*="video"]', 'input[type="file"]')
EDITOR = ('div[contenteditable="true"]', ".public-DraftEditor-content", '[data-e2e="caption-editor"] [contenteditable]')
POST_READY = (
    'button[data-e2e="post_video_button"]:not([disabled]):not([aria-disabled="true"])',
    'button:has-text("Post"):not([disabled]):not([aria-disabled="true"])',
    'button:has-text("Posten"):not([disabled]):not([aria-disabled="true"])',
    'button:has-text("Veröffentlichen"):not([disabled]):not([aria-disabled="true"])',
)
DONE = (
    'text="Your video has been uploaded"',
    'text="Dein Video wurde hochgeladen"',
    'text="Video published"',
    'text="Video veröffentlicht"',
    '[data-e2e="upload-success"]',
)
VISIBILITY = {
    "public": ("Everyone", "Alle", "Öffentlich"),
    "friends": ("Friends", "Freunde"),
    "private": ("Only you", "Nur du", "Privat"),
}
VISIBILITY_TRIGGER = (
    '[data-e2e="video_visibility_container"]',
    'div:has-text("Who can watch this video") >> [role="combobox"]',
    'div:has-text("Wer kann dieses Video ansehen") >> [role="combobox"]',
)
MORE = ('div:has-text("Show more")', 'div:has-text("Mehr anzeigen")', 'button:has-text("Show more")')


class TikTokAdapter(PlatformAdapter):
    platform = "tiktok"
    label = "TikTok"
    login_url = "https://www.tiktok.com/login"
    upload_url = "https://www.tiktok.com/tiktokstudio/upload?from=webapp"
    profile_url = "https://www.tiktok.com/@{handle}"
    logged_out_url_parts = ("/login", "/signup")
    logged_in_selectors = (
        *FILE_INPUT,
        '[data-e2e="profile-icon"]',
        '[data-e2e="nav-profile"]',
        '[data-e2e="upload-icon"]',
    )

    async def publish(self, s: BrowserSession, job: PublishJob, on_stage: StageFn) -> PublishResult:
        await s.goto(self.upload_url)
        await s.wait(2_000)
        await self.guard(s)
        await self.require_login(s)
        file_input = await self.first(s, FILE_INPUT, 30_000)
        if file_input is None:
            raise RetryableError("TikTok: Upload-Feld nicht gefunden", code="selector")
        await on_stage("uploading")
        await s.upload(file_input, job.video_path)
        if await self.first(s, EDITOR, 180_000) is None:
            await self.guard(s)
            raise RetryableError("TikTok hat das Video nicht rechtzeitig verarbeitet", code="processing")
        await self.guard(s)
        await on_stage("details")
        await self.replace_text(s, EDITOR, job.content.text(), "Beschreibung")
        await self._settings(s, job)
        await on_stage("processing")
        post = await self.first(s, POST_READY, 600_000)
        if post is None:
            await self.guard(s)
            raise RetryableError("TikTok: Veröffentlichen-Knopf wurde nicht freigegeben", code="processing")
        await self.submit(s, on_stage, post)
        # A content-check prompt of TikTok ("Continue to post?") is not answered: no confirmation → needs_review.
        ok = await self.wait_confirmed(s, DONE, ("/tiktokstudio/content",), 120_000)
        if not ok:
            raise RetryableError("TikTok hat die Veröffentlichung nicht bestätigt", code="unconfirmed")
        return PublishResult(url=self.post_url(job.handle))

    async def _settings(self, s: BrowserSession, job: PublishJob) -> None:
        o = job.content.options
        if o.get("visibility", "public") != "public":
            await self.click_first(s, VISIBILITY_TRIGGER, "Sichtbarkeit", setting=True)
            labels = VISIBILITY[str(o["visibility"])]
            await self.click_first(
                s, [f'[role="option"]:has-text("{t}")' for t in labels], "Sichtbarkeit", setting=True
            )
        wanted = {k: o[k] for k in ("allow_comments", "allow_duet", "allow_stitch") if o.get(k) is False}
        if wanted or o.get("ai_generated"):
            sel = await self.first(s, MORE, 3_000)
            if sel:
                await s.click(sel)
        if "allow_comments" in wanted:
            await self.set_checkbox(s, ("Comment", "Kommentar"), False, "Kommentare erlauben")
        if "allow_duet" in wanted:
            await self.set_checkbox(s, ("Duet",), False, "Duett erlauben")
        if "allow_stitch" in wanted:
            await self.set_checkbox(s, ("Stitch",), False, "Stitch erlauben")
        if o.get("ai_generated"):
            await self.set_checkbox(s, ("AI-generated", "KI-generiert"), True, "KI-generierte Inhalte")
