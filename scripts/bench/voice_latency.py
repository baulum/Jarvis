"""Measures every stage of the voice pipeline against the running core.

    cd core && uv run python ../scripts/bench/voice_latency.py [--wav q.wav] [--runs 3] [--tts-only]

Stages (all wall clock, ms):
  stt            POST /api/voice/stt with a WAV (whisper.cpp)
  recall         GET  /api/memory/search (embedding + pgvector)
  agent_ttft     POST /api/chat → first chat.delta on the WebSocket
  agent_first_sentence  → first complete sentence (what the old daemon waited for)
  agent_done     → chat.done
  tts_ttfb       POST /api/voice/tts → first PCM byte (HTTP streaming)
  tts_total      → last PCM byte
  tts_ws_ttfb    Fish Audio WebSocket (if available): first audio chunk
The VAD end-of-turn silence (voice.end_of_turn_silence_ms) is added as a fixed cost.
Nothing here speaks through the speakers; each Fish Audio call bills a few characters.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import uuid
from pathlib import Path

import httpx
import websockets

from jarvis.config import get_settings
from jarvis.voice.speech import split_sentences


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


async def bench_stt(http: httpx.AsyncClient, wav: bytes) -> tuple[float, str]:
    t0 = time.perf_counter()
    r = await http.post("/api/voice/stt", content=wav, headers={"Content-Type": "audio/wav"})
    r.raise_for_status()
    return _ms(t0), r.json().get("text", "")


async def bench_recall(http: httpx.AsyncClient, q: str) -> float:
    t0 = time.perf_counter()
    r = await http.get("/api/memory/search", params={"q": q, "limit": 4})
    r.raise_for_status()
    return _ms(t0)


async def bench_agent(http: httpx.AsyncClient, ws_url: str, text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    async with websockets.connect(ws_url) as ws:
        await ws.recv()  # hello
        t0 = time.perf_counter()
        r = await http.post("/api/chat", json={"message": text, "channel": "voice"})
        r.raise_for_status()
        sid = r.json()["session_id"]
        out["accept"] = _ms(t0)
        buf = ""
        async for raw in ws:
            ev = json.loads(raw)
            d = ev.get("data", {})
            if d.get("session_id") != sid:
                continue
            if ev["type"] == "chat.delta":
                out.setdefault("ttft", _ms(t0))
                buf += d.get("text", "")
                if "first_sentence" not in out and split_sentences(buf)[0]:
                    out["first_sentence"] = _ms(t0)
            elif ev["type"] == "chat.done":
                out["done"] = _ms(t0)
                out.setdefault("first_sentence", out["done"])
                break
    return out


async def bench_tts_http(http: httpx.AsyncClient, text: str) -> dict[str, float]:
    t0 = time.perf_counter()
    first = None
    n = 0
    async with http.stream("POST", "/api/voice/tts", json={"text": text, "sample_rate": 24000}) as r:
        async for chunk in r.aiter_bytes():
            if chunk and first is None:
                first = _ms(t0)
            n += len(chunk)
    return {"ttfb": first or -1, "total": _ms(t0), "audio_ms": round(n / 2 / 24, 1)}


async def bench_tts_ws(text: str) -> dict[str, float] | None:
    from jarvis.voice.fish_stream import FishStreamSession  # noqa: PLC0415 - optional new module

    s = get_settings()
    sess = FishStreamSession.from_settings(s)
    if sess is None:
        return None
    t0 = time.perf_counter()
    first = None
    n = 0
    await sess.open()
    connected = _ms(t0)
    t1 = time.perf_counter()
    await sess.send_text(text)
    await sess.flush()
    async for chunk in sess.audio():
        if first is None:
            first = _ms(t1)
        n += len(chunk)
    await sess.close()
    return {"connect": connected, "ttfb_after_connect": first or -1, "total": _ms(t0), "audio_ms": round(n / 2 / 24, 1)}


def _summary(name: str, values: list[float]) -> str:
    if not values:
        return f"{name:24s} –"
    return f"{name:24s} median {statistics.median(values):8.1f}  min {min(values):8.1f}  max {max(values):8.1f}"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", type=Path)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--question", default="Welcher Wochentag ist heute?")
    ap.add_argument("--tts-text", default="Heute ist Freitag, Sir.")
    ap.add_argument("--tts-only", action="store_true")
    ap.add_argument("--ws", action="store_true", help="also measure the Fish Audio WebSocket session")
    a = ap.parse_args()

    s = get_settings()
    token = (s.paths.data_dir / "desktop.token").read_text().strip()
    base = f"http://127.0.0.1:{s.server.port}"
    ws_url = base.replace("http", "ws") + f"/ws?token={token}"
    res: dict[str, list[float]] = {}

    def add(k: str, v: float) -> None:
        res.setdefault(k, []).append(v)

    async with httpx.AsyncClient(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=120) as http:
        for i in range(a.runs):
            if not a.tts_only:
                if a.wav:
                    ms, text = await bench_stt(http, a.wav.read_bytes())
                    add("stt", ms)
                    if i == 0:
                        print(f"STT heard: {text!r}")
                add("recall", await bench_recall(http, a.question))
                ag = await bench_agent(http, ws_url, f"{a.question} (Messlauf {uuid.uuid4().hex[:4]})")
                for k, v in ag.items():
                    add(f"agent_{k}", v)
            tts = await bench_tts_http(http, a.tts_text)
            add("tts_http_ttfb", tts["ttfb"])
            add("tts_http_total", tts["total"])
            if a.ws:
                w = await bench_tts_ws(a.tts_text)
                if w:
                    for k, v in w.items():
                        add(f"tts_ws_{k}", v)
    print(f"\nVAD end-of-turn silence (config): {s.voice.end_of_turn_silence_ms} ms")
    for k, v in res.items():
        print(_summary(k, v))


if __name__ == "__main__":
    asyncio.run(main())
