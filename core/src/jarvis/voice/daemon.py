"""Voice daemon: "Hey Jarvis" → listen → transcribe → Jarvis → streamed, interruptible speech.

Runs as its own local process (``jarvisctl voice``) because it owns the microphone and the speakers
(macOS-only concern, kept out of the server core). It talks to the core with the voice device token.

Pipeline (everything overlaps instead of running one after the other):

* Microphone + speaker: the Swift helper ``jarvis-audio`` (Apple voice processing → echo
  cancellation); fallback sounddevice + afplay.
* Wake word (openWakeWord) or push-to-talk start a conversation.
* ``Endpointer``: after ~280 ms of silence whisper.cpp already transcribes the utterance
  (speculative STT) and the core warms TTS (``prepare``). A finished-sounding sentence ends the
  turn after ``min_end_of_turn_silence_ms``; an unfinished one ("… und", "ähm") waits up to
  ``end_of_turn_silence_ms``. Speech that resumes discards the speculative result.
* ``/api/voice/speak`` (one WebSocket): the core runs the agent turn and streams PCM while the
  answer is still being written; chunks go to the speaker the moment they arrive.
* Barge-in: with echo cancellation Sir can simply start talking (``BargeInDetector``) – playback
  stops at once, the core cancels TTS and the agent run, stale chunks (old tag) are dropped and
  the interrupting words become the start of the next utterance. Without echo cancellation
  "Hey Jarvis" interrupts.
* After a reply a follow-up window allows answering without the wake word; "danke, das war's" ends.
* Every turn is measured (end of speech → endpoint → transcript → first audio) and reported.
"""

from __future__ import annotations

import asyncio
import contextlib
import faulthandler
import json
import logging
import re
import signal
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import numpy as np
import numpy.typing as npt
import websockets

from jarvis.config import Settings, get_settings
from jarvis.voice.audio import BargeInDetector, Endpointer, looks_complete, pcm_to_wav, rms
from jarvis.voice.audio_io import AudioIO, open_audio
from jarvis.voice.wakeword import RATE, WakeWordDetector

logger = logging.getLogger("jarvis.voice")
PLAY_RATE = 24000
_END_PHRASES = re.compile(
    r"\b(danke,? das war'?s|das wär'?s|das war alles|stopp|ende|tschüss|thanks,? that'?s all)\b", re.I
)


@dataclass
class Reply:
    """State of one spoken reply (identified by a tag that is stamped on every audio frame)."""

    tag: int
    started: float = field(default_factory=time.perf_counter)
    session_id: str | None = None
    first_audio: float | None = None
    ended: asyncio.Event = field(default_factory=asyncio.Event)
    metrics: dict[str, Any] = field(default_factory=dict)
    pieces: list[str] = field(default_factory=list)
    error: str | None = None


def earcon(rate: int = PLAY_RATE) -> bytes:
    """Short, soft two-tone chime (played through the echo-cancelled path, unlike a system sound)."""
    out = []
    for f, dur in ((880.0, 0.06), (1320.0, 0.08)):
        t = np.arange(int(rate * dur)) / rate
        env = np.sin(np.pi * t / dur) ** 2
        out.append(0.18 * env * np.sin(2 * np.pi * f * t))
    return (np.concatenate(out) * 32767).astype(np.int16).tobytes()


class VoiceDaemon:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.cfg = settings.voice
        self.base = f"http://127.0.0.1:{settings.server.port}"
        self.token = (settings.paths.data_dir / "voice.token").read_text().strip()
        self.http = httpx.AsyncClient(
            base_url=self.base, headers={"Authorization": f"Bearer {self.token}"}, timeout=httpx.Timeout(120, connect=5)
        )
        self.detector = WakeWordDetector(settings.paths.data_dir / "models", threshold=self.cfg.wake_threshold)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.mic_q: asyncio.Queue[npt.NDArray[np.int16]] = asyncio.Queue(maxsize=400)  # raw microphone
        self.aec_q: asyncio.Queue[npt.NDArray[np.int16]] = asyncio.Queue(maxsize=100)  # echo-cancelled
        self.audio: AudioIO | None = None
        self.state = "idle"
        self.muted = False
        self.ptt = False
        self.session_id: str | None = None
        self.speak_ws: Any = None
        self._ws_ready = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self.tag = 0
        self.reply: Reply | None = None
        self._prepared: int | None = None
        self._level = 0.0
        self._primed: npt.NDArray[np.int16] | None = None  # audio of a barge-in, start of the next utterance
        self._interrupt = asyncio.Event()

    # ------------------------------------------------------------------ audio in
    def _on_mic(self, pcm: npt.NDArray[np.int16]) -> None:
        if self.mic_q.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self.mic_q.get_nowait()
        self.mic_q.put_nowait(pcm)

    def _on_aec(self, pcm: npt.NDArray[np.int16]) -> None:
        if self.aec_q.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self.aec_q.get_nowait()
        self.aec_q.put_nowait(pcm)

    async def open_audio(self) -> None:
        self.audio = await open_audio(self.s.paths.data_dir, self._on_mic, native=self.cfg.native_audio)
        self.audio.on_aec = self._on_aec
        logger.info("Audio: %s (echo cancellation %s)", type(self.audio).__name__, self.audio.aec)

    async def close_audio(self) -> None:
        if self.audio is not None:
            await self.audio.stop()
            self.audio = None

    @property
    def voice_barge_in(self) -> bool:
        mode = self.cfg.barge_in
        return mode == "voice" or (mode == "auto" and bool(self.audio and self.audio.aec))

    # ------------------------------------------------------------------ status
    async def set_state(self, state: str, detail: str | None = None) -> None:
        self.state = state
        try:
            await self.http.post(
                "/api/voice/status",
                json={
                    "state": self.state,
                    "muted": self.muted,
                    "wake_word": True,
                    "level": round(self._level, 3),
                    "mic_permission": "granted" if self.audio is not None or self.muted else "unknown",
                    "detail": detail,
                },
            )
        except httpx.HTTPError:
            pass

    async def _level_ticker(self) -> None:
        """Live input/output levels for the interface (~12 Hz) over the speak socket – no HTTP per frame."""
        while True:
            await asyncio.sleep(0.083)
            if self.speak_ws is None or self.muted:
                continue
            out = self.audio.out_level if self.audio else 0.0
            with contextlib.suppress(Exception):
                await self._send({"type": "level", "in": round(self._level, 3), "out": round(out, 3)})

    # ------------------------------------------------------------------ core connections
    async def events(self) -> None:
        """Bus events (commands from the app, notifications to speak)."""
        url = self.base.replace("http", "ws") + f"/ws?token={self.token}&client=voice"
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    backoff = 1.0
                    logger.info("Connected to core event stream")
                    async for raw in ws:
                        ev = json.loads(raw)
                        t, d = ev.get("type"), ev.get("data", {})
                        if t == "voice.command":
                            await self._command(d.get("action", ""))
                        elif t == "voice.say" and not self.muted and self.reply is None:
                            await self.say_text(d.get("text", ""))
            except Exception as e:
                logger.warning("Event stream lost (%s); reconnecting", e)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def speak_socket(self) -> None:
        url = self.base.replace("http", "ws") + f"/api/voice/speak?token={self.token}"
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, max_size=None) as ws:
                    self.speak_ws = ws
                    self._ws_ready.set()
                    backoff = 1.0
                    logger.info("Connected to speech stream")
                    async for msg in ws:
                        await self._on_speak(msg)
            except Exception as e:
                logger.warning("Speech stream lost (%s); reconnecting", e)
            finally:
                self.speak_ws = None
                self._ws_ready.clear()
                if self.reply is not None:  # a reply in flight cannot finish any more
                    self.reply.error = "speech stream lost"
                    self.reply.ended.set()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _send(self, obj: dict[str, Any]) -> None:
        if self.speak_ws is None:
            await asyncio.wait_for(self._ws_ready.wait(), 10)
        async with self._send_lock:
            await self.speak_ws.send(json.dumps(obj, ensure_ascii=False))

    async def _on_speak(self, msg: Any) -> None:
        r = self.reply
        if isinstance(msg, bytes):
            if r is None or not msg or msg[0] != r.tag or self.audio is None:
                return  # stale chunk of an interrupted reply
            if r.first_audio is None:
                r.first_audio = time.perf_counter()
                if self.state != "speaking":
                    spawn_bg(self.set_state("speaking"))
            await self.audio.play(msg[1:])
            return
        data = json.loads(msg)
        if r is None or int(data.get("tag", -1)) != r.tag:
            return
        kind = data.get("type")
        if kind == "session":
            r.session_id = self.session_id = data.get("session_id")
        elif kind == "piece":
            r.pieces.append(str(data.get("text", "")))
        elif kind == "speech_end":
            r.metrics = data.get("metrics") or {}
            r.error = data.get("error")
            r.ended.set()

    async def _command(self, action: str) -> None:
        if action == "mute":
            self.muted = True
            await self.interrupt()
            await self.close_audio()  # microphone closed → macOS indicator off
            await self.set_state("muted")
        elif action == "unmute":
            self.muted = False
            await self.open_audio()
            await self.set_state("idle")
        elif action == "ptt_start" and not self.muted:
            self.ptt = True
            await self.interrupt()
        elif action == "ptt_stop":
            self.ptt = False
        elif action == "interrupt":
            await self.interrupt()

    async def interrupt(self) -> None:
        """Stop speaking now; the core cancels synthesis and the agent run."""
        r = self.reply
        if self.audio is not None:
            await self.audio.flush()
        if r is not None:
            with contextlib.suppress(Exception):
                await self._send({"type": "cancel", "tag": r.tag})
            r.error = r.error or "interrupted"
            r.ended.set()
        self._interrupt.set()

    def _next_tag(self) -> int:
        self.tag = (self.tag + 1) % 256
        return self.tag

    # ------------------------------------------------------------------ speech out
    async def say_text(self, text: str) -> None:
        if not text.strip():
            return
        r = Reply(self._next_tag())
        self.reply = r
        try:
            await self._send({"type": "say", "tag": r.tag, "text": text})
            await self._await_reply(r, listen_for_barge_in=True)
        finally:
            self.reply = None
        if self.state == "speaking":
            await self.set_state("idle")

    async def _await_reply(self, r: Reply, listen_for_barge_in: bool) -> str:
        """Wait until the reply was spoken; watch the microphone for an interruption meanwhile.

        Returns "done", "barge_in" or "error".
        """
        assert self.audio is not None
        barge = BargeInDetector(RATE, need_ms=self.cfg.barge_in_ms, min_rms=max(450.0, self.cfg.vad_min_rms * 2.5))
        self.detector.reset()
        self._interrupt.clear()
        while not self.aec_q.empty():  # frames of the question itself must not count as an interruption
            self.aec_q.get_nowait()
        drained: asyncio.Task[bool] | None = None
        while True:
            if r.ended.is_set() and drained is None:
                if r.error == "interrupted":
                    return "barge_in" if self._primed is not None else "done"
                drained = asyncio.create_task(self.audio.drained(120))
            if drained is not None and drained.done():
                return "done" if drained.result() else ("barge_in" if self._primed is not None else "done")
            if self._interrupt.is_set():
                if drained is not None:
                    drained.cancel()
                return "barge_in" if self._primed is not None else "done"
            try:
                pcm = await asyncio.wait_for(self.mic_q.get(), timeout=0.1)
            except TimeoutError:
                pcm = None
            voiced = False
            while not self.aec_q.empty():  # echo-cancelled signal: Sir's voice without Jarvis' own
                frame = self.aec_q.get_nowait()
                if listen_for_barge_in and self.voice_barge_in and (self.audio.playing or r.first_audio is None):
                    voiced = barge.feed(frame) or voiced
            if pcm is None and not voiced:
                continue
            woke = False
            if pcm is not None:
                self._level = min(1.0, rms(pcm) / 6000)
                if listen_for_barge_in:
                    woke = self.detector.feed(pcm)
            if not listen_for_barge_in:
                continue
            if woke or voiced:
                logger.info("Barge-in (%s)", "wake word" if woke else "voice")
                # keep what Sir said (voice barge-in) as the start of the next utterance
                self._primed = barge.captured() if voiced else np.zeros(0, dtype=np.int16)
                await self.interrupt()
                if drained is not None:
                    drained.cancel()
                return "barge_in"

    # ------------------------------------------------------------------ listening
    async def _stt(self, audio: npt.NDArray[np.int16]) -> str:
        r = await self.http.post(
            "/api/voice/stt", content=pcm_to_wav(audio, RATE), headers={"Content-Type": "audio/wav"}
        )
        return str(r.json().get("text", "")).strip() if r.status_code == 200 else ""

    async def listen(
        self, require_speech_within: float | None
    ) -> tuple[npt.NDArray[np.int16] | None, str | None, dict[str, float]]:
        """Record one utterance. Returns (audio, transcript-if-already-known, timing marks)."""
        marks: dict[str, float] = {}
        if self.ptt:
            collected: list[npt.NDArray[np.int16]] = []
            while self.ptt and sum(c.size for c in collected) < RATE * self.cfg.max_utterance_s:
                pcm = await self.mic_q.get()
                self._level = min(1.0, rms(pcm) / 6000)
                collected.append(pcm)
            marks["speech_end"] = marks["endpoint"] = time.perf_counter()
            return (np.concatenate(collected) if collected else None), None, marks

        ep = Endpointer(
            RATE,
            min_rms=self.cfg.vad_min_rms,
            spec_ms=self.cfg.speculative_stt_ms,
            max_utterance_s=self.cfg.max_utterance_s,
        )
        if self._primed is not None and self._primed.size:
            ep.prime(self._primed)
        self._primed = None
        spec: asyncio.Task[str] | None = None
        spec_len = 0
        started = time.perf_counter()
        while True:
            pcm = await self.mic_q.get()
            self._level = min(1.0, rms(pcm) / 6000)
            for ev in ep.feed(pcm):
                if ev == "pause":
                    marks["speech_end"] = time.perf_counter() - ep.silence_ms / 1000
                    audio_now = ep.audio()
                    spec_len = audio_now.size
                    spec = asyncio.create_task(self._stt(audio_now))
                    marks["spec_started"] = time.perf_counter()
                    await self._prepare()
                elif ev == "resume":
                    if spec is not None:
                        spec.cancel()
                    spec = None
            if self.ptt:  # PTT pressed while listening → switch modes
                return await self.listen(require_speech_within)
            if not ep.active:
                waited = time.perf_counter() - started
                if (require_speech_within and waited > require_speech_within) or (
                    not require_speech_within and waited > 8
                ):
                    return None, None, marks  # nobody spoke
                continue
            silence = ep.silence_ms
            done = ep.too_long or silence >= self.cfg.end_of_turn_silence_ms
            if not done and spec is not None and silence >= self.cfg.min_end_of_turn_silence_ms and spec.done():
                with contextlib.suppress(Exception):
                    done = looks_complete(spec.result())
            if done:
                marks["endpoint"] = time.perf_counter()
                marks.setdefault("speech_end", marks["endpoint"] - silence / 1000)
                if not ep.has_speech:
                    return None, None, marks
                audio = ep.audio()
                text: str | None = None
                if spec is not None and spec_len == audio.size:  # nothing new since the speculative run
                    try:
                        text = await spec
                        marks["stt_reused"] = 1
                    except Exception:
                        text = None
                elif spec is not None:
                    spec.cancel()
                return audio, text, marks

    async def _prepare(self) -> None:
        tag = (self.tag + 1) % 256
        if self._prepared == tag:
            return
        self._prepared = tag
        with contextlib.suppress(Exception):
            await self._send({"type": "prepare", "tag": tag})

    # ------------------------------------------------------------------ conversation
    async def handle_turn(self, audio: npt.NDArray[np.int16], text: str | None, marks: dict[str, float]) -> bool:
        """Returns True if the conversation should continue (follow-up window or barge-in)."""
        await self.set_state("thinking")
        if text is None:
            text = await self._stt(audio)
        marks["stt_done"] = time.perf_counter()
        if not text:
            await self.set_state("idle", "Nichts verstanden")
            return False
        text = strip_wake_word(text)
        if len(re.sub(r"\W", "", text)) < 3:  # fragments like "K." (tail of the wake word, a cough)
            logger.info("Ignored fragment: %r", text)
            return True
        logger.info("Heard: %s", text)
        if _END_PHRASES.search(text) and len(text) < 40:
            await self.say_text("Sehr wohl, Sir.")
            return False
        r = Reply(self._next_tag())
        self._prepared = None
        self.reply = r
        try:
            await self._send({"type": "turn", "tag": r.tag, "text": text, "session_id": self.session_id})
            outcome = await self._await_reply(r, listen_for_barge_in=True)
        finally:
            self.reply = None
        await self._report(r, marks, text, outcome)
        return outcome in ("done", "barge_in")

    async def _report(self, r: Reply, marks: dict[str, float], text: str, outcome: str) -> None:
        end = marks.get("speech_end", r.started)

        def ms(t: float | None) -> float | None:
            return round((t - end) * 1000, 1) if t is not None else None

        record = {
            "ts": time.time(),
            "outcome": outcome,
            "chars_heard": len(text),
            "endpoint_ms": ms(marks.get("endpoint")),
            "stt_done_ms": ms(marks.get("stt_done")),
            "stt_reused": bool(marks.get("stt_reused")),
            "first_audio_ms": ms(r.first_audio),  # end of Sir's speech → first audio chunk at the speaker
            "audio_backend": type(self.audio).__name__ if self.audio else None,
            "aec": bool(self.audio and self.audio.aec),
            "core": r.metrics,
            "error": r.error,
        }
        logger.info(
            "Turn latency: endpoint %s ms · transcript %s ms · first audio %s ms (agent first token %s ms, "
            "recall %s ms, tts first audio %s ms after first text)",
            record["endpoint_ms"],
            record["stt_done_ms"],
            record["first_audio_ms"],
            r.metrics.get("first_token_ms"),
            r.metrics.get("recall_ms"),
            _diff(r.metrics.get("first_audio_ms"), r.metrics.get("first_piece_ms")),
        )
        with contextlib.suppress(Exception):
            await self._send({"type": "metrics", **record})

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        tasks = [
            asyncio.create_task(self.events()),
            asyncio.create_task(self.speak_socket()),
            asyncio.create_task(self._level_ticker()),
        ]
        try:
            await self.open_audio()
        except Exception as e:
            logger.error("Microphone unavailable: %s", e)
            await self.set_state("error", f"Mikrofon nicht verfügbar: {e}")
        await self.set_state("idle")
        logger.info("Voice daemon ready – say 'Hey Jarvis'")
        try:
            while True:
                if self.muted or self.audio is None:
                    await asyncio.sleep(0.3)
                    continue
                pcm = await self.mic_q.get()
                self._level = min(1.0, rms(pcm) / 6000)
                woke = self.detector.feed(pcm)
                if not woke and not self.ptt:
                    continue
                if self.audio is not None:
                    await self.audio.play(earcon())
                self.detector.reset()
                while not self.mic_q.empty():  # the wake word itself is not part of the request
                    self.mic_q.get_nowait()
                await self.set_state("listening")
                follow_up = await self._converse(first=True)
                while follow_up:
                    await self.set_state("listening", "Folgefrage möglich")
                    follow_up = await self._converse(first=False)
                self.detector.reset()
                await self.set_state("idle")
        finally:
            for t in tasks:
                t.cancel()
            await self.close_audio()

    async def _converse(self, first: bool) -> bool:
        primed = self._primed is not None and self._primed.size > 0
        audio, text, marks = await self.listen(None if first or primed else float(self.cfg.followup_window_s))
        if audio is None or audio.size < RATE * 0.3:
            return False
        await self.set_state("recording")
        return await self.handle_turn(audio, text, marks)


_WAKE_PREFIX = re.compile(
    r"^\s*(?:(?:hey|hi|he|ey|hallo|okay|ok)[\s,.!]*)?(?:jarvis|javis|travis|jarwis|dschawis|chavis)\b[\s,.!?]*", re.I
)


def strip_wake_word(text: str) -> str:
    """Whisper often hears the tail of the wake word at the start ("Hey, Javis. Wie spät …")."""
    return _WAKE_PREFIX.sub("", text, count=1).strip()


def _diff(a: float | None, b: float | None) -> float | None:
    return round(a - b, 1) if a is not None and b is not None else None


_bg: set[asyncio.Task[Any]] = set()


def spawn_bg(coro: Any) -> None:
    t = asyncio.get_running_loop().create_task(coro)
    _bg.add(t)
    t.add_done_callback(_bg.discard)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    faulthandler.register(signal.SIGUSR1)  # `kill -USR1 <pid>` dumps all stacks to the log (hang diagnosis)
    asyncio.run(VoiceDaemon(get_settings()).run())


if __name__ == "__main__":
    main()
