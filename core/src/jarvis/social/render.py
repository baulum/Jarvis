"""Rendering: storyboard → silent H.264 via Remotion (``social/render.mjs``, headless Chrome) → synthesized sound
design mixed in with FFmpeg → MP4 (1080×1920, 30 fps, H.264 + AAC, faststart), shrunk if it exceeds the limit.

``check_dependencies`` reports exactly what is missing (Node, the Remotion project's node_modules, the headless
browser, FFmpeg/ffprobe) – the dashboard shows these messages instead of a failed run."""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.config import REPO_ROOT, SocialMediaCfg
from jarvis.social import sfx
from jarvis.social.media import MediaError, probe, run, shrink
from jarvis.social.storyboard import total_seconds

logger = logging.getLogger(__name__)


class RenderError(RuntimeError):
    pass


@dataclass(slots=True)
class Tools:
    node: str | None
    ffmpeg: str | None
    ffprobe: str | None
    project: Path

    @classmethod
    def resolve(cls, cfg: SocialMediaCfg) -> Tools:
        return cls(
            node=shutil.which(cfg.node_binary),
            ffmpeg=shutil.which(cfg.ffmpeg_binary),
            ffprobe=shutil.which(cfg.ffprobe_binary),
            project=cfg.project_dir or (REPO_ROOT / "social"),
        )


async def check_dependencies(cfg: SocialMediaCfg) -> dict[str, Any]:
    t = Tools.resolve(cfg)
    problems: list[str] = []
    if t.node is None:
        problems.append(f"Node.js fehlt ({cfg.node_binary} nicht im PATH) – Node ≥ 20 installieren")
    if t.ffmpeg is None:
        problems.append(f"FFmpeg fehlt ({cfg.ffmpeg_binary} nicht im PATH)")
    if t.ffprobe is None:
        problems.append(f"ffprobe fehlt ({cfg.ffprobe_binary} nicht im PATH, gehört zu FFmpeg)")
    if not (t.project / "render.mjs").is_file():
        problems.append(f"Remotion-Projekt nicht gefunden: {t.project}")
    elif not (t.project / "node_modules" / "@remotion" / "renderer").is_dir():
        problems.append(f"Remotion ist nicht installiert – in {t.project}: npm ci && npm run browser")
    browser = "unbekannt"
    if not problems and t.node is not None:
        try:
            code, _, err = await run([t.node, str(t.project / "render.mjs"), "--check"], timeout_s=300)
        except MediaError as e:
            code, err = 1, str(e)
        if code != 0:
            problems.append(
                "Headless-Browser für Remotion fehlt oder startet nicht – in social/: npm run browser. "
                + (err.strip().splitlines() or [""])[0][:200]  # render.mjs puts the message first, then the stack
            )
        else:
            browser = "ok"
    return {
        "ok": not problems,
        "problems": problems,
        "node": t.node,
        "ffmpeg": t.ffmpeg,
        "ffprobe": t.ffprobe,
        "project": str(t.project),
        "browser": browser,
    }


async def render_video(
    cfg: SocialMediaCfg,
    board: dict[str, Any],
    work_dir: Path,
    out: Path,
    *,
    voiceover: Path | None = None,
    progress: Callable[[int], Any] | None = None,
    loud_gain_db: float = 0.0,
) -> dict[str, Any]:
    t = Tools.resolve(cfg)
    if t.node is None or t.ffmpeg is None or t.ffprobe is None:
        raise RenderError("Node.js, FFmpeg oder ffprobe fehlt – siehe Social Media › Status")
    work_dir.mkdir(parents=True, exist_ok=True)
    props = work_dir / "storyboard.json"
    props.write_text(json.dumps(board, ensure_ascii=False), encoding="utf-8")
    silent = work_dir / "silent.mp4"
    argv = [t.node, str(t.project / "render.mjs"), "--props", str(props), "--out", str(silent)]
    # Headless Chrome occasionally drops its connection when the machine is busy (other builds, a coding job):
    # one more attempt with fewer parallel tabs before the run counts as failed.
    for attempt in (1, 2):
        silent.unlink(missing_ok=True)
        extra = [] if attempt == 1 else ["--concurrency", "2"]
        code, stdout, stderr = await run(argv + extra, timeout_s=cfg.render_timeout_s)
        for line in stdout.splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "progress" and progress is not None:
                progress(int(ev.get("progress", 0)))
        if code == 0 and silent.is_file():
            break
        logger.warning("Remotion render attempt %d failed (exit %s)", attempt, code)
    if code != 0 or not silent.is_file():
        first = next((ln.strip() for ln in stderr.splitlines() if ln.strip()), "")  # the message, not the stack
        log = work_dir / "render-error.log"
        hint = f" (Details: {log})" if log.is_file() else ""
        raise RenderError(f"Remotion-Render fehlgeschlagen: {first[:400] or f'Exit {code}'}{hint}")
    duration = total_seconds(board)
    mixed = work_dir / "mixed.mp4"
    cmd = sfx.mix_command(
        t.ffmpeg, silent, mixed, sfx.cues_for(board), str(board.get("music", "pulse")), duration,
        voiceover=voiceover, loud_gain_db=loud_gain_db,
    )  # fmt: skip
    code, _, err = await run(cmd, timeout_s=300)
    if code != 0 or not mixed.is_file():
        raise RenderError(f"Tonmischung fehlgeschlagen: {err.strip()[-400:]}")
    limit = int(cfg.max_video_mb * 1024 * 1024)
    if mixed.stat().st_size > limit:
        small = work_dir / "small.mp4"
        await shrink(t.ffmpeg, mixed, small, limit, duration)
        mixed = small
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(mixed, out)
    info = await probe(t.ffprobe, out)
    return {"probe": info.to_dict(), "cues": len(sfx.cues_for(board))}
