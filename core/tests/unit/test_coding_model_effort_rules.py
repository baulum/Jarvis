"""Modell/Effort bei Coding-Jobs gehören dem Job, nicht dem Chat-Modell: Tool-Beschreibungen, Prompt-Regeln und die
Warnung, wenn agent_set_model aufgerufen wird, während ein Coding-Job läuft."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.agent.persona import OPERATING_RULES
from jarvis.connectors import system as system_mod
from jarvis.connectors.coding import CodingConnector
from jarvis.connectors.system import SystemConnector
from jarvis.security.auth import Principal
from jarvis.tools.registry import ToolContext


class FakeRouter:
    def __init__(self) -> None:
        self.applied: list[tuple[Any, str, str | None]] = []

    def apply(self, model: Any, scope: str, session_id: str | None) -> None:
        self.applied.append((model, scope, session_id))

    def status(self, session_id: str | None = None) -> dict[str, Any]:
        return {"model": "sonnet"}


def _system_tool(name: str) -> tuple[Any, Any]:
    state = SimpleNamespace(model_router=FakeRouter(), usage=SimpleNamespace(status=lambda: {}))
    tool = next(t for t in SystemConnector(state).tools() if t.name == name)
    return state, tool


def _coding_tool(name: str) -> Any:
    state = SimpleNamespace(settings=SimpleNamespace(paths=SimpleNamespace(data_dir=None)), coding=None)
    return next(t for t in CodingConnector(state).tools() if t.name == name)


def test_set_model_description_is_chat_only() -> None:
    _, tool = _system_tool("agent_set_model")
    desc = tool.description
    assert "CHAT-Modell" in desc
    assert "NIEMALS" in desc and "Coding-Job" in desc
    assert "coding_cancel_job" in desc and "coding_start_job" in desc


def test_coding_tool_descriptions_cover_model_and_effort() -> None:
    start = _coding_tool("coding_start_job").description
    assert "agent_set_model" in start and "coding_cancel_job" in start
    assert "5.5" in start and "ausdrücklich" in start
    assert "coding_start_job" in _coding_tool("coding_cancel_job").description


def test_operating_rules_cover_coding_model_effort() -> None:
    rules = OPERATING_RULES
    assert "Modell und Effort bei Coding-Jobs" in rules
    assert "niemals für dein Chat-Modell" in rules
    assert "`coding_cancel_job`" in rules and "`coding_start_job`" in rules
    assert "bisherigen Wert des Jobs" in rules
    assert "ohne Zahl" in rules and "Familie des bisherigen Jobs" in rules
    assert "mit welchem Modell und Effort gestartet" in rules


def test_operating_rules_explicit_instruction_beats_memory() -> None:
    assert "Vorrang vor gespeicherten Standardregeln" in OPERATING_RULES
    assert "ohne Rückfrage" in OPERATING_RULES and "Abbrechen und Neustart" in OPERATING_RULES


@pytest.fixture
def _no_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def audit(*a: Any, **kw: Any) -> None:
        return None

    monkeypatch.setattr(system_mod, "audit", audit)


async def _set_model(monkeypatch: pytest.MonkeyPatch, jobs: list[dict[str, str]]) -> dict[str, Any]:
    async def active() -> list[dict[str, str]]:
        return jobs

    monkeypatch.setattr(system_mod, "_active_coding_jobs", active)
    state, tool = _system_tool("agent_set_model")
    ctx = ToolContext(principal=Principal(id="desktop", role="owner", channel="desktop"), session_id=None)
    out = await tool.handler(ctx, {"model": "sonnet"})
    assert state.model_router.applied == [("sonnet", "session", None)]  # the chat switch itself still works
    return out


async def test_set_model_warns_while_coding_job_runs(monkeypatch: pytest.MonkeyPatch, _no_audit: None) -> None:
    out = await _set_model(monkeypatch, [{"job_id": "abcdef12-0000", "model": "opus", "effort": "medium"}])
    warning = out["warnung_coding_job"]
    assert "CHAT-Modell" in warning and "abcdef12 (opus, medium)" in warning
    assert "coding_cancel_job" in warning and "coding_start_job" in warning


async def test_set_model_without_coding_job_has_no_warning(monkeypatch: pytest.MonkeyPatch, _no_audit: None) -> None:
    out = await _set_model(monkeypatch, [])
    assert "warnung_coding_job" not in out and out["ok"] is True
