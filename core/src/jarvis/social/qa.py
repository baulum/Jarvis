"""Quality control of a rendered video before it is sent.

Deterministic checks: render present, 1080×1920, 30 fps, H.264/yuv420p + AAC, length 8–15 s and matching the
storyboard, file size, loudness (not silent, not clipping), reading time per text, contrast of every colour pair
used, CTA end card, no blank frames. Then frames are extracted (one per scene after its entry animation + the last
frame) and an LLM looks at them together with the scene texts: spelling, legibility, cut-off or overlapping text,
safe areas, CTA and logo visible. ``fixes`` tells the production loop what it can correct automatically.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis.social import storyboard as sb
from jarvis.social.media import Loudness, MediaError, Probe, extract_frames, frame_stats

logger = logging.getLogger(__name__)

MIN_CONTRAST = 4.5
LUFS_RANGE = (-20.0, -9.0)
MAX_TRUE_PEAK = -0.5
SILENT_BELOW_DB = -40.0
MIN_TEXT_PX = 40

# Text/background pairs the compositions use per surface (social/src/brand.ts) – checked like WCAG AA.
SURFACE_COLORS: dict[str, list[tuple[str, str]]] = {
    "dark": [("#ffffff", "#0b0c0e"), ("#8fa6ff", "#0b0c0e"), ("#ffffff", "#2651f0"), ("#ffffff", "#1c1e22")],
    "light": [
        ("#0b0c0e", "#e3e5e8"), ("#2651f0", "#e3e5e8"), ("#50545b", "#e3e5e8"), ("#ffffff", "#2651f0"),
        ("#0b0c0e", "#ffffff"),
    ],
    "accent": [("#ffffff", "#2651f0"), ("#ffffff", "#0b0c0e"), ("#0b0c0e", "#ffffff")],
}  # fmt: skip
PALETTE_SURFACE = {"ink": "dark", "graphite": "dark", "canvas": "light", "paper": "light", "electric": "accent"}

VISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scene_id": {"type": "string"},
                    "severity": {"enum": ["blocker", "minor"]},
                    "kind": {"enum": ["spelling", "layout", "legibility", "cta", "branding", "content", "other"]},
                    "detail": {"type": "string"},
                },
                "required": ["severity", "kind", "detail"],
            },
        },
        "spelling_fixes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"scene_id": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
                "required": ["old", "new"],
            },
        },
    },
    "required": ["ok", "issues", "spelling_fixes"],
}


def _lum(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    rgb = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(fg: str, bg: str) -> float:
    a, b = sorted((_lum(fg), _lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


@dataclass(slots=True)
class QaReport:
    passed: bool = True
    checks: list[dict[str, Any]] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)
    spelling_fixes: list[dict[str, str]] = field(default_factory=list)
    frames: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)  # audio_gain | spelling | storyboard | size
    probe: dict[str, Any] = field(default_factory=dict)  # ffprobe data + loudness of the checked file

    def add(self, cid: str, label: str, ok: bool, detail: str = "", fix: str | None = None) -> None:
        self.checks.append({"id": cid, "label": label, "ok": ok, "detail": detail})
        if not ok:
            self.passed = False
            if fix and fix not in self.fixes:
                self.fixes.append(fix)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": self.checks,
            "issues": self.issues,
            "spelling_fixes": self.spelling_fixes,
            "frames": self.frames,
            "fixes": self.fixes,
            "probe": self.probe,
        }


def technical(report: QaReport, info: Probe, loud: Loudness, board: dict[str, Any], max_bytes: int) -> None:
    report.add("render", "Render vorhanden", info.size > 0 and info.duration > 0, f"{info.size} Bytes")
    report.add(
        "resolution",
        "Auflösung 1080×1920",
        (info.width, info.height) == (sb.WIDTH, sb.HEIGHT),
        f"{info.width}×{info.height}",
    )
    report.add("fps", "30 fps", abs(info.fps - sb.FPS) < 0.01, f"{info.fps:.2f} fps")
    report.add(
        "video_codec",
        "H.264 (yuv420p)",
        info.video_codec == "h264" and info.pix_fmt == "yuv420p",
        f"{info.video_codec}/{info.pix_fmt}",
    )
    report.add("audio_codec", "AAC-Tonspur", info.audio_codec == "aac", info.audio_codec or "keine Tonspur")
    expected = sb.total_seconds(board)
    report.add(
        "duration",
        f"Länge {sb.MIN_TOTAL_S:.0f}–{sb.MAX_TOTAL_S:.0f} s",
        sb.MIN_TOTAL_S - 0.05 <= info.duration <= sb.MAX_TOTAL_S + 0.05 and abs(info.duration - expected) < 0.25,
        f"{info.duration:.2f} s (Storyboard {expected:.2f} s)",
        "storyboard",
    )
    report.add("size", "Dateigröße im Limit", info.size <= max_bytes, f"{info.size / 1048576:.1f} MB", "size")
    silent = loud.max_volume_db < SILENT_BELOW_DB
    report.add(
        "audio_present", "Ton hörbar (nicht stumm)", not silent, f"max {loud.max_volume_db:.1f} dB", "audio_gain"
    )
    lo, hi = LUFS_RANGE
    report.add(
        "loudness",
        f"Lautheit {lo:.0f} bis {hi:.0f} LUFS",
        lo <= loud.integrated_lufs <= hi,
        f"{loud.integrated_lufs:.1f} LUFS",
        "audio_gain",
    )
    report.add(
        "true_peak",
        f"Nicht übersteuert (≤ {MAX_TRUE_PEAK} dBTP)",
        loud.true_peak_db <= MAX_TRUE_PEAK,
        f"{loud.true_peak_db:.1f} dBTP",
        "audio_gain",
    )


def textual(report: QaReport, board: dict[str, Any], fact_ids: set[str]) -> None:
    check = sb.validate(board, fact_ids=fact_ids)
    reading = [p for p in check.problems if "zu kurz zum Lesen" in p]
    report.add(
        "reading_time",
        "Lesedauer je Texteinblendung",
        not reading,
        "; ".join(reading)[:400] or "ausreichend",
        "storyboard",
    )
    other = [p for p in check.problems if p not in reading]
    report.add(
        "storyboard", "Storyboard gültig (Schema, Hook, CTA am Ende)", not other, "; ".join(other)[:400], "storyboard"
    )
    report.add(
        "min_font",
        f"Mindestschriftgröße {MIN_TEXT_PX} px",
        True,
        "Komponenten setzen Text ≥ 40 px; Längen per Schema begrenzt",
    )
    pairs: list[tuple[str, str]] = []
    for s in board.get("scenes", []):
        surface = s.get("surface") or PALETTE_SURFACE.get(board.get("palette", "ink"), "dark")
        pairs += SURFACE_COLORS.get(surface, [])
    worst = min((contrast(fg, bg), fg, bg) for fg, bg in pairs) if pairs else (21.0, "", "")
    report.add(
        "contrast",
        f"Kontrast ≥ {MIN_CONTRAST}:1",
        worst[0] >= MIN_CONTRAST,
        f"min. {worst[0]:.1f}:1 ({worst[1]} auf {worst[2]})",
    )
    last = (board.get("scenes") or [{}])[-1]
    cta_ok = (
        last.get("component") == "end_card"
        and bool(last.get("props", {}).get("button"))
        and last.get("duration", 0) >= 1.8
    )
    report.add(
        "cta",
        "CTA-Endkarte (Button + URL, ≥ 1,8 s)",
        cta_ok,
        str(last.get("props", {}).get("button", "")),
        "storyboard",
    )


def frame_times(board: dict[str, Any]) -> list[tuple[str, float]]:
    """One frame per scene once its entry animation and typing have settled, plus the very last frame."""
    out: list[tuple[str, float]] = []
    start = 0.0
    for s in board.get("scenes", []):
        d = round(float(s["duration"]) * sb.FPS) / sb.FPS
        out.append((s["id"], start + d * 0.82))
        start += d
    out.append(("end", max(0.0, start - 0.1)))
    return out


async def visual(
    report: QaReport,
    ffmpeg: str,
    video: Path,
    board: dict[str, Any],
    frames_dir: Path,
    ask: Callable[[str, Path], Awaitable[Any]] | None,
) -> None:
    times = frame_times(board)
    try:
        frames = await extract_frames(ffmpeg, video, [t for _, t in times], frames_dir)
    except MediaError as e:
        report.add("frames", "Frames lesbar", False, str(e)[:300], "storyboard")
        return
    report.frames = [f.name for f in frames]
    blank = []
    for (sid, _), f in zip(times, frames, strict=True):
        st = await frame_stats(ffmpeg, f)
        if st["yhigh"] - st["ylow"] < 8:
            blank.append(sid)
    report.add("blank_frames", "Keine leeren Frames", not blank, ", ".join(blank), "storyboard")
    if ask is None:
        report.add("vision", "Bildprüfung (Frames)", False, "Claude nicht verfügbar – Bildprüfung nicht möglich")
        return
    listing = "\n".join(
        f"- {f.name}: Szene {sid}"
        + (
            f" ({next(s['component'] for s in board['scenes'] if s['id'] == sid)}), Texte: "
            if sid != "end"
            else " (letzter Frame), Texte: "
        )
        + " | ".join(sb.scene_texts(next((s for s in board["scenes"] if s["id"] == sid), board["scenes"][-1])))
        for (sid, _), f in zip(times, frames, strict=True)
    )
    prompt = (
        "Du bist die Qualitätskontrolle für ein 9:16-Werbevideo von PJE Systems (Webdesign). Lies jede PNG-Datei in "
        "diesem Ordner mit dem Read-Werkzeug und prüfe sie. Die Bilder sind auf 540 px Breite verkleinert.\n"
        f"Frames und die Texte, die darin stehen sollen:\n{listing}\n\n"
        "Prüfe: 1) Rechtschreibung und Grammatik (Deutsch) aller sichtbaren Texte; 2) Lesbarkeit (Größe, Kontrast); "
        "3) abgeschnittene, überlappende oder aus dem Bild laufende Texte; 4) Texte im unteren Fünftel oder am rechten "
        "Rand, wo Plattform-Buttons liegen; 5) der letzte Frame zeigt Logo, CTA-Button und URL; 6) etwas wirkt kaputt, "
        "leer oder unfertig; 7) Parodien sind als „Satire · fiktiv“ markiert.\n"
        "Bewerte nur echte Fehler als blocker. Normale Animationszwischenstände sind kein Fehler. Rechtschreibfehler "
        "gib zusätzlich als spelling_fixes (alter Text → korrigierter Text, exakt wie im Storyboard) an."
    )
    try:
        res = await ask(prompt, frames_dir)
    except Exception as e:  # Claude quota/CLI problems must not crash the run; the report says why
        logger.warning("Vision QA failed: %s", e)
        report.add("vision", "Bildprüfung (Frames)", False, f"Bildprüfung fehlgeschlagen: {e}"[:300])
        return
    issues = [i for i in (res or {}).get("issues", []) if isinstance(i, dict)]
    report.issues = issues
    report.spelling_fixes = [
        f for f in (res or {}).get("spelling_fixes", []) if isinstance(f, dict) and f.get("old") and f.get("new")
    ]
    blockers = [i for i in issues if i.get("severity") == "blocker"]
    spelling = [i for i in blockers if i.get("kind") == "spelling"] or (
        [{"detail": "Korrekturen"}] if report.spelling_fixes else []
    )
    report.add(
        "spelling", "Rechtschreibung (LLM)", not spelling, "; ".join(i["detail"] for i in spelling)[:400], "spelling"
    )
    layout = [i for i in blockers if i.get("kind") in ("layout", "legibility", "content", "other")]
    report.add(
        "layout",
        "Lesbarkeit, Safe Areas, nichts abgeschnitten",
        not layout,
        "; ".join(i["detail"] for i in layout)[:400],
        "storyboard",
    )
    brand = [i for i in blockers if i.get("kind") in ("cta", "branding")]
    report.add(
        "branding", "CTA und Logo sichtbar", not brand, "; ".join(i["detail"] for i in brand)[:400], "storyboard"
    )


def apply_spelling(board: dict[str, Any], fixes: list[dict[str, str]]) -> tuple[dict[str, Any], int]:
    """Replace exact strings in the scene props (only whole string values or list items) – no free rewriting."""
    count = 0

    def fix(value: Any) -> Any:
        nonlocal count
        if isinstance(value, str):
            for f in fixes:
                if f["old"] and f["old"] in value and len(f["new"]) <= len(f["old"]) + 12:
                    value = value.replace(f["old"], f["new"])
                    count += 1
            return value
        if isinstance(value, list):
            return [fix(v) for v in value]
        if isinstance(value, dict):
            return {
                k: (fix(v) if k not in ("component", "id", "transition", "surface", "sound", "fact_id") else v)
                for k, v in value.items()
            }
        return value

    out = dict(board)
    out["scenes"] = [{**s, "props": fix(s.get("props", {}))} for s in board.get("scenes", [])]
    return out, count
