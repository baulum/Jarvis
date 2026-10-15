from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

from jarvis.config import get_settings, load_permissions
from jarvis.db import dispose_engine, init_engine, session_scope
from jarvis.security.guards import KillSwitch
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.registry import ToolContext, ToolRegistry, ToolSpec

TABLES = ["approvals", "tool_executions", "audit_log", "task_steps", "tasks", "chat_messages", "chat_sessions",
          "devices", "memory_items", "memory_links", "notifications", "schedules"]


@pytest.fixture(scope="session", autouse=True)
async def db() -> AsyncIterator[None]:
    settings = get_settings()
    assert settings.database.name == "jarvis_test", "integration tests must never touch the production DB"
    init_engine(settings)
    yield
    await dispose_engine()


@pytest.fixture(autouse=True)
async def clean() -> None:
    async with session_scope() as s:
        await s.execute(text("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE"))


class Counter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, ctx: ToolContext, a: dict[str, Any]) -> Any:
        self.calls.append(a)
        return {"n": len(self.calls), "echo": a}


@pytest.fixture
def stack(tmp_path: Path) -> SimpleNamespace:
    perms = load_permissions()
    reg = ToolRegistry(perms)
    ks = KillSwitch(tmp_path)
    ex = ToolExecutor(reg, perms, ks)
    counters = {}
    for name, risk, writes, untrusted in [("t_read", "read", False, False), ("t_low", "low", True, False),
                                          ("t_medium", "medium", True, False), ("t_critical", "critical", True, False),
                                          ("t_web", "read", False, True)]:
        c = Counter()
        counters[name] = c
        reg.register(ToolSpec(name, name, {"type": "object", "properties": {"x": {"type": "integer"}},
                                           "additionalProperties": False}, c, risk, "test", writes=writes,  # type: ignore[arg-type]
                              untrusted_output=untrusted))
    return SimpleNamespace(registry=reg, executor=ex, kill_switch=ks, counters=counters, permissions=perms)
