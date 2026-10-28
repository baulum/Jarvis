"""FFmpeg/ffprobe helpers: probe a video, measure loudness, extract frames, shrink to a size limit.

All calls are plain argv lists (no shell) with a timeout; output is parsed defensively."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class MediaError(RuntimeError):
    pass


async def run(argv: list[str], timeout_s: float = 300) -> tuple[int, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError as e:
        raise MediaError(f"{Path(argv[0]).name} nicht gefunden") from e
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise MediaError(f"{Path(argv[0]).name}: Zeitüberschreitung nach {timeout_s:.0f} s") from None
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


@dataclass(slots=True)
class Probe:
    width: int = 0
    height: int = 0
    fps: float = 0.0
    video_codec: str = ""
    pix_fmt: str = ""
    audio_codec: str = ""
    sample_rate: int = 0
    channels: int = 0
    duration: float = 0.0
    size: int = 0
    bitrate: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width, "height": self.height, "fps": round(self.fps, 3), "video_codec": self.video_codec,
            "pix_fmt": self.pix_fmt, "audio_codec": self.audio_codec, "sample_rate": self.sample_rate,
            "channels": self.channels, "duration": round(self.duration, 3), "size": self.size, "bitrate": self.bitrate,
        }  # fmt: skip


def parse_probe(data: dict[str, Any]) -> Probe:
    p = Probe()
    for s in data.get("streams") or []:
        if s.get("codec_type") == "video" and not p.video_codec:
            p.width, p.height = int(s.get("width") or 0), int(s.get("height") or 0)
            p.video_codec = str(s.get("codec_name") or "")
            p.pix_fmt = str(s.get("pix_fmt") or "")
            num, _, den = str(s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/1").partition("/")
            p.fps = float(num) / float(den or 1) if float(den or 1) else 0.0
        elif s.get("codec_type") == "audio" and not p.audio_codec:
            p.audio_codec = str(s.get("codec_name") or "")
            p.sample_rate = int(s.get("sample_rate") or 0)
            p.channels = int(s.get("channels") or 0)
    fmt = data.get("format") or {}
    p.duration = float(fmt.get("duration") or 0)
    p.size = int(fmt.get("size") or 0)
    p.bitrate = int(fmt.get("bit_rate") or 0)
    return p


async def probe(ffprobe: str, path: Path) -> Probe:
    code, out, err = await run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], timeout_s=60
    )
    if code != 0:
        raise MediaError(f"ffprobe: {err.strip()[:300] or 'Datei nicht lesbar'}")
    return parse_probe(json.loads(out or "{}"))


@dataclass(slots=True)
class Loudness:
    integrated_lufs: float
    true_peak_db: float
    max_volume_db: float
    mean_volume_db: float

    def to_dict(self) -> dict[str, float]:
        return {
            "integrated_lufs": self.integrated_lufs,
            "true_peak_db": self.true_peak_db,
            "max_volume_db": self.max_volume_db,
            "mean_volume_db": self.mean_volume_db,
        }


def parse_loudness(stderr: str) -> Loudness:
    def last(pattern: str, default: float) -> float:
        hits = re.findall(pattern, stderr)
        if not hits:
            return default
        v = hits[-1]
        return float("-inf") if v in ("-inf", "inf") else float(v)

    return Loudness(
        integrated_lufs=last(r"I:\s+(-?[\d.]+|-inf)\s+LUFS", float("-inf")),
        true_peak_db=last(r"Peak:\s+(-?[\d.]+|-inf)\s+dBFS", float("-inf")),
        max_volume_db=last(r"max_volume:\s+(-?[\d.]+|-inf)\s+dB", float("-inf")),
        mean_volume_db=last(r"mean_volume:\s+(-?[\d.]+|-inf)\s+dB", float("-inf")),
    )


async def loudness(ffmpeg: str, path: Path) -> Loudness:
    code, _, err = await run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-vn",
            "-af",
            "ebur128=peak=true,volumedetect",
            "-f",
            "null",
            "-",
        ],
        timeout_s=120,
    )
    if code != 0:
        raise MediaError(f"Pegelanalyse fehlgeschlagen: {err.strip()[-300:]}")
    return parse_loudness(err)


async def extract_frames(ffmpeg: str, video: Path, times: list[float], out_dir: Path, width: int = 540) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i, t in enumerate(times):
        target = out_dir / f"frame_{i:02d}_{t:05.2f}s.png"
        code, _, err = await run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
             "-vf", f"scale={width}:-2", str(target)],
            timeout_s=60,
        )  # fmt: skip
        if code != 0 or not target.is_file():
            raise MediaError(f"Frame bei {t:.2f} s nicht lesbar: {err.strip()[:200]}")
        paths.append(target)
    return paths


async def shrink(ffmpeg: str, src: Path, dst: Path, max_bytes: int, duration: float) -> None:
    """Re-encode the video stream to fit ``max_bytes`` (audio copied): bitrate from the budget, 2 % headroom."""
    audio_bits = 192_000 * duration
    video_kbps = max(800, int(((max_bytes * 8 * 0.98) - audio_bits) / max(duration, 1) / 1000))
    code, _, err = await run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-c:v", "libx264", "-preset", "slow",
         "-b:v", f"{video_kbps}k", "-maxrate", f"{video_kbps}k", "-bufsize", f"{video_kbps * 2}k",
         "-pix_fmt", "yuv420p",
         "-profile:v", "high", "-c:a", "copy", "-movflags", "+faststart", str(dst)],
        timeout_s=600,
    )  # fmt: skip
    if code != 0:
        raise MediaError(f"Verkleinern fehlgeschlagen: {err.strip()[-300:]}")


async def frame_stats(ffmpeg: str, image: Path) -> dict[str, float]:
    """Mean luma and its standard deviation of one frame (a blank or black frame has a tiny deviation)."""
    code, _, err = await run(
        [ffmpeg, "-hide_banner", "-i", str(image), "-vf", "signalstats,metadata=print", "-f", "null", "-"], timeout_s=30
    )
    if code != 0:
        raise MediaError(f"Bildanalyse fehlgeschlagen: {err.strip()[-200:]}")

    def val(key: str) -> float:
        m = re.findall(rf"lavfi\.signalstats\.{key}=(-?[\d.]+)", err)
        return float(m[-1]) if m else 0.0

    return {"yavg": val("YAVG"), "ylow": val("YLOW"), "yhigh": val("YHIGH")}
