"""Video hand-over: the narrow interface between a video production and the UpTik connector.

Any producer hands over a finished clip with ``UptikService.on_video_ready(VideoClip(...))``. The connector decides
the rest (off by default: ``handoff_enabled``; per account ``autopost``; approval always) and never raises back.

``SocialBridge`` connects the existing daily PJE video (``jarvis.social``) without changing it: it listens to the
``social.updated`` event (status ``ready``), reads the video and its per-platform captions and calls
``on_video_ready``. It is the only place of the connector that knows the social module; the work runs in a background
task, so a slow or failing hand-over never holds up the production step that published the event.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from jarvis.events import Event, bus, spawn

if TYPE_CHECKING:
    from jarvis.uptik.service import UptikService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class VideoClip:
    path: str
    source: str  # producer id, e.g. "social"
    source_ref: str  # the producer's id of this clip (hand-over is idempotent per source + ref)
    title: str = ""
    caption: str = ""
    hashtags: list[str] = field(default_factory=list)
    # per platform (tiktok | instagram_reels | youtube_shorts): {"caption", "hashtags", "title"}
    captions: dict[str, dict[str, Any]] = field(default_factory=dict)
    duration_s: float | None = None
    platforms: list[str] = field(default_factory=list)  # empty = every platform with autopost


_SOCIAL_MAP = {"tiktok": "tiktok", "instagram": "instagram_reels", "youtube": "youtube_shorts"}


def clip_from_social(video: Any, path: str) -> VideoClip:
    """A ``SocialVideo`` row → ``VideoClip`` (captions: text/description + hashtags; YouTube title)."""
    caps: dict[str, dict[str, Any]] = {}
    for src, dst in _SOCIAL_MAP.items():
        c = (video.captions or {}).get(src) or {}
        if not c:
            continue
        entry: dict[str, Any] = {
            "caption": str(c.get("description") if src == "youtube" else c.get("text") or ""),
            "hashtags": list(c.get("hashtags") or []),
        }
        if src == "youtube" and c.get("title"):
            entry["title"] = str(c["title"])[:100]
        caps[dst] = entry
    base = caps.get("tiktok") or next(iter(caps.values()), {"caption": "", "hashtags": []})
    return VideoClip(
        path=path,
        source="social",
        source_ref=str(video.id),
        title=str(video.title or "")[:100],
        caption=str(base.get("caption") or ""),
        hashtags=list(base.get("hashtags") or []),
        captions=caps,
        duration_s=float(video.duration_s) if video.duration_s else None,
    )


class SocialBridge:
    def __init__(self, svc: UptikService) -> None:
        self.svc = svc
        self._subscribed = False

    def start(self) -> None:
        social = getattr(self.svc.state, "social", None)
        if social is None or self._subscribed:
            return
        lib = getattr(social, "library_dir", None)
        if lib is not None and lib not in self.svc.extra_video_roots:
            self.svc.extra_video_roots.append(lib)
        bus.subscribe("social.updated", self._on_event)
        self._subscribed = True

    def stop(self) -> None:
        if self._subscribed:
            bus.unsubscribe("social.updated", self._on_event)
            self._subscribed = False

    async def _on_event(self, ev: Event) -> None:
        if ev.data.get("status") != "ready" or not ev.data.get("video_id"):
            return
        if not self.svc.active() or not self.svc.settings["handoff_enabled"]:
            return
        spawn(self.handoff(str(ev.data["video_id"])), name="uptik-handoff")

    async def handoff(self, video_id: str) -> dict[str, Any] | None:
        try:
            social = self.svc.state.social
            v = await social.repo.get(uuid.UUID(video_id))
            if v is None or v.status != "ready":
                return None
            path = social.file_path(v.file_name)
            if path is None:
                return None
            return await self.svc.on_video_ready(clip_from_social(v, str(path)))
        except Exception:
            logger.exception("Social → UpTik hand-over failed (production unaffected)")
            return None
