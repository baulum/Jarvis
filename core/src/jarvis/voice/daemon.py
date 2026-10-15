"""Voice daemon: "Hey Jarvis" → listen → transcribe → Jarvis → streamed, interruptible speech.

Runs as its own local process (``jarvisctl voice``) because it owns the microphone and the
speakers (macOS-only concern, kept out of the server core). It talks to the core through the
normal device API (role ``voice_owner``):

* wake word (openWakeWord ONNX) or push-to-talk start a turn; an energy VAD detects the end
  of the utterance; whisper.cpp transcribes;
* the reply streams back as ``chat.delta`` events; complete sentences are synthesised
  immediately (TTS provider adapter in the core) → low latency;
* saying "Hey Jarvis" while Jarvis speaks interrupts (barge-in) — playback stops and the
  agent run is cancelled;
* after a reply a short follow-up window allows answering without the wake word; silence or
  phrases like "danke, das war's" end the conversation;
* mute closes the microphone stream entirely (macOS mic indicator turns off).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import numpy.typing as npt
import sounddevice as sd
import websockets

from jarvis.config import Settings, get_settings
from jarvis.voice.audio import EnergyVAD, pcm_to_wav, rms
from jarvis.voice.speech import speakable, split_sentences
from jarvis.voice.wakeword import CHUNK, RATE, WakeWordDetector

logger = logging.getLogger("jarvis.voice")
PLAY_RATE = 24000
_END_PHRASES = re.compile(
    r"\b(danke,? das war'?s|das wär'?s|das war alles|stopp|ende|tschüss|thanks,? that'?s all)\b", re.I
)


@dataclass
class Turn:
    session_id: str | None = None
    text_buffer: str = ""
    sentences: asyncio.Queue[str | None] = field(default_factory=asyncio.Queue)
    done: asyncio.Event = field(default_factory=asyncio.Event)


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
        self.mic_q: asyncio.Queue[npt.NDArray[np.int16]] = asyncio.Queue(maxsize=200)
        self.stream: sd.InputStream | None = None
        self.state = "idle"
        self.muted = False
        self.ptt = False
        self.session_id: str | None = None
        self.turn: Turn | None = None
        self.speaking = False
        self._stop_playback = asyncio.Event()
        self._level = 0.0
        self._last_status = 0.0

    # ------------------------------------------------------------------ audio in
    def _callback(self, indata: Any, frames: int, time_info: Any, status: Any) -> None:
        pcm = (indata[:, 0] * 32767).astype(np.int16) if indata.dtype != np.int16 else indata[:, 0].copy()
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self._enqueue, pcm)

    def _enqueue(self, pcm: npt.NDArray[np.int16]) -> None:
        if self.mic_q.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self.mic_q.get_nowait()
        self.mic_q.put_nowait(pcm)

    def open_mic(self) -> None:
        if self.stream is None:
            self.stream = sd.InputStream(
                samplerate=RATE, channels=1, dtype="int16", blocksize=CHUNK, callback=self._callback
            )
            self.stream.start()

    def close_mic(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None

    # ------------------------------------------------------------------ status
    async def set_state(self, state: str, detail: str | None = None) -> None:
        self.state = state
        await self._post_status(detail=detail, force=True)

    async def _post_status(self, detail: str | None = None, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_status < 0.2:
            return
        self._last_status = now
        try:
            await self.http.post(
                "/api/voice/status",
                json={
                    "state": self.state,
                    "muted": self.muted,
                    "wake_word": True,
                    "level": round(self._level, 3),
                    "mic_permission": "granted" if self.stream is not None or self.muted else "unknown",
                    "detail": detail,
                },
            )
        except httpx.HTTPError:
            pass

    # ------------------------------------------------------------------ core events
    async def events(self) -> None:
        url = self.base.replace("http", "ws") + f"/ws?token={self.token}&client=voice"
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    backoff = 1.0
                    logger.info("Connected to core event stream")
                    if self.turn is not None:  # events of a turn in flight were lost → end it cleanly
                        logger.warning("Core reconnected during a turn; aborting it")
                        await self.turn.sentences.put(None)
                        self.turn.done.set()
                    async for raw in ws:
                        await self._on_event(json.loads(raw))
            except Exception as e:
                logger.warning("Event stream lost (%s); reconnecting", e)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _on_event(self, ev: dict[str, Any]) -> None:
        t, d = ev.get("type"), ev.get("data", {})
        turn = self.turn
        if t == "chat.delta" and turn and d.get("session_id") == turn.session_id:
            turn.text_buffer += d.get("text", "")
            sentences, rest = split_sentences(turn.text_buffer)
            turn.text_buffer = rest
            for sentence in sentences:
                await turn.sentences.put(sentence)
        elif t == "chat.done" and turn and d.get("session_id") == turn.session_id:
            if turn.text_buffer.strip():
                await turn.sentences.put(turn.text_buffer.strip())
            turn.text_buffer = ""
            await turn.sentences.put(None)
            turn.done.set()
        elif t == "voice.command":
            await self._command(d.get("action", ""))
        elif t == "voice.say" and not self.muted and self.turn is None:
            await self.say_text(d.get("text", ""))

    async def _command(self, action: str) -> None:
        if action == "mute":
            self.muted = True
            self.close_mic()
            await self.interrupt()
            await self.set_state("muted")
        elif action == "unmute":
            self.muted = False
            self.open_mic()
            await self.set_state("idle")
        elif action == "ptt_start" and not self.muted:
            self.ptt = True
            await self.interrupt()
        elif action == "ptt_stop":
            self.ptt = False
        elif action == "interrupt":
            await self.interrupt()

    async def interrupt(self) -> None:
        if self.speaking or self.turn is not None:
            self._stop_playback.set()
            with contextlib.suppress(httpx.HTTPError):
                await self.http.post("/api/chat/interrupt")

    # ------------------------------------------------------------------ speech out
    async def _synth(self, text: str) -> bytes:
        r = await self.http.post("/api/voice/tts", json={"text": text, "sample_rate": PLAY_RATE})
        r.raise_for_status()
        return r.content

    async def _play(self, pcm: bytes) -> bool:
        """Play PCM through macOS' native player (afplay); returns False if interrupted.

        Feeding audio from a Python callback underran under load and crackled; afplay plays the
        whole buffer natively. A short fade-in/out removes clicks at sentence boundaries.
        """
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        fade = min(int(PLAY_RATE * 0.008), len(audio) // 2)
        if fade > 0:
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
            audio[:fade] *= ramp
            audio[-fade:] *= ramp[::-1]
        wav = pcm_to_wav(np.clip(audio, -32768, 32767).astype(np.int16), PLAY_RATE)
        with tempfile.NamedTemporaryFile(prefix="jarvis-tts-", suffix=".wav", delete=False) as f:
            f.write(wav)
            path = f.name
        try:
            proc = await asyncio.create_subprocess_exec("afplay", path)
            stop_wait = asyncio.create_task(self._stop_playback.wait())
            done_wait = asyncio.create_task(proc.wait())
            await asyncio.wait({stop_wait, done_wait}, return_when=asyncio.FIRST_COMPLETED)
            stop_wait.cancel()
            if proc.returncode is None:
                proc.terminate()
                await proc.wait()
        finally:
            Path(path).unlink(missing_ok=True)
        return not self._stop_playback.is_set()

    async def speak_stream(self, turn: Turn) -> None:
        """Pipelined TTS: sentence n+1 is synthesised while sentence n plays."""
        self.speaking = True
        self._stop_playback.clear()
        audio_q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=2)

        async def producer() -> None:
            try:
                while not self._stop_playback.is_set():
                    try:
                        sentence = await asyncio.wait_for(turn.sentences.get(), timeout=90)
                    except TimeoutError:
                        logger.warning("No reply within 90 s; ending turn")
                        break
                    if sentence is None:
                        break
                    text = speakable(sentence)
                    if text:
                        await audio_q.put(await self._synth(text))
            except httpx.HTTPError as e:
                logger.warning("TTS failed: %s", e)
            finally:
                with contextlib.suppress(asyncio.QueueFull):
                    audio_q.put_nowait(None)

        prod = asyncio.create_task(producer())
        try:
            while True:
                audio = await audio_q.get()
                if audio is None:
                    break
                if self.state != "speaking":
                    await self.set_state("speaking")
                if not await self._play(audio):
                    break
        finally:
            prod.cancel()
            self.speaking = False

    async def say_text(self, text: str) -> None:
        turn = Turn()
        for s in split_sentences(text + " ")[0] or [text]:
            await turn.sentences.put(s)
        await turn.sentences.put(None)
        await self.speak_stream(turn)
        await self.set_state("idle")

    # ------------------------------------------------------------------ conversation
    async def record_utterance(self, require_speech_within: float | None = None) -> npt.NDArray[np.int16] | None:
        """Push-to-talk: record while the button is held. Otherwise: VAD end-of-utterance detection."""
        if self.ptt:
            collected: list[npt.NDArray[np.int16]] = []
            while self.ptt and sum(c.size for c in collected) < RATE * self.cfg.max_utterance_s:
                pcm = await self.mic_q.get()
                self._level = min(1.0, rms(pcm) / 6000)
                await self._post_status()
                collected.append(pcm)
            return np.concatenate(collected) if collected else None
        vad = EnergyVAD(
            RATE,
            silence_ms=self.cfg.end_of_turn_silence_ms,
            max_utterance_s=self.cfg.max_utterance_s,
            min_rms=self.cfg.vad_min_rms,
            ratio=2.5,
        )
        started = time.monotonic()
        while True:
            pcm = await self.mic_q.get()
            self._level = min(1.0, rms(pcm) / 6000)
            await self._post_status()
            done = vad.feed(pcm)
            if done:
                return done[0]
            if self.ptt:  # PTT pressed while listening -> switch modes
                return await self.record_utterance(require_speech_within)
            if require_speech_within and not vad.active and time.monotonic() - started > require_speech_within:
                return None
            if not require_speech_within and not vad.active and time.monotonic() - started > 8:
                return None  # woke up but nobody spoke

    async def handle_turn(self, audio: npt.NDArray[np.int16]) -> bool:
        """Returns True if the conversation should continue (follow-up window)."""
        await self.set_state("thinking")
        r = await self.http.post(
            "/api/voice/stt", content=pcm_to_wav(audio, RATE), headers={"Content-Type": "audio/wav"}
        )
        text = r.json().get("text", "").strip() if r.status_code == 200 else ""
        if not text:
            await self.set_state("idle", "Nichts verstanden")
            return False
        if len(re.sub(r"\W", "", text)) < 3:  # fragments like "K." (tail of the wake word, a cough) → keep listening
            logger.info("Ignored fragment: %r", text)
            return True
        logger.info("Heard: %s", text)
        if _END_PHRASES.search(text) and len(text) < 40:
            await self.say_text("Sehr wohl, Sir.")
            return False
        self.turn = Turn()
        try:
            r = await self.http.post(
                "/api/chat", json={"message": text, "session_id": self.session_id, "channel": "voice"}
            )
            r.raise_for_status()
            self.turn.session_id = self.session_id = r.json()["session_id"]
            self.detector.reset()  # forget the audio that triggered this turn → no false barge-in
            speaker = asyncio.create_task(self.speak_stream(self.turn))
            # Keep listening for the wake word during the reply → barge-in.
            while not speaker.done():
                try:
                    pcm = await asyncio.wait_for(self.mic_q.get(), timeout=0.2)
                except TimeoutError:
                    continue
                if self.detector.feed(pcm):
                    logger.info("Barge-in")
                    await self.interrupt()
                    await speaker
                    return True
            interrupted = self._stop_playback.is_set()
        finally:
            self.turn = None
        return not interrupted

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        events = asyncio.create_task(self.events())
        try:
            self.open_mic()
        except Exception as e:
            logger.error("Microphone unavailable: %s", e)
            await self.set_state("error", f"Mikrofon nicht verfügbar: {e}")
        await self.set_state("idle")
        logger.info("Voice daemon ready – say 'Hey Jarvis'")
        try:
            while True:
                if self.muted or self.stream is None:
                    await asyncio.sleep(0.3)
                    continue
                pcm = await self.mic_q.get()
                self._level = min(1.0, rms(pcm) / 6000)
                await self._post_status()
                woke = self.detector.feed(pcm)
                if not woke and not self.ptt:
                    continue
                _earcon()
                self.detector.reset()
                await self.set_state("listening")
                follow_up = await self._converse(first=True)
                while follow_up:
                    await self.set_state("listening", "Folgefrage möglich")
                    follow_up = await self._converse(first=False)
                self.detector.reset()
                await self.set_state("idle")
        finally:
            events.cancel()
            self.close_mic()

    async def _converse(self, first: bool) -> bool:
        audio = await self.record_utterance(None if first else float(self.cfg.followup_window_s))
        if audio is None or audio.size < RATE * 0.3:
            return False
        await self.set_state("recording")
        return await self.handle_turn(audio)


def _earcon() -> None:
    sound = Path("/System/Library/Sounds/Tink.aiff")
    if sound.exists():
        subprocess.Popen(["afplay", "-v", "0.4", str(sound)])


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(VoiceDaemon(get_settings()).run())


if __name__ == "__main__":
    main()
