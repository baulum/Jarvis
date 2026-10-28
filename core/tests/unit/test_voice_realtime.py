"""Endpointing, speculative-STT hooks, barge-in detection, STT splitting, stale-chunk filtering, playback."""

import asyncio
import json

import numpy as np
import pytest

from jarvis.voice.audio import BargeInDetector, Endpointer, looks_complete
from jarvis.voice.speech import split_long

RATE = 16000


def tone(ms: int, amp: float = 3000, f: float = 220) -> np.ndarray:
    t = np.arange(int(RATE * ms / 1000)) / RATE
    return (amp * np.sin(2 * np.pi * f * t)).astype(np.int16)


def quiet(ms: int, amp: float = 30) -> np.ndarray:
    return (np.random.default_rng(0).standard_normal(int(RATE * ms / 1000)) * amp).astype(np.int16)


def feed(ep: Endpointer, audio: np.ndarray, step: int = 640) -> list[str]:
    out = []
    for i in range(0, len(audio), step):
        out += ep.feed(audio[i : i + step])
    return out


@pytest.mark.parametrize(
    "text,complete",
    [
        ("Welcher Wochentag ist heute?", True),
        ("Erstell mir eine Rechnung für Westside Taxi.", True),
        ("Erstell mir eine Rechnung und", False),
        ("Ich wollte fragen, ähm", False),
        ("Schick das an,", False),
        ("Ja.", True),
        ("", False),
    ],
)
def test_looks_complete(text: str, complete: bool) -> None:
    assert looks_complete(text) is complete


def test_endpointer_pause_resume_and_silence() -> None:
    ep = Endpointer(RATE, spec_ms=280)
    events = feed(ep, quiet(300))
    assert events == [] and not ep.active
    events = feed(ep, tone(600))
    assert events == ["start"] and ep.active
    events = feed(ep, quiet(300))
    assert events == ["pause"] and ep.silence_ms >= 280
    events = feed(ep, tone(400))
    assert events == ["resume"] and ep.silence_ms == 0
    feed(ep, quiet(700))
    assert ep.silence_ms >= 680
    audio = ep.audio()
    # pre-roll + both speech parts + inner pause, trailing silence trimmed to ~200 ms
    assert 1.2 * RATE < len(audio) < 1.8 * RATE


def test_endpointer_prime_keeps_barge_in_audio() -> None:
    ep = Endpointer(RATE)
    ep.prime(tone(400))
    assert ep.active and ep.has_speech
    feed(ep, quiet(400))
    assert len(ep.audio()) >= 0.4 * RATE


def test_barge_in_ignores_residual_echo_but_fires_on_speech() -> None:
    det = BargeInDetector(RATE, need_ms=320, min_rms=450)
    echo = tone(1500, amp=350, f=300)  # echo-cancelled residual of Jarvis' voice
    assert not any(det.feed(echo[i : i + 640]) for i in range(0, len(echo), 640))
    speech = tone(500, amp=2500, f=180)
    fired = [det.feed(speech[i : i + 640]) for i in range(0, len(speech), 640)]
    assert any(fired) and len(det.captured()) >= 0.3 * RATE
    det.reset()
    blip = tone(100, amp=4000)  # a cough/click is too short
    assert not any(det.feed(blip[i : i + 640]) for i in range(0, len(blip), 640))


def test_raw_mic_barge_in_learns_jarvis_voice_before_it_fires() -> None:
    """Without echo cancellation the microphone hears Jarvis himself. The warm-up must not be used up by the silent
    lead-in of the TTS audio, otherwise his voice that follows counts as Sir and he interrupts himself."""
    det = BargeInDetector(RATE, need_ms=320, min_rms=450, ratio=1.8, peak_hold=True)
    own = np.concatenate([quiet(400), tone(700, amp=2500), quiet(120), tone(300, amp=3200), tone(1500, amp=2500)])
    assert not any(det.feed(own[i : i + 640]) for i in range(0, len(own), 640))
    sir = tone(600, amp=7000, f=180)  # Sir, clearly louder than the echo of Jarvis' voice
    assert any(det.feed(sir[i : i + 640]) for i in range(0, len(sir), 640))


def test_barge_in_arm_restarts_the_warm_up_when_playback_starts() -> None:
    det = BargeInDetector(RATE, need_ms=320, min_rms=450)
    pre = quiet(400)  # echo-cancelled microphone while Jarvis is still thinking
    assert not any(det.feed(pre[i : i + 640]) for i in range(0, len(pre), 640))
    det.arm()  # playback starts: the canceller settles on a loud residual first
    burst = tone(200, amp=1200, f=300)
    assert not any(det.feed(burst[i : i + 640]) for i in range(0, len(burst), 640))
    assert det.floor > 450 / 3.0


def test_split_long_cuts_at_quiet_points() -> None:
    audio = np.concatenate([tone(10_000), quiet(300, 5), tone(9_000), quiet(200, 5), tone(6_000)])
    parts = split_long(audio, RATE, 14.0)
    assert all(len(p) / RATE <= 14.0 for p in parts) and sum(len(p) for p in parts) == len(audio)
    assert 10.0 <= len(parts[0]) / RATE <= 10.3  # cut inside the first quiet gap
    assert split_long(tone(3000), RATE) and len(split_long(tone(3000), RATE)) == 1


class FakeAudio:
    aec = True
    out_level = 0.0
    in_level = 0.0

    def __init__(self) -> None:
        self.played: list[bytes] = []
        self.flushed = 0

    async def play(self, pcm: bytes) -> None:
        self.played.append(pcm)

    async def flush(self) -> None:
        self.flushed += 1
        self.played.clear()

    @property
    def playing(self) -> bool:
        return bool(self.played)


async def test_stale_chunks_of_interrupted_reply_are_dropped(monkeypatch, tmp_path) -> None:
    from jarvis.voice import daemon as d

    monkeypatch.setattr(d.WakeWordDetector, "__init__", lambda self, *a, **k: None)
    s = d.get_settings()
    (tmp_path / "voice.token").write_text("x")
    settings = s.model_copy(update={"paths": s.paths.model_copy(update={"data_dir": tmp_path})})
    vd = d.VoiceDaemon(settings)
    vd.audio = FakeAudio()  # type: ignore[assignment]
    sent: list[dict] = []

    async def send(obj: dict) -> None:
        sent.append(obj)

    vd._send = send  # type: ignore[method-assign]
    vd.set_state = lambda *a, **k: asyncio.sleep(0)  # type: ignore[method-assign]
    old = d.Reply(tag=4)
    vd.reply = old
    await vd._on_speak(bytes([4]) + b"\x01\x00" * 10)
    assert vd.audio.played  # type: ignore[union-attr]
    await vd.interrupt()
    assert vd.audio.flushed == 1 and sent[-1] == {"type": "cancel", "tag": 4}  # type: ignore[union-attr]
    vd.reply = d.Reply(tag=5)
    await vd._on_speak(bytes([4]) + b"\x02\x00" * 10)  # late chunk of the interrupted reply
    await vd._on_speak(json.dumps({"type": "speech_end", "tag": 4}))
    assert vd.audio.played == [] and not vd.reply.ended.is_set()  # type: ignore[union-attr]
    await vd._on_speak(bytes([5]) + b"\x03\x00" * 10)
    assert vd.audio.played == [b"\x03\x00" * 10] and vd.reply.first_audio is not None  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "heard,clean",
    [("Hey, Javis. Wie spät ist es?", "Wie spät ist es?"), ("Hey Jarvis, Frage und zwar: was steht heute an", "Frage und zwar: was steht heute an"),
     ("Hey, Javis.", ""), ("Jarvis, mach das Licht an.", "mach das Licht an."), ("Wie geht es Jarvis heute?", "Wie geht es Jarvis heute?")],
)
def test_strip_wake_word(heard: str, clean: str) -> None:
    from jarvis.voice.daemon import strip_wake_word

    assert strip_wake_word(heard) == clean
