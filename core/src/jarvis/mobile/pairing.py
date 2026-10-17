"""Pairing a phone: one-time code from the owner (desktop/CLI) → device with its own Keystore key.

The QR code carries ``{"t": "jarvis-pair", "v": 1, "urls": [...], "fp": <cert sha256>, "code": <one-time>}``.
The phone proves possession of its new EC key by signing the pairing request; the core then creates a
``devices`` row (role ``mobile_owner``), the mobile metadata, a per-device AES key for end-to-end encrypted
push payloads (Google only sees ciphertext) and the first token pair.
"""

from __future__ import annotations

import base64
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any

from jarvis.db import session_scope
from jarvis.db.models import Device, MobileDevice
from jarvis.mobile.tokens import issue_refresh, sha256_hex, signer, verify_device_signature
from jarvis.secrets import secret_store
from jarvis.security.auth import hash_token

MOBILE_ROLE = "mobile_owner"


def push_key_secret(device_id: uuid.UUID | str) -> str:
    return f"mobile-push-{device_id}"


@dataclass(slots=True)
class PendingPairing:
    role: str
    expires: float
    created_by: str


class PairingError(PermissionError):
    pass


class PairingService:
    def __init__(self, ttl_s: int = 600) -> None:
        self.ttl_s = ttl_s
        self._pending: dict[str, PendingPairing] = {}

    def create(self, created_by: str, role: str = MOBILE_ROLE) -> tuple[str, float]:
        self._gc()
        code = secrets.token_urlsafe(18)
        expires = time.time() + self.ttl_s
        self._pending[sha256_hex(code)] = PendingPairing(role, expires, created_by)
        return code, expires

    def _gc(self) -> None:
        now = time.time()
        for k in [k for k, v in self._pending.items() if v.expires < now]:
            del self._pending[k]

    def consume(self, code: str) -> PendingPairing:
        self._gc()
        entry = self._pending.pop(sha256_hex(code), None)
        if entry is None:
            raise PairingError("Kopplungscode ungültig oder abgelaufen")
        return entry

    async def pair(
        self,
        *,
        code: str,
        name: str,
        platform: str,
        model: str,
        app_version: str,
        public_key: str,
        ts: int,
        nonce: str,
        signature: str,
        access_ttl_s: int,
        refresh_ttl_days: int,
        client_ip: str | None,
    ) -> dict[str, Any]:
        if not verify_device_signature(public_key, "pair", "-", ts, nonce, sha256_hex(code), signature):
            raise PairingError("Gerätesignatur ungültig")
        entry = self.consume(code)
        push_key = secrets.token_bytes(32)
        async with session_scope() as s:
            # The device row's token_hash is a random placeholder: phones never use static jdv_ tokens.
            device = Device(name=name[:120], role=entry.role, token_hash=hash_token(secrets.token_urlsafe(32)))
            s.add(device)
            await s.flush()
            device_id = device.id
            s.add(
                MobileDevice(
                    device_id=device_id,
                    platform=platform[:20],
                    model=model[:120],
                    app_version=app_version[:40],
                    public_key=public_key,
                    push_key_id="1",
                    last_ip=client_ip,
                )
            )
        secret_store.set(push_key_secret(device_id), base64.b64encode(push_key).decode())
        family = uuid.uuid4()
        access, exp = signer.mint(device_id, entry.role, name[:120], family, access_ttl_s)
        refresh = await issue_refresh(device_id, family, refresh_ttl_days)
        return {
            "device_id": str(device_id),
            "role": entry.role,
            "access_token": access,
            "access_expires_at": exp,
            "refresh_token": refresh,
            "push_key": base64.b64encode(push_key).decode(),
            "push_key_id": "1",
            "paired_by": entry.created_by,
        }
