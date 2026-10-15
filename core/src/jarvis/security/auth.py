"""Device authentication.

Each client (desktop app, voice daemon, CLI, iPhone later) is a *device* with a role and a
random bearer token. Only the SHA-256 hash of the token is stored. Tokens are shown once at
pairing time; the desktop app/voice daemon store theirs in the Keychain.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from jarvis.db import session_scope
from jarvis.db.models import Device


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated actor behind a request or tool call."""

    id: str
    role: str
    channel: str
    device_id: uuid.UUID | None = None

    @property
    def label(self) -> str:
        return f"{self.channel}:{self.id}"


SYSTEM = Principal(id="system", role="system", channel="system")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_device(name: str, role: str) -> tuple[Device, str]:
    token = "jdv_" + secrets.token_urlsafe(32)
    async with session_scope() as s:
        device = Device(name=name, role=role, token_hash=hash_token(token))
        s.add(device)
        await s.flush()
    return device, token


async def authenticate_token(token: str | None) -> Principal | None:
    if not token or not token.startswith("jdv_"):
        return None
    digest = hash_token(token)
    async with session_scope() as s:
        device = (await s.execute(select(Device).where(Device.token_hash == digest))).scalar_one_or_none()
        if device is None or device.revoked or not hmac.compare_digest(device.token_hash, digest):
            return None
        device.last_seen_at = datetime.now(UTC)
        return Principal(id=device.name, role=device.role, channel="device", device_id=device.id)


async def revoke_device(device_id: uuid.UUID) -> bool:
    async with session_scope() as s:
        device = await s.get(Device, device_id)
        if device is None:
            return False
        device.revoked = True
        return True


async def list_devices() -> list[Device]:
    async with session_scope() as s:
        return list((await s.execute(select(Device).order_by(Device.created_at))).scalars())
