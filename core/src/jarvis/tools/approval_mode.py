"""Approval modes: how much Jarvis may do without asking Sir first.

Modes (chosen by the owner in the desktop app, stored in ``approval-mode.json``):

* ``yolo``            – nothing needs approval.
* ``block_dangerous`` – read/low/medium run directly; high and critical need approval (default).
* ``block_everything``– every action with side effects needs approval; only reading runs directly.
* ``custom``          – per risk level ``auto``/``ask`` plus per-tool overrides ``auto``/``ask``/``deny``.

Independent of the mode, these stay in force (they are not "approvals" but hard limits):
the kill switch, each role's ``max_risk`` (e.g. an unverified caller can only read),
rate limits and the audit log. Prompt-injection protection: in a session that contains
untrusted external content (web pages, e-mails, documents), ``critical`` actions always need
approval — even in yolo — and in the stricter modes every action with side effects does.
"""

from __future__ import annotations

import fnmatch
import json
import logging
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

Mode = Literal["yolo", "block_dangerous", "block_everything", "custom"]
Decision = Literal["auto", "ask", "deny"]
MODES: tuple[Mode, ...] = ("yolo", "block_dangerous", "block_everything", "custom")
LEVELS = ("low", "medium", "high", "critical")

_PRESETS: dict[str, dict[str, Decision]] = {
    "yolo": {"low": "auto", "medium": "auto", "high": "auto", "critical": "auto"},
    "block_dangerous": {"low": "auto", "medium": "auto", "high": "ask", "critical": "ask"},
    "block_everything": {"low": "ask", "medium": "ask", "high": "ask", "critical": "ask"},
}
_DEFAULT_CUSTOM: dict[str, Decision] = {"low": "auto", "medium": "auto", "high": "ask", "critical": "ask"}


class ApprovalPolicy:
    def __init__(self, state_file: Path, default_mode: Mode = "block_dangerous") -> None:
        self.state_file = state_file
        self.mode: Mode = default_mode
        self.custom_levels: dict[str, Decision] = dict(_DEFAULT_CUSTOM)
        self.tool_overrides: dict[str, Decision] = {}
        self.taint_strict = True  # custom only: tainted sessions ask for every action with side effects
        self._load()

    # ------------------------------------------------------------------ persistence
    def _load(self) -> None:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return
        try:
            self._apply(data)
        except ValueError as e:
            logger.warning("Ignoring invalid %s: %s", self.state_file.name, e)

    def _save(self) -> None:
        self.state_file.write_text(json.dumps(self.to_dict(), indent=1, ensure_ascii=False), encoding="utf-8")

    def _apply(self, data: dict[str, Any]) -> None:
        mode = data.get("mode", self.mode)
        if mode not in MODES:
            raise ValueError(f"Unbekannter Modus: {mode}")
        levels = {**self.custom_levels, **(data.get("custom_levels") or {})}
        if set(levels) != set(LEVELS) or any(v not in ("auto", "ask") for v in levels.values()):
            raise ValueError("custom_levels: je Stufe low/medium/high/critical 'auto' oder 'ask'")
        overrides = data.get("tool_overrides", self.tool_overrides) or {}
        if any(v not in ("auto", "ask", "deny") for v in overrides.values()):
            raise ValueError("tool_overrides: 'auto', 'ask' oder 'deny'")
        self.mode = mode
        self.custom_levels = levels
        self.tool_overrides = {str(k): v for k, v in overrides.items()}
        self.taint_strict = bool(data.get("taint_strict", self.taint_strict))

    def update(self, data: dict[str, Any]) -> dict[str, Any]:
        self._apply(data)
        self._save()
        return self.to_dict()

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "custom_levels": self.custom_levels,
            "tool_overrides": self.tool_overrides,
            "taint_strict": self.taint_strict,
            "effective_levels": self.levels(),
        }

    # ------------------------------------------------------------------ decision
    def levels(self) -> dict[str, Decision]:
        return dict(self.custom_levels) if self.mode == "custom" else dict(_PRESETS[self.mode])

    def _override(self, tool: str) -> Decision | None:
        if tool in self.tool_overrides:
            return self.tool_overrides[tool]
        for pattern, decision in self.tool_overrides.items():
            if fnmatch.fnmatchcase(tool, pattern):
                return decision
        return None

    def decide(self, tool: str, risk: str, tainted: bool) -> tuple[Decision, str]:
        """Returns (decision, reason). ``risk`` is the tool's effective risk level."""
        if risk == "read":
            return "auto", ""
        if tainted and risk == "critical":
            return "ask", "Kritische Aktion in einer Sitzung mit nicht vertrauenswürdigen externen Inhalten"
        if tainted and self.mode != "yolo" and (self.mode != "custom" or self.taint_strict):
            return "ask", "Sitzung enthält nicht vertrauenswürdige externe Inhalte"
        if self.mode == "custom":
            override = self._override(tool)
            if override == "deny":
                return "deny", f"{tool} ist in den Freigabe-Einstellungen gesperrt"
            if override is not None:
                return override, "" if override == "auto" else f"Freigabe-Einstellung für {tool}"
        decision = self.levels().get(risk, "ask")
        return decision, "" if decision == "auto" else f"Risikostufe {risk} (Modus {self.mode})"
