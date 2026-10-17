"""Firebase Cloud Messaging (HTTP v1) with end-to-end encrypted, replay-protected payloads.

Only data messages are sent; the app renders notifications natively (channels, actions, CallStyle) so
they work when the app is not running. The payload is AES-256-GCM encrypted with the per-device key from
pairing (the phone keeps it inside the Android Keystore) – Google and anyone else on the path see only
ciphertext. Every message has an id and an expiry; the phone drops duplicates and stale messages.
FCM is best effort: delivery to a phone that is offline is retried by FCM until ``ttl`` expires.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select

from jarvis.config import FirebaseCfg
from jarvis.db import session_scope
from jarvis.db.models import Device, MobileDevice
from jarvis.mobile.pairing import push_key_secret
from jarvis.secrets import secret_store

logger = logging.getLogger(__name__)
FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
MAX_PLAINTEXT = 2600  # FCM data limit is 4 KB; base64 of ciphertext + envelope must fit


def encrypt_payload(key: bytes, device_id: str, payload: dict[str, Any]) -> dict[str, str]:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    if len(raw) > MAX_PLAINTEXT:
        raise ValueError("push payload too large")
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, raw, f"jarvis-push|{device_id}".encode())
    return {"v": "1", "k": "1", "n": base64.b64encode(nonce).decode(), "c": base64.b64encode(ct).decode()}


def decrypt_payload(key: bytes, device_id: str, envelope: dict[str, str]) -> dict[str, Any]:
    """Reference implementation of what the phone does (used by tests)."""
    pt = AESGCM(key).decrypt(
        base64.b64decode(envelope["n"]), base64.b64decode(envelope["c"]), f"jarvis-push|{device_id}".encode()
    )
    data: dict[str, Any] = json.loads(pt)
    return data


@dataclass(slots=True)
class PushResult:
    device_id: str
    ok: bool
    error: str | None = None


class FcmSender:
    def __init__(self, cfg: FirebaseCfg) -> None:
        self.cfg = cfg
        self._creds: Any = None
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------ configuration
    def _service_account(self) -> dict[str, Any] | None:
        raw = secret_store.get(self.cfg.service_account_secret)
        if not raw:
            return None
        try:
            info: dict[str, Any] = json.loads(raw)
            return info
        except json.JSONDecodeError:
            logger.warning("FCM service account secret is not valid JSON")
            return None

    def project_id(self) -> str:
        sa = self._service_account() or {}
        return self.cfg.project_id or str(sa.get("project_id") or "")

    def configured(self) -> bool:
        return self._service_account() is not None and bool(self.project_id())

    def missing(self) -> list[str]:
        out = []
        if self._service_account() is None:
            out.append(f"Firebase-Dienstkonto (jarvisctl secret set {self.cfg.service_account_secret})")
        if not (self.cfg.api_key and self.cfg.app_id and self.cfg.sender_id and self.project_id()):
            out.append("Firebase-App-Konfiguration (mobile.firebase.* in jarvis.yaml)")
        return out

    def client_options(self) -> dict[str, str] | None:
        """Public Firebase client config for the app (identifiers, not secrets)."""
        if not (self.cfg.api_key and self.cfg.app_id and self.cfg.sender_id and self.project_id()):
            return None
        return {
            "apiKey": self.cfg.api_key,
            "appId": self.cfg.app_id,
            "messagingSenderId": self.cfg.sender_id,
            "projectId": self.project_id(),
        }

    async def _token(self) -> str:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        if self._creds is None:
            info = self._service_account()
            if info is None:
                raise RuntimeError("FCM nicht eingerichtet")
            self._creds = service_account.Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
                info, scopes=[FCM_SCOPE]
            )
        if not self._creds.valid:
            await asyncio.to_thread(self._creds.refresh, Request())
        return str(self._creds.token)

    # ------------------------------------------------------------------ sending
    async def send(self, token: str, data: dict[str, str], *, high_priority: bool, ttl_s: int) -> str | None:
        """Returns None on success, an error code otherwise (``UNREGISTERED`` → drop the token)."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15)
        body = {
            "message": {
                "token": token,
                "data": data,
                "android": {"priority": "high" if high_priority else "normal", "ttl": f"{max(0, ttl_s)}s"},
            }
        }
        r = await self._client.post(
            f"https://fcm.googleapis.com/v1/projects/{self.project_id()}/messages:send",
            headers={"Authorization": f"Bearer {await self._token()}"},
            json=body,
        )
        if r.status_code == 200:
            return None
        try:
            err = r.json().get("error", {})
            code = next((d.get("errorCode") for d in err.get("details", []) if d.get("errorCode")), err.get("status"))
        except (ValueError, AttributeError):
            code = None
        return str(code or f"HTTP {r.status_code}")

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()


class PushService:
    """Fans an encrypted message out to every paired, non-revoked phone with a push token."""

    def __init__(self, cfg: FirebaseCfg) -> None:
        self.fcm = FcmSender(cfg)

    async def targets(self) -> list[tuple[uuid.UUID, str]]:
        async with session_scope() as s:
            rows = (
                await s.execute(
                    select(MobileDevice.device_id, MobileDevice.push_token)
                    .join(Device, Device.id == MobileDevice.device_id)
                    .where(Device.revoked.is_(False), MobileDevice.push_token.is_not(None))
                )
            ).all()
        return [(r[0], str(r[1])) for r in rows]

    async def paired_count(self) -> int:
        async with session_scope() as s:
            rows = (
                await s.execute(
                    select(MobileDevice.device_id)
                    .join(Device, Device.id == MobileDevice.device_id)
                    .where(Device.revoked.is_(False))
                )
            ).all()
        return len(rows)

    async def send_all(
        self, kind: str, payload: dict[str, Any], *, high_priority: bool = False, ttl_s: int = 86400
    ) -> list[PushResult]:
        if not self.fcm.configured():
            return []
        msg_id = uuid.uuid4().hex
        now = int(time.time())
        body = {"id": msg_id, "type": kind, "ts": now, "exp": now + ttl_s, **payload}
        results: list[PushResult] = []
        for device_id, token in await self.targets():
            raw_key = secret_store.get(push_key_secret(device_id))
            if not raw_key:
                results.append(PushResult(str(device_id), False, "no push key"))
                continue
            try:
                envelope = encrypt_payload(base64.b64decode(raw_key), str(device_id), body)
                error = await self.fcm.send(token, envelope, high_priority=high_priority, ttl_s=ttl_s)
            except Exception as e:
                error = type(e).__name__ + ": " + str(e)[:160]
            if error in ("UNREGISTERED", "INVALID_ARGUMENT", "SENDER_ID_MISMATCH"):
                await self._drop_token(device_id)
            results.append(PushResult(str(device_id), error is None, error))
        return results

    async def _drop_token(self, device_id: uuid.UUID) -> None:
        async with session_scope() as s:
            mob = await s.get(MobileDevice, device_id)
            if mob is not None:
                mob.push_token = None
        logger.info("Dropped invalid push token of device %s", device_id)

    async def close(self) -> None:
        await self.fcm.close()
