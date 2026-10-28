"""E2E: synthetic speech → wake word → endpointing + speculative STT → Claude (core) → streamed TTS.

Requires the running stack (core, whisper-server). Microphone and speakers are replaced by
``SimAudio`` (injected microphone frames, playback that takes real time), so no macOS permission is
needed and nothing is audible. Each spoken reply bills a few characters of Fish Audio if it is active.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import tempfile
import time

import numpy as np
import pytest

from jarvis.config import get_settings
from jarvis.voice.daemon import VoiceDaemon
from jarvis.voice.wakeword import CHUNK, RATE

pytestmark = pytest.mark.e2e


def synth(text: str, voice: str = "Anna") -> np.ndarray:
    with tempfile.TemporaryDirectory() as td:
        path = f"{td}/a.aiff"
        subprocess.run(["say", "-v", voice, "-o", path, text], check=True)
        raw = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", path, "-ar", str(RATE), "-ac", "1", "-f", "s16le", "-"],
            capture_output=True,
            check=True,
        ).stdout
    return np.frombuffer(raw, dtype=np.int16)


class SimAudio:
    """AudioIO stand-in: playback takes real time (24 kHz), flush stops it at once."""

    aec = True
    in_level = 0.0

    def __init__(self) -> None:
        self.played: list[bytes] = []
        self.flushes: list[float] = []
        self._until = 0.0
        self._gen = 0

    @property
    def out_level(self) -> float:
        return 0.3 if self.playing else 0.0

    async def start(self, on_mic) -> None:  # mic frames are put into mic_q by the test
        pass

    async def stop(self) -> None:
        pass

    async def play(self, pcm: bytes) -> None:
        self.played.append(pcm)
        self._until = max(self._until, time.monotonic()) + len(pcm) / 2 / 24000

    async def flush(self) -> None:
        self.flushes.append(time.monotonic())
        self._until = 0.0
        self._gen += 1

    async def hold(self) -> bool:  # no pausing in the simulation: silenced like an older audio helper
        await self.flush()
        return False

    async def resume(self) -> None:
        pass

    async def drained(self, wait_s: float = 120) -> bool:
        gen = self._gen
        while time.monotonic() < self._until:
            await asyncio.sleep(0.02)
            if gen != self._gen:
                return False
        return gen == self._gen

    @property
    def playing(self) -> bool:
        return time.monotonic() < self._until


async def _start(aec: bool = True) -> tuple[VoiceDaemon, SimAudio, asyncio.Task]:
    daemon = VoiceDaemon(get_settings())
    sim = SimAudio()
    sim.aec = aec

    async def open_audio() -> None:
        daemon.audio = sim  # type: ignore[assignment]

    daemon.open_audio = open_audio  # type: ignore[method-assign]
    run = asyncio.create_task(daemon.run())
    await asyncio.wait_for(daemon._ws_ready.wait(), 10)
    await asyncio.sleep(0.5)
    return daemon, sim, run


async def _inject(daemon: VoiceDaemon, audio: np.ndarray, speed: float = 1.0) -> None:
    """Sir's voice reaches both microphone signals (raw and echo-cancelled; there is no echo in the simulation)."""
    for i in range(0, len(audio), CHUNK):
        frame = audio[i : i + CHUNK]
        await daemon.mic_q.put(frame)
        if daemon.audio is not None and daemon.audio.aec:
            daemon._on_aec(frame)
        await asyncio.sleep(CHUNK / RATE / speed)


async def _wait(cond, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        await asyncio.sleep(0.1)
    return False


def _noise(seconds: float, seed: int = 1) -> np.ndarray:
    return (np.random.default_rng(seed).standard_normal(int(RATE * seconds)) * 40).astype(np.int16)


async def test_full_voice_turn_streams_and_reports_latency() -> None:
    daemon, sim, run = await _start()
    metrics: list[dict] = []
    orig = daemon._send

    async def spy(obj: dict) -> None:
        if obj.get("type") == "metrics":
            metrics.append(obj)
        await orig(obj)

    daemon._send = spy  # type: ignore[method-assign]
    try:
        audio = np.concatenate(
            [_noise(1), synth("Hey Jarvis", "Samantha"), _noise(0.6), synth("Wie viel ist sieben mal acht?"),
             _noise(6)]
        )  # fmt: skip
        await _inject(daemon, audio, speed=1.0)  # real time: endpointing depends on it
        assert await _wait(lambda: bool(metrics), 90), f"no turn finished (state={daemon.state})"
    finally:
        run.cancel()
    m = metrics[-1]
    assert sum(len(p) for p in sim.played) / 2 / 24000 > 0.5
    assert m["first_audio_ms"] is not None and m["endpoint_ms"] < 1700
    print("\nVOICE TURN METRICS", m)
    r = await daemon.http.get(f"/api/sessions/{daemon.session_id}/messages")
    msgs = r.json()
    assert re.search(r"7|sieben", msgs[-2]["content"]) and re.search(r"8|acht", msgs[-2]["content"])
    assert "56" in msgs[-1]["content"] or "sechsundfünfzig" in msgs[-1]["content"].lower()


async def test_voice_barge_in_stops_playback_and_keeps_the_words() -> None:
    """Sir talks while Jarvis speaks: playback stops at once and his words become the next turn."""
    daemon, sim, run = await _start(aec=True)
    try:
        q = synth("Erzähl mir bitte ausführlich in fünf Sätzen, wie ein Elektromotor funktioniert.")
        await _inject(daemon, np.concatenate([_noise(1), synth("Hey Jarvis", "Samantha"), _noise(0.6), q, _noise(2)]))
        assert await _wait(lambda: sim.playing, 60), "Jarvis never started speaking"
        await asyncio.sleep(1.0)
        loud = synth("Stopp. Wie spät ist es?")
        loud = np.clip(loud.astype(np.int32) * 3, -32768, 32767).astype(np.int16)
        t_inject = time.monotonic()
        await _inject(daemon, np.concatenate([loud, _noise(3)]))
        assert sim.flushes and sim.flushes[0] - t_inject < 1.5, "playback was not stopped quickly"
        assert await _wait(lambda: daemon.session_id is not None and daemon.reply is None and daemon.state != "speaking",
                           60)  # fmt: skip
    finally:
        run.cancel()
    await asyncio.sleep(1)
    r = await daemon.http.get(f"/api/sessions/{daemon.session_id}/messages")
    users = [m["content"] for m in r.json() if m["role"] == "user"]
    assert any(re.search(r"spät|uhr", u, re.I) for u in users), users  # the interrupting words were heard


async def test_wake_word_barge_in_without_echo_cancellation() -> None:
    daemon, sim, run = await _start(aec=False)
    try:
        q = synth("Erzähl mir in vier Sätzen etwas über den Mond.")
        await _inject(daemon, np.concatenate([_noise(1), synth("Hey Jarvis", "Samantha"), _noise(0.6), q, _noise(2)]))
        assert await _wait(lambda: sim.playing, 60)
        await asyncio.sleep(0.8)
        await _inject(daemon, np.concatenate([synth("Hey Jarvis", "Samantha"), _noise(1)]))
        assert sim.flushes, "wake word did not interrupt"
        assert await _wait(lambda: daemon.state in ("listening", "idle", "recording"), 10)
    finally:
        run.cancel()
