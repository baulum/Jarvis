"""TLS front for LiveKit signalling: ``wss://<gateway>/rtc…`` → ``ws://127.0.0.1:7880/rtc…``.

LiveKit itself only listens on loopback; the phone's room token travels inside the TLS connection of the
gateway (pinned certificate). Media does not pass through here – it flows directly via DTLS-SRTP.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import httpx
import websockets
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger(__name__)
router = APIRouter()
MAX_SIGNAL_BYTES = 1024 * 1024  # LiveKit signalling messages are tiny; never accept unbounded frames
MAX_CONNECTIONS = 8
_open = 0


def _token_of(auth_header: str | None, query: str) -> str | None:
    """The LiveKit room token: ``Authorization: Bearer`` (newer SDKs) or ``?access_token=``."""
    if auth_header and auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    from urllib.parse import parse_qs

    vals = parse_qs(query).get("access_token")
    return vals[0] if vals else None


def valid_room_token(state: object, token: str | None) -> bool:
    """Only a token minted by this core (signature + expiry) may use the pre-auth signalling proxy."""
    if not token:
        return False
    try:
        from livekit import api

        key, secret = state.livekit.credentials()  # type: ignore[attr-defined]
        api.TokenVerifier(key, secret).verify(token)
    except Exception:
        return False
    return True


def _internal(request_url_path: str, query: str, base: str) -> str:
    return base.rstrip("/") + request_url_path + (f"?{query}" if query else "")


@router.websocket("/rtc")
@router.websocket("/rtc/{rest:path}")
async def rtc_ws(ws: WebSocket, rest: str = "") -> None:
    global _open
    state = ws.app.state.jarvis
    token = _token_of(ws.headers.get("authorization"), ws.url.query)
    if not valid_room_token(state, token) or _open >= MAX_CONNECTIONS:
        await ws.close(code=1008)
        return
    _open += 1
    try:
        await _proxy_ws(ws, state)
    finally:
        _open -= 1


async def _proxy_ws(ws: WebSocket, state: object) -> None:
    base = state.settings.mobile.livekit.internal_url  # type: ignore[attr-defined]
    target = _internal(ws.url.path, ws.url.query, base)
    # newer SDKs send the room token as "Authorization: Bearer …" instead of ?access_token=
    auth = {"Authorization": ws.headers["authorization"]} if "authorization" in ws.headers else None
    try:
        upstream = await websockets.connect(
            target, max_size=MAX_SIGNAL_BYTES, open_timeout=10, ping_interval=None, additional_headers=auth
        )
    except Exception as e:
        logger.warning("LiveKit signalling unreachable: %s", e)
        await ws.close(code=1011)
        return
    await ws.accept()

    async def down() -> None:
        async for msg in upstream:
            if isinstance(msg, bytes):
                await ws.send_bytes(msg)
            else:
                await ws.send_text(msg)

    async def up() -> None:
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect":
                return
            if m.get("bytes") is not None:
                await upstream.send(m["bytes"])
            elif m.get("text") is not None:
                await upstream.send(m["text"])

    tasks = [asyncio.create_task(down()), asyncio.create_task(up())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except (WebSocketDisconnect, ConnectionClosed):
        pass
    finally:
        for t in tasks:
            t.cancel()
        with contextlib.suppress(Exception):
            await upstream.close()
        with contextlib.suppress(Exception):
            await ws.close()


@router.get("/rtc/{rest:path}")
async def rtc_http(rest: str, request: Request) -> Response:
    """``/rtc/validate`` – the SDK calls it after a failed connect to get a readable error."""
    state = request.app.state.jarvis
    if rest.strip("/") != "validate" or not valid_room_token(
        state, _token_of(request.headers.get("authorization"), request.url.query)
    ):
        return Response(status_code=401)  # only the SDK's own /rtc/validate call, and only with a valid room token
    base = state.settings.mobile.livekit.internal_url.replace("ws://", "http://")
    async with httpx.AsyncClient(timeout=10) as c:
        auth = {"Authorization": request.headers["authorization"]} if "authorization" in request.headers else {}
        r = await c.get(_internal(request.url.path, request.url.query, base), headers=auth)
    return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))
