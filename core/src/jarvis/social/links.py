"""Signed, time-limited download links for a video (``GET /api/social/download/<token>``).

Token = base64url("<video-id>.<expiry-unix>") + "." + HMAC-SHA256 over it, key ``social-link-key`` in the
Keychain (created on first use, never leaves it). No database row, nothing to clean up; a changed key revokes all."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import uuid

from jarvis.secrets import secret_store

KEY_NAME = "social-link-key"


def _key() -> bytes:
    key = secret_store.get(KEY_NAME)
    if not key:
        key = secrets.token_urlsafe(48)
        secret_store.set(KEY_NAME, key)
    return key.encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def sign(video_id: uuid.UUID, ttl_s: int, now: float | None = None) -> str:
    payload = _b64(f"{video_id}.{int((now or time.time()) + ttl_s)}".encode())
    mac = _b64(hmac.new(_key(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{mac}"


def verify(token: str, now: float | None = None) -> uuid.UUID | None:
    try:
        payload, mac = token.split(".", 1)
        expected = _b64(hmac.new(_key(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(mac, expected):
            return None
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
        vid, exp = raw.rsplit(".", 1)
        if int(exp) < (now or time.time()):
            return None
        return uuid.UUID(vid)
    except (ValueError, UnicodeDecodeError):
        return None
