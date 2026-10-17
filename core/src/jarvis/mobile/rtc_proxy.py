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


def _internal(request_url_path: str, query: str, base: str) -> str:
    return base.rstrip("/") + request_url_path + (f"?{query}" if query else "")


@router.websocket("/rtc")
@router.websocket("/rtc/{rest:path}")
async def rtc_ws(ws: WebSocket, rest: str = "") -> None:
    base = ws.app.state.jarvis.settings.mobile.livekit.internal_url
    target = _internal(ws.url.path, ws.url.query, base)
    try:
        upstream = await websockets.connect(target, max_size=None, open_timeout=10, ping_interval=None)
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
    base = request.app.state.jarvis.settings.mobile.livekit.internal_url.replace("ws://", "http://")
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(_internal(request.url.path, request.url.query, base))
    return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))
