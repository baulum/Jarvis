"""The greeting of a LiveKit call and what it tells about the phone's audio connection."""

import asyncio
import uuid
from typing import Any

import pytest

from jarvis.mobile.voice_agent import CallVoiceAgent


def _agent(greeting_pcm: bytes) -> Any:
    agent: Any = CallVoiceAgent.__new__(CallVoiceAgent)
    agent.call_id = uuid.uuid4()
    agent.metrics = {"turns": 0, "barge_ins": 0}
    agent.transcript = []
    agent._greeting_spoken = False
    agent._owner_track = asyncio.Event()
    agent._started = 0.0
    agent.phone_audio_wait_s = 0.01
    fut: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
    fut.set_result(greeting_pcm)
    agent._greeting_pcm = fut
    agent.played = []

    async def transcript(role: str, text: str) -> None:
        agent.transcript.append((role, text))

    async def on_audio(pcm: bytes) -> None:
        agent.played.append(pcm)

    async def drain() -> None:
        return None

    agent._transcript, agent._on_audio, agent._drain = transcript, on_audio, drain
    return agent


async def test_missing_phone_microphone_is_recorded(caplog: pytest.LogCaptureFixture) -> None:
    # 2026-10-28: every call ended after ~15 s as "owner_hangup", the greeting stood in the chat, Sir heard nothing –
    # the phone never published its microphone (LiveKit: negotiation failed). The call must say so.
    agent = _agent(b"\x01\x00" * 480)
    await agent._speak_greeting("Guten Morgen, Sir. Ich höre.")
    assert agent.metrics["phone_audio"] is False
    assert "microphone did not arrive" in caplog.text
    assert agent.played  # the greeting is still sent: the phone may only be late


async def test_phone_microphone_present_leaves_no_note() -> None:
    agent = _agent(b"\x01\x00" * 480)
    agent._owner_track.set()
    await agent._speak_greeting("Guten Morgen, Sir. Ich höre.")
    assert "phone_audio" not in agent.metrics
    assert agent.transcript == [("jarvis", "Guten Morgen, Sir. Ich höre.")]
