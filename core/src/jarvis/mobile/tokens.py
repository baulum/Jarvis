"""Mobile credentials: short-lived access tokens, rotating refresh tokens, device-key signatures.

* **Access token** ``jat_<payload>.<mac>`` – HMAC-SHA256 over a compact JSON payload (device, role, family,
  expiry). Valid ``mobile.access_token_ttl_s`` (10 min). Verification needs no DB round trip except the
  revocation check of the device (cached for a few seconds).
* **Refresh token** ``jrt_…`` – random, stored only as SHA-256, single use. Every refresh issues a new one in
  the same *family*; presenting an already used token revokes the whole family (token theft detection).
* **Device binding** – a refresh (and every approval from the phone) must be signed with the phone's
  EC P-256 key that never leaves the Android Keystore. Signed message: ``purpose|device|ts|nonce|body-hash``;
  timestamps older than 120 s and repeated nonces are rejected (replay protection).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_der_public_key
from sqlalchemy import select, update

from jarvis.db import session_scope
from jarvis.db.models import Device, MobileDevice, MobileRefreshToken
from jarvis.secrets import secret_store

TOKEN_KEY_SECRET = "mobile-token-key"  # noqa: S105 - Keychain account name
ACCESS_PREFIX = "jat_"
REFRESH_PREFIX = "jrt_"
SIGNATURE_MAX_SKEW_S = 120
REFRESH_GRACE_S = 30  # a retry of a refresh whose response got lost (mobile network) must not kill the session


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sha256_hex(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


@dataclass(frozen=True, slots=True)
class AccessClaims:
    device_id: uuid.UUID
    role: str
    name: str
    family: uuid.UUID
    exp: int


class TokenSigner:
    def __init__(self, key: bytes | None = None) -> None:
        self._key = key

    @property
    def key(self) -> bytes:
        if self._key is None:
            self._key = secret_store.ensure_random(TOKEN_KEY_SECRET, 48).encode()
        return self._key

    def mint(self, device_id: uuid.UUID, role: str, name: str, family: uuid.UUID, ttl_s: int) -> tuple[str, int]:
        exp = int(time.time()) + ttl_s
        payload = json.dumps(
            {"d": str(device_id), "r": role, "n": name, "f": str(family), "e": exp}, separators=(",", ":")
        ).encode()
        body = _b64(payload)
        mac = _b64(hmac.new(self.key, body.encode(), hashlib.sha256).digest())
        return f"{ACCESS_PREFIX}{body}.{mac}", exp

    def verify(self, token: str, now: float | None = None) -> AccessClaims | None:
        if not token.startswith(ACCESS_PREFIX) or "." not in token:
            return None
        body, _, mac = token[len(ACCESS_PREFIX) :].partition(".")
        expected = _b64(hmac.new(self.key, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(mac, expected):
            return None
        try:
            data = json.loads(_unb64(body))
            claims = AccessClaims(
                uuid.UUID(data["d"]), str(data["r"]), str(data["n"]), uuid.UUID(data["f"]), int(data["e"])
            )
        except (ValueError, KeyError, TypeError):
            return None
        if claims.exp < (now or time.time()):
            return None
        return claims


signer = TokenSigner()


# --------------------------------------------------------------------------- device signatures
class NonceCache:
    """Remembers nonces for the signature window – a captured request cannot be replayed."""

    def __init__(self, max_items: int = 5000) -> None:
        self._seen: OrderedDict[str, float] = OrderedDict()
        self.max_items = max_items

    def use(self, nonce: str, now: float | None = None) -> bool:
        now = now or time.time()
        while self._seen and next(iter(self._seen.values())) < now - 2 * SIGNATURE_MAX_SKEW_S:
            self._seen.popitem(last=False)
        if nonce in self._seen or len(nonce) < 16:
            return False
        self._seen[nonce] = now
        if len(self._seen) > self.max_items:
            self._seen.popitem(last=False)
        return True


nonces = NonceCache()


def signed_message(purpose: str, device_id: str, ts: int, nonce: str, body_hash: str) -> bytes:
    return f"{purpose}|{device_id}|{ts}|{nonce}|{body_hash}".encode()


def verify_device_signature(
    public_key_b64: str,
    purpose: str,
    device_id: str,
    ts: int,
    nonce: str,
    body_hash: str,
    signature_b64: str,
    now: float | None = None,
    cache: NonceCache | None = None,
) -> bool:
    """ECDSA P-256/SHA-256 (DER signature, as produced by ``Signature.getInstance("SHA256withECDSA")``)."""
    now = now or time.time()
    if abs(now - ts) > SIGNATURE_MAX_SKEW_S:
        return False
    try:
        key = load_der_public_key(base64.b64decode(public_key_b64))
        if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
            return False
        key.verify(
            base64.b64decode(signature_b64),
            signed_message(purpose, device_id, ts, nonce, body_hash),
            ec.ECDSA(hashes.SHA256()),
        )
    except (InvalidSignature, ValueError, TypeError):
        return False
    return (cache or nonces).use(nonce, now)


# --------------------------------------------------------------------------- refresh tokens
class RefreshError(PermissionError):
    pass


async def issue_refresh(device_id: uuid.UUID, family: uuid.UUID, ttl_days: int) -> str:
    token = REFRESH_PREFIX + secrets.token_urlsafe(32)
    async with session_scope() as s:
        s.add(
            MobileRefreshToken(
                device_id=device_id,
                family=family,
                token_hash=sha256_hex(token),
                expires_at=datetime.now(UTC) + timedelta(days=ttl_days),
            )
        )
    return token


async def rotate_refresh(token: str, device_id: uuid.UUID, ttl_days: int) -> tuple[uuid.UUID, str, Device]:
    """Spend ``token`` and return (family, new refresh token, device). Re-use revokes the family."""
    if not token.startswith(REFRESH_PREFIX):
        raise RefreshError("invalid refresh token")
    now = datetime.now(UTC)
    async with session_scope() as s:
        row = (
            await s.execute(
                select(MobileRefreshToken).where(MobileRefreshToken.token_hash == sha256_hex(token)).with_for_update()
            )
        ).scalar_one_or_none()
        if row is None or row.device_id != device_id:
            raise RefreshError("invalid refresh token")
        if row.revoked or (row.used_at is not None and (now - row.used_at).total_seconds() > REFRESH_GRACE_S):
            reused_family = row.family
        else:
            reused_family = None
    if reused_family is not None:  # own transaction: raising inside the one above would roll the revocation back
        async with session_scope() as s:
            await s.execute(
                update(MobileRefreshToken).where(MobileRefreshToken.family == reused_family).values(revoked=True)
            )
        raise RefreshError("refresh token reuse detected – session revoked")
    async with session_scope() as s:
        row = (
            await s.execute(
                select(MobileRefreshToken).where(MobileRefreshToken.token_hash == sha256_hex(token)).with_for_update()
            )
        ).scalar_one()
        if row.revoked:
            raise RefreshError("refresh token revoked")
        if row.expires_at < now:
            raise RefreshError("refresh token expired")
        device = await s.get(Device, device_id)
        if device is None or device.revoked:
            raise RefreshError("device revoked")
        row.used_at = row.used_at or now
        family = row.family
        new = REFRESH_PREFIX + secrets.token_urlsafe(32)
        s.add(
            MobileRefreshToken(
                device_id=device_id,
                family=family,
                token_hash=sha256_hex(new),
                expires_at=now + timedelta(days=ttl_days),
            )
        )
        s.expunge(device)
    return family, new, device


async def revoke_mobile_device(device_id: uuid.UUID) -> None:
    async with session_scope() as s:
        await s.execute(
            update(MobileRefreshToken).where(MobileRefreshToken.device_id == device_id).values(revoked=True)
        )
        dev = await s.get(Device, device_id)
        if dev is not None:
            dev.revoked = True
        mob = await s.get(MobileDevice, device_id)
        if mob is not None:
            mob.push_token = None
    _revoked_cache.pop(device_id, None)


# --------------------------------------------------------------------------- access verification
_revoked_cache: dict[uuid.UUID, tuple[bool, float]] = {}


async def device_active(device_id: uuid.UUID) -> bool:
    cached = _revoked_cache.get(device_id)
    if cached and cached[1] > time.monotonic():
        return cached[0]
    async with session_scope() as s:
        dev = await s.get(Device, device_id)
        active = dev is not None and not dev.revoked
        if dev is not None and active:
            dev.last_seen_at = datetime.now(UTC)
    _revoked_cache[device_id] = (active, time.monotonic() + 5)
    return active
