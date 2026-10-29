"""Platform adapters (one per publishing target)."""

from __future__ import annotations

from jarvis.uptik.adapters.base import PlatformAdapter
from jarvis.uptik.adapters.instagram import InstagramReelsAdapter
from jarvis.uptik.adapters.tiktok import TikTokAdapter
from jarvis.uptik.adapters.youtube import YouTubeShortsAdapter


def default_adapters() -> dict[str, PlatformAdapter]:
    return {a.platform: a for a in (TikTokAdapter(), InstagramReelsAdapter(), YouTubeShortsAdapter())}


__all__ = ["PlatformAdapter", "default_adapters"]
