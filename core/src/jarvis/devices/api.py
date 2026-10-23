"""REST/WebSocket endpoints for Jarvis Device Agents.

- ``POST /api/devices/agents/pairing`` (owner): one-time code + gateway URLs + certificate fingerprint
- ``POST /api/devices/agents/register`` (code): the agent trades the code for its device token (role device_agent)
- ``GET  /api/devices/agent/ws`` (device token in the first frame): the agent's command channel
- ``GET  /api/devices/agents`` (read): paired and connected agents with their capabilities
- ``POST /api/devices/agents/{device}/command`` (owner): remote command from a client, audited
- ``DELETE /api/devices/agents/{device}`` (owner): revoke the token and disconnect

Device tokens only open the agent WebSocket: ``device_agent`` is not a role of permissions.yaml, so the rest of the
API (chats, memory, approvals, the event stream) answers 403 / closes for them.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from jarvis.api.deps import get_state, require, require_owner
from jarvis.app_state import AppState
from jarvis.devices.hub import DeviceConnection, DeviceError, parse_hello
from jarvis.devices.protocol import ACTIONS, DEVICE_ROLE, MAX_FRAME_BYTES, PROTOCOL_VERSION
from jarvis.mobile.gateway import advertised_urls
from jarvis.mobile.pairing import PairingError
from jarvis.security.audit import audit
from jarvis.security.auth import Principal, authenticate_token, create_device, revoke_device
from jarvis.security.guards import rate_limiter

logger = logging.getLogger(__name__)
router = APIRouter()
StateDep = Depends(get_state)
Owner = Depends(require_owner)
Reader = Depends(require("read"))

HTTP_ERRORS = {
    "invalid": 400,
    "unknown_action": 400,
    "denied": 403,
    "kill_switch": 423,
    "unavailable": 409,
    "offline": 503,
    "timeout": 504,
    "failed": 502,
}


def http_error(e: DeviceError) -> HTTPException:
    return HTTPException(HTTP_ERRORS.get(e.code, 502), e.message)


class PairingRequest(BaseModel):
    name: str = Field(default="", max_length=80)  # suggested name, the agent may override it


@router.post("/api/devices/agents/pairing")
async def create_pairing(
    body: PairingRequest | None = None, st: AppState = StateDep, p: Principal = Owner
) -> dict[str, Any]:
    code, expires = st.device_pairing.create(p.label, role=DEVICE_ROLE)
    await audit(p.label, "device_agent.pairing", details={"expires": int(expires)})
    urls = advertised_urls(st.settings)
    fingerprint = st.gateway.fingerprint()
    hint = f"jarvis-device pair --core {urls[0] if urls else 'https://<host>:8767'} --code {code}"
    if fingerprint:
        hint += f" --fingerprint {fingerprint}"
    return {
        "code": code,
        "expires_at": expires,
        "urls": urls,
        "fingerprint": fingerprint,
        "protocol": PROTOCOL_VERSION,
        "command": hint,
        "name": (body.name if body else "") or None,
    }


class RegisterBody(BaseModel):
    code: str = Field(min_length=8, max_length=64)
    name: str = Field(min_length=2, max_length=80)
    platform: str = Field(default="", max_length=20)
    hostname: str = Field(default="", max_length=120)


@router.post("/api/devices/agents/register")
async def register_agent(body: RegisterBody, request: Request, st: AppState = StateDep) -> dict[str, Any]:
    client = request.client.host if request.client else "?"
    if not rate_limiter.check(f"device-register:{client}", 10, 3600):
        raise HTTPException(429, "Zu viele Kopplungsversuche")
    try:
        st.device_pairing.consume(body.code)
    except PairingError as e:
        await audit(f"device:{client}", "device_agent.register", outcome="denied", details={"name": body.name})
        raise HTTPException(403, str(e)) from e
    name = body.name.strip()
    device, token = await create_device(name, DEVICE_ROLE)
    await audit(
        f"device:{name}",
        "device_agent.register",
        target=str(device.id),
        details={"platform": body.platform, "hostname": body.hostname, "ip": client},
    )
    await st.device_hub.set_paired(device.id, name)
    return {"device_id": str(device.id), "token": token, "protocol": PROTOCOL_VERSION}


@router.get("/api/devices/agents")
async def list_agents(st: AppState = StateDep, p: Principal = Reader) -> list[dict[str, Any]]:
    return st.device_hub.describe()


class CommandBody(BaseModel):
    action: str = Field(min_length=3, max_length=40)
    args: dict[str, Any] = Field(default_factory=dict)
    timeout_s: float | None = Field(default=None, ge=1, le=900)


@router.post("/api/devices/agents/{device}/command")
async def device_command(
    device: str, body: CommandBody, st: AppState = StateDep, p: Principal = Owner
) -> dict[str, Any]:
    if body.action not in ACTIONS:
        raise HTTPException(400, f"Unbekannte Aktion {body.action}")
    await audit(p.label, f"device.{body.action}", target=device, details={"args": sorted(body.args)})
    try:
        data = await st.device_hub.call(device, body.action, body.args, body.timeout_s)
    except DeviceError as e:
        raise http_error(e) from e
    return {"ok": True, "data": data}


@router.delete("/api/devices/agents/{device}")
async def revoke_agent(device: str, st: AppState = StateDep, p: Principal = Owner) -> dict[str, bool]:
    try:
        did = uuid.UUID(device)
    except ValueError as e:
        raise HTTPException(400, "invalid id") from e
    if did not in st.device_hub.paired:
        raise HTTPException(404, "Kein gekoppelter Device Agent mit dieser ID")
    ok = await revoke_device(did)
    await st.device_hub.set_paired(did, None)
    await audit(p.label, "device_agent.revoke", target=device)
    return {"revoked": ok}


# ------------------------------------------------------------------ the agent's WebSocket
@router.websocket("/api/devices/agent/ws")
async def agent_socket(ws: WebSocket) -> None:
    """The agent checks the pinned gateway certificate first and only then sends its token – in the ``hello``
    frame (a header would leave before the certificate could be inspected)."""
    st: AppState = ws.app.state.jarvis
    client = ws.client.host if ws.client else "?"
    if not rate_limiter.check(f"device-ws:{client}", 120, 3600):
        await ws.close(code=4429)
        return
    await ws.accept()

    async def reject(code: int, message: str) -> None:
        with contextlib.suppress(Exception):
            await ws.send_text(json.dumps({"type": "error", "message": message}))
            await ws.close(code=code)

    try:
        hello = json.loads(await asyncio.wait_for(ws.receive_text(), 10))
        if not isinstance(hello, dict):
            raise ValueError("hello")
        info, caps = parse_hello(hello)
    except DeviceError as e:
        await reject(4400, e.message)
        return
    except (TimeoutError, ValueError, WebSocketDisconnect):
        await reject(4400, "hello fehlt")
        return
    auth = ws.headers.get("authorization", "")
    token = str(hello.get("token") or "") or (auth[7:].strip() if auth.lower().startswith("bearer ") else "")
    principal = await authenticate_token(token or None)
    if principal is None or principal.role != DEVICE_ROLE or principal.device_id is None:
        await audit(f"device:{client}", "device_agent.connect", outcome="denied")
        await reject(4401, "Geräte-Token ungültig oder widerrufen – neu koppeln")
        return
    send_lock = asyncio.Lock()

    async def send(frame: dict[str, Any]) -> None:
        async with send_lock:
            await ws.send_text(json.dumps(frame, ensure_ascii=False, default=str))

    async def close() -> None:
        await ws.close(code=4000)

    conn = DeviceConnection(
        device_id=principal.device_id,
        name=principal.id,
        info=info,
        capabilities=caps,
        send=send,
        close=close,
    )
    await st.device_hub.attach(conn)
    await audit(
        f"device:{conn.name}",
        "device_agent.connect",
        target=str(conn.device_id),
        details={"platform": info.get("platform"), "ip": client},
    )
    try:
        await send(
            {
                "type": "welcome",
                "device_id": str(conn.device_id),
                "kill_switch": st.kill_switch.engaged,
                "server_time": time.time(),
            }
        )
        while True:
            raw = await ws.receive_text()
            if len(raw) > MAX_FRAME_BYTES:
                logger.warning("Device %s sent an oversized frame (%d bytes)", conn.name, len(raw))
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if isinstance(msg, dict) and (reply := st.device_hub.handle_message(conn, msg)) is not None:
                await send(reply)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Device socket %s failed", conn.name)
    finally:
        await st.device_hub.detach(conn)
