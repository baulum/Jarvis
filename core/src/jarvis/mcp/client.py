"""External MCP servers (``config/mcp_servers.yaml``) registered into the Jarvis tool registry.

Their tools go through the same executor (risk, approvals, audit). Tools without a
``readOnlyHint`` default to risk ``high``; the config can set a different default per server.
Secrets are referenced as ``${secret:name}`` and resolved from the Keychain at connect time.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import httpx2
import yaml
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from jarvis.config import CONFIG_DIR, RISK_ORDER, RiskLevel
from jarvis.secrets import secret_store
from jarvis.security.guards import wrap_untrusted
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext, ToolSpec

if TYPE_CHECKING:
    from jarvis.app_state import AppState

logger = logging.getLogger(__name__)
_SECRET_REF = re.compile(r"\$\{secret:([a-z0-9._-]+)\}")


def _resolve(value: Any) -> Any:
    if isinstance(value, str):

        def repl(m: re.Match[str]) -> str:
            v = secret_store.get(m.group(1))
            if not v:
                raise ToolUserError(f"Secret {m.group(1)} fehlt")
            return v

        return _SECRET_REF.sub(repl, value)
    if isinstance(value, dict):
        return {k: _resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v) for v in value]
    return value


class ExternalServer:
    def __init__(self, name: str, cfg: dict[str, Any]) -> None:
        self.name = name
        self.cfg = cfg
        self.session: ClientSession | None = None
        self.tools: list[Any] = []
        self.error: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()

    async def start(self, connect_timeout: float = 30) -> None:
        self._task = asyncio.create_task(self._hold())
        try:
            await asyncio.wait_for(self._ready.wait(), connect_timeout)
        except TimeoutError:
            self.error = self.error or "Verbindungs-Timeout"

    async def _hold(self) -> None:
        try:
            async with contextlib.AsyncExitStack() as stack:
                if "url" in self.cfg:
                    http = httpx2.AsyncClient(headers=_resolve(self.cfg.get("headers", {})), timeout=60)
                    await stack.enter_async_context(http)
                    streams = await stack.enter_async_context(streamable_http_client(self.cfg["url"], http_client=http))
                else:
                    params = StdioServerParameters(
                        command=self.cfg["command"],
                        args=_resolve(self.cfg.get("args", [])),
                        env=_resolve(self.cfg.get("env")) or None,
                    )
                    streams = await stack.enter_async_context(stdio_client(params))
                read, write = streams[0], streams[1]
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                listed = await session.list_tools()
                self.session = session
                self.tools = list(listed.tools)
                self._ready.set()
                await self._stop.wait()
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"[:300]
            logger.warning("MCP server %s failed: %s", self.name, self.error)
            self._ready.set()

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._task, 5)

    async def call(self, tool: str, args: dict[str, Any]) -> Any:
        if self.session is None:
            raise ToolUserError(f"MCP-Server {self.name} ist nicht verbunden: {self.error}")
        res = await self.session.call_tool(tool, args)
        texts = [getattr(c, "text", "") for c in res.content if getattr(c, "type", "") == "text"]
        out = "\n".join(texts) or json.dumps(getattr(res, "structured_content", None) or {}, default=str)
        if getattr(res, "is_error", False):
            raise ToolUserError(out[:1000])
        return out


def _summary(server: str, tool: str) -> Callable[[dict[str, Any]], str]:
    def summarize(a: dict[str, Any]) -> str:
        return f"MCP {server}.{tool}({json.dumps(a)[:150]})"

    return summarize


class MCPClientHub:
    def __init__(self, state: AppState, path: Path | None = None) -> None:
        self.state = state
        self.path = path or (CONFIG_DIR / "mcp_servers.yaml")
        self.servers: dict[str, ExternalServer] = {}

    def load_config(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        return {k: v for k, v in (data.get("servers") or {}).items() if isinstance(v, dict)}

    async def start(self) -> None:
        for name, cfg in self.load_config().items():
            if not cfg.get("enabled", False):
                continue
            srv = ExternalServer(name, cfg)
            await srv.start()
            self.servers[name] = srv
            if srv.error:
                continue
            self._register(srv)

    def _register(self, srv: ExternalServer) -> None:
        default_risk = srv.cfg.get("default_risk", "high")
        include = set(srv.cfg.get("include_tools") or [])
        overrides = srv.cfg.get("risk_overrides") or {}
        for t in srv.tools:
            if include and t.name not in include:
                continue
            ro = bool(getattr(getattr(t, "annotations", None), "read_only_hint", False))
            risk = overrides.get(t.name) or ("read" if ro else default_risk)
            if risk not in RISK_ORDER:
                risk = "high"
            name = re.sub(r"[^a-z0-9_]", "_", f"ext_{srv.name}_{t.name}".lower())[:64]

            async def handler(
                ctx: ToolContext, a: dict[str, Any], _srv: ExternalServer = srv, _tool: str = t.name
            ) -> Any:
                out = await _srv.call(_tool, a)
                return wrap_untrusted(out, f"mcp:{_srv.name}", 12000)

            schema = dict(getattr(t, "input_schema", None) or {"type": "object", "properties": {}})
            schema.setdefault("type", "object")
            try:
                self.state.registry.register(
                    ToolSpec(
                        name=name,
                        description=f"[{srv.name}] {t.description or t.name}"[:1000],
                        parameters=schema,
                        handler=handler,
                        risk=cast(RiskLevel, risk),
                        connector="mcp",
                        writes=risk != "read",
                        untrusted_output=True,
                        summarize=_summary(srv.name, t.name),
                    )
                )
            except ValueError as e:
                logger.warning("Skipping MCP tool %s: %s", name, e)

    async def stop(self) -> None:
        for srv in self.servers.values():
            await srv.stop()

    def status(self) -> list[dict[str, Any]]:
        cfg = self.load_config()
        out = []
        for name, c in cfg.items():
            srv = self.servers.get(name)
            out.append(
                {
                    "name": name,
                    "enabled": bool(c.get("enabled")),
                    "connected": bool(srv and srv.session),
                    "error": srv.error if srv else None,
                    "tools": len(srv.tools) if srv else 0,
                    "transport": "http" if "url" in c else "stdio",
                    "description": c.get("description", ""),
                }
            )
        return out
