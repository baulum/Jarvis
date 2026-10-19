"""Pairing, token rotation, reuse detection and revocation against the test database."""

import base64
import os
import time
import uuid
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from jarvis.mobile import pairing as pairing_mod
from jarvis.mobile.pairing import PairingError, PairingService
from jarvis.mobile.tokens import RefreshError, revoke_mobile_device, rotate_refresh, sha256_hex, signed_message
from jarvis.security.auth import authenticate_token

pytestmark = pytest.mark.integration


class MemorySecrets:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def set(self, name: str, value: str) -> None:
        self.data[name] = value

    def get(self, name: str) -> str | None:
        return self.data.get(name)


@pytest.fixture(autouse=True)
def fake_secrets(monkeypatch: pytest.MonkeyPatch) -> MemorySecrets:
    store = MemorySecrets()
    monkeypatch.setattr(pairing_mod, "secret_store", store)
    return store


def _device_key() -> tuple[ec.EllipticCurvePrivateKey, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return key, base64.b64encode(spki).decode()


async def _pair(svc: PairingService, code: str, key: ec.EllipticCurvePrivateKey, pub: str) -> dict[str, Any]:
    ts, nonce = int(time.time()), os.urandom(12).hex()
    sig = key.sign(signed_message("pair", "-", ts, nonce, sha256_hex(code)), ec.ECDSA(hashes.SHA256()))
    return await svc.pair(
        code=code,
        name="Pixel Test",
        platform="android",
        model="Pixel 9",
        app_version="1.0.0",
        public_key=pub,
        ts=ts,
        nonce=nonce,
        signature=base64.b64encode(sig).decode(),
        access_ttl_s=600,
        refresh_ttl_days=30,
        client_ip="192.168.1.20",
    )


async def _age_refresh_use(token: str) -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from jarvis.db import session_scope
    from jarvis.db.models import MobileRefreshToken
    from jarvis.mobile.tokens import sha256_hex

    async with session_scope() as s:
        await s.execute(
            update(MobileRefreshToken)
            .where(MobileRefreshToken.token_hash == sha256_hex(token))
            .values(used_at=datetime.now(UTC) - timedelta(minutes=5))
        )


async def test_pair_refresh_rotation_and_reuse_detection(fake_secrets: MemorySecrets) -> None:
    svc = PairingService()
    code, _ = svc.create("device:desktop")
    key, pub = _device_key()
    res = await _pair(svc, code, key, pub)
    dev = uuid.UUID(res["device_id"])
    assert fake_secrets.get(f"mobile-push-{dev}")  # push encryption key stored server-side
    p = await authenticate_token(res["access_token"])
    assert p is not None and p.role == "mobile_owner" and p.channel == "mobile" and p.device_id == dev

    with pytest.raises(PairingError):  # one-time code
        await _pair(svc, code, key, pub)

    _, second, _ = await rotate_refresh(res["refresh_token"], dev, 30)
    _, third, _ = await rotate_refresh(second, dev, 30)
    # a retry right after (lost response on a flaky mobile network) is tolerated, not treated as theft
    _, retry, _ = await rotate_refresh(second, dev, 30)
    assert retry != third
    await _age_refresh_use(second)
    with pytest.raises(RefreshError, match="reuse"):  # old token used again after the grace window = theft
        await rotate_refresh(second, dev, 30)
    with pytest.raises(RefreshError):  # … which revoked the whole family, including the newest token
        await rotate_refresh(third, dev, 30)


async def test_pair_requires_proof_of_key_possession() -> None:
    svc = PairingService()
    code, _ = svc.create("device:desktop")
    key, _ = _device_key()
    _, other_pub = _device_key()
    with pytest.raises(PairingError, match="signatur"):
        await _pair(svc, code, key, other_pub)


async def test_refresh_bound_to_device_and_revocation() -> None:
    svc = PairingService()
    key, pub = _device_key()
    a = await _pair(svc, svc.create("x")[0], key, pub)
    b = await _pair(svc, svc.create("x")[0], key, pub)
    with pytest.raises(RefreshError):
        await rotate_refresh(a["refresh_token"], uuid.UUID(b["device_id"]), 30)
    dev = uuid.UUID(a["device_id"])
    await revoke_mobile_device(dev)
    assert await authenticate_token(a["access_token"]) is None
    with pytest.raises(RefreshError):
        await rotate_refresh(a["refresh_token"], dev, 30)
