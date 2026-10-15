"""Tool registry: the single catalogue of everything Jarvis can do.

Connectors register ``ToolSpec`` objects. The registry is exposed to Hermes via the Jarvis
MCP server, to the task engine (playbooks) and to the desktop app (Skills/Permissions view).
Tool names use ``<connector>_<action>`` (OpenAI function-name compatible: no dots).
"""

from __future__ import annotations

import fnmatch
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from jarvis.config import RISK_ORDER, Permissions, RiskLevel

if TYPE_CHECKING:
    from jarvis.security.auth import Principal

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


@dataclass(slots=True)
class ToolContext:
    principal: Principal
    session_id: uuid.UUID | None = None
    task_step_id: uuid.UUID | None = None
    idempotency_key: str | None = None
    approved: bool = False  # set only by the approval service after an explicit decision
    approval_id: uuid.UUID | None = None
    extra: dict[str, Any] = field(default_factory=dict)


Handler = Callable[[ToolContext, dict[str, Any]], Awaitable[Any]]


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Handler
    risk: RiskLevel
    connector: str
    # True for actions with external side effects: guarded by the idempotency ledger.
    writes: bool = False
    # True when the result contains third-party content (web, mail, files): taints the session.
    untrusted_output: bool = False
    timeout_s: float = 60.0
    summarize: Callable[[dict[str, Any]], str] | None = None

    def __post_init__(self) -> None:
        if not _NAME_RE.match(self.name):
            raise ValueError(f"Invalid tool name {self.name!r}")
        if self.parameters.get("type") != "object":
            raise ValueError(f"{self.name}: parameters must be a JSON object schema")

    def describe(self, args: dict[str, Any]) -> str:
        if self.summarize:
            try:
                return self.summarize(args)
            except Exception:  # noqa: BLE001, S110 - fall back to generic summary
                pass
        shown = ", ".join(f"{k}={str(v)[:80]}" for k, v in args.items())
        return f"{self.name}({shown})"


class ToolRegistry:
    def __init__(self, permissions: Permissions) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._connector_enabled: dict[str, Callable[[], bool]] = {}
        self.permissions = permissions

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool {spec.name} already registered")
        self._tools[spec.name] = spec

    def unregister_connector(self, connector: str) -> None:
        for name in [n for n, s in self._tools.items() if s.connector == connector]:
            del self._tools[name]

    def set_connector_gate(self, connector: str, is_enabled: Callable[[], bool]) -> None:
        self._connector_enabled[connector] = is_enabled

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def _override(self, name: str) -> tuple[RiskLevel | None, bool | None]:
        risk: RiskLevel | None = None
        enabled: bool | None = None
        # Most specific (longest) pattern wins.
        for pattern in sorted(self.permissions.tools, key=len):
            normalized = pattern.replace(".", "_")
            if fnmatch.fnmatchcase(name, normalized):
                ov = self.permissions.tools[pattern]
                risk = ov.risk or risk
                enabled = ov.enabled if ov.enabled is not None else enabled
        return risk, enabled

    def effective_risk(self, spec: ToolSpec) -> RiskLevel:
        override, _ = self._override(spec.name)
        if override is None:
            return spec.risk
        # Overrides may only *raise* risk; lowering requires editing the connector manifest.
        return override if RISK_ORDER[override] >= RISK_ORDER[spec.risk] else spec.risk

    def is_enabled(self, spec: ToolSpec) -> bool:
        _, enabled = self._override(spec.name)
        if enabled is False:
            return False
        gate = self._connector_enabled.get(spec.connector)
        return gate() if gate else True

    def all(self, include_disabled: bool = False) -> list[ToolSpec]:
        tools = sorted(self._tools.values(), key=lambda s: s.name)
        return tools if include_disabled else [t for t in tools if self.is_enabled(t)]

    def for_role(self, role: str) -> list[ToolSpec]:
        cfg = self.permissions.roles.get(role)
        if cfg is None:
            return []
        limit = RISK_ORDER[cfg.max_risk]
        return [t for t in self.all() if RISK_ORDER[self.effective_risk(t)] <= limit]
