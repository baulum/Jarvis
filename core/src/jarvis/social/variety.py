"""Anti-repetition rules of the daily video (enforced in code, not only asked of the LLM).

Hard rules (``violations``):
* never the same category on two consecutive days
* no identical base concept (``concept_key``) within 30 days
* the hook does not open like one of the last 14 videos, and never with a banned template opener
* opening component, tone, palette and music differ from the previous video
* components and transitions are not (almost) the same set as in the previous video

``constraints`` turns the history into the brief for the next concept (what to avoid, which categories and tones
are open), so the first attempt usually passes; ``violations`` is the final gate.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from jarvis.social.storyboard import BANNED_OPENERS, CATEGORIES, TONES

CONCEPT_WINDOW_DAYS = 30
HOOK_WINDOW = 14
OPENING_WINDOW = 3
MAX_COMPONENT_OVERLAP = 0.75


@dataclass(slots=True)
class Past:
    day: date
    category: str
    concept_key: str
    hook: str = ""
    tone: str = ""
    palette: str = ""
    music: str = ""
    components: list[str] = field(default_factory=list)
    transitions: list[str] = field(default_factory=list)
    title: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)  # per platform, from the official APIs


@dataclass(slots=True)
class Candidate:
    category: str
    concept_key: str
    hook: str
    tone: str
    palette: str
    music: str
    components: list[str]
    transitions: list[str]


def slug(text: str, limit: int = 80) -> str:
    t = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:limit]


def opener(text: str, words: int = 3) -> str:
    """The first words of a hook, normalized ("Deine Website ist…" == "deine website ist")."""
    return " ".join(re.findall(r"\w+", text.lower())[:words])


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if sa | sb else 0.0


def ordered(history: list[Past]) -> list[Past]:
    return sorted(history, key=lambda p: p.day, reverse=True)


def violations(c: Candidate, history: list[Past], today: date) -> list[str]:
    out: list[str] = []
    hist = ordered(history)
    yesterday = [p for p in hist if p.day == today - timedelta(days=1)]
    if any(p.category == c.category for p in yesterday):
        out.append(f"Kategorie {c.category} lief gestern schon – heute eine andere Kategorie")
    key = slug(c.concept_key)
    recent = [p for p in hist if (today - p.day).days < CONCEPT_WINDOW_DAYS]
    if any(slug(p.concept_key) == key for p in recent):
        out.append(f"Grundkonzept „{c.concept_key}“ gab es in den letzten {CONCEPT_WINDOW_DAYS} Tagen")
    op = opener(c.hook)
    hook_text = " ".join(re.findall(r"\w+", c.hook.lower()))
    for banned in BANNED_OPENERS:
        if banned in hook_text:
            out.append(f"Hook beginnt mit der verbotenen Floskel „{banned}“")
    if op and any(opener(p.hook) == op for p in hist[:HOOK_WINDOW]):
        out.append(f"Hook-Einstieg „{op}“ wurde in den letzten {HOOK_WINDOW} Videos schon benutzt")
    if c.components and any(p.components and p.components[0] == c.components[0] for p in hist[:OPENING_WINDOW]):
        out.append(f"Einstieg mit {c.components[0]} gab es in den letzten {OPENING_WINDOW} Videos")
    if hist:
        last = hist[0]
        if last.tone and c.tone == last.tone:
            out.append(f"Tonalität {c.tone} wie beim letzten Video")
        if last.palette and c.palette == last.palette:
            out.append(f"Farbvariante {c.palette} wie beim letzten Video")
        if last.music and c.music == last.music and c.music != "none":
            out.append(f"Musik-Bett {c.music} wie beim letzten Video")
        if last.components and _jaccard(c.components, last.components) > MAX_COMPONENT_OVERLAP:
            out.append("Fast dieselben Bausteine wie beim letzten Video – andere Komponenten kombinieren")
        if last.transitions and sorted(c.transitions) == sorted(last.transitions):
            out.append("Dieselben Übergänge wie beim letzten Video")
    return out


def constraints(history: list[Past], today: date) -> dict[str, Any]:
    """What the next concept must avoid and what is open – the brief for the concept prompt."""
    hist = ordered(history)
    yesterday = {p.category for p in hist if p.day == today - timedelta(days=1)}
    window = [p for p in hist if (today - p.day).days < CONCEPT_WINDOW_DAYS]
    used_cats = [p.category for p in window]
    open_cats = [c for c in CATEGORIES if c not in yesterday]
    # least used category of the window first: variety over the month, not only day to day
    open_cats.sort(key=lambda c: (used_cats.count(c), CATEGORIES.index(c)))
    last = hist[0] if hist else None
    tones = [t for t in TONES if not last or t != last.tone]
    recent_tones = [p.tone for p in hist[:5]]
    tones.sort(key=lambda t: recent_tones.count(t))
    comp_counts: dict[str, int] = {}
    for p in hist[:7]:
        for comp in set(p.components):
            comp_counts[comp] = comp_counts.get(comp, 0) + 1
    return {
        "forbidden_categories": sorted(yesterday),
        "preferred_categories": open_cats,
        "forbidden_concept_keys": sorted({slug(p.concept_key) for p in window if p.concept_key}),
        "recent_titles": [p.title for p in window[:12] if p.title],
        "forbidden_hook_openers": sorted({opener(p.hook) for p in hist[:HOOK_WINDOW] if p.hook}),
        "banned_openers": BANNED_OPENERS,
        "avoid_opening_components": sorted({p.components[0] for p in hist[:OPENING_WINDOW] if p.components}),
        "preferred_tones": tones,
        "forbidden_tone": last.tone if last else None,
        "forbidden_palette": last.palette if last else None,
        "forbidden_music": last.music if last and last.music != "none" else None,
        "overused_components": sorted(c for c, n in comp_counts.items() if n >= 3),
        "last_transitions": last.transitions if last else [],
        "performance": [
            f"{p.title} [{p.category}, {p.tone}]: "
            + "; ".join(
                f"{pf} " + ", ".join(f"{k}={v}" for k, v in m.items() if v is not None)
                for pf, m in p.metrics.items()
                if m
            )
            for p in hist[:20]
            if p.metrics
        ],
    }
