"""End-to-end: a simulated phone pairs through the TLS gateway, calls Jarvis over LiveKit and asks a question.

Needs the running core (gateway :8767, LiveKit), whisper and the agent. The "phone" speaks with the macOS
voice (``say``), so the full path is real: gateway auth → call session → LiveKit room → Jarvis voice agent →
Whisper → Claude → Fish TTS → audio back. Run: ``uv run pytest -m e2e tests/e2e/test_mobile_call.py -s``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import ssl
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from jarvis.mobile.tokens import sha256_hex, signed_message

pytestmark = pytest.mark.e2e
DATA = Path.home() / "Library/Application Support/Jarvis"
CORE = "http://127.0.0.1:8765"
GATEWAY = "https://127.0.0.1:8767"


def _desktop() -> dict[str, str]:
    return {"Authorization": "Bearer " + (DATA / "desktop.token").read_text().strip()}


def _pinned_client(fp: str) -> httpx.AsyncClient:
    """Like the app: accept the gateway certificate only if its SHA-256 matches the pin from the QR code."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    transport = httpx.AsyncHTTPTransport(verify=ctx)
    client = httpx.AsyncClient(base_url=GATEWAY, transport=transport, timeout=60)
    cert_der = ssl.PEM_cert_to_DER_cert(ssl.get_server_certificate(("127.0.0.1", 8767)))
    import hashlib

    assert hashlib.sha256(cert_der).hexdigest() == fp, "certificate pin mismatch"
    return client


def _say(text: str, rate: int = 16000) -> np.ndarray:
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "q.wav"
        subprocess.run(
            ["say", "-v", "Anna", "-o", str(out), f"--data-format=LEI16@{rate}", text], check=True
        )
        with wave.open(str(out)) as w:
            return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


async def _pair(client: httpx.AsyncClient, code: str) -> tuple[dict[str, Any], ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    pub = base64.b64encode(
        key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    ).decode()
    ts, nonce = int(time.time()), os.urandom(12).hex()
    sig = key.sign(signed_message("pair", "-", ts, nonce, sha256_hex(code)), ec.ECDSA(hashes.SHA256()))
    r = await client.post(
        "/api/mobile/pair",
        json={
            "code": code,
            "name": "E2E-Testtelefon",
            "model": "pytest",
            "app_version": "e2e",
            "public_key": pub,
            "ts": ts,
            "nonce": nonce,
            "signature": base64.b64encode(sig).decode(),
        },
    )
    r.raise_for_status()
    return r.json(), key


async def _refresh(client: httpx.AsyncClient, creds: dict[str, Any], key: ec.EllipticCurvePrivateKey) -> dict[str, Any]:
    body = json.dumps({"device_id": creds["device_id"], "refresh_token": creds["refresh_token"]}).encode()
    ts, nonce = int(time.time()), os.urandom(12).hex()
    sig = key.sign(signed_message("refresh", creds["device_id"], ts, nonce, sha256_hex(body)), ec.ECDSA(hashes.SHA256()))
    r = await client.post(
        "/api/mobile/token",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Jarvis-Ts": str(ts),
            "X-Jarvis-Nonce": nonce,
            "X-Jarvis-Signature": base64.b64encode(sig).decode(),
        },
    )
    r.raise_for_status()
    return dict(r.json())


async def test_phone_calls_jarvis_over_livekit() -> None:
    from livekit import rtc

    async with httpx.AsyncClient(base_url=CORE, headers=_desktop()) as desk:
        pairing = (await desk.post("/api/mobile/pairings", json={})).json()
    client = _pinned_client(pairing["fingerprint"])
    creds, key = await _pair(client, pairing["code"])
    device_id = creds["device_id"]
    try:
        new = await _refresh(client, creds, key)
        assert new["refresh_token"] != creds["refresh_token"]
        auth = {"Authorization": f"Bearer {new['access_token']}"}
        assert (await client.get("/api/audit", headers=auth)).status_code == 404  # not on the gateway
        av = (await client.get("/api/mobile/calls/availability", headers=auth)).json()
        print("availability:", av)
        assert av["can_call_jarvis"], av["problems"]

        t_call = time.perf_counter()
        info = (await client.post("/api/mobile/calls", headers=auth)).json()
        assert info["via_gateway"] and info["token"]

        room = rtc.Room()
        events: list[dict[str, Any]] = []
        heard: list[float] = []
        first_audio: dict[str, float] = {}

        def on_data(pkt: rtc.DataPacket) -> None:
            if pkt.topic == "jarvis":
                msg = json.loads(pkt.data)
                msg["_t"] = time.perf_counter()
                events.append(msg)
                print("  data:", {k: v for k, v in msg.items() if k != "_t"})

        async def listen(track: rtc.Track) -> None:
            async for ev in rtc.AudioStream(track, sample_rate=16000, num_channels=1):
                pcm = np.frombuffer(ev.frame.data, dtype=np.int16)
                if pcm.size and float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2))) > 300:
                    heard.append(time.perf_counter())
                    first_audio.setdefault("t", time.perf_counter())

        tasks: list[asyncio.Task[None]] = []
        room.on("data_received", on_data)
        room.on(
            "track_subscribed",
            lambda track, pub, part: tasks.append(asyncio.ensure_future(listen(track)))
            if track.kind == rtc.TrackKind.KIND_AUDIO
            else None,
        )
        await room.connect("ws://127.0.0.1:7880", info["token"])
        source = rtc.AudioSource(16000, 1, queue_size_ms=60)  # real-time pacing → honest timings
        mic = rtc.LocalAudioTrack.create_audio_track("mic", source)
        await room.local_participant.publish_track(mic, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))

        async def stream(pcm: np.ndarray) -> None:
            frame = 320
            for i in range(0, len(pcm) - frame, frame):
                await source.capture_frame(rtc.AudioFrame(pcm[i : i + frame].tobytes(), 16000, 1, frame))

        async def silence(seconds: float) -> None:
            await stream(np.zeros(int(16000 * seconds), dtype=np.int16))

        # 1. greeting
        deadline = time.perf_counter() + 25
        while not any(e.get("type") == "transcript" and e.get("role") == "jarvis" for e in events):
            await silence(0.2)
            assert time.perf_counter() < deadline, "no greeting"
        greet_ms = (first_audio.get("t", time.perf_counter()) - t_call) * 1000
        while any(e.get("state") == "speaking" for e in events[-1:]):
            await silence(0.2)
        await silence(1.0)

        # 2. a question
        question = _say("Jarvis, wie viel ist drei plus vier?")
        n_before = len(events)
        await stream(question)
        t_spoken = time.perf_counter()
        heard.clear()
        deadline = time.perf_counter() + 60
        reply = None
        while reply is None:
            await silence(0.2)
            reply = next(
                (e for e in events[n_before:] if e.get("type") == "transcript" and e.get("role") == "jarvis"), None
            )
            assert time.perf_counter() < deadline, f"no reply; events={events[n_before:]}"
        user = next(e for e in events[n_before:] if e.get("type") == "transcript" and e.get("role") == "user")
        first_reply_audio = min((t for t in heard if t > t_spoken), default=None)
        print(f"\n  greeting audio after call start: {greet_ms:.0f} ms")
        print(f"  Sir: {user['text']!r}\n  Jarvis: {reply['text']!r}")
        if first_reply_audio:
            print(f"  end of question → first reply audio: {(first_reply_audio - t_spoken) * 1000:.0f} ms")
        assert "7" in reply["text"] or "sieben" in reply["text"].lower()
        assert first_reply_audio is not None, "reply text arrived but no audio was heard"

        # 3. hang up
        r = await client.post(f"/api/mobile/calls/{info['call_id']}/hangup", headers=auth)
        assert r.status_code == 200
        await room.disconnect()
        for t in tasks:
            t.cancel()
        await asyncio.sleep(2)
        hist = (await client.get("/api/mobile/calls", headers=auth)).json()
        mine = next(c for c in hist if c["id"] == info["call_id"])
        assert mine["status"] == "ended" and len(mine["transcript"]) >= 3

        # 4. LiveKit signalling through the TLS gateway (what the Android app uses)
        info = (await client.post("/api/mobile/calls", headers=auth)).json()
        # LiveKit signalling through the TLS gateway: the handshake must reach LiveKit (pinned TLS)
        import websockets

        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        async with websockets.connect(
            f"wss://127.0.0.1:8767/rtc?access_token={info['token']}&protocol=16&auto_subscribe=1", ssl=ssl_ctx
        ) as ws:
            first = await asyncio.wait_for(ws.recv(), 10)
            assert isinstance(first, bytes) and len(first) > 20  # SignalResponse(join) protobuf

        await client.post(f"/api/mobile/calls/{info['call_id']}/hangup", headers=auth)
    finally:
        async with httpx.AsyncClient(base_url=CORE, headers=_desktop()) as desk:
            await desk.delete(f"/api/mobile/devices/{device_id}")
        await client.aclose()
