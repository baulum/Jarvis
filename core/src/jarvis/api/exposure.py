"""Only the telephony endpoints may be reached through a public tunnel (and mobile paths on the gateway port).

The core listens on 127.0.0.1, but a tunnel (Cloudflare) forwards internet requests to it, and those
arrive from 127.0.0.1 too. They are recognised by their Host header (the tunnel's public hostname)
or Cloudflare's ``cf-*`` headers; for them everything except the Twilio webhooks and the media
stream answers 404 — independent of how the tunnel itself is configured. Pure ASGI so it also
covers WebSocket connections.
"""

from __future__ import annotations

from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

PUBLIC_PREFIXES = ("/api/telephony/twilio/", "/api/telephony/stream")
_LOOPBACK = {"127.0.0.1", "localhost", "[::1]", "::1"}


def is_public(scope: Scope) -> bool:
    headers: dict[bytes, bytes] = dict(scope.get("headers") or [])
    if any(k.startswith(b"cf-") for k in headers):
        return True
    host = headers.get(b"host", b"").decode("latin-1").lower()
    hostname = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
    return bool(host) and hostname not in _LOOPBACK


def on_port(scope: Scope, port: int | None) -> bool:
    server = scope.get("server")
    return bool(port and server and server[1] == port)


class PublicExposureGuard:
    def __init__(self, app: ASGIApp, mobile_port: int | None = None) -> None:
        self.app = app
        self.mobile_port = mobile_port

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and on_port(scope, self.mobile_port):
            # Mobile gateway listener (LAN/Tailscale): only the paths a paired phone needs, never tunnel traffic.
            from jarvis.mobile.gateway import mobile_path_allowed

            headers: dict[bytes, bytes] = dict(scope.get("headers") or [])
            method = "GET" if scope["type"] == "websocket" else str(scope.get("method", "GET"))
            if any(k.startswith(b"cf-") for k in headers) or not mobile_path_allowed(method, scope.get("path", "")):
                await _reject(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return
        if scope["type"] in ("http", "websocket") and is_public(scope):
            path: str = scope.get("path", "")
            if not path.startswith(PUBLIC_PREFIXES):
                await _reject(scope, receive, send)
                return
        await self.app(scope, receive, send)


async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1008})
        return
    body = b'{"detail":"Not Found"}'
    headers: list[Any] = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    await send({"type": "http.response.start", "status": 404, "headers": headers})
    await send({"type": "http.response.body", "body": body})
