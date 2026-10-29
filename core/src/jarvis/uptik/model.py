"""Shared vocabulary of the UpTik connector: platforms, post states, error classes and the content of a post."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from jarvis.tools.executor import ToolUserError

PLATFORMS: tuple[str, ...] = ("tiktok", "instagram_reels", "youtube_shorts")
PLATFORM_LABEL = {"tiktok": "TikTok", "instagram_reels": "Instagram Reels", "youtube_shorts": "YouTube Shorts"}
CONNECTOR_ID = "uptik"

# queued → running → published | retry_wait → … | failed | blocked (account stopped) | needs_review | cancelled
POST_STATUSES = ("queued", "running", "retry_wait", "published", "failed", "cancelled", "blocked", "needs_review")
OPEN_STATUSES = ("queued", "running", "retry_wait", "blocked")
FINISHED_STATUSES = ("published", "failed", "cancelled", "needs_review")
# The adapter calls on_stage("submitting") right before it clicks the final publish button. From then on a post is
# never retried automatically: whether it went out is unknown, a second run could post it twice.
SUBMITTING = "submitting"

ErrorClass = Literal["retryable", "permanent", "manual_review"]


class UptikError(ToolUserError):
    """An expected failure with a German message that is safe to show (no cookies, tokens or page dumps)."""

    error_class: ErrorClass = "permanent"

    def __init__(self, message: str, *, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


class RetryableError(UptikError):
    """Transient: network, slow processing, a page that did not load. Retried with exponential backoff."""

    error_class: ErrorClass = "retryable"


class PermanentError(UptikError):
    """Will not get better by trying again: file missing, content rejected, option not settable."""

    error_class: ErrorClass = "permanent"


class ManualReviewError(UptikError):
    """The platform put up a roadblock (captcha, checkpoint, 2FA, rate limit, HTTP 429, unusual activity) or wants
    a new login. Trips the circuit breaker of the account: its queue stops, Sir is notified, nothing is retried."""

    error_class: ErrorClass = "manual_review"


_TAG_RE = re.compile(r"^[^\W_][\w]{0,99}$", re.UNICODE)


def normalize_hashtags(raw: Any) -> list[str]:
    """ "#Webdesign, kmu  #Webdesign" → ["#Webdesign", "#kmu"]: split, strip '#', drop duplicates (case-insensitive).

    Raises ``ToolUserError`` for tags with spaces or punctuation – platforms would cut them silently."""
    if raw is None or raw == "":
        return []
    parts = re.split(r"[\s,;]+", raw) if isinstance(raw, str) else [str(x) for x in raw]
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        tag = part.strip().lstrip("#")
        if not tag:
            continue
        if not _TAG_RE.match(tag):
            raise ToolUserError(f"Ungültiger Hashtag „#{tag[:40]}“ – nur Buchstaben, Ziffern und _")
        if tag.lower() not in seen:
            seen.add(tag.lower())
            out.append("#" + tag)
    return out


@dataclass(slots=True)
class PostContent:
    caption: str = ""
    hashtags: list[str] = field(default_factory=list)
    title: str = ""  # YouTube only
    options: dict[str, Any] = field(default_factory=dict)

    def text(self) -> str:
        """What goes into the caption field: text, blank line, hashtags."""
        tags = " ".join(self.hashtags)
        body = self.caption.strip()
        return f"{body}\n\n{tags}".strip() if tags else body


@dataclass(slots=True)
class VideoInfo:
    path: str
    name: str
    size: int
    sha256: str
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None
    has_video: bool | None = None  # None: not probed (no ffprobe)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "size": self.size,
            "sha256": self.sha256,
            "duration_s": self.duration_s,
            "width": self.width,
            "height": self.height,
            "has_video": self.has_video,
        }


@dataclass(slots=True)
class PublishJob:
    """What an adapter needs for one post on one account (the video path as the runner's browser sees it)."""

    post_id: str
    account_id: str
    platform: str
    video_path: str
    content: PostContent
    handle: str = ""


@dataclass(slots=True)
class PublishResult:
    url: str | None = None
    note: str = ""
