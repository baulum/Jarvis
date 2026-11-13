"""Kundensuche: the PJE-Sales-AI Lead-Tool inside Jarvis.

The Lead-Tool (TypeScript) is reached through its own MCP server (``config/mcp_servers.yaml › pje_sales_ai``), which the
``MCPClientHub`` already runs for the agent. That server wraps the Lead-Tool's own libraries – Jarvis copies no query
and never sees the Lead-Tool's database URL (see docs/adr/0002-kundensuche-lead-tool-in-jarvis.md).

* Reading calls the MCP tool directly over the hub's connection and translates the answer (``views``).
* Acting (redraft, start a campaign) goes only through ``ToolExecutor.execute`` – kill switch, role ceiling, approval
  mode, ledger and audit apply exactly as for the agent.
* Approving or sending a draft is not offered: the Lead-Tool's MCP has no such tool on purpose; that stays in the
  Lead-Tool's own interface (``web_url``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, Protocol

from jarvis.mcp.client import registry_name
from jarvis.sales import views
from jarvis.security.auth import Principal
from jarvis.tools.executor import ToolResult, ToolUserError, args_hash
from jarvis.tools.registry import ToolContext

if TYPE_CHECKING:
    from jarvis.app_state import AppState

logger = logging.getLogger(__name__)

SERVER = "pje_sales_ai"
DEFAULT_WEB_URL = "http://127.0.0.1:3100"
CALL_TIMEOUT_S = 30.0
ACTIONS = {"redraft": "request_redraft", "create_campaign": "create_campaign"}
READ_TOOLS = ("status", "list_campaigns", "get_campaign", "list_leads", "get_lead", "review_queue", "find_industry")


class SalesError(Exception):
    """A failure the API maps to an HTTP status; ``message`` is safe to show."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class _Server(Protocol):  # the part of jarvis.mcp.client.ExternalServer used here
    session: Any
    tools: list[Any]
    error: str | None

    async def call(self, tool: str, args: dict[str, Any]) -> Any: ...


class SalesService:
    def __init__(self, state: AppState) -> None:
        self.state = state

    # ------------------------------------------------------------------ connection
    def _config(self) -> dict[str, Any] | None:
        return self.state.mcp_hub.load_config().get(SERVER)

    def web_url(self) -> str | None:
        cfg = self._config() or {}
        url = cfg.get("web_url", DEFAULT_WEB_URL)
        return str(url).rstrip("/") if url else None

    def _server(self) -> _Server:
        cfg = self._config()
        if cfg is None or not cfg.get("enabled"):
            raise SalesError(409, f"Das Lead-Tool ist nicht eingerichtet (config/mcp_servers.yaml › {SERVER}).")
        srv: _Server | None = self.state.mcp_hub.servers.get(SERVER)
        if srv is None or srv.session is None:
            why = views.scrub(srv.error) if srv is not None and srv.error else "der MCP-Server läuft nicht"
            raise SalesError(503, f"Das Lead-Tool ist nicht erreichbar: {why}.")
        return srv

    def _tool_enabled(self, tool: str) -> bool:
        spec = self.state.registry.get(registry_name(SERVER, tool))
        return spec is not None and self.state.registry.is_enabled(spec)

    def _has_tool(self, srv: _Server, tool: str) -> bool:
        return any(getattr(t, "name", None) == tool for t in srv.tools)

    async def read(self, tool: str, args: dict[str, Any] | None = None) -> Any:
        """Calls a read-only tool of the Lead-Tool's MCP server and returns its parsed JSON."""
        srv = self._server()
        if not self._has_tool(srv, tool) or not self._tool_enabled(tool):
            raise SalesError(503, f"Das Lead-Tool bietet „{tool}“ nicht an oder es ist in Jarvis deaktiviert.")
        try:
            out = await asyncio.wait_for(srv.call(tool, args or {}), CALL_TIMEOUT_S)
        except TimeoutError as e:
            raise SalesError(504, f"Das Lead-Tool hat nicht rechtzeitig geantwortet ({tool}).") from e
        except ToolUserError as e:
            msg = views.scrub(str(e))
            raise SalesError(404 if "nicht gefunden" in msg.lower() else 502, msg) from e
        except Exception as e:  # broken pipe, closed session …
            logger.warning("Lead-Tool %s failed: %s", tool, type(e).__name__)
            raise SalesError(502, views.scrub(f"Lead-Tool-Fehler: {type(e).__name__}: {e}")) from e
        try:
            return json.loads(out)
        except (TypeError, json.JSONDecodeError) as e:
            raise SalesError(502, f"Unerwartete Antwort des Lead-Tools ({tool}).") from e

    # ------------------------------------------------------------------ reading
    async def status(self) -> dict[str, Any]:
        cfg = self._config()
        out: dict[str, Any] = {
            "configured": cfg is not None,
            "enabled": bool(cfg and cfg.get("enabled")),
            "connected": False,
            "error": None,
            "web_url": self.web_url(),
            "capabilities": {"read": False, "redraft": False, "create_campaign": False, "outbox_list": False},
            "overview": None,
        }
        try:
            srv = self._server()
        except SalesError as e:
            out["error"] = e.message
            return out
        out["connected"] = True
        out["capabilities"] = {
            "read": all(self._has_tool(srv, t) for t in ("list_campaigns", "list_leads", "get_lead")),
            "redraft": self._has_tool(srv, ACTIONS["redraft"]) and self._tool_enabled(ACTIONS["redraft"]),
            "create_campaign": self._has_tool(srv, ACTIONS["create_campaign"])
            and self._tool_enabled(ACTIONS["create_campaign"])
            and self._has_tool(srv, "find_industry"),
            "outbox_list": False,  # needs a read tool the Lead-Tool's MCP does not have yet (ADR 0002)
        }
        try:
            out["overview"] = views.overview(await self.read("status"))
        except SalesError as e:
            out["error"] = e.message
        return out

    async def statuses(self) -> list[dict[str, Any]]:
        """The Lead-Tool's status list, taken from the schema of its ``list_leads`` tool (no copy in Jarvis)."""
        srv = self._server()
        tool = next((t for t in srv.tools if getattr(t, "name", None) == "list_leads"), None)
        schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None) or {}
        prop = (schema.get("properties") or {}).get("status") or {}
        enum = prop.get("enum") or next((x.get("enum") for x in prop.get("anyOf", []) if x.get("enum")), None)
        if not enum:
            enum = list(views.STATUS_LABELS)
        return views.statuses([str(s) for s in enum])

    async def campaigns(self) -> list[dict[str, Any]]:
        return [views.campaign(c) for c in await self.read("list_campaigns")]

    async def campaign(self, campaign_id: str) -> dict[str, Any]:
        return views.campaign_detail(await self.read("get_campaign", {"campaignId": campaign_id}))

    async def leads(self, *, campaign_id: str | None, status: str | None, limit: int, offset: int) -> dict[str, Any]:
        args: dict[str, Any] = {"limit": limit, "offset": offset}
        if campaign_id:
            args["campaignId"] = campaign_id
        if status:
            args["status"] = status
        return views.lead_page(await self.read("list_leads", args))

    async def lead(self, lead_id: str) -> dict[str, Any]:
        return views.lead_detail(await self.read("get_lead", {"leadId": lead_id}))

    async def review(self, limit: int) -> list[dict[str, Any]]:
        return [views.review_item(x) for x in await self.read("review_queue", {"limit": limit})]

    async def industries(self, query: str) -> dict[str, Any]:
        return views.industries(await self.read("find_industry", {"name": query}))

    async def outbox(self) -> dict[str, Any]:
        ov = views.overview(await self.read("status"))
        return {
            "available": False,
            "preview_count": ov["outbox_preview"],
            "outreach_mode": ov["outreach_mode"],
            "reason": "Das Lead-Tool liefert über seine Jarvis-Schnittstelle bisher nur die Anzahl der Nachrichten im "
            "Postausgang. Prüfen, Testversand und Versand laufen im Lead-Tool.",
            "items": [],
        }

    async def analytics(self) -> dict[str, Any]:
        raw_campaigns, raw_status = await asyncio.gather(self.read("list_campaigns"), self.read("status"))
        return views.analytics([views.campaign(c) for c in raw_campaigns], views.overview(raw_status))

    # ------------------------------------------------------------------ acting (only through the executor)
    async def act(self, principal: Principal, action: str, args: dict[str, Any], request_id: str) -> ToolResult:
        """One submission from a client. ``request_id`` (new per submission) plus the arguments form the idempotency
        key: a retried request does not run twice, a new submission with the same text does."""
        tool = ACTIONS[action]
        self._server()
        name = registry_name(SERVER, tool)
        key = f"sales:{action}:{request_id}:{args_hash(name, args)[:16]}"
        return await self.state.executor.execute(name, args, ToolContext(principal=principal, idempotency_key=key))
