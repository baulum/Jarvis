"""WebSocket event stream + webhook endpoints (Twilio, generic HMAC webhooks)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from jarvis.app_state import AppState
from jarvis.events import Event, bus, spawn
from jarvis.logging_setup import ring
from jarvis.security.audit import audit
from jarvis.security.auth import Principal, authenticate_token
from jarvis.security.guards import rate_limiter
from jarvis.telephony.service import public_url_for

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws")
async def events_ws(ws: WebSocket) -> None:
    """Realtime events for the desktop app / voice daemon. Auth: ?token=<device token>."""
    principal = await authenticate_token(ws.query_params.get("token"))
    state: AppState = ws.app.state.jarvis
    if principal is None or principal.role not in state.permissions.roles:
        await ws.close(code=4401)
        return
    await ws.accept()
    queue = bus.open_queue()
    log_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
    want_logs = ws.query_params.get("logs") == "1"
    loop = asyncio.get_running_loop()

    def on_log(entry: dict[str, Any]) -> None:
        def put() -> None:
            if not log_queue.full():
                log_queue.put_nowait(entry)

        loop.call_soon_threadsafe(put)

    if want_logs:
        ring.subscribe(on_log)
    is_voice = ws.query_params.get("client") == "voice"
    if is_voice:
        state.voice_state["connected"] = True

    async def pump_events() -> None:
        while True:
            ev: Event = await queue.get()
            if not _visible(ev, principal):
                continue
            await ws.send_text(json.dumps(ev.to_dict(), ensure_ascii=False, default=str))

    async def pump_logs() -> None:
        while True:
            entry = await log_queue.get()
            await ws.send_text(json.dumps({"type": "log", "data": entry}, ensure_ascii=False, default=str))

    async def receive() -> None:
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text('{"type":"pong"}')

    tasks = [asyncio.create_task(pump_events()), asyncio.create_task(receive())]
    if want_logs:
        tasks.append(asyncio.create_task(pump_logs()))
    try:
        await ws.send_text(
            json.dumps({"type": "hello", "data": {"principal": principal.label, "role": principal.role}})
        )
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for t in done:
            exc = t.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                logger.debug("ws task ended: %r", exc)
    except WebSocketDisconnect:
        pass
    finally:
        for t in tasks:
            t.cancel()
        bus.close_queue(queue)
        if is_voice:
            state.voice_state["connected"] = False
        with contextlib.suppress(Exception):
            await ws.close()


def _visible(ev: Event, p: Principal) -> bool:
    if p.role == "owner":
        return True
    # non-owner devices do not see audit/memory internals
    return not ev.type.startswith(("audit.", "memory.pending"))


# ------------------------------------------------------------------ telephony
@router.post("/api/telephony/twilio/voice")
async def twilio_voice(request: Request) -> Response:
    state: AppState = request.app.state.jarvis
    if state.telephony is None:
        raise HTTPException(404)
    form = {k: str(v) for k, v in (await request.form()).items()}
    url = public_url_for(str(request.url), state.settings.server.public_base_url)
    try:
        twiml = await state.telephony.handle_inbound(url, form, request.headers.get("x-twilio-signature"))
    except PermissionError as e:
        raise HTTPException(403, "invalid signature") from e
    return Response(twiml, media_type="application/xml")


@router.post("/api/telephony/twilio/status")
async def twilio_status(request: Request) -> Response:
    state: AppState = request.app.state.jarvis
    if state.telephony is None:
        raise HTTPException(404)
    form = {k: str(v) for k, v in (await request.form()).items()}
    url = public_url_for(str(request.url), state.settings.server.public_base_url)
    try:
        await state.telephony.handle_status_callback(url, form, request.headers.get("x-twilio-signature"))
    except PermissionError as e:
        raise HTTPException(403, "invalid signature") from e
    return Response("", status_code=204)


@router.websocket("/api/telephony/stream")
async def telephony_stream(ws: WebSocket) -> None:
    state: AppState = ws.app.state.jarvis
    if state.telephony is None:
        await ws.close(code=4404)
        return
    await state.telephony.media_stream(ws)


# ------------------------------------------------------------------ generic inbound webhooks
@router.post("/api/webhooks/{name}")
async def inbound_webhook(name: str, request: Request) -> dict[str, Any]:
    state: AppState = request.app.state.jarvis
    conn = state.connectors.connectors["webhook"]
    body = await request.body()
    client = request.client.host if request.client else "?"
    if not rate_limiter.check(f"webhook:{name}:{client}", 30, 60):
        raise HTTPException(429, "rate limit")
    if not conn.active or not conn.verify(body, request.headers.get("x-jarvis-signature")):  # type: ignore[attr-defined]
        await audit(f"webhook:{client}", f"webhook.{name}", outcome="denied")
        raise HTTPException(401, "invalid signature")
    route = (conn.settings.get("inbound_routes") or {}).get(name)
    if not route:
        raise HTTPException(404, "unknown webhook")
    payload = body.decode(errors="replace")[:8000]
    from jarvis.security.guards import wrap_untrusted

    wrapped = wrap_untrusted(payload, f"webhook:{name}", 8000)
    prompt = route.replace("{payload}", wrapped["notice"] + "\n" + wrapped["content"])
    principal = Principal(id=f"webhook-{name}", role="system", channel="webhook")
    await audit(principal.label, f"webhook.{name}", details={"bytes": len(body)})
    spawn(state.orchestrator.handle(principal, "webhook", prompt), name=f"webhook-{name}")
    return {"accepted": True}
