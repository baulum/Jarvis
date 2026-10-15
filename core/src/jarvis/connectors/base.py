"""Connector framework.

A connector bundles: a manifest (id, name, category, auth type, required secrets, settings
schema, permissions summary), its tools, a health check and a configuration state. The
``ConnectorManager`` persists non-secret settings + enabled flag in Postgres, stores secrets
in the Keychain and gates tool availability on "enabled AND configured".
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

import jsonschema
from sqlalchemy import select

from jarvis.db import session_scope
from jarvis.db.models import ConnectorConfig
from jarvis.events import bus
from jarvis.secrets import secret_store
from jarvis.security.audit import audit
from jarvis.tools.registry import ToolSpec

if TYPE_CHECKING:
    from jarvis.app_state import AppState

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SecretField:
    name: str  # Keychain account, e.g. "lexware-api-key"
    label: str
    help: str = ""
    required: bool = True


@dataclass(slots=True)
class Manifest:
    id: str
    name: str
    description: str
    category: Literal["business", "communication", "productivity", "developer", "system", "knowledge", "web"]
    auth: Literal["none", "api_key", "oauth2", "token", "local"]
    secrets: list[SecretField] = field(default_factory=list)
    settings_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    setup_instructions: str = ""
    docs_url: str = ""
    mcp_note: str = ""  # result of checking for an official MCP server
    version: str = "1.0.0"
    default_enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "description": self.description, "category": self.category,
            "auth": self.auth, "version": self.version, "docs_url": self.docs_url,
            "setup_instructions": self.setup_instructions, "mcp_note": self.mcp_note,
            "settings_schema": self.settings_schema,
            "secrets": [{"name": s.name, "label": s.label, "help": s.help, "required": s.required}
                        for s in self.secrets],
        }


@dataclass(slots=True)
class Health:
    status: Literal["ok", "degraded", "error", "not_configured", "disabled"]
    message: str = ""
    checked_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "message": self.message, "checked_at": self.checked_at,
                "details": self.details}


class Connector:
    manifest: Manifest

    def __init__(self, state: AppState) -> None:
        self.state = state
        self.settings: dict[str, Any] = {}
        self.enabled: bool = self.manifest.default_enabled

    # -- to implement -------------------------------------------------------
    def tools(self) -> list[ToolSpec]:
        return []

    async def health(self) -> Health:
        return Health("ok")

    async def start(self) -> None:
        """Start background work (polling, watchers). Called when enabled + configured."""

    async def stop(self) -> None:
        """Stop background work."""

    # -- helpers ------------------------------------------------------------
    def secret(self, name: str) -> str | None:
        return secret_store.get(name)

    def missing_secrets(self) -> list[str]:
        return [s.name for s in self.manifest.secrets if s.required and not secret_store.exists(s.name)]

    def missing_settings(self) -> list[str]:
        required = self.manifest.settings_schema.get("required", [])
        return [k for k in required if self.settings.get(k) in (None, "", [])]

    @property
    def configured(self) -> bool:
        return not self.missing_secrets() and not self.missing_settings()

    @property
    def active(self) -> bool:
        return self.enabled and self.configured


class ConnectorManager:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self.connectors: dict[str, Connector] = {}
        self.health: dict[str, Health] = {}
        self._started: set[str] = set()
        self._health_task: asyncio.Task[None] | None = None

    def add(self, connector: Connector) -> None:
        cid = connector.manifest.id
        self.connectors[cid] = connector
        for spec in connector.tools():
            if spec.connector != cid:
                raise ValueError(f"Tool {spec.name} declares connector {spec.connector}, expected {cid}")
            self.state.registry.register(spec)
        self.state.registry.set_connector_gate(cid, lambda c=connector: c.active)

    async def load(self) -> None:
        async with session_scope() as s:
            rows = {r.connector_id: r for r in (await s.execute(select(ConnectorConfig))).scalars()}
            for cid, conn in self.connectors.items():
                row = rows.get(cid)
                if row is None:
                    row = ConnectorConfig(connector_id=cid, enabled=conn.manifest.default_enabled, settings={})
                    s.add(row)
                conn.enabled = row.enabled
                conn.settings = dict(row.settings or {})
        for conn in self.connectors.values():
            await self._sync_running(conn)
        self._health_task = asyncio.create_task(self._health_loop())

    async def shutdown(self) -> None:
        if self._health_task:
            self._health_task.cancel()
        for cid in list(self._started):
            try:
                await self.connectors[cid].stop()
            except Exception:
                logger.exception("Stopping connector %s failed", cid)
        self._started.clear()

    async def _sync_running(self, conn: Connector) -> None:
        cid = conn.manifest.id
        if conn.active and cid not in self._started:
            try:
                await conn.start()
                self._started.add(cid)
            except Exception:
                logger.exception("Starting connector %s failed", cid)
        elif not conn.active and cid in self._started:
            await conn.stop()
            self._started.discard(cid)

    async def configure(self, cid: str, *, actor: str, enabled: bool | None = None,
                        settings: dict[str, Any] | None = None, secrets: dict[str, str] | None = None) -> None:
        conn = self.connectors[cid]
        if settings is not None:
            merged = {**conn.settings, **settings}
            jsonschema.validate(merged, conn.manifest.settings_schema)
            conn.settings = merged
        allowed = {s.name for s in conn.manifest.secrets}
        for name, value in (secrets or {}).items():
            if name not in allowed:
                raise ValueError(f"Secret {name} gehört nicht zu {cid}")
            if value:
                secret_store.set(name, value)
        if enabled is not None:
            conn.enabled = enabled
        async with session_scope() as s:
            row = await s.get(ConnectorConfig, cid)
            if row is None:
                row = ConnectorConfig(connector_id=cid)
                s.add(row)
            row.enabled = conn.enabled
            row.settings = conn.settings
        await audit(actor, "connector.configure", target=cid,
                    details={"enabled": conn.enabled, "settings_keys": sorted((settings or {}).keys()),
                             "secrets_set": sorted((secrets or {}).keys())})
        await self._sync_running(conn)
        await self.check(cid)

    async def check(self, cid: str) -> Health:
        conn = self.connectors[cid]
        if not conn.configured:
            missing = conn.missing_secrets() + conn.missing_settings()
            h = Health("not_configured", "Fehlt: " + ", ".join(missing))
        elif not conn.enabled:
            h = Health("disabled", "Connector ist deaktiviert")
        else:
            try:
                h = await asyncio.wait_for(conn.health(), timeout=20)
            except Exception as e:  # noqa: BLE001 - surfaced as health status
                h = Health("error", f"{type(e).__name__}: {e}"[:300])
        previous = self.health.get(cid)
        self.health[cid] = h
        if previous is None or previous.status != h.status:
            await bus.publish("connector.health", connector=cid, **h.to_dict())
            async with session_scope() as s:
                row = await s.get(ConnectorConfig, cid)
                if row is not None:
                    row.last_health = h.to_dict()
        return h

    async def _health_loop(self) -> None:
        while True:
            for cid in list(self.connectors):
                try:
                    await self.check(cid)
                except Exception:
                    logger.exception("Health check %s crashed", cid)
            await asyncio.sleep(300)

    def describe(self) -> list[dict[str, Any]]:
        out = []
        for cid, conn in sorted(self.connectors.items()):
            h = self.health.get(cid)
            out.append({
                **conn.manifest.to_dict(),
                "enabled": conn.enabled,
                "configured": conn.configured,
                "missing": conn.missing_secrets() + conn.missing_settings(),
                "settings": conn.settings,
                "secrets_present": {s.name: secret_store.exists(s.name) for s in conn.manifest.secrets},
                "health": h.to_dict() if h else None,
                "tools": [{"name": t.name, "description": t.description,
                           "risk": self.state.registry.effective_risk(t)}
                          for t in self.state.registry.all(include_disabled=True) if t.connector == cid],
            })
        return out
