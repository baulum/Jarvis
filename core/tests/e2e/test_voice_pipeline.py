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
        raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", path, "-ar", str(RATE), "-ac", "1", "-f",
                              "s16le", "-"], capture_output=True, check=True).stdout
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
    audio = np.concatenate([silence, synth("Hey Jarvis", "Samantha"), silence,
                            synth("Wie viel ist sieben mal acht?"), noise])
    run = asyncio.create_task(daemon.run())
    await asyncio.sleep(1.5)  # event stream connects
    for i in range(0, len(audio), CHUNK):
        await daemon.mic_q.put(audio[i:i + CHUNK])
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
    assert msgs[0]["role"] == "user" and re.search(r"7|sieben", msgs[0]["content"]) and re.search(r"8|acht", msgs[0]["content"])
    assert "56" in msgs[-1]["content"] or "sechsundfünfzig" in msgs[-1]["content"].lower()
