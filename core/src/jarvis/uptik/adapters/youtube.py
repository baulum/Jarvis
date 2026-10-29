"""YouTube Shorts via YouTube Studio's upload dialog (optional, off unless ``youtube_shorts_enabled``).

A vertical video of up to 3 minutes uploaded in Studio becomes a Short; there is no separate Shorts uploader."""

from __future__ import annotations

from jarvis.uptik.adapters.base import PlatformAdapter, StageFn, find_url
from jarvis.uptik.model import PermanentError, PublishJob, PublishResult, RetryableError
from jarvis.uptik.runners import BrowserSession

CREATE = ("#create-icon", "ytcp-button#create-icon", 'button[aria-label="Create"]', 'button[aria-label="Erstellen"]')
UPLOAD_ITEM = (
    "#text-item-0",
    'tp-yt-paper-item:has-text("Upload videos")',
    'tp-yt-paper-item:has-text("Videos hochladen")',
)
FILE_INPUT = ('ytcp-uploads-file-picker input[type="file"]', 'input[type="file"]')
TITLE = ("#title-textarea #textbox", 'ytcp-social-suggestions-textbox#title-textarea [contenteditable="true"]')
DESCRIPTION = (
    "#description-textarea #textbox",
    'ytcp-social-suggestions-textbox#description-textarea [contenteditable="true"]',
)
KIDS = {
    False: ('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]',),
    True: ('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_MFK"]',),
}
NEXT = ("#next-button:not([disabled])", 'ytcp-button#next-button:not([aria-disabled="true"])')
VISIBILITY = {
    "public": ('tp-yt-paper-radio-button[name="PUBLIC"]',),
    "unlisted": ('tp-yt-paper-radio-button[name="UNLISTED"]',),
    "private": ('tp-yt-paper-radio-button[name="PRIVATE"]',),
}
DONE_BUTTON = ("#done-button:not([disabled])", 'ytcp-button#done-button:not([aria-disabled="true"])')
DONE = ("ytcp-video-share-dialog", 'text="Video published"', 'text="Video veröffentlicht"', 'text="Video saved"')
LINK_RE = r"https://(?:youtu\.be/|(?:www\.)?youtube\.com/shorts/)[\w-]{6,}"


class YouTubeShortsAdapter(PlatformAdapter):
    platform = "youtube_shorts"
    label = "YouTube Shorts"
    login_url = "https://accounts.google.com/ServiceLogin?service=youtube&continue=https%3A%2F%2Fstudio.youtube.com%2F"
    upload_url = "https://studio.youtube.com/"
    logged_out_url_parts = ("accounts.google.com", "/servicelogin", "/signin")
    logged_in_selectors = CREATE

    async def publish(self, s: BrowserSession, job: PublishJob, on_stage: StageFn) -> PublishResult:
        await s.goto(self.upload_url)
        await s.wait(3_000)
        await self.guard(s)
        await self.require_login(s)
        await self.click_first(s, CREATE, "Erstellen", timeout_ms=30_000)
        await self.click_first(s, UPLOAD_ITEM, "Videos hochladen")
        file_input = await self.first(s, FILE_INPUT, 30_000)
        if file_input is None:
            raise RetryableError("YouTube: Upload-Feld nicht gefunden", code="selector")
        await on_stage("uploading")
        await s.upload(file_input, job.video_path)
        if await self.first(s, TITLE, 180_000) is None:
            await self.guard(s)
            raise RetryableError("YouTube hat das Video nicht rechtzeitig angenommen", code="processing")
        await self.guard(s)
        await on_stage("details")
        await self.replace_text(s, TITLE, job.content.title.strip(), "Titel")
        await self.replace_text(s, DESCRIPTION, job.content.text(), "Beschreibung")
        o = job.content.options
        await self.click_first(s, KIDS[bool(o.get("made_for_kids", False))], "Speziell für Kinder", setting=True)
        for _ in range(3):  # details → video elements → checks → visibility
            await self.click_first(s, NEXT, "Weiter", timeout_ms=60_000)
            await s.wait(1_500)
            await self.guard(s)
        vis = str(o.get("visibility", "public"))
        if vis not in VISIBILITY:
            raise PermanentError(f"YouTube: unbekannte Sichtbarkeit {vis}")
        await self.click_first(s, VISIBILITY[vis], "Sichtbarkeit", setting=True)
        await on_stage("processing")
        done = await self.first(s, DONE_BUTTON, 600_000)
        if done is None:
            raise RetryableError("YouTube: Fertig-Knopf wurde nicht freigegeben", code="processing")
        await self.submit(s, on_stage, done)
        if not await self.wait_confirmed(s, DONE, (), 180_000):
            raise RetryableError("YouTube hat die Veröffentlichung nicht bestätigt", code="unconfirmed")
        return PublishResult(url=find_url(await s.text("ytcp-video-share-dialog"), LINK_RE))
