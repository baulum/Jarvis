"""Barge-in and stop command on every voice channel: Mac daemon, LiveKit call agent, Twilio phone line.

Sir talks over Jarvis (or says "Stopp") → speech stops at once, the audio buffer is dropped, the running answer is
cancelled, and what he said is treated as the next utterance. Jarvis' own voice must not trigger or be answered.
"""

import asyncio
import json
import time
import uuid
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from jarvis.mobile.voice_agent import CallVoiceAgent
from jarvis.telephony.service import CallState, TelephonyService
from jarvis.voice.audio import ulaw_encode

RATE = 16000


def tone(ms: int, amp: float = 2500, f: float = 180, rate: int = RATE) -> np.ndarray:
    t = np.arange(int(rate * ms / 1000)) / rate
    return (amp * np.sin(2 * np.pi * f * t)).astype(np.int16)


def quiet(ms: int, rate: int = RATE) -> np.ndarray:
    return (np.random.default_rng(3).standard_normal(int(rate * ms / 1000)) * 20).astype(np.int16)


def chunks(audio: np.ndarray, size: int = 320) -> list[np.ndarray]:
    """20 ms frames, like the audio the call agent reads."""
    return [audio[i : i + size] for i in range(0, len(audio) - size + 1, size)]


async def _until(cond: Any, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        await asyncio.sleep(0.01)
    return False


# ---------------------------------------------------------------------------------------------- LiveKit call agent
class _Speech:
    def __init__(self) -> None:
        self.cancelled = 0

    async def cancel(self) -> None:
        self.cancelled += 1


def _agent() -> Any:
    agent: Any = CallVoiceAgent.__new__(CallVoiceAgent)
    agent.call_id = uuid.uuid4()
    agent.session_id = uuid.uuid4()
    agent.metrics = {"turns": 0, "barge_ins": 0}
    agent.transcript = []
    agent._speech = _Speech()
    agent._prepared = None
    agent._speaking = True
    agent._out_buf = b"\x01\x00" * 100
    agent._turn_task = None
    agent._source = SimpleNamespace(cleared=0, clear_queue=lambda: None)
    agent.states = []
    agent.interrupted_sessions = []

    async def set_state(value: str, detail: str | None = None) -> None:
        agent.states.append((value, detail))

    async def orch_interrupt(sid: uuid.UUID) -> bool:
        agent.interrupted_sessions.append(sid)
        return True

    agent.set_state = set_state
    agent.state = SimpleNamespace(orchestrator=SimpleNamespace(interrupt=orch_interrupt))
    return agent


async def test_call_interrupt_drops_audio_synthesis_and_agent_run() -> None:
    agent = _agent()
    cleared: list[int] = []
    agent._source = SimpleNamespace(clear_queue=lambda: cleared.append(1))
    speech = agent._speech
    agent._turn_task = asyncio.create_task(asyncio.sleep(60))

    assert await agent.interrupt_speech() is True  # what the Stopp button calls

    assert speech.cancelled == 1  # TTS synthesis stopped
    assert cleared == [1] and agent._out_buf == b"" and agent._speaking is False  # audio already queued is dropped
    assert agent._turn_task.done() and agent._turn_task.cancelled()  # answer generation cancelled
    assert agent.interrupted_sessions == [agent.session_id]  # only this call's conversation
    assert agent.states[-1] == ("listening", "unterbrochen")
    assert agent.metrics["stops"] == 1


async def test_call_stop_button_without_anything_to_stop_does_nothing() -> None:
    agent = _agent()
    agent._speaking = False
    assert await agent.interrupt_speech() is False
    assert agent.states == [] and agent.interrupted_sessions == []


async def test_call_voice_barge_in_keeps_the_interrupting_words(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _agent()
    agent._audio_q = asyncio.Queue()
    agent.state.settings = SimpleNamespace(
        voice=SimpleNamespace(
            vad_min_rms=180,
            speculative_stt_ms=280,
            barge_in_ms=320,
            end_of_turn_silence_ms=1100,
            min_end_of_turn_silence_ms=400,
        )
    )
    interrupts: list[float] = []
    turns: list[np.ndarray] = []

    async def interrupt() -> None:
        interrupts.append(time.monotonic())
        agent._speaking = False

    async def stt(audio: np.ndarray) -> str:
        return "Wie spät ist es?"

    async def turn(audio: np.ndarray, text: str | None) -> None:
        turns.append(audio)

    agent._interrupt = interrupt
    agent._stt = stt
    agent._turn = turn
    agent._make_speech = lambda: SimpleNamespace(prepare=lambda: asyncio.sleep(0))
    loop = asyncio.create_task(agent._listen_loop())
    try:
        # Jarvis is speaking; the residual echo is quiet, then Sir speaks for one second
        for chunk in chunks(np.concatenate([quiet(400), tone(1000), quiet(1500)])):
            agent._audio_q.put_nowait(chunk)
        assert await _until(lambda: bool(turns))
    finally:
        loop.cancel()
    assert len(interrupts) == 1 and agent.metrics["barge_ins"] == 1
    # the first 320 ms (needed to detect him) are not lost: ~1 s of speech arrives as the next utterance
    assert len(turns[0]) >= 0.95 * RATE


async def test_call_ignores_a_click_while_jarvis_speaks() -> None:
    agent = _agent()
    agent._audio_q = asyncio.Queue()
    agent.state.settings = SimpleNamespace(
        voice=SimpleNamespace(
            vad_min_rms=180,
            speculative_stt_ms=280,
            barge_in_ms=320,
            end_of_turn_silence_ms=1100,
            min_end_of_turn_silence_ms=400,
        )
    )
    called: list[int] = []

    async def interrupt() -> None:
        called.append(1)

    agent._interrupt = interrupt
    loop = asyncio.create_task(agent._listen_loop())
    try:
        for chunk in chunks(np.concatenate([quiet(300), tone(100, amp=4000), quiet(600)])):
            agent._audio_q.put_nowait(chunk)
        await asyncio.sleep(0.2)
    finally:
        loop.cancel()
    assert called == [] and agent.metrics["barge_ins"] == 0


async def test_call_barge_in_switched_off_in_the_app_never_interrupts() -> None:
    agent = _agent()
    agent.barge_in = False  # setting "Jarvis ins Wort fallen" off: only the Stopp button interrupts
    agent._audio_q = asyncio.Queue()
    agent.state.settings = SimpleNamespace(
        voice=SimpleNamespace(
            vad_min_rms=180,
            speculative_stt_ms=280,
            barge_in_ms=320,
            end_of_turn_silence_ms=1100,
            min_end_of_turn_silence_ms=400,
        )
    )
    called: list[int] = []

    async def interrupt() -> None:
        called.append(1)

    agent._interrupt = interrupt
    loop = asyncio.create_task(agent._listen_loop())
    try:
        for chunk in chunks(np.concatenate([quiet(400), tone(1500), quiet(300)])):
            agent._audio_q.put_nowait(chunk)
        await asyncio.sleep(0.2)
    finally:
        loop.cancel()
    assert called == [] and agent.metrics["barge_ins"] == 0
    # the Stopp button is the fallback and still works
    cleared: list[int] = []
    agent._source = SimpleNamespace(clear_queue=lambda: cleared.append(1))
    agent._interrupt = CallVoiceAgent._interrupt.__get__(agent)
    assert await agent.interrupt_speech() is True and cleared == [1]


def test_call_barge_in_is_on_by_default_and_follows_the_app_setting() -> None:
    assert CallVoiceAgent.barge_in is True

    from jarvis.mobile.api import CallOptions

    assert CallOptions().barge_in is True
    assert CallOptions.model_validate({"barge_in": False}).barge_in is False


async def test_call_stop_command_is_not_sent_to_the_agent_and_does_not_hang_up() -> None:
    agent = _agent()
    agent.principal = SimpleNamespace()
    agent.greeting = None
    agent._ending = False
    agent._owner_left = asyncio.Event()
    heard: list[str] = []

    async def transcript(role: str, text: str) -> None:
        heard.append(text)

    class Boom:
        async def handle(self, *a: Any, **k: Any) -> None:
            raise AssertionError("the stop command must not reach the agent")

    agent._transcript = transcript
    agent.state.orchestrator = Boom()
    await agent._turn(np.ones(16000, dtype=np.int16), "Jarvis, stopp!")
    assert heard == ["Jarvis, stopp!"] and not agent._owner_left.is_set()
    assert agent.states[-1][0] == "listening"


# ---------------------------------------------------------------------------------------------- Twilio phone line
class _WS:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(json.loads(data))


def _telephony() -> TelephonyService:
    s = TelephonyService.__new__(TelephonyService)
    s.state = SimpleNamespace(settings=SimpleNamespace(voice=SimpleNamespace(barge_in_ms=320)))  # type: ignore[assignment]
    return s


def _call_state() -> CallState:
    return CallState(uuid.uuid4(), "inbound", "+4900", "+4911", authenticated=True, stream_sid="MZ1")


def test_phone_barge_in_needs_sustained_speech_not_a_click() -> None:
    s = _telephony()
    st = _call_state()
    st.speaking = True
    frame = tone(20, amp=3000, rate=8000)  # 160 samples = one 20 ms Twilio frame
    assert not s._barge_in_heard(st, frame)  # a single loud frame (click, line crackle)
    for _ in range(5):
        assert not s._barge_in_heard(st, quiet(20, 8000))
    assert st.barge_frames == 0
    fired = [s._barge_in_heard(st, frame) for _ in range(20)]
    assert fired.index(True) == 15 and sum(fired) == 1  # 16 frames = 320 ms of speech
    quiet_frame = quiet(20, 8000)
    assert not any(s._barge_in_heard(st, quiet_frame) for _ in range(30))


def test_phone_barge_in_is_off_while_nobody_speaks_or_works() -> None:
    s = _telephony()
    st = _call_state()
    loud = tone(20, amp=3000, rate=8000)
    assert not any(s._barge_in_heard(st, loud) for _ in range(40))  # Sir's normal turn is no interruption


async def test_phone_barge_in_clears_provider_audio_and_cancels_the_answer() -> None:
    s = _telephony()
    st = _call_state()
    st.speaking = True
    sid = uuid.uuid4()
    interrupted: list[uuid.UUID] = []
    started = asyncio.Event()

    class Orch:
        async def interrupt(self, session_id: uuid.UUID) -> bool:
            interrupted.append(session_id)
            return True

        async def get_or_create_session(self, *a: Any, **k: Any) -> Any:
            return SimpleNamespace(id=sid)

        async def handle(self, *a: Any, **k: Any) -> Any:
            started.set()
            await asyncio.sleep(60)

    s.state.orchestrator = Orch()  # type: ignore[attr-defined]
    ws = _WS()
    st.turn_task = asyncio.create_task(s._answer(ws, st, "Erzähl mir etwas"))  # type: ignore[arg-type]
    await asyncio.wait_for(started.wait(), 2)
    assert st.session_id == sid

    await s._barge_in(ws, st)  # type: ignore[arg-type]
    await asyncio.wait({st.turn_task}, timeout=2)

    assert ws.sent == [{"event": "clear", "streamSid": "MZ1"}]  # Twilio drops what it has buffered
    assert st.turn_task.cancelled() and st.speaking is False
    assert interrupted == [sid]


async def test_phone_stop_command_is_no_request_and_keeps_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    import jarvis.telephony.service as svc_mod

    said: list[str] = []
    lines = iter(["eins zwei drei vier", "Stopp.", "Jarvis, stopp!", "tschüss"])

    class STT:
        async def transcribe_pcm(self, *_a: Any) -> str:
            return next(lines)

    class Boom:
        async def handle(self, *a: Any, **k: Any) -> None:
            raise AssertionError("the stop command must not reach the agent")

        async def get_or_create_session(self, *a: Any, **k: Any) -> None:
            raise AssertionError("the stop command must not reach the agent")

    s = TelephonyService.__new__(TelephonyService)
    s.cfg = SimpleNamespace(pin_secret="phone-pin", provider="local_test")  # type: ignore[assignment]
    s.state = SimpleNamespace(stt=STT(), orchestrator=Boom())  # type: ignore[assignment]

    async def say(_ws: Any, _st: CallState, text: str) -> None:
        said.append(text)

    async def noop(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(s, "_say", say)
    monkeypatch.setattr(s, "_hangup", noop)
    monkeypatch.setattr(svc_mod, "audit", noop)
    monkeypatch.setattr(svc_mod.secret_store, "get", lambda name: "1234" if name == "phone-pin" else None)
    st = CallState(uuid.uuid4(), "inbound", "+4900", "+4911")
    q: asyncio.Queue[np.ndarray] = asyncio.Queue()
    for _ in range(4):
        await q.put(np.ones(1600, dtype=np.int16))
    await asyncio.wait_for(s._conversation(None, st, q), 5)  # type: ignore[arg-type]
    assert st.authenticated
    assert said[-1] == "Sehr wohl, Sir. Auf Wiederhören."  # only "tschüss" ended the call, "Stopp" did not
    assert sum("Auf Wiederhören" in t for t in said) == 1


def test_ulaw_frames_are_the_same_length_as_pcm_frames() -> None:
    assert len(ulaw_encode(tone(20, rate=8000))) == 160  # the frame counting of the barge-in relies on 160 per 20 ms


# ---------------------------------------------------------------------------------------------- Mac voice daemon
class _FakeAudio:
    aec = True
    out_level = 0.0

    def __init__(self) -> None:
        self.flushed = 0

    async def flush(self) -> None:
        self.flushed += 1

    @property
    def playing(self) -> bool:
        return False


def _daemon(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Any:
    from jarvis.voice import daemon as d

    monkeypatch.setattr(d.WakeWordDetector, "__init__", lambda self, *a, **k: None)
    s = d.get_settings()
    (tmp_path / "voice.token").write_text("x")
    settings = s.model_copy(update={"paths": s.paths.model_copy(update={"data_dir": tmp_path})})
    vd = d.VoiceDaemon(settings)
    vd.audio = _FakeAudio()
    vd.sent = []

    async def send(obj: dict[str, Any]) -> None:
        vd.sent.append(obj)

    async def set_state(*a: Any, **k: Any) -> None:
        return None

    vd._send = send
    vd.set_state = set_state
    return vd


async def test_daemon_stop_command_silences_and_keeps_the_conversation(monkeypatch, tmp_path) -> None:
    vd = _daemon(monkeypatch, tmp_path)
    audio = np.ones(16000, dtype=np.int16)
    for heard in ("Stopp.", "Jarvis, stopp!", "Hey Jarvis, hör auf"):
        assert await vd.handle_turn(audio, heard, {}) is True  # conversation stays open
    assert vd.audio.flushed == 3
    assert [m for m in vd.sent if m.get("type") == "turn"] == []  # never sent to the agent
    assert vd.session_id is None


async def test_daemon_ignores_its_own_voice_coming_back(monkeypatch, tmp_path) -> None:
    from jarvis.voice import daemon as d

    vd = _daemon(monkeypatch, tmp_path)
    vd._spoken.append("Heute wird es sonnig bei einundzwanzig Grad in Hamburg.")
    vd._playback_ended = time.perf_counter()
    audio = np.ones(16000, dtype=np.int16)
    marks = {"speech_start": time.perf_counter()}
    assert await vd.handle_turn(audio, "heute wird es sonnig bei einundzwanzig Grad", marks) is True
    assert [m for m in vd.sent if m.get("type") == "turn"] == []

    # the same words much later are Sir repeating them: a normal request
    async def reply(r: Any, listen_for_barge_in: bool) -> str:
        return "done"

    async def report(*a: Any, **k: Any) -> None:
        return None

    vd._await_reply = reply
    vd._report = report
    vd._playback_ended = time.perf_counter() - 30
    assert await vd.handle_turn(audio, "heute wird es sonnig bei einundzwanzig Grad", marks) is True
    assert [m["text"] for m in vd.sent if m.get("type") == "turn"] == ["heute wird es sonnig bei einundzwanzig Grad"]
    assert d.is_echo("Wie spät ist es?", vd._spoken) is False


async def test_daemon_remembers_what_it_said_for_the_echo_check(monkeypatch, tmp_path) -> None:
    from jarvis.voice import daemon as d

    vd = _daemon(monkeypatch, tmp_path)
    r = d.Reply(tag=1)
    r.pieces = ["Guten Abend, Sir.", "Alle Systeme laufen."]

    async def watch(reply: Any, listen: bool) -> str:
        return "done"

    vd._watch_reply = watch
    before = time.perf_counter()
    assert await vd._await_reply(r, True) == "done"
    assert list(vd._spoken) == ["Guten Abend, Sir. Alle Systeme laufen."] and vd._playback_ended >= before
