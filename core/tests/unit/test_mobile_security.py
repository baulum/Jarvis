"""Mobile credentials: access tokens, device signatures (replay), push encryption, gateway allowlist."""

import base64
import os
import time
import uuid

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from jarvis.api.exposure import PublicExposureGuard
from jarvis.mobile.gateway import mobile_path_allowed
from jarvis.mobile.push import decrypt_payload, encrypt_payload
from jarvis.mobile.tokens import NonceCache, TokenSigner, sha256_hex, signed_message, verify_device_signature


def _keypair() -> tuple[ec.EllipticCurvePrivateKey, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    spki = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return key, base64.b64encode(spki).decode()


def _sign(key: ec.EllipticCurvePrivateKey, purpose: str, dev: str, ts: int, nonce: str, body: bytes) -> str:
    sig = key.sign(signed_message(purpose, dev, ts, nonce, sha256_hex(body)), ec.ECDSA(hashes.SHA256()))
    return base64.b64encode(sig).decode()


def test_access_token_roundtrip_and_tamper() -> None:
    s = TokenSigner(b"k" * 48)
    dev, fam = uuid.uuid4(), uuid.uuid4()
    tok, exp = s.mint(dev, "mobile_owner", "Pixel", fam, 600)
    claims = s.verify(tok)
    assert claims is not None and claims.device_id == dev and claims.role == "mobile_owner"
    assert exp > time.time()
    body, mac = tok[4:].split(".")
    forged = "jat_" + body[:-2] + ("AA" if body[-2:] != "AA" else "BB") + "." + mac
    assert s.verify(forged) is None
    assert TokenSigner(b"x" * 48).verify(tok) is None  # other key


def test_access_token_expires() -> None:
    s = TokenSigner(b"k" * 48)
    tok, _ = s.mint(uuid.uuid4(), "mobile_owner", "P", uuid.uuid4(), 1)
    assert s.verify(tok, now=time.time() + 5) is None


def test_role_cannot_be_escalated_in_payload() -> None:
    s = TokenSigner(b"k" * 48)
    tok, _ = s.mint(uuid.uuid4(), "mobile_owner", "P", uuid.uuid4(), 60)
    import json

    body, mac = tok[4:].split(".")
    data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    data["r"] = "owner"
    evil = base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    assert s.verify(f"jat_{evil}.{mac}") is None


def test_device_signature_and_replay() -> None:
    key, pub = _keypair()
    dev, ts, nonce, body = str(uuid.uuid4()), int(time.time()), os.urandom(12).hex(), b'{"approve":true}'
    sig = _sign(key, "approval:1", dev, ts, nonce, body)
    cache = NonceCache()
    args = (pub, "approval:1", dev, ts, nonce, sha256_hex(body), sig)
    assert verify_device_signature(*args, cache=cache)
    assert not verify_device_signature(*args, cache=cache)  # replay of the same request


def test_device_signature_rejects_wrong_purpose_body_key_and_old_ts() -> None:
    key, pub = _keypair()
    other, other_pub = _keypair()
    dev, ts, nonce, body = str(uuid.uuid4()), int(time.time()), os.urandom(12).hex(), b"{}"
    sig = _sign(key, "refresh", dev, ts, nonce, body)
    h = sha256_hex(body)
    assert not verify_device_signature(pub, "approval:x", dev, ts, nonce, h, sig, cache=NonceCache())
    assert not verify_device_signature(pub, "refresh", dev, ts, nonce, sha256_hex(b"[]"), sig, cache=NonceCache())
    assert not verify_device_signature(other_pub, "refresh", dev, ts, nonce, h, sig, cache=NonceCache())
    old = ts - 600
    sig_old = _sign(key, "refresh", dev, old, nonce, body)
    assert not verify_device_signature(pub, "refresh", dev, old, nonce, h, sig_old, cache=NonceCache())


def test_push_payload_is_encrypted_and_bound_to_device() -> None:
    key = os.urandom(32)
    env = encrypt_payload(key, "dev-1", {"type": "call", "call_id": "abc", "title": "Website fertig"})
    assert "Website" not in str(env) and "call" not in env["c"]
    assert decrypt_payload(key, "dev-1", env)["call_id"] == "abc"
    with pytest.raises(Exception):
        decrypt_payload(key, "dev-2", env)


@pytest.mark.parametrize(
    ("method", "path", "allowed"),
    [
        ("GET", "/api/health", True),
        ("POST", "/api/mobile/pair", True),
        ("POST", "/api/chat", True),
        ("GET", "/ws", True),
        ("GET", "/rtc", True),
        ("GET", "/api/approvals", True),
        ("POST", "/api/approvals/0b7e6a7e-0000-0000-0000-000000000000", False),  # unsigned decide
        ("PUT", "/api/approvals/mode", False),
        ("POST", "/api/connectors/lexware", False),
        ("GET", "/api/audit", False),
        ("POST", "/mcp", False),
        ("DELETE", "/api/memory/items/0b7e6a7e-0000-0000-0000-000000000000", False),
        ("POST", "/api/devices", False),
        ("GET", "/api/memory/export", False),
    ],
)
def test_gateway_allowlist(method: str, path: str, allowed: bool) -> None:
    assert mobile_path_allowed(method, path) is allowed


async def ok(request: object) -> PlainTextResponse:
    return PlainTextResponse("ok")


def test_exposure_guard_on_gateway_port() -> None:
    app = Starlette(
        routes=[Route("/api/health", ok), Route("/api/audit", ok), Route("/api/telephony/twilio/voice", ok, methods=["POST"])]
    )
    app.add_middleware(PublicExposureGuard, mobile_port=8767)
    phone = TestClient(app, base_url="https://192.168.1.20:8767")
    assert phone.get("/api/health").status_code == 200
    assert phone.get("/api/audit").status_code == 404
    assert phone.post("/api/telephony/twilio/voice").status_code == 404  # not a mobile path
    assert phone.get("/api/health", headers={"cf-ray": "1"}).status_code == 404  # tunnel traffic never
    local = TestClient(app, base_url="http://127.0.0.1:8765")
    assert local.get("/api/audit").status_code == 200
