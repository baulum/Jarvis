"""The daily e-mail to Sir: subject, branded HTML (inline CSS, table layout – renders in Gmail and Apple Mail,
no remote images) and a plain-text fallback with the same content."""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from jarvis.social.captions import PLATFORMS, full_text

PLATFORM_LABEL = {
    "instagram": "Instagram Reels",
    "tiktok": "TikTok",
    "facebook": "Facebook Reels",
    "youtube": "YouTube Shorts",
}
STATUS_LABEL = {
    "published": "veröffentlicht",
    "scheduled": "geplant",
    "waiting_approval": "wartet auf Freigabe",
    "publishing": "wird veröffentlicht",
    "failed": "fehlgeschlagen",
    "manual": "manuell posten",
}
CATEGORY_LABEL = {
    "A": "Humor & provokante Hooks",
    "B": "Überraschung & Pattern Interrupts",
    "C": "Premium Motion Graphics",
    "D": "Psychologie & Kundenprobleme",
    "E": "Angebote & Projekte",
    "F": "Experimentell",
}

INK, ACCENT, CANVAS, SLATE, LINE = "#0b0c0e", "#2651f0", "#e3e5e8", "#50545b", "#d3d6dc"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"


@dataclass(slots=True)
class Mail:
    subject: str
    text: str
    html: str


def _e(s: Any) -> str:
    return html.escape(str(s or ""))


def build(
    video: dict[str, Any],
    *,
    day: datetime,
    attachment: str | None,
    download_url: str | None,
    link_hours: int,
    publications: list[dict[str, Any]],
    autopost_enabled: bool,
) -> Mail:
    title = video.get("title") or "Social-Video"
    concept = video.get("concept") or {}
    captions = video.get("captions") or {}
    date_s = day.strftime("%d.%m.%Y")
    subject = f"PJE Social · {date_s} · {title}"[:180]
    probe = (video.get("qa") or {}).get("probe") or {}
    meta = f"{probe.get('width', 1080)}×{probe.get('height', 1920)} · {float(video.get('duration_s') or 0):.1f} s · {int(video.get('size') or 0) / 1048576:.1f} MB"

    if attachment:
        delivery = f"Das Video hängt als MP4 an ({attachment})."
    elif download_url:
        delivery = f"Download (gültig {link_hours} h): {download_url}"
    else:
        delivery = "Das Video liegt in der Jarvis-App unter Social Media › Bibliothek."
    pubs = {p["platform"]: p for p in publications}
    status_lines = []
    for pf in PLATFORMS:
        p = pubs.get(pf)
        if p is None:
            st = (
                "nicht veröffentlicht (Autopost aus)"
                if not autopost_enabled
                else "nicht verbunden/aktiviert – manuell posten"
            )
        else:
            st = STATUS_LABEL.get(p.get("status", ""), p.get("status", ""))
            if p.get("post_url"):
                st += f" – {p['post_url']}"
        status_lines.append((PLATFORM_LABEL[pf], st))

    # ------------------------------------------------------------------ text
    t = [
        f"PJE Systems · Tagesvideo {date_s}",
        "",
        title,
        f"Kategorie {video.get('category', '')} ({CATEGORY_LABEL.get(video.get('category', ''), '')}) · Ton: {video.get('tone', '')}",
        meta,
        "",
        "Konzept:",
        str(concept.get("summary") or ""),
        f"Hook: {concept.get('hook', '')}",
        "",
        delivery,
        "",
    ]
    yt = captions.get("youtube") or {}
    t += [f"YouTube-Titel: {yt.get('title', '')}", ""]
    for pf in PLATFORMS:
        entry = captions.get(pf) or {}
        t += [f"— {PLATFORM_LABEL[pf]} —", full_text(pf, entry), ""]
    t += ["Veröffentlichung:"] + [f"- {name}: {st}" for name, st in status_lines]
    t += ["", "Gesendet von Jarvis. Einstellungen: Jarvis › Social Media › Automatisierung."]
    text = "\n".join(t)

    # ------------------------------------------------------------------ html
    def caption_block(pf: str) -> str:
        entry = captions.get(pf) or {}
        body = _e(entry.get("description" if pf == "youtube" else "text")).replace("\n", "<br>")
        tags = " ".join(_e(x) for x in entry.get("hashtags") or [])
        head = f'<div style="font:600 15px/1.3 {FONT};color:{INK};margin:0 0 6px">{PLATFORM_LABEL[pf]}</div>'
        yt_title = (
            f'<div style="font:600 16px/1.35 {FONT};color:{INK};margin:0 0 6px">{_e(entry.get("title"))}</div>'
            if pf == "youtube"
            else ""
        )
        return (
            f'<tr><td style="padding:18px 0;border-top:1px solid {LINE}">{head}{yt_title}'
            f'<div style="font:15px/1.5 {FONT};color:#2b2d33">{body}</div>'
            f'<div style="font:14px/1.5 {MONO};color:{ACCENT};margin-top:8px">{tags}</div></td></tr>'
        )

    status_rows = "".join(
        f'<tr><td style="padding:6px 0;font:14px/1.4 {FONT};color:{SLATE}">{_e(name)}</td>'
        f'<td style="padding:6px 0;font:14px/1.4 {FONT};color:{INK};text-align:right">{_e(st)}</td></tr>'
        for name, st in status_lines
    )
    button = (
        f'<a href="{_e(download_url)}" style="display:inline-block;background:{ACCENT};color:#fff;text-decoration:none;'
        f'font:600 15px/1 {FONT};padding:14px 22px;border-radius:999px">MP4 herunterladen →</a>'
        f'<div style="font:12px/1.5 {FONT};color:{SLATE};margin-top:8px">Link gültig {link_hours} Stunden.</div>'
        if download_url
        else ""
    )
    html_doc = f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{_e(subject)}</title></head>
<body style="margin:0;padding:0;background:{CANVAS}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{CANVAS}"><tr><td align="center" style="padding:28px 12px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:18px;overflow:hidden">
<tr><td style="background:{INK};padding:26px 32px">
<div style="font:800 26px/1 {FONT};letter-spacing:6px;color:#ffffff">PJE</div>
<div style="font:500 10px/1 {FONT};letter-spacing:7px;color:#ffffff;margin-top:6px">SYSTEMS</div>
<div style="font:12px/1.4 {MONO};color:#8fa6ff;margin-top:18px;letter-spacing:1px">TAGESVIDEO · {_e(date_s)}</div>
</td></tr>
<tr><td style="padding:30px 32px 6px">
<div style="font:700 30px/1.1 {FONT};color:{INK};letter-spacing:-0.5px">{_e(title.rstrip("."))}<span style="color:{ACCENT}">.</span></div>
<div style="font:13px/1.5 {MONO};color:{SLATE};margin-top:10px">Kategorie {_e(video.get("category"))} · {_e(CATEGORY_LABEL.get(video.get("category", ""), ""))} · {_e(video.get("tone"))} · {_e(meta)}</div>
<p style="font:16px/1.55 {FONT};color:#2b2d33;margin:18px 0 6px">{_e(concept.get("summary"))}</p>
<p style="font:15px/1.5 {FONT};color:{SLATE};margin:0 0 18px">Hook: „{_e(concept.get("hook"))}“</p>
<p style="font:15px/1.5 {FONT};color:{INK};margin:0 0 14px">{_e(delivery if not download_url else ("Das Video hängt als MP4 an." if attachment else "Das Video ist zu groß für einen Anhang:"))}</p>
{button}
</td></tr>
<tr><td style="padding:16px 32px 0"><div style="font:600 12px/1 {MONO};letter-spacing:2px;color:{SLATE}">CAPTIONS &amp; HASHTAGS</div></td></tr>
<tr><td style="padding:6px 32px 8px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">
{"".join(caption_block(pf) for pf in PLATFORMS)}
</table></td></tr>
<tr><td style="padding:10px 32px 4px"><div style="font:600 12px/1 {MONO};letter-spacing:2px;color:{SLATE}">VERÖFFENTLICHUNG</div></td></tr>
<tr><td style="padding:8px 32px 26px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{status_rows}</table></td></tr>
<tr><td style="background:{CANVAS};padding:18px 32px;font:12px/1.5 {FONT};color:{SLATE}">Gesendet von Jarvis an Sir. Einstellungen: Jarvis › Social Media › Automatisierung.</td></tr>
</table></td></tr></table>
</body></html>"""
    return Mail(subject=subject, text=text, html=html_doc)
