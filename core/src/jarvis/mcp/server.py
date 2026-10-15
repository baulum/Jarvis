"""Jarvis MCP server (Streamable HTTP at ``/mcp``).

Hermes connects to this server as an MCP client; every call goes through the policy-
enforcing ToolExecutor. Calls are attributed to the currently running conversation (agent
runs are serialised by the orchestrator); calls outside a Jarvis-initiated run (e.g. Hermes'
own cron) are attributed to the ``system`` role. Authentication: bearer token of a device
with role ``agent`` (created by ``jarvisctl setup-hermes``).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from mcp import types
from mcp.server.lowlevel.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import Receive, Scope, Send

from jarvis.config import RISK_ORDER
from jarvis.security.auth import SYSTEM, authenticate_token
from jarvis.tools.registry import ToolContext

if TYPE_CHECKING:
    from jarvis.app_state import AppState

logger = logging.getLogger(__name__)
MAX_RESULT_CHARS = 12000


def build_mcp(state: AppState) -> tuple[Server[Any], StreamableHTTPSessionManager]:
    async def list_tools(ctx: Any, params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
        tools = []
        for spec in state.registry.for_role("owner"):
            risk = state.registry.effective_risk(spec)
            note = "" if RISK_ORDER[risk] <= RISK_ORDER["low"] else f" [Risiko: {risk} – ggf. Freigabe nötig]"
            tools.append(
                types.Tool(
                    name=spec.name,
                    description=(spec.description + note)[:1000],
                    input_schema=spec.parameters,
                    annotations=types.ToolAnnotations(
                        read_only_hint=risk == "read",
                        destructive_hint=RISK_ORDER[risk] >= RISK_ORDER["high"],
                        open_world_hint=spec.untrusted_output,
                    ),
                )
            )
        return types.ListToolsResult(tools=tools)

    async def call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        run = state.orchestrator.active_run
        principal = run.principal if run else SYSTEM
        tctx = ToolContext(principal=principal, session_id=run.session_id if run else None)
        result = await state.executor.execute(params.name, dict(params.arguments or {}), tctx)
        payload = json.dumps(result.to_dict(), ensure_ascii=False, default=str)
        if len(payload) > MAX_RESULT_CHARS:
            payload = payload[:MAX_RESULT_CHARS] + "… [gekürzt]"
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=payload)], is_error=result.status in ("error", "denied")
        )

    server: Server[Any] = Server(
        "jarvis",
        version="0.1.0",
        instructions=(
            "Werkzeuge von J.A.R.V.I.S. für Sirs Mac, Geschäftsprozesse, Gedächtnis und Projekte. "
            "Aktionen mit Außenwirkung laufen über das Freigabesystem."
        ),
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    manager = StreamableHTTPSessionManager(
        app=server,
        stateless=True,
        json_response=True,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
        ),
    )
    return server, manager


class MCPAuthApp:
    """ASGI wrapper: bearer auth (role ``agent``) in front of the MCP session manager."""

    def __init__(self, manager: StreamableHTTPSessionManager) -> None:
        self.manager = manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        principal = await authenticate_token(auth.removeprefix("Bearer ").strip() if auth else None)
        if principal is None or principal.role != "agent":
            await send(
                {"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"application/json")]}
            )
            await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
            return
        await self.manager.handle_request(scope, receive, send)
