"""Checks before a post is queued – per platform, so a post fails here and not halfway through an upload.

The limits are deliberately conservative (platforms raise and change them); they are data in ``RULES``. Video
facts come from ffprobe when it is installed; without it duration and format are not checked (said in the result).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.uptik.model import PLATFORM_LABEL, PostContent, VideoInfo

GIB = 1024**3


@dataclass(frozen=True, slots=True)
class PlatformRules:
    caption_max: int
    hashtags_max: int
    extensions: tuple[str, ...]
    max_bytes: int
    min_s: float
    max_s: float
    title_max: int = 0  # 0 = the platform has no title field
    vertical: bool = False  # height >= width required


RULES: dict[str, PlatformRules] = {
    "tiktok": PlatformRules(2200, 30, (".mp4", ".mov", ".webm"), 2 * GIB, 3, 600),
    "instagram_reels": PlatformRules(2200, 30, (".mp4", ".mov"), 1 * GIB, 3, 180),
    "youtube_shorts": PlatformRules(5000, 15, (".mp4", ".mov", ".webm"), 2 * GIB, 1, 180, title_max=100, vertical=True),
}

# Platform settings Sir can choose per post: name → allowed values (bool or a list of strings) and the default.
OPTIONS: dict[str, dict[str, tuple[Any, Any]]] = {
    "tiktok": {
        "visibility": (["public", "friends", "private"], "public"),
        "allow_comments": (bool, True),
        "allow_duet": (bool, True),
        "allow_stitch": (bool, True),
        "ai_generated": (bool, False),
    },
    "instagram_reels": {
        "allow_comments": (bool, True),
        "hide_like_count": (bool, False),
    },
    "youtube_shorts": {
        "visibility": (["public", "unlisted", "private"], "public"),
        "made_for_kids": (bool, False),
    },
}


def default_options(platform: str) -> dict[str, Any]:
    return {k: default for k, (_, default) in OPTIONS.get(platform, {}).items()}


def options_schema() -> dict[str, Any]:
    """JSON schema of ``options`` in the tool arguments: {platform: {option: value}}."""
    props: dict[str, Any] = {}
    for pf, opts in OPTIONS.items():
        inner = {k: ({"type": "boolean"} if kind is bool else {"enum": list(kind)}) for k, (kind, _) in opts.items()}
        props[pf] = {"type": "object", "properties": inner, "additionalProperties": False}
    return {"type": "object", "properties": props, "additionalProperties": False}


def merged_options(platform: str, given: dict[str, Any] | None) -> dict[str, Any]:
    out = default_options(platform)
    for k, v in (given or {}).items():
        if k in out:
            out[k] = v
    return out


def validate(platform: str, content: PostContent, video: VideoInfo | None) -> list[str]:
    """Problems (German, empty = fine) for one platform."""
    rules = RULES.get(platform)
    if rules is None:
        return [f"Unbekannte Plattform {platform}"]
    label = PLATFORM_LABEL[platform]
    out: list[str] = []
    text = content.text()
    if len(text) > rules.caption_max:
        out.append(f"{label}: Text mit Hashtags hat {len(text)} Zeichen, erlaubt sind {rules.caption_max}")
    if len(content.hashtags) > rules.hashtags_max:
        out.append(f"{label}: {len(content.hashtags)} Hashtags, erlaubt sind {rules.hashtags_max}")
    if rules.title_max:
        title = content.title.strip()
        if not title:
            out.append(f"{label}: Titel fehlt")
        elif len(title) > rules.title_max:
            out.append(f"{label}: Titel hat {len(title)} Zeichen, erlaubt sind {rules.title_max}")
        if "<" in title or ">" in title:
            out.append(f"{label}: Titel darf keine spitzen Klammern enthalten")
    if not text and not rules.title_max:
        out.append(f"{label}: Caption fehlt")
    for k, v in content.options.items():
        spec = OPTIONS.get(platform, {}).get(k)
        if spec is None:
            out.append(f"{label}: unbekannte Einstellung {k}")
        elif spec[0] is bool and not isinstance(v, bool):
            out.append(f"{label}: {k} muss ja/nein sein")
        elif spec[0] is not bool and v not in spec[0]:
            out.append(f"{label}: {k} muss eines von {', '.join(spec[0])} sein")
    if video is None:
        return out
    ext = Path(video.name).suffix.lower()
    if ext not in rules.extensions:
        out.append(f"{label}: Dateiformat {ext or '?'} nicht erlaubt ({', '.join(rules.extensions)})")
    if video.size <= 0:
        out.append(f"{label}: Videodatei ist leer")
    elif video.size > rules.max_bytes:
        out.append(f"{label}: Video ist {video.size / GIB:.2f} GB groß, erlaubt sind {rules.max_bytes / GIB:.0f} GB")
    if video.has_video is False:
        out.append(f"{label}: Die Datei enthält keine Videospur")
    if video.duration_s is not None:
        if video.duration_s < rules.min_s:
            out.append(f"{label}: Video ist kürzer als {rules.min_s:.0f} s")
        if video.duration_s > rules.max_s:
            out.append(f"{label}: Video ist {video.duration_s:.0f} s lang, erlaubt sind {rules.max_s:.0f} s")
    if rules.vertical and video.width and video.height and video.height < video.width:
        out.append(f"{label}: Shorts müssen hochkant oder quadratisch sein")
    return out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


async def probe_video(path: Path, ffprobe: str | None = None) -> VideoInfo:
    """Size and SHA-256 always; duration and resolution when ffprobe is available (else ``None``)."""
    size = path.stat().st_size
    digest = await asyncio.to_thread(_sha256, path)
    info = VideoInfo(str(path), path.name, size, digest)
    exe = ffprobe or shutil.which("ffprobe")
    if not exe:
        return info
    try:
        proc = await asyncio.create_subprocess_exec(
            exe,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), 60)
        data = json.loads(out.decode(errors="replace") or "{}")
    except (OSError, TimeoutError, ValueError):
        return info
    video = next((s for s in data.get("streams") or [] if s.get("codec_type") == "video"), None)
    info.has_video = video is not None
    if video is not None:
        info.width, info.height = int(video.get("width") or 0) or None, int(video.get("height") or 0) or None
    try:
        info.duration_s = float((data.get("format") or {}).get("duration") or "")
    except (TypeError, ValueError):
        info.duration_s = None
    return info
