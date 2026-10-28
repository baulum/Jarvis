"""Storyboard: the structured scene description the concept LLM writes and Remotion renders (social/src/types.ts).

``validate`` checks a storyboard against the JSON schema (per component its own props schema) and the rules the
schema cannot express: total length, end card last, facts only from the brand file, satire marking, readable text
(reading time per scene). It returns human-readable problems – the concept step feeds them back to the LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import jsonschema

FPS = 30
WIDTH, HEIGHT = 1080, 1920
MIN_TOTAL_S, IDEAL_MAX_S, MAX_TOTAL_S = 8.0, 12.0, 15.0
MIN_SCENE_S, MAX_SCENE_S = 0.8, 4.5
HOOK_WITHIN_S = 2.0
WORDS_PER_SECOND = 3.5  # reading speed for short on-screen text (subtitle norms: ~15–17 characters/s)
READ_BASE_S = 0.3  # orientation time before the first word is read

PALETTES = ["ink", "canvas", "electric", "graphite", "paper"]
SURFACES = ["dark", "light", "accent"]
TONES = ["humorvoll", "provokant", "minimalistisch", "cineastisch", "angebotsorientiert"]
CATEGORIES = ["A", "B", "C", "D", "E", "F"]
TRANSITIONS = [
    "cut",
    "fade",
    "slide_up",
    "slide_left",
    "push",
    "wipe",
    "zoom",
    "glitch",
    "flash",
    "blur",
    "whip",
    "iris",
]
MUSIC = ["pulse", "tension", "glow", "none"]
SOUNDS = [
    "whoosh", "swoosh_down", "click", "pop", "ding", "notify", "error_buzz", "glitch", "bass_hit", "riser", "typing",
    "tick", "shutter", "swipe", "success",
]  # fmt: skip


def _s(max_len: int, min_len: int = 1) -> dict[str, Any]:
    return {"type": "string", "minLength": min_len, "maxLength": max_len}


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def _arr(item: dict[str, Any], lo: int, hi: int) -> dict[str, Any]:
    return {"type": "array", "items": item, "minItems": lo, "maxItems": hi}


# Every building block with its props schema, a one-line description for the concept prompt and the texts the QA
# reads (reading time, spelling).
COMPONENTS: dict[str, dict[str, Any]] = {
    "kinetic_type": {
        "about": "Kinetic Typography: 1–4 kurze Zeilen, Stil stack|slam|typewriter|split|scale; accent=true färbt eine Zeile blau",
        "props": _obj(
            {
                "lines": _arr(_obj({"text": _s(22), "accent": {"type": "boolean"}}, ["text"]), 1, 4),
                "style": {"enum": ["stack", "slam", "typewriter", "split", "scale"]},
                "kicker": _s(28),
            },
            ["lines", "style"],
        ),
    },
    "pov_title": {
        "about": "POV-Titelkarte im Social-Stil („POV: …“), Text wird getippt",
        "props": _obj({"text": _s(60), "caption": _s(36)}, ["text"]),
    },
    "chat": {
        "about": "Messenger-Chat (whatsapp|imessage) im Handy, 1–5 Nachrichten me/them mit Tipp-Indikator; wird als fiktiv markiert",
        "props": _obj(
            {
                "app": {"enum": ["whatsapp", "imessage"]},
                "contact": _s(22),
                "messages": _arr(_obj({"from": {"enum": ["me", "them"]}, "text": _s(64)}, ["from", "text"]), 1, 5),
            },
            ["contact", "messages"],
        ),
    },
    "error_dialog": {
        "about": "Fake-Systemmeldung (error|warning|critical) mit Titel, Text, optional Code, 1–2 Buttons",
        "props": _obj(
            {
                "severity": {"enum": ["error", "warning", "critical"]},
                "title": _s(34),
                "message": _s(80),
                "code": _s(18),
                "buttons": _arr(_s(14), 1, 2),
            },
            ["title", "message"],
        ),
    },
    "system_crash": {
        "about": "Fake-Systemabsturz mit Scanlines, Headline, bis 3 Zeilen, optional Prozentanzeige",
        "props": _obj(
            {"headline": _s(40), "lines": _arr(_s(36), 0, 3), "percent": {"type": "integer", "minimum": 0, "maximum": 100}},
            ["headline"],
        ),
    },
    "browser": {
        "about": "Browserfenster mit getippter URL; state old (Website von 2011)|new (moderne Beispielseite)|loading|not_found",
        "props": _obj(
            {"url": _s(34), "state": {"enum": ["old", "new", "loading", "not_found"]}, "headline": _s(36)},
            ["url", "state"],
        ),
    },
    "tab_chaos": {
        "about": "3–8 Browser-Tabs stapeln sich chaotisch, danach eine Pointe",
        "props": _obj({"tabs": _arr(_s(26), 3, 8), "punchline": _s(40)}, ["tabs", "punchline"]),
    },
    "notification": {
        "about": "Sperrbildschirm mit Mitteilung (App-Name, Titel, Text), optional 1–2 weitere darunter",
        "props": _obj(
            {
                "app": _s(18),
                "title": _s(64),
                "body": _s(80),
                "time": _s(10),
                "stack": _arr(_obj({"app": _s(18), "title": _s(48), "body": _s(60)}, ["app", "title"]), 0, 2),
            },
            ["app", "title"],
        ),
    },
    "terminal": {
        "about": "Terminal; Zeilen kind cmd (wird getippt)|out|ok|err",
        "props": _obj(
            {
                "lines": _arr(_obj({"kind": {"enum": ["cmd", "out", "ok", "err"]}, "text": _s(38)}, ["kind", "text"]), 1, 6),
                "title": _s(24),
            },
            ["lines"],
        ),
    },
    "search": {
        "about": "Suchmaske, Suchanfrage wird getippt, 1–3 Ergebnisse (highlight hebt eines hervor) – generische Suche, kein Logo, keine Ranking-Behauptung",
        "props": _obj(
            {
                "query": _s(38),
                "results": _arr(
                    _obj({"title": _s(46), "url": _s(32), "note": _s(56), "highlight": {"type": "boolean"}}, ["title", "url"]),
                    1,
                    3,
                ),
            },
            ["query", "results"],
        ),
    },
    "split_screen": {
        "about": "Split-Screen: links alte Baukasten-Website, rechts moderne Seite, Trenner wischt; Labels oben",
        "props": _obj(
            {"left_label": _s(18), "right_label": _s(18), "left_caption": _s(24), "right_caption": _s(24)},
            ["left_label", "right_label"],
        ),
    },
    "glass_stack": {
        "about": "Glassmorphism: Headline + 2–4 gläserne Zeilen über Licht in Markenblau",
        "props": _obj({"headline": _s(36), "items": _arr(_s(30), 2, 4)}, ["headline", "items"]),
    },
    "browser_3d": {
        "about": "3D-Browserfenster dreht sich in den Raum, Headline darüber, zeigt neutrale Beispielseite",
        "props": _obj({"headline": _s(36), "url": _s(32)}, ["headline", "url"]),
    },
    "cursor_click": {
        "about": "Cursor-Storytelling: optional Headline + bis 3 abgehakte Schritte, Cursor fährt zum Button und klickt",
        "props": _obj({"headline": _s(36), "button": _s(22), "steps": _arr(_s(26), 0, 3)}, ["button"]),
    },
    "countdown": {
        "about": "Countdown von 3–5 mit Ring, danach end_text",
        "props": _obj(
            {"from": {"type": "integer", "minimum": 3, "maximum": 5}, "label": _s(30), "end_text": _s(18)},
            ["label", "end_text"],
        ),
    },
    "loading": {
        "about": "Ladebildschirm: Spinner, Label, Balken bleibt bei stall_at % hängen",
        "props": _obj(
            {"label": _s(32), "stall_at": {"type": "integer", "minimum": 1, "maximum": 100}, "caption": _s(28)},
            ["label"],
        ),
    },
    "fact": {
        "about": "Großer Wert aus den Markenfakten (fact_id MUSS aus der Faktenliste stammen), optional Kicker",
        "props": _obj({"fact_id": _s(40), "kicker": _s(24)}, ["fact_id"]),
    },
    "weather": {
        "about": "Wetterbericht-Parodie mit Regen und Blitz (Satire, wird markiert): Ort, Vorhersage, Temperatur, bis 3 Aussichten",
        "props": _obj(
            {"location": _s(28), "forecast": _s(20), "temp": _s(6), "outlook": _arr(_s(30), 0, 3)},
            ["location", "forecast", "temp"],
        ),
    },
    "review": {
        "about": "Satirische Bewertungskarte (wird als fiktiv markiert): Sterne 1–5, fiktiver Autor, Text",
        "props": _obj(
            {"stars": {"type": "integer", "minimum": 1, "maximum": 5}, "author": _s(22), "text": _s(96)},
            ["stars", "author", "text"],
        ),
    },
    "dating": {
        "about": "Dating-App-Parodie (wird markiert): Profil der alten Website, Bio, Wisch nach left|right",
        "props": _obj({"name": _s(24), "bio": _s(80), "verdict": {"enum": ["left", "right"]}}, ["name", "bio"]),
    },
    "end_card": {
        "about": "Abschluss (immer die letzte Szene): Logo, Headline, CTA-Button, URL; optional Subline",
        "props": _obj({"headline": _s(36), "button": _s(22), "url": _s(32), "subline": _s(44)}, ["headline", "button", "url"]),
    },
}  # fmt: skip

SATIRE_COMPONENTS = {"review", "weather", "dating", "chat"}


def _scene_schema() -> dict[str, Any]:
    branches = [
        {"if": {"properties": {"component": {"const": name}}}, "then": {"properties": {"props": spec["props"]}}}
        for name, spec in COMPONENTS.items()
    ]
    return {
        "type": "object",
        "properties": {
            "id": {"type": "string", "pattern": "^[a-z0-9_-]{1,20}$"},
            "component": {"enum": list(COMPONENTS)},
            "duration": {"type": "number", "minimum": MIN_SCENE_S, "maximum": MAX_SCENE_S},
            "transition": {"enum": TRANSITIONS},
            "surface": {"enum": SURFACES},
            "props": {"type": "object"},
            "sfx": _arr(
                _obj(
                    {
                        "sound": {"enum": SOUNDS},
                        "at": {"type": "number", "minimum": 0, "maximum": MAX_SCENE_S},
                        "gain": {"type": "number", "minimum": 0.05, "maximum": 1},
                    },
                    ["sound", "at"],
                ),
                0,
                4,
            ),
        },
        "required": ["id", "component", "duration", "props"],
        "additionalProperties": False,
        "allOf": branches,
    }


STORYBOARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "version": {"const": 1},
        "title": _s(80),
        "category": {"enum": CATEGORIES},
        "tone": {"enum": TONES},
        "palette": {"enum": PALETTES},
        "music": {"enum": MUSIC},
        "satire": {"type": "boolean"},
        "voiceover": _s(220),
        "scenes": _arr(_scene_schema(), 3, 8),
        "facts": {"type": "array"},
    },
    "required": ["version", "title", "category", "tone", "palette", "scenes"],
    "additionalProperties": False,
}

# Simplified shape for the LLM's structured output (``--json-schema``): props stay a free object there and are
# checked by STORYBOARD_SCHEMA afterwards – deep if/then schemas are not reliable as an output constraint.
LLM_STORYBOARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": _s(80),
        "palette": {"enum": PALETTES},
        "music": {"enum": MUSIC},
        "satire": {"type": "boolean"},
        "scenes": _arr(
            {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "component": {"enum": list(COMPONENTS)},
                    "duration": {"type": "number"},
                    "transition": {"enum": TRANSITIONS},
                    "surface": {"enum": SURFACES},
                    "props": {"type": "object"},
                    "sfx": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["id", "component", "duration", "props"],
            },
            3,
            8,
        ),
    },
    "required": ["title", "palette", "scenes"],
}


@dataclass(slots=True)
class Check:
    ok: bool
    problems: list[str]


def scene_texts(scene: dict[str, Any], facts: dict[str, str] | None = None) -> list[str]:
    """All visible text of a scene (for reading time, spelling and the email/library summary). ``facts``: id →
    value and label of the brand facts, so a ``fact`` scene counts the text it shows."""
    p = scene.get("props") or {}
    out: list[str] = []
    if scene.get("component") == "fact" and facts and p.get("fact_id") in facts:
        out.append(facts[p["fact_id"]])

    def add(v: Any) -> None:
        if isinstance(v, str) and v.strip():
            out.append(v.strip())

    for key in ("text", "caption", "title", "message", "code", "headline", "url", "punchline", "body", "query",
                "left_label", "right_label", "left_caption", "right_caption", "label", "end_text", "kicker",
                "location", "forecast", "temp", "author", "name", "bio", "button", "subline", "contact"):  # fmt: skip
        add(p.get(key))
    for key in ("lines", "messages", "results", "stack"):
        for item in p.get(key) or []:
            if isinstance(item, dict):
                for k in ("text", "title", "note", "body", "url"):
                    add(item.get(k))
            else:
                add(item)
    for key in ("buttons", "tabs", "items", "steps", "outlook"):
        for item in p.get(key) or []:
            add(item)
    return out


def reading_seconds(texts: list[str]) -> float:
    words = sum(len(re.findall(r"\w+", t)) for t in texts)
    return READ_BASE_S + words / WORDS_PER_SECOND


# Phrases that give away a template opener; the brief bans them as video starts.
BANNED_OPENERS = [
    "deine website ist veraltet",
    "ist deine website veraltet",
    "brauchst du eine website",
    "du brauchst eine neue website",
    "willkommen bei pje",
    "hallo und willkommen",
]


def total_seconds(board: dict[str, Any]) -> float:
    return sum(round(float(s.get("duration", 0)) * FPS) for s in board.get("scenes", [])) / FPS


def validate(board: dict[str, Any], *, fact_ids: set[str] | None = None) -> Check:
    problems: list[str] = []
    try:
        jsonschema.validate(board, STORYBOARD_SCHEMA)
    except jsonschema.ValidationError as e:
        where = "/".join(str(p) for p in e.absolute_path) or "storyboard"
        problems.append(f"Schema: {where}: {e.message}"[:300])
        return Check(False, problems)
    scenes = board["scenes"]
    ids = [s["id"] for s in scenes]
    if len(set(ids)) != len(ids):
        problems.append("Szenen-IDs müssen eindeutig sein")
    total = total_seconds(board)
    if total < MIN_TOTAL_S or total > MAX_TOTAL_S:
        problems.append(f"Gesamtlänge {total:.1f} s – erlaubt {MIN_TOTAL_S:.0f}–{MAX_TOTAL_S:.0f} s (ideal 8–12 s)")
    if scenes[-1]["component"] != "end_card":
        problems.append("Die letzte Szene muss end_card sein (klarer CTA)")
    if any(s["component"] == "end_card" for s in scenes[:-1]):
        problems.append("end_card nur als letzte Szene")
    first = scenes[0]
    if first["duration"] > HOOK_WITHIN_S + 0.6 and first["component"] not in ("chat", "notification", "review"):
        problems.append(f"Hook: die erste Szene muss in {HOOK_WITHIN_S:.0f} s greifen (ist {first['duration']} s lang)")
    opener = " ".join(scene_texts(first)).lower()
    for banned in BANNED_OPENERS:
        if banned in opener:
            problems.append(f"Abgedroschener Einstieg „{banned}“ – eigenen Hook finden")
    fact_text = {
        f["id"]: f"{f['value']} {f['label']}" for f in board.get("facts") or [] if isinstance(f, dict) and "id" in f
    }
    for s in scenes:
        need = reading_seconds(scene_texts(s, fact_text))
        if s["component"] in ("end_card",):
            need = max(1.8, need * 0.8)  # logo + button are glanced at, not read
        if need > s["duration"] + 0.35:
            problems.append(
                f"Szene {s['id']} ({s['component']}): {s['duration']} s zu kurz zum Lesen (braucht ~{need:.1f} s)"
            )
        if s["component"] == "fact":
            fid = s["props"].get("fact_id")
            if fact_ids is not None and fid not in fact_ids:
                problems.append(f"Szene {s['id']}: fact_id {fid!r} gibt es nicht – nur {sorted(fact_ids)}")
        for cue in s.get("sfx") or []:
            if cue["at"] > s["duration"]:
                problems.append(f"Szene {s['id']}: Sound bei {cue['at']} s liegt hinter dem Szenenende")
    if any(s["component"] in ("review", "dating", "weather") for s in scenes) and not board.get("satire", False):
        problems.append("Parodie-Szenen (review/dating/weather) brauchen satire: true")
    return Check(not problems, problems)


def normalize(board: dict[str, Any], *, title: str, category: str, tone: str) -> dict[str, Any]:
    """Fill in what the LLM may leave out and round durations to whole frames."""
    out = dict(board)
    out["version"] = 1
    out.setdefault("title", title)
    out["category"] = category
    out["tone"] = tone
    out.setdefault("music", "pulse")
    scenes = []
    for i, s in enumerate(out.get("scenes") or []):
        s = dict(s)
        s["id"] = re.sub(r"[^a-z0-9_-]", "", str(s.get("id") or f"s{i + 1}").lower())[:20] or f"s{i + 1}"
        s["duration"] = round(round(float(s.get("duration", 1.5)) * FPS) / FPS, 3)
        s.setdefault("transition", "cut" if i == 0 else "fade")
        s["sfx"] = [c for c in (s.get("sfx") or []) if isinstance(c, dict)][:4]
        scenes.append(s)
    out["scenes"] = scenes
    if any(s.get("component") in ("review", "dating", "weather") for s in scenes):
        out["satire"] = True
    return out


def summary(board: dict[str, Any]) -> dict[str, list[str]]:
    """Components, transitions and sounds used – the creative memory compares these across videos."""
    scenes = board.get("scenes") or []
    return {
        "components": [s.get("component", "") for s in scenes],
        "transitions": [s.get("transition", "cut") for s in scenes[1:]],
        "sounds": sorted(
            {c.get("sound", "") for s in scenes for c in (s.get("sfx") or [])} | {f"music:{board.get('music', 'none')}"}
        ),
    }


def component_catalog() -> str:
    return "\n".join(
        f"- {name}: {spec['about']}. props: {_props_hint(spec['props'])}" for name, spec in COMPONENTS.items()
    )


def _props_hint(schema: dict[str, Any]) -> str:
    req = set(schema.get("required", []))
    parts = []
    for k, v in schema["properties"].items():
        kind = v.get("enum") or v.get("type")
        if v.get("type") == "string":
            kind = f"Text≤{v['maxLength']}"
        elif v.get("type") == "array":
            item = v["items"]
            kind = f"Liste[{item.get('type') if 'properties' not in item else '{' + ', '.join(item['properties']) + '}'}] {v['minItems']}–{v['maxItems']}"
            if item.get("type") == "string":
                kind = f"Liste[Text≤{item['maxLength']}] {v['minItems']}–{v['maxItems']}"
        parts.append(f"{k}{'' if k in req else '?'}={kind}")
    return ", ".join(parts)
