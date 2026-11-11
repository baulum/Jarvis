"""Model selection for the Claude Code agent.

* Default: Sonnet. Opus only when Sir asks for it (voice/chat command, app, API, agent tool).
* Automatic downgrade to Haiku when Sonnet would be overkill (small talk, short questions,
  simple single actions) — never for business processes, planning, code or research.
* Scopes: ``turn`` (only this answer), ``session`` (this conversation), ``default`` (from now on,
  persisted in ``agent-model.json``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from jarvis.agent.model_names import (
    FAMILY_PATTERN,
    KNOWN_VERSION,
    VERSION_PATTERN,
    adjacent_version,
    family_of,
    find_mentions,
    parse_spec,
    version_of,
)

if TYPE_CHECKING:
    from jarvis.agent.usage import UsageTracker

Scope = Literal["turn", "session", "default"]

MODELS: dict[str, dict[str, str]] = {
    "haiku": {"id": "claude-haiku-5-5", "label": "Haiku 5.5"},
    "sonnet": {"id": "claude-sonnet-5-5", "label": "Sonnet 5.5"},
    "opus": {"id": "claude-opus-5-5", "label": "Opus 5.5"},
}

EFFORTS: dict[str, str] = {
    "low": "Niedrig",
    "medium": "Mittel",
    "high": "Hoch",
    "xhigh": "Sehr hoch",
    "max": "Maximal",
}

_MODEL_WORD = FAMILY_PATTERN  # also "h.a.i.k.u", "h a i k u", "h-a-i-k-u"
_VERB = (
    r"(?:nimm|nehm\w*|nutz\w*|benutz\w*|verwend\w*|wechs\w*|schalt\w*|stell\w*|geh\w*|wähl\w*|switch\w*|"
    r"use|take|ich (?:will|möchte|brauche)|soll\w*)"
)
_SWITCH_RE = re.compile(
    # "nimm Opus", "nutze bitte Haiku", "stell auf Sonnet um"
    r"(?:\b" + _VERB + r"\b(?:[^.?!]|(?<=\d)\.(?=\d)){0,30}?\b" + _MODEL_WORD + r"\b"
    # "Haiku nutzen", "auf Opus wechseln"
    # (the gap may contain a version such as "5.5", hence the digit-dot-digit exception)
    r"|\b" + _MODEL_WORD + r"\b(?:[^.?!]|(?<=\d)\.(?=\d)){0,20}?\b(?:nutz\w*|benutz\w*|verwend\w*|nehmen|wechseln|"
    r"umschalten|umstellen|aktivier\w*)\b"
    # "Claude Opus", "Modell Haiku", "auf Sonnet", "Opus-Modus"
    r"|\b(?:nur diesmal|einmalig|nur (?:für )?(?:diese|die nächste) \w+|just this once)\W+" + _MODEL_WORD
    + r"|\b(?:claude|modell|model|auf|mit)\s+" + _MODEL_WORD + r"\b"
    r"|\b" + _MODEL_WORD + r"[- ]?modus\b)",
    re.IGNORECASE,
)
_DEFAULT_RE = re.compile(
    r"\b(ab jetzt|ab sofort|dauerhaft|standardmäßig|als standard|immer|from now on|default)\b", re.I
)
_TURN_RE = re.compile(
    r"\b(nur (?:für )?(?:diese|die nächste) (?:frage|antwort|aufgabe)|nur diesmal|einmalig|just this once)\b", re.I
)
_AUTO_RE = re.compile(r"\b(automatisch\w* (?:modell)?wahl|auto[- ]?modus|wieder automatisch)\b", re.I)

# Anything touching business processes, planning, code, research or long-form writing stays on Sonnet.
_COMPLEX_RE = re.compile(
    r"\b(analys\w*|plan\w*|strateg\w*|vergleich\w*|programmier\w*|code|coding|entwickl\w*|debug\w*|fehler\w*|"
    r"website|webseite|projekt\w*|rechnung\w*|kunde\w*|lexware|angebot\w*|vertrag\w*|recherch\w*|zusammenfass\w*|"
    r"erklär\w*|warum|wieso|schreib\w*|formulier\w*|e-?mail\w*|mail|entwurf|konzept\w*|bewert\w*|optimier\w*|"
    r"aufgabe\w*|termin\w*|kalender|github|repository|repo|pull request|bericht\w*|präsentation\w*|übersetz\w*|"
    r"berechn\w*|kalkulier\w*|budget\w*|preis\w*|steuer\w*|buchhaltung|telefonier\w*|anruf\w*)\b",
    re.IGNORECASE,
)


def normalize(word: str) -> str | None:
    """Family alias for one spelling ("Opus", "sonett", "h.a.i.k.u"); None if it is no model family."""
    return family_of(word)


def resolve_alias(value: str | None) -> str | None:
    """haiku|sonnet|opus (also spelled out or with version 5.5: ``h.a.i.k.u 5,5``) or a full model id
    (``claude-opus-5-5``) → alias; None for empty/``auto``/unknown – an unknown version is never rounded to 5.5."""
    if not value:
        return None
    v = value.strip().lower()
    for alias, m in MODELS.items():
        if v == alias or v == m["id"]:
            return alias
    mention = parse_spec(value)
    if mention is not None and mention.version_known:
        return mention.family
    return None


def model_id(alias: str) -> str:
    return MODELS[alias]["id"]


def describe(alias: str) -> str:
    """"Haiku 5.5 (claude-haiku-5-5)" – how a recognised model is mirrored back to Sir."""
    return f"{MODELS[alias]['label']} ({MODELS[alias]['id']})"


def unclear_model_note(value: str) -> str:
    """Why a model statement could not be mapped – for error messages: never guess, ask Sir."""
    mention = parse_spec(value)
    if mention is not None and not mention.version_known:
        return (
            f"Version {mention.version} von {MODELS[mention.family]['label'].split()[0]} ist unbekannt "
            f"(bekannt ist nur {KNOWN_VERSION}) – nicht raten, Sir nachfragen"
        )
    return "Modellangabe nicht eindeutig (z. B. nur eine Versionszahl) – nicht raten, Sir nachfragen"


@dataclass(slots=True)
class SwitchCommand:
    model: str | None  # None = back to automatic selection
    scope: Scope
    remainder: str  # the rest of the utterance that still needs an answer ("" = pure command)
    clarify: str | None = None  # set = the model statement is unclear: ask Sir this, switch nothing


# "nimm 5.5", "Modell 5.5", "stell auf Version fünf punkt fünf" – a version with a switching verb but no family
_BARE_VERSION_RE = re.compile(
    r"(?:\b" + _VERB + r"\b(?:\s+(?:bitte|doch|mal|jetzt|ab sofort|auf|das|die|den|dem|neue\w*|version|modell|model))"
    r"{0,4}"
    r"|\b(?:modell|model)\s*(?:auf|:)?)\s*(" + VERSION_PATTERN + ")",
    re.IGNORECASE,
)
# a model named together with a coding job belongs to the job (coding_start_job), not to the chat model
_JOB_CONTEXT_RE = re.compile(r"\b(coding|codier\w*|jobs?|auftrag\w*|aufträge)\b", re.IGNORECASE)


def _unknown_version_question(family: str, version: str) -> str:
    label = MODELS[family]["label"]
    return (
        f"„{label.split()[0]} {version}“ kenne ich nicht, Sir – verfügbar ist nur Version {KNOWN_VERSION}. "
        f"Ich schalte nichts um. Soll es {label} sein?"
    )


def parse_command(text: str) -> SwitchCommand | None:
    if _AUTO_RE.search(text):
        rest = _AUTO_RE.sub("", text).strip(" ,.!?")
        return SwitchCommand(None, "default" if _DEFAULT_RE.search(text) else "session", _clean(rest))
    m = _SWITCH_RE.search(text)
    if not m:
        bare = _BARE_VERSION_RE.search(text)
        if bare and not _JOB_CONTEXT_RE.search(text):
            version = version_of(bare.group(1)) or bare.group(1)
            return SwitchCommand(
                None,
                "session",
                "",
                clarify=(
                    f"Verstanden habe ich nur die Version „{version}“, Sir, nicht das Modell. Haiku, Sonnet oder "
                    "Opus? Ich schalte nichts um, bis das Modell feststeht."
                ),
            )
        return None
    if _JOB_CONTEXT_RE.search(text):
        return None  # "Opus für den Coding-Job": belongs to coding_start_job, never to the chat model
    group = next(i for i, g in enumerate(m.groups(), 1) if g)
    model = normalize(m.group(group))
    if model is None:
        return None
    version, start, end = adjacent_version(text, m.start(group), m.end(group))
    start, end = min(start, m.start()), max(end, m.end())
    scope: Scope = "turn" if _TURN_RE.search(text) else "default" if _DEFAULT_RE.search(text) else "session"
    if version is not None and version != KNOWN_VERSION:
        return SwitchCommand(None, scope, "", clarify=_unknown_version_question(model, version))
    rest = text[:start] + text[end:]
    rest = _TURN_RE.sub("", _DEFAULT_RE.sub("", rest))
    return SwitchCommand(model, scope, _clean(rest))


def model_hint(text: str) -> str:
    """Note for the agent about model names in Sir's message (cleaned spelling, canonical id, ask-don't-guess rule).
    Empty when nothing model-like (with version or spelled out) is in the text – plain "Haiku-Gedichte" stays quiet."""
    lines: list[str] = []
    for mention in find_mentions(text):
        decimal = mention.version is not None and "." in mention.version
        if not (mention.spelled_out or decimal):
            continue
        name = MODELS[mention.family]["label"].split()[0]
        if mention.version_known:
            lines.append(f"„{name}“ → {describe(mention.family)}")
        else:
            lines.append(f"„{name} {mention.version}“ → unbekannte Version (nur {KNOWN_VERSION} existiert)")
    bare = _BARE_VERSION_RE.search(text)
    if bare and not find_mentions(text):
        lines.append(f"nur die Version „{version_of(bare.group(1)) or bare.group(1)}“ ohne Modellfamilie")
    if not lines:
        return ""
    return (
        "Modellangaben in Sirs Nachricht (Schreibweise bereinigt): "
        + "; ".join(lines)
        + ". Verwende genau das Modell, das Sir nennt. Bei Coding-Aufträgen gehört es als `model` an den Job "
        "(`coding_start_job`); das Chat-Modell änderst du nur mit `agent_set_model`, wenn Sir ausdrücklich den Chat "
        "meint. Spiegle die erkannte Angabe vorher kurz zurück („Erkannt: Haiku 5.5 (claude-haiku-5-5)“). Ist die "
        "Angabe unklar (nur eine Zahl, unbekannte Version), rate nicht, sondern frage nach."
    )


def _clean(rest: str) -> str:
    rest = re.sub(
        r"^\s*(jarvis|claude|bitte|und|dann|für|als modell|das modell|modell)\b[\s,:]*",
        "",
        rest.strip(" ,.:;!?-"),
        flags=re.I,
    )
    rest = rest.strip(" ,.:;!?-")
    return rest if len(rest.split()) >= 3 else ""


def is_simple(text: str, channel: str) -> bool:
    """Heuristic: Haiku is enough (short, no business/planning/code vocabulary, at most one question)."""
    t = text.strip()
    if _COMPLEX_RE.search(t):
        return False
    words = len(t.split())
    sentences = len([s for s in re.split(r"[.!?]+", t) if s.strip()])
    limit = 25 if channel in ("voice", "phone") else 18
    return words <= limit and sentences <= 2


class ModelRouter:
    def __init__(self, default: str, auto_downgrade: bool, state_file: Path) -> None:
        self.state_file = state_file
        self.default = default if default in MODELS else "sonnet"
        self.auto_downgrade = auto_downgrade
        self.session_overrides: dict[str, str | None] = {}
        self.next_turn: dict[str, str] = {}
        self.effort: str | None = None  # None = Claude Code's own default
        self.usage: UsageTracker | None = None  # set by AppState: quota pressure steps automatic choices down
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return
        if data.get("default") in MODELS:
            self.default = data["default"]
        if isinstance(data.get("auto_downgrade"), bool):
            self.auto_downgrade = data["auto_downgrade"]
        if data.get("effort") in EFFORTS:
            self.effort = data["effort"]

    def _save(self) -> None:
        self.state_file.write_text(
            json.dumps({"default": self.default, "auto_downgrade": self.auto_downgrade, "effort": self.effort}),
            encoding="utf-8"
        )

    def set(self, model: str | None, scope: Scope, session_id: str | None = None) -> None:
        if model is not None and model not in MODELS:
            raise ValueError(f"Unbekanntes Modell: {model}")
        if scope == "default":
            if model is None:
                self.auto_downgrade = True
                self.default = "sonnet"
            else:
                self.default = model
                self.auto_downgrade = model == "sonnet"  # an explicit Opus/Haiku default is taken literally
            self.session_overrides.clear()
            self._save()
        elif scope == "session" and session_id:
            self.session_overrides[session_id] = model
            if model is None:
                self.session_overrides.pop(session_id, None)

    def apply(self, model: str | None, scope: Scope, session_id: str | None) -> None:
        """Like :meth:`set`, plus the one-shot ``turn`` scope (the next answer of that conversation only)."""
        if scope == "turn":
            if model is None or model not in MODELS or not session_id:
                raise ValueError("Für 'nur diese Antwort' sind Modell und Gespräch nötig")
            self.next_turn[session_id] = model
        else:
            self.set(model, scope, session_id)

    def set_effort(self, effort: str | None) -> None:
        if effort is not None and effort not in EFFORTS:
            raise ValueError(f"Unbekannter Effort: {effort}")
        self.effort = effort
        self._save()

    def set_auto_downgrade(self, enabled: bool) -> None:
        self.auto_downgrade = enabled
        self._save()

    def _relieve(self, model: str, reason: str) -> tuple[str, str]:
        """Automatic choices only: with the subscription quota nearly used up, step down one model."""
        pressure = self.usage.pressure() if self.usage is not None else 0.0
        if model == "opus" and pressure >= 0.9:
            return "sonnet", f"Kontingent bei {round(pressure * 100)} % – Sonnet statt Opus"
        if model == "sonnet" and pressure >= 0.97:
            return "haiku", f"Kontingent bei {round(pressure * 100)} % – Haiku statt Sonnet"
        return model, reason

    def choose(self, text: str, channel: str, session_id: str, turn_override: str | None = None) -> tuple[str, str]:
        """Returns (model alias, reason). Explicit commands are always honoured; defaults/auto react to the quota."""
        if turn_override:
            return turn_override, "Befehl (nur diese Antwort)"
        if session_id in self.next_turn:
            return self.next_turn.pop(session_id), "Befehl (nur diese Antwort)"
        override = self.session_overrides.get(session_id)
        if override:
            return override, "für dieses Gespräch gewählt"
        if self.default != "sonnet":
            return self._relieve(self.default, "Standard")
        if self.auto_downgrade and is_simple(text, channel):
            return "haiku", "automatisch: einfache Anfrage"
        return self._relieve("sonnet", "Standard")

    def status(self, session_id: str | None = None) -> dict[str, object]:
        return {
            "default": self.default,
            "auto_downgrade": self.auto_downgrade,
            "session_override": self.session_overrides.get(session_id) if session_id else None,
            "models": [{"alias": k, **v} for k, v in MODELS.items()],
            "effort": self.effort,
            "efforts": [{"id": k, "label": v} for k, v in EFFORTS.items()],
        }
