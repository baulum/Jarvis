"""``/api/mobile/*`` – everything the companion app needs beyond the regular REST API.

Security model (see SECURITY.md › Smartphone): pairing via one-time QR code, device-bound EC key in the
Android Keystore, 10-minute access tokens, rotating refresh tokens (re-use → revocation), signed requests
(timestamp + nonce) for refresh and approvals, critical approvals stay desktop-only.
"""

from __future__ import annotations

import io
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from jarvis.api.deps import current_principal, get_state, require_owner
from jarvis.app_state import AppState
from jarvis.db import session_scope
from jarvis.db.models import Device, MobileDevice
from jarvis.events import spawn
from jarvis.mobile.alerts import Alert
from jarvis.mobile.gateway import advertised_urls
from jarvis.mobile.pairing import MOBILE_ROLE, PairingError
from jarvis.mobile.tokens import (
    RefreshError,
    revoke_mobile_device,
    rotate_refresh,
    sha256_hex,
    signer,
    verify_device_signature,
)
from jarvis.security.audit import audit
from jarvis.security.auth import Principal
from jarvis.security.guards import rate_limiter

router = APIRouter(prefix="/api/mobile")
StateDep = Depends(get_state)


def _uuid(v: str) -> uuid.UUID:
    try:
        return uuid.UUID(v)
    except ValueError as e:
        raise HTTPException(400, "invalid id") from e


Current = Depends(current_principal)
OwnerOnly = Depends(require_owner)


async def mobile_principal(p: Principal = Current) -> Principal:
    if p.role != MOBILE_ROLE or p.device_id is None:
        raise HTTPException(403, "only paired phones")
    return p


async def owner_or_mobile(p: Principal = Current) -> Principal:
    if p.role not in ("owner", MOBILE_ROLE):
        raise HTTPException(403, "owner devices only")
    return p


Mobile = Depends(mobile_principal)
OwnerOrMobile = Depends(owner_or_mobile)


async def _mobile_row(device_id: uuid.UUID) -> MobileDevice:
    async with session_scope() as s:
        mob = await s.get(MobileDevice, device_id)
    if mob is None:
        raise HTTPException(404, "device not paired")
    return mob


async def _verify_signed(request: Request, purpose: str, device_id: uuid.UUID) -> None:
    """Headers ``X-Jarvis-Ts``, ``X-Jarvis-Nonce``, ``X-Jarvis-Signature`` over purpose|device|ts|nonce|sha256(body)."""
    h = request.headers
    try:
        ts = int(h.get("x-jarvis-ts", "0"))
    except ValueError as e:
        raise HTTPException(401, "bad signature") from e
    mob = await _mobile_row(device_id)
    body = await request.body()
    ok = verify_device_signature(
        mob.public_key,
        purpose,
        str(device_id),
        ts,
        h.get("x-jarvis-nonce", ""),
        sha256_hex(body),
        h.get("x-jarvis-signature", ""),
    )
    if not ok:
        await audit(f"mobile:{device_id}", f"mobile.signature.{purpose}", outcome="denied")
        raise HTTPException(401, "invalid device signature")


# ------------------------------------------------------------------ pairing
class PairingCreate(BaseModel):
    name_hint: str | None = None


@router.post("/pairings")
async def pairing_create(body: PairingCreate, st: AppState = StateDep, p: Principal = OwnerOnly) -> Any:
    import segno

    code, expires = st.pairing.create(p.label)
    payload = {
        "t": "jarvis-pair",
        "v": 1,
        "urls": advertised_urls(st.settings),
        "fp": st.gateway.fingerprint(),
        "code": code,
    }
    raw = json.dumps(payload, separators=(",", ":"))
    buf = io.BytesIO()
    segno.make(raw, error="m").save(buf, kind="svg", scale=6, dark="#0b1a24", light="#ffffff", border=2)
    await audit(p.label, "mobile.pairing.create")
    return {
        "code": code,
        "expires_at": datetime.fromtimestamp(expires, UTC).isoformat(),
        "payload": raw,
        "svg": buf.getvalue().decode(),
        "urls": payload["urls"],
        "fingerprint": payload["fp"],
    }


class PairBody(BaseModel):
    code: str = Field(min_length=10, max_length=64)
    name: str = Field(min_length=2, max_length=120)
    platform: str = "android"
    model: str = ""
    app_version: str = ""
    public_key: str = Field(min_length=40, max_length=400)
    ts: int
    nonce: str = Field(min_length=16, max_length=64)
    signature: str = Field(min_length=40, max_length=200)


@router.post("/pair")
async def pair(body: PairBody, request: Request, st: AppState = StateDep) -> dict[str, Any]:
    client = request.client.host if request.client else "?"
    if not rate_limiter.check(f"pair:{client}", 10, 3600):
        raise HTTPException(429, "too many pairing attempts")
    m = st.settings.mobile
    try:
        result = await st.pairing.pair(
            code=body.code,
            name=body.name,
            platform=body.platform,
            model=body.model,
            app_version=body.app_version,
            public_key=body.public_key,
            ts=body.ts,
            nonce=body.nonce,
            signature=body.signature,
            access_ttl_s=m.access_token_ttl_s,
            refresh_ttl_days=m.refresh_token_ttl_days,
            client_ip=client,
        )
    except PairingError as e:
        await audit(f"mobile:{client}", "mobile.pair", outcome="denied", details={"error": str(e)})
        raise HTTPException(403, str(e)) from e
    await audit(f"mobile:{body.name}", "mobile.pair", target=result["device_id"], details={"model": body.model})
    return result


class RefreshBody(BaseModel):
    device_id: str
    refresh_token: str = Field(min_length=20, max_length=200)


@router.post("/token")
async def token(body: RefreshBody, request: Request, st: AppState = StateDep) -> dict[str, Any]:
    client = request.client.host if request.client else "?"
    if not rate_limiter.check(f"refresh:{client}", 60, 3600):
        raise HTTPException(429, "too many refreshes")
    device_id = _uuid(body.device_id)
    await _verify_signed(request, "refresh", device_id)
    m = st.settings.mobile
    try:
        family, refresh, device = await rotate_refresh(body.refresh_token, device_id, m.refresh_token_ttl_days)
    except RefreshError as e:
        await audit(f"mobile:{device_id}", "mobile.refresh", outcome="denied", details={"error": str(e)})
        raise HTTPException(401, str(e)) from e
    access, exp = signer.mint(device_id, device.role, device.name, family, m.access_token_ttl_s)
    async with session_scope() as s:
        mob = await s.get(MobileDevice, device_id)
        if mob is not None:
            mob.last_ip = client
    return {"access_token": access, "access_expires_at": exp, "refresh_token": refresh}


@router.post("/logout")
async def logout(st: AppState = StateDep, p: Principal = Mobile) -> dict[str, bool]:
    assert p.device_id is not None
    await revoke_mobile_device(p.device_id)
    st.location.clear(p.device_id, sharing_off=False)
    await audit(p.label, "mobile.logout", target=str(p.device_id))
    return {"ok": True}


# ------------------------------------------------------------------ device / config
@router.get("/me")
async def me(st: AppState = StateDep, p: Principal = Mobile) -> dict[str, Any]:
    assert p.device_id is not None
    mob = await _mobile_row(p.device_id)
    return {
        "device_id": str(p.device_id),
        "name": p.id,
        "role": p.role,
        "push_registered": bool(mob.push_token),
        "firebase": st.push.fcm.client_options(),
        "push_missing": st.push.fcm.missing(),
        "policy": st.alerts.policy.data,
        "owner_title": st.personality.owner_title,
        "livekit_public_url": st.settings.mobile.livekit.public_url or None,
        "server_time": datetime.now(UTC).isoformat(),
    }


class PushTokenBody(BaseModel):
    token: str = Field(min_length=20, max_length=4096)
    app_version: str | None = None


@router.put("/push-token")
async def push_token(body: PushTokenBody, p: Principal = Mobile) -> dict[str, bool]:
    assert p.device_id is not None
    async with session_scope() as s:
        mob = await s.get(MobileDevice, p.device_id)
        if mob is None:
            raise HTTPException(404, "device not paired")
        mob.push_token = body.token
        mob.push_token_updated_at = datetime.now(UTC)
        if body.app_version:
            mob.app_version = body.app_version[:40]
    await audit(p.label, "mobile.push_token", target=str(p.device_id))
    return {"ok": True}


# ------------------------------------------------------------------ location (only while Sir shares it)
class LocationBody(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0, le=100_000)
    timestamp_ms: int = Field(gt=0, description="when the phone measured the fix (Unix ms)")


@router.put("/location")
async def location_put(body: LocationBody, st: AppState = StateDep, p: Principal = Mobile) -> dict[str, bool]:
    """Latest fix only (in memory). Deliberately no audit/log entry: the coordinates must not end up anywhere."""
    assert p.device_id is not None
    if not rate_limiter.check(f"location:{p.device_id}", 240, 3600):
        raise HTTPException(429, "too many location updates")
    try:
        measured = datetime.fromtimestamp(body.timestamp_ms / 1000, UTC)
        stored = st.location.update(p.device_id, body.latitude, body.longitude, body.accuracy_m, measured)
    except (ValueError, OverflowError, OSError) as e:
        raise HTTPException(422, "invalid timestamp") from e
    return {"ok": True, "stored": stored}


@router.delete("/location")
async def location_delete(st: AppState = StateDep, p: Principal = Mobile) -> dict[str, bool]:
    """Sir switched sharing off in the app: forget the stored fix."""
    assert p.device_id is not None
    st.location.clear(p.device_id)
    await audit(p.label, "mobile.location.sharing_off", target=str(p.device_id))
    return {"ok": True}


@router.get("/devices")
async def devices(p: Principal = OwnerOrMobile) -> list[dict[str, Any]]:
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(Device, MobileDevice)
                .join(MobileDevice, MobileDevice.device_id == Device.id)
                .order_by(Device.created_at.desc())
            )
        ).all()
    return [
        {
            "id": str(d.id),
            "name": d.name,
            "model": m.model,
            "platform": m.platform,
            "app_version": m.app_version,
            "revoked": d.revoked,
            "push": bool(m.push_token),
            "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
            "created_at": d.created_at.isoformat(),
            "current": d.id == p.device_id,
        }
        for d, m in rows
    ]


@router.delete("/devices/{did}")
async def device_revoke(did: str, st: AppState = StateDep, p: Principal = OwnerOrMobile) -> dict[str, bool]:
    device_id = _uuid(did)
    if p.role != "owner" and device_id != p.device_id:
        raise HTTPException(403, "only the owner may remove other devices")
    await revoke_mobile_device(device_id)
    st.location.clear(device_id, sharing_off=False)
    await audit(p.label, "mobile.device.revoke", target=did)
    return {"revoked": True}


@router.get("/status")
async def status(st: AppState = StateDep, p: Principal = OwnerOrMobile) -> dict[str, Any]:
    return {
        "gateway": st.gateway.status(),
        "livekit": await st.livekit.status(),
        "push": {"configured": st.push.fcm.configured(), "missing": st.push.fcm.missing()},
        "paired_devices": await st.push.paired_count(),
        "calls": await st.calls.availability(),
    }


# ------------------------------------------------------------------ alert policy / alerts
@router.get("/alert-policy")
async def policy_get(st: AppState = StateDep, p: Principal = OwnerOrMobile) -> dict[str, Any]:
    return st.alerts.policy.data


@router.put("/alert-policy")
async def policy_put(body: dict[str, Any], st: AppState = StateDep, p: Principal = OwnerOrMobile) -> dict[str, Any]:
    try:
        data = st.alerts.policy.update(body)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    await audit(p.label, "mobile.alert_policy", details=body)
    return data


@router.get("/alerts")
async def alerts(limit: int = 50, st: AppState = StateDep, p: Principal = OwnerOrMobile) -> list[dict[str, Any]]:
    return [
        {
            "id": str(a.id),
            "category": a.category,
            "level": a.level,
            "title": a.title,
            "body": a.body,
            "decision": a.decision,
            "reason": a.reason,
            "status": a.status,
            "link": a.deep_link,
            "actions": a.actions,
            "data": a.data,
            "call_id": str(a.call_id) if a.call_id else None,
            "created_at": a.created_at.isoformat(),
        }
        for a in await st.alerts.recent(min(limit, 200))
    ]


class SnoozeBody(BaseModel):
    minutes: int = Field(default=30, ge=1, le=1440)


@router.post("/alerts/{aid}/snooze")
async def alert_snooze(aid: str, body: SnoozeBody, st: AppState = StateDep, p: Principal = OwnerOrMobile) -> Any:
    try:
        at = await st.alerts.snooze(_uuid(aid), body.minutes)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    return {"remind_at": at.isoformat()}


class ReplyBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/alerts/{aid}/reply")
async def alert_reply(aid: str, body: ReplyBody, st: AppState = StateDep, p: Principal = OwnerOrMobile) -> Any:
    row = await st.alerts.get(_uuid(aid))
    if row is None:
        raise HTTPException(404, "not found")
    sess = await st.orchestrator.get_or_create_session(p, "mobile", None)
    prompt = f"(Antwort auf die Benachrichtigung „{row.title}“: {row.body[:400]})\n{body.text}"
    spawn(st.orchestrator.handle(p, "mobile", prompt, session_id=sess.id), name="mobile-reply")
    return {"session_id": str(sess.id), "accepted": True}


class NotifyTest(BaseModel):
    category: str = "system"
    level: str = "info"


@router.post("/test/notify")
async def test_notify(body: NotifyTest, st: AppState = StateDep, p: Principal = OwnerOrMobile) -> Any:
    return await st.alerts.dispatch(
        Alert(
            body.category if body.category in st.alerts.policy.data["categories"] else "system",
            "Testbenachrichtigung",
            f"{st.personality.owner_title}, die Benachrichtigungen funktionieren.",
            body.level,
            deep_link="jarvis://home",
            source="test",
        )
    )


@router.post("/test/call")
async def test_call(st: AppState = StateDep, p: Principal = OwnerOrMobile) -> Any:
    """Explicit test call (Sir pressed the button) – goes through the same blockers as real calls."""
    try:
        return await st.calls.ring_owner(
            message="Dies ist ein Testanruf. Die Verbindung steht.", reason="test", event_key=None
        )
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/test/ring-local")
async def test_ring_local(request: Request, st: AppState = StateDep, p: Principal = OwnerOnly) -> Any:
    """DEVICE TEST without FCM: creates a ringing Jarvis call; the phone is told over adb (see docs/MOBILE.md).
    Owner device on loopback only – not reachable through the mobile gateway (not in its allowlist)."""
    if request.client is None or request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "loopback only")
    await audit(p.label, "call.test_ring_local")
    return await st.calls.ring_owner(
        message="Dies ist ein lokaler Testanruf ohne Push. Die Website ist fertig.", reason="test", skip_push=True
    )


# ------------------------------------------------------------------ calls
@router.get("/calls/availability")
async def calls_availability(st: AppState = StateDep, p: Principal = OwnerOrMobile) -> dict[str, Any]:
    return await st.calls.availability()


@router.get("/calls")
async def calls(limit: int = 50, st: AppState = StateDep, p: Principal = OwnerOrMobile) -> list[dict[str, Any]]:
    return await st.calls.history(limit)


@router.post("/calls")
async def call_start(st: AppState = StateDep, p: Principal = Mobile) -> dict[str, Any]:
    try:
        info = await st.calls.start_from_phone(p)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    await audit(p.label, "call.start", target=info["call_id"])
    return info


@router.post("/calls/{cid}/answer")
async def call_answer(cid: str, st: AppState = StateDep, p: Principal = Mobile) -> dict[str, Any]:
    try:
        return await st.calls.answer(_uuid(cid), p)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e


@router.post("/calls/{cid}/decline")
async def call_decline(cid: str, st: AppState = StateDep, p: Principal = Mobile) -> dict[str, bool]:
    try:
        await st.calls.decline(_uuid(cid), p)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    return {"ok": True}


@router.post("/calls/{cid}/hangup")
async def call_hangup(cid: str, st: AppState = StateDep, p: Principal = Mobile) -> dict[str, bool]:
    await st.calls.hangup(_uuid(cid), "owner_hangup")
    return {"ok": True}


@router.get("/watches")
async def watches(st: AppState = StateDep, p: Principal = OwnerOrMobile) -> list[dict[str, Any]]:
    return st.calls.watches()


@router.delete("/watches/{wid}")
async def watch_delete(wid: str, st: AppState = StateDep, p: Principal = OwnerOrMobile) -> dict[str, bool]:
    return {"removed": st.calls.remove_watch(wid)}


# ------------------------------------------------------------------ approvals (signed)
class MobileDecision(BaseModel):
    approve: bool
    note: str | None = None


@router.post("/approvals/{aid}")
async def approval_decide(aid: str, request: Request, st: AppState = StateDep, p: Principal = Mobile) -> dict[str, Any]:
    """Approve/reject from the phone – only with a fresh Keystore signature (after biometric unlock in the app)."""
    assert p.device_id is not None
    await _verify_signed(request, f"approval:{aid}", p.device_id)
    body = MobileDecision.model_validate_json(await request.body())
    # Mobile principal has no desktop device binding for critical actions: Principal.device_id is set,
    # but critical approvals are restricted to the roles in permissions.critical_approval_roles (owner).
    try:
        return await st.executor.decide(_uuid(aid), body.approve, p, body.note)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
