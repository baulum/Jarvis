"""Captions, hashtags and the YouTube title per platform – limits checked in code.

Limits (checked 2026-10-28, sources in docs/SOCIAL_MEDIA.md):
* Instagram Reels: caption ≤ 2 200 characters, at most 5 hashtags per post (Instagram, since Dec 2025)
* TikTok: description ≤ 2 200 UTF-16 code units (Content Posting API ``post_info.title``)
* Facebook Reels: we keep the same 2 200 as Instagram (shared Meta tooling, longer texts get truncated in the feed)
* YouTube Shorts: title ≤ 100 characters, description ≤ 5 000 bytes, no ``<`` or ``>`` (rejected by the API);
  more than 60 hashtags make YouTube ignore all of them

Our own rules on top: 3–5 hashtags (YouTube 2–5), a CTA in every caption, prices only from the brand file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

PLATFORMS = ("instagram", "tiktok", "facebook", "youtube")
LIMITS = {
    "instagram": {"caption": 2200, "hashtags_max": 5},
    "tiktok": {"caption": 2200, "hashtags_max": 5},
    "facebook": {"caption": 2200, "hashtags_max": 5},
    "youtube": {"title": 100, "description_bytes": 5000, "hashtags_max": 5},
}
HASHTAG_RE = re.compile(r"^#[0-9A-Za-zÄÖÜäöüß_]{2,40}$")
PRICE_RE = re.compile(r"\d[\d.,]*\s?(?:€|euro\b|eur\b)", re.IGNORECASE)
CTA_WORDS = ("anfrag", "pje-systems.de", "link", "schreib", "meld", "kontakt", "projekt", "termin", "dm", "nachricht")
# promises nobody can check in a caption: never written by Jarvis on PJE's behalf
FORBIDDEN = ("garantiert", "100 % mehr", "platz 1", "nummer 1", "bestbewertet", "testsieger", "kostenlos", "gratis")

CAPTIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "instagram": {"$ref": "#/$defs/post"},
        "tiktok": {"$ref": "#/$defs/post"},
        "facebook": {"$ref": "#/$defs/post"},
        "youtube": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "hashtags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title", "description", "hashtags"],
        },
    },
    "required": list(PLATFORMS),
    "$defs": {
        "post": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "hashtags": {"type": "array", "items": {"type": "string"}}},
            "required": ["text", "hashtags"],
        }
    },
}


@dataclass(slots=True)
class CaptionCheck:
    ok: bool
    problems: list[str]


def full_text(platform: str, entry: dict[str, Any]) -> str:
    """The text as it is pasted/posted: caption, blank line, hashtags (YouTube: the description)."""
    tags = " ".join(entry.get("hashtags") or [])
    body = entry.get("description" if platform == "youtube" else "text", "").strip()
    return f"{body}\n\n{tags}".strip() if tags else body


def _amount(price: str) -> str:
    """ "1.499 €" → "1499", "499,00 €" → "499": compare amounts, not spellings."""
    num = re.sub(r"[^\d.,]", "", price)
    num = re.sub(r"[.,]00$", "", num)
    return re.sub(r"\D", "", num)


def prices_in(text: str) -> set[str]:
    return set(PRICE_RE.findall(text))


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def clean(captions: dict[str, Any]) -> dict[str, Any]:
    """Normalize hashtags (leading #, no spaces, no duplicates) and trim whitespace."""
    out: dict[str, Any] = {}
    for platform in PLATFORMS:
        entry = dict(captions.get(platform) or {})
        tags: list[str] = []
        for t in entry.get("hashtags") or []:
            t = "#" + re.sub(r"[^0-9A-Za-zÄÖÜäöüß_]", "", str(t).lstrip("#"))
            if len(t) > 2 and t.lower() not in {x.lower() for x in tags}:
                tags.append(t)
        entry["hashtags"] = tags
        for k in ("text", "title", "description"):
            if isinstance(entry.get(k), str):
                entry[k] = entry[k].strip()
        out[platform] = entry
    return out


def validate(captions: dict[str, Any], allowed_prices: set[str] | None = None) -> CaptionCheck:
    problems: list[str] = []
    allowed = {_amount(p) for p in (allowed_prices or set())}
    for platform in PLATFORMS:
        entry = captions.get(platform)
        if not isinstance(entry, dict):
            problems.append(f"{platform}: fehlt")
            continue
        lim = LIMITS[platform]
        tags = entry.get("hashtags") or []
        lo = 2 if platform == "youtube" else 3
        if not lo <= len(tags) <= lim["hashtags_max"]:
            problems.append(f"{platform}: {len(tags)} Hashtags – erlaubt {lo}–{lim['hashtags_max']}")
        for t in tags:
            if not HASHTAG_RE.match(t):
                problems.append(f"{platform}: ungültiger Hashtag {t!r}")
        text = full_text(platform, entry)
        if platform == "youtube":
            title = entry.get("title", "")
            if not title.strip():
                problems.append("youtube: Titel fehlt")
            if len(title) > lim["title"]:
                problems.append(f"youtube: Titel {len(title)} Zeichen – max. {lim['title']}")
            if len(text.encode()) > lim["description_bytes"]:
                problems.append(f"youtube: Beschreibung {len(text.encode())} Bytes – max. {lim['description_bytes']}")
            if any(ch in title + text for ch in "<>"):
                problems.append("youtube: < und > lehnt die YouTube-API ab")
        else:
            if not entry.get("text", "").strip():
                problems.append(f"{platform}: Text fehlt")
            if utf16_len(text) > lim["caption"]:
                problems.append(f"{platform}: {utf16_len(text)} Zeichen – max. {lim['caption']}")
        body = (entry.get("text") or entry.get("description") or "").lower()
        if not any(w in body for w in CTA_WORDS):
            problems.append(f"{platform}: kein klarer CTA (z. B. „Projekt anfragen auf pje-systems.de“)")
        whole = (text + " " + entry.get("title", "")).lower()
        for word in FORBIDDEN:
            if word in whole:
                problems.append(f"{platform}: „{word}“ ist ein unbelegtes Versprechen")
        for price in PRICE_RE.findall(whole):
            if _amount(price) not in allowed:
                problems.append(f"{platform}: Preis „{price}“ steht nicht als gültiges Angebot in der Konfiguration")
    return CaptionCheck(not problems, problems)


def prompt(
    concept: dict[str, Any], script: str, brand: dict[str, Any], offers: list[str], problems: list[str] | None = None
) -> str:
    lines = [
        "Schreibe Social-Media-Captions für ein 8–15-Sekunden-Werbevideo von PJE Systems (Webdesign aus Wolnzach).",
        f"Video: {concept.get('title')} – {concept.get('summary')}",
        f"Hook: {concept.get('hook')}",
        f"Skript: {script}",
        f"Tonalität: {concept.get('tone')}",
        f"Website/CTA: {brand.get('cta', {}).get('default_button', 'Projekt anfragen')} auf {brand.get('website', 'pje-systems.de')}",
        "Gültige Angebote (nur diese Preise/Angebote nennen, sonst keine): " + ("; ".join(offers) or "keine"),
        "Regeln:",
        "- Deutsch, du-Form, kurz und natürlich, wie ein Mensch schreibt; 1–3 passende Emojis, nicht mehr.",
        "- Jede Plattform eigener Text: Instagram (1–3 kurze Zeilen + CTA), TikTok (sehr kurz, locker),",
        "  Facebook (etwas erklärender, 2–3 Sätze), YouTube Shorts (Titel ≤ 70 Zeichen ohne Hashtags, Beschreibung 1–2 Sätze).",
        "- 3–5 Hashtags je Plattform (YouTube 2–5), spezifisch statt generisch (z. B. #Webdesign #Wolnzach #Handwerk),",
        "  keine Hashtag-Wand, keine Hashtags im Fließtext.",
        "- Klarer CTA in jedem Text (z. B. „Projekt anfragen auf pje-systems.de“ oder „Link im Profil“).",
        "- Keine erfundenen Zahlen, Kunden, Bewertungen, Referenzen, Ergebnisse oder Leistungsversprechen;",
        "  nicht „kostenlos/gratis/garantiert/Nummer 1“. Satire bleibt als Satire erkennbar.",
        "- Keine < oder > Zeichen.",
    ]
    if problems:
        lines += ["Der letzte Entwurf hatte diese Probleme – behebe sie:", *[f"- {p}" for p in problems]]
    return "\n".join(lines)
