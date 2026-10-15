"""E2E: synthetic speech → wake word → VAD → whisper STT → Hermes → streamed TTS (captured).

Requires the running stack (core, Hermes, Ollama, whisper-server). The microphone and the
speakers are replaced by injected/captured audio, so no macOS permission is needed.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import tempfile

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


async def test_full_voice_turn() -> None:
    daemon = VoiceDaemon(get_settings())
    played: list[bytes] = []

    async def fake_play(pcm: bytes) -> bool:
        played.append(pcm)
        return True

    daemon.open_mic = lambda: setattr(daemon, "stream", object())  # type: ignore[method-assign]
    daemon.close_mic = lambda: None  # type: ignore[method-assign]
    daemon._play = fake_play  # type: ignore[method-assign]
    silence = np.zeros(RATE, dtype=np.int16)
    noise = (np.random.default_rng(1).standard_normal(RATE * 8) * 40).astype(np.int16)
    audio = np.concatenate(
        [silence, synth("Hey Jarvis", "Samantha"), silence, synth("Wie viel ist sieben mal acht?"), noise]
    )
    run = asyncio.create_task(daemon.run())
    await asyncio.sleep(1.5)  # event stream connects
    for i in range(0, len(audio), CHUNK):
        await daemon.mic_q.put(audio[i : i + CHUNK])
        await asyncio.sleep(CHUNK / RATE / 4)
    for _ in range(240):
        if played and daemon.state in ("idle", "listening") and daemon.turn is None:
            break
        await asyncio.sleep(0.5)
    run.cancel()
    states = daemon.state
    assert played, f"no speech output produced (state={states})"
    total_s = sum(len(p) for p in played) / 2 / 24000
    assert total_s > 0.5
    r = await daemon.http.get(f"/api/sessions/{daemon.session_id}/messages")
    msgs = r.json()
    assert (
        msgs[0]["role"] == "user"
        and re.search(r"7|sieben", msgs[0]["content"])
        and re.search(r"8|acht", msgs[0]["content"])
    )
    assert "56" in msgs[-1]["content"] or "sechsundfünfzig" in msgs[-1]["content"].lower()


async def test_barge_in_returns_to_listening() -> None:
    """'Hey Jarvis' while Jarvis is speaking stops playback at once and the daemon listens again."""
    daemon = VoiceDaemon(get_settings())
    played: list[int] = []

    async def fake_play(pcm: bytes) -> bool:  # simulates real-time playback that honours the stop event
        played.append(len(pcm))
        for _ in range(int(len(pcm) / 2 / 24000 / 0.05) + 1):
            if daemon._stop_playback.is_set():
                return False
            await asyncio.sleep(0.05)
        return True

    daemon.open_mic = lambda: setattr(daemon, "stream", object())  # type: ignore[method-assign]
    daemon.close_mic = lambda: None  # type: ignore[method-assign]
    daemon._play = fake_play  # type: ignore[method-assign]
    run = asyncio.create_task(daemon.run())
    await asyncio.sleep(1.5)
    silence = np.zeros(RATE, dtype=np.int16)
    question = np.concatenate(
        [
            silence,
            synth("Hey Jarvis", "Samantha"),
            silence,
            synth("Erzähl mir ausführlich in zehn Sätzen etwas über die Geschichte von Hamburg."),
            np.zeros(RATE * 3, dtype=np.int16),
        ]
    )

    async def feed(audio: np.ndarray) -> None:
        for i in range(0, len(audio), CHUNK):
            await daemon.mic_q.put(audio[i : i + CHUNK])
            await asyncio.sleep(CHUNK / RATE)

    await feed(question)
    for _ in range(120):  # wait until Jarvis speaks
        if daemon.speaking:
            break
        await feed(np.zeros(CHUNK * 4, dtype=np.int16))
    assert daemon.speaking, "Jarvis never started speaking"
    await feed(np.concatenate([synth("Hey Jarvis", "Samantha"), silence]))
    for _ in range(40):
        if not daemon.speaking and daemon.state in ("listening", "idle", "recording", "thinking"):
            break
        await feed(np.zeros(CHUNK * 2, dtype=np.int16))
    run.cancel()
    assert not daemon.speaking, f"still speaking after barge-in (state={daemon.state})"
