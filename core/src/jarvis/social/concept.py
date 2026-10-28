"""Concept generator: one Claude call (Jarvis' existing Claude Code connection) writes the idea, the script and the
storyboard; the code then enforces the storyboard schema and the anti-repetition rules and sends problems back
for a corrected version (bounded attempts). Nothing is rendered from an unchecked storyboard."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from jarvis.social import storyboard as sb
from jarvis.social.brand import Brand
from jarvis.social.variety import Candidate, Past, constraints, slug, violations

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 4

CONCEPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "concept": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "title": {"type": "string"},
                "category": {"enum": sb.CATEGORIES},
                "inspiration": {"type": "array", "items": {"type": "string"}},
                "summary": {"type": "string"},
                "idea": {"type": "string"},
                "hook": {"type": "string"},
                "tone": {"enum": sb.TONES},
                "why": {"type": "string"},
            },
            "required": ["key", "title", "category", "summary", "idea", "hook", "tone"],
        },
        "script": {"type": "string"},
        "storyboard": sb.LLM_STORYBOARD_SCHEMA,
    },
    "required": ["concept", "script", "storyboard"],
}

SYSTEM = (
    "Du bist Creative Director und Motion Designer für PJE Systems, eine kleine Webdesign-Agentur aus Wolnzach. "
    "Du entwickelst täglich ein eigenes, überraschendes 9:16-Kurzvideo (8–12 s) und lieferst es als exakt "
    "strukturiertes Storyboard. Du erfindest nie Fakten, Kunden, Bewertungen, Zahlen, Preise oder Ergebnisse."
)

Ask = Callable[[str, str, dict[str, Any]], Awaitable[Any]]


@dataclass(slots=True)
class ConceptResult:
    concept: dict[str, Any]
    script: str
    storyboard: dict[str, Any]
    attempts: int
    notes: list[str]


def candidate_of(concept: dict[str, Any], board: dict[str, Any]) -> Candidate:
    summ = sb.summary(board)
    return Candidate(
        category=str(concept.get("category", "")),
        concept_key=str(concept.get("key", "")),
        hook=str(concept.get("hook", "")),
        tone=str(concept.get("tone", "")),
        palette=str(board.get("palette", "")),
        music=str(board.get("music", "pulse")),
        components=summ["components"],
        transitions=summ["transitions"],
    )


def build_prompt(
    brand: Brand,
    inspiration: dict[str, Any],
    history: list[Past],
    today: date,
    *,
    hint: str = "",
    voiceover: bool = False,
) -> str:
    c = constraints(history, today)
    cats = inspiration.get("categories") or {}
    fmt_lines = "\n".join(
        f"- {f['id']} [{f['category']}] {f['title']}: {f.get('idea', '')} (Bausteine: {', '.join(f.get('components') or [])})"
        for f in inspiration.get("formats") or []
    )
    rules = [
        "AUFGABE: Entwickle für heute EIN neues Video. Die Formate unten sind nur Inspiration – kombiniere, verdrehe, "
        "übertrage sie zu einer eigenen Idee. Kein Template mit ausgetauschtem Text.",
        f"Kategorien: {json.dumps(cats, ensure_ascii=False)}",
        f"PFLICHT – heute verboten (gestern gelaufen): {c['forbidden_categories'] or 'keine'}; bevorzugt in dieser "
        f"Reihenfolge: {c['preferred_categories']}",
        f"PFLICHT – diese Grundkonzepte (concept.key) gab es in den letzten 30 Tagen: {c['forbidden_concept_keys'] or 'keine'}",
        f"Letzte Titel (nicht wiederholen): {c['recent_titles'] or 'keine'}",
        f"PFLICHT – der Hook darf nicht so beginnen: {c['forbidden_hook_openers'] + c['banned_openers']}",
        f"PFLICHT – nicht mit diesen Bausteinen einsteigen: {c['avoid_opening_components'] or 'keine Einschränkung'}",
        f"Tonalität: bevorzugt {c['preferred_tones']}; verboten (wie gestern): {c['forbidden_tone'] or '–'}",
        f"Farbvariante verboten (wie gestern): {c['forbidden_palette'] or '–'}; Musik-Bett verboten: {c['forbidden_music'] or '–'}",
        f"Zuletzt oft benutzte Bausteine (eher meiden): {c['overused_components'] or 'keine'}; letzte Übergänge: {c['last_transitions']}",
        "Bisherige Wirkung (offizielle Plattform-Statistik; Reichweite, Website-Besuche und Anfragen zählen mehr als "
        "Views – Muster erfolgreicher Videos aufgreifen, schwache meiden): "
        + ("; ".join(c["performance"]) or "noch keine Daten"),
        "",
        "MARKE (nur das hier ist belegt):",
        brand.prompt_block(today),
        "",
        "INSPIRATION (60 Formate):",
        fmt_lines,
        "",
        "BAUKASTEN – Szenen-Komponenten mit ihren props (Textlängen sind harte Obergrenzen):",
        sb.component_catalog(),
        "",
        f"Übergänge (transition, Eintritt der Szene): {sb.TRANSITIONS}",
        f"Farbvarianten (palette, alle aus der Marke): {sb.PALETTES}; je Szene optional surface {sb.SURFACES} für Kontrastwechsel",
        f"Sounds (sfx je Szene, at = Sekunden ab Szenenbeginn, gain 0.05–1): {sb.SOUNDS}; Schnitte bekommen automatisch einen "
        "passenden Übergangssound, setze eigene Sounds für Klicks, Mitteilungen, Fehler, Treffer.",
        f"Musik-Bett (music): {sb.MUSIC}",
        "",
        "REGELN:",
        f"- Gesamtlänge 8–12 s (höchstens {sb.MAX_TOTAL_S:.0f} s), 3–7 Szenen, jede Szene {sb.MIN_SCENE_S}–{sb.MAX_SCENE_S} s.",
        "- Hook in den ersten 1–2 Sekunden: die erste Szene muss sofort stoppen lassen (Pattern Interrupt, Frage, Konflikt); "
        f"sie dauert höchstens {sb.HOOK_SCENE_MAX_S:g} s, ihr Kern muss in 2 s erfasst sein.",
        f"- Lesezeit: pro Szene ≥ {sb.READ_BASE_S} s + Wörter / {sb.WORDS_PER_SECOND}; lieber weniger Text, größer.",
        f"- Letzte Szene ist end_card mit button „{brand.cta_button}“ und url „{brand.website}“, mindestens 2 s lang.",
        "- Szene fact nur mit einer fact_id aus der Faktenliste. Preise/Angebote nur aus „Gültige Angebote“.",
        "- Keine echten Kunden, Marken oder Personen; Chats/Bewertungen/Parodien sind fiktiv (satire: true setzen, wenn "
        "review, dating oder weather vorkommen). Keine Ranking- oder Ergebnisversprechen.",
        "- Texte auf Deutsch, kurz, konkret, mit Witz oder Haltung; kein Marketing-Sprech („innovativ“, „nächstes Level“).",
        "- Headlines dürfen mit Punkt enden (Markenmotiv: der Punkt wird blau).",
        "- concept.key: kurzer, sprechender Slug des Grundkonzepts (z. B. „captcha-kein-kunde-von-2011“).",
        "- concept.summary: 1–2 Sätze für Sir; concept.inspiration: IDs der Formate, die dich inspiriert haben.",
        "- script: das Skript Szene für Szene in Prosa (was man sieht, liest, hört).",
    ]
    if voiceover:
        rules.append("- Zusätzlich storyboard.voiceover: ein gesprochener Satz (≤ 200 Zeichen), der zum Timing passt.")
    if hint:
        rules += ["", f"WUNSCH VON SIR für dieses Video: {hint}"]
    return "\n".join(rules)


def revise_prompt(base: str, previous: dict[str, Any], problems: list[str], earlier: list[str] | None = None) -> str:
    """``earlier``: violations of previous attempts – fixing one rule must not bring back another."""
    old = [p for p in earlier or [] if p not in problems]
    return (
        base
        + "\n\nDEIN LETZTER ENTWURF:\n"
        + json.dumps(previous, ensure_ascii=False)[:12000]
        + "\n\nER VERLETZT DIESE REGELN – liefere eine korrigierte, vollständige Fassung (gleiche Struktur):\n"
        + "\n".join(f"- {p}" for p in problems)
        + (
            "\n\nFRÜHERE ENTWÜRFE VERLETZTEN DIESE REGELN – nicht wieder einführen:\n"
            + "\n".join(f"- {p}" for p in old)
            if old
            else ""
        )
        + "\nTiming: Szenen mit wenig Text kurz halten, Text eher kürzen als Szenen verlängern; Gesamtlänge beachten."
    )


def check(raw: dict[str, Any], brand: Brand, history: list[Past], today: date) -> tuple[dict[str, Any], list[str]]:
    concept = dict(raw.get("concept") or {})
    concept["key"] = slug(str(concept.get("key") or concept.get("title") or ""))
    board = sb.normalize(
        dict(raw.get("storyboard") or {}),
        title=str(concept.get("title", "")),
        category=str(concept.get("category", "")),
        tone=str(concept.get("tone", "")),
    )
    used = {s.get("props", {}).get("fact_id") for s in board.get("scenes", []) if s.get("component") == "fact"}
    board["facts"] = [f for f in brand.facts if f["id"] in used]
    sb.fit_durations(board)
    problems = list(sb.validate(board, fact_ids=brand.fact_ids).problems)
    end = board["scenes"][-1] if board.get("scenes") else {}
    if end.get("component") == "end_card" and str(end.get("props", {}).get("url", "")).strip() != brand.website:
        problems.append(f"end_card.url muss „{brand.website}“ sein")
    if not concept.get("key"):
        problems.append("concept.key fehlt")
    if board.get("scenes"):
        problems += violations(candidate_of(concept, board), history, today)
    raw["concept"] = concept
    raw["storyboard"] = board
    return raw, problems


async def generate(
    ask: Ask,
    brand: Brand,
    inspiration: dict[str, Any],
    history: list[Past],
    today: date,
    *,
    hint: str = "",
    voiceover: bool = False,
    attempts: int = MAX_ATTEMPTS,
) -> ConceptResult:
    base = build_prompt(brand, inspiration, history, today, hint=hint, voiceover=voiceover)
    prompt = base
    notes: list[str] = []
    problems: list[str] = []
    seen: list[str] = []
    raw: dict[str, Any] = {}
    for attempt in range(1, attempts + 1):
        out = await ask(SYSTEM, prompt, CONCEPT_SCHEMA)
        if not isinstance(out, dict):
            problems = ["Antwort war kein JSON-Objekt"]
            prompt = base + "\n\nAntworte ausschließlich im vorgegebenen JSON-Format."
            continue
        raw, problems = check(out, brand, history, today)
        if not problems:
            board = raw["storyboard"]
            return ConceptResult(raw["concept"], str(raw.get("script", "")), board, attempt, notes)
        notes.append(f"Versuch {attempt}: " + "; ".join(problems)[:600])
        logger.info("Concept attempt %d rejected: %s", attempt, problems)
        prompt = revise_prompt(base, {k: raw.get(k) for k in ("concept", "script", "storyboard")}, problems, seen)
        seen += [p for p in problems if p not in seen]
    raise ConceptRejected(problems, notes)


class ConceptRejected(RuntimeError):
    def __init__(self, problems: list[str], notes: list[str]) -> None:
        super().__init__("Kein regelkonformes Konzept nach mehreren Versuchen: " + "; ".join(problems)[:500])
        self.problems = problems
        self.notes = notes


async def revise_storyboard(ask: Ask, brand: Brand, board: dict[str, Any], issues: list[str]) -> dict[str, Any]:
    """After a failed QA: same idea, fixed storyboard (shorter texts, longer scenes …). Checked again by the caller."""
    prompt = (
        "Dieses Storyboard eines PJE-Systems-Videos hat in der Qualitätskontrolle Probleme. Korrigiere NUR, was nötig "
        "ist (Texte kürzen/korrigieren, Szenen verlängern, Komponenten tauschen, wenn ein Text nicht passt). Idee, "
        "Kategorie und Tonalität bleiben.\n\nPROBLEME:\n"
        + "\n".join(f"- {i}" for i in issues)
        + "\n\nBAUKASTEN:\n"
        + sb.component_catalog()
        + "\n\nSTORYBOARD:\n"
        + json.dumps({k: v for k, v in board.items() if k != "facts"}, ensure_ascii=False)
    )
    out = await ask(SYSTEM, prompt, sb.LLM_STORYBOARD_SCHEMA)
    if not isinstance(out, dict):
        raise ConceptRejected(["Korrektur war kein JSON"], [])
    fixed = sb.normalize(out, title=board.get("title", ""), category=board["category"], tone=board["tone"])
    fixed["facts"] = board.get("facts", [])
    sb.fit_durations(fixed)
    return fixed
