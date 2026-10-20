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

_MODEL_WORD = r"(opus|sonnet|sonett|haiku|hayku)"
_VERB = (
    r"(?:nimm|nehm\w*|nutz\w*|benutz\w*|verwend\w*|wechs\w*|schalt\w*|stell\w*|geh\w*|wähl\w*|switch\w*|"
    r"use|take|ich (?:will|möchte|brauche)|soll\w*)"
)
_SWITCH_RE = re.compile(
    # "nimm Opus", "nutze bitte Haiku", "stell auf Sonnet um"
    r"(?:\b" + _VERB + r"\b[^.?!]{0,30}?\b" + _MODEL_WORD + r"\b"
    # "Haiku nutzen", "auf Opus wechseln"
    r"|\b" + _MODEL_WORD + r"\b[^.?!]{0,20}?\b(?:nutz\w*|benutz\w*|verwend\w*|nehmen|wechseln|umschalten|"
    r"umstellen|aktivier\w*)\b"
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
    w = word.lower()
    if w.startswith("opus"):
        return "opus"
    if w.startswith(("sonnet", "sonett")):
        return "sonnet"
    if w.startswith(("haiku", "hayku")):
        return "haiku"
    return None


def resolve_alias(value: str | None) -> str | None:
    """haiku|sonnet|opus or a full model id (``claude-opus-5-5``) → alias; None for empty/``auto``/unknown."""
    if not value:
        return None
    v = value.strip().lower()
    for alias, m in MODELS.items():
        if v == alias or v == m["id"]:
            return alias
    return None


def model_id(alias: str) -> str:
    return MODELS[alias]["id"]


@dataclass(slots=True)
class SwitchCommand:
    model: str | None  # None = back to automatic selection
    scope: Scope
    remainder: str  # the rest of the utterance that still needs an answer ("" = pure command)


def parse_command(text: str) -> SwitchCommand | None:
    if _AUTO_RE.search(text):
        rest = _AUTO_RE.sub("", text).strip(" ,.!?")
        return SwitchCommand(None, "default" if _DEFAULT_RE.search(text) else "session", _clean(rest))
    m = _SWITCH_RE.search(text)
    if not m:
        return None
    model = normalize(next(g for g in m.groups() if g))
    if model is None:
        return None
    scope: Scope = "turn" if _TURN_RE.search(text) else "default" if _DEFAULT_RE.search(text) else "session"
    rest = text[: m.start()] + text[m.end() :]
    rest = _TURN_RE.sub("", _DEFAULT_RE.sub("", rest))
    return SwitchCommand(model, scope, _clean(rest))


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
