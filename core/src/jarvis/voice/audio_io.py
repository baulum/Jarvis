"""Microphone + speaker for the voice daemon.

``NativeAudio`` runs the Swift helper ``jarvis-audio`` (one AVAudioEngine with Apple's voice processing:
echo cancellation + noise suppression; PCM is streamed in and played the moment it arrives).
``FallbackAudio`` is the previous path (sounddevice microphone, ``afplay`` per sentence) for when the
helper is missing or fails – no echo cancellation, so barge-in then needs the wake word.

Both deliver 16 kHz int16 microphone frames to ``on_mic`` (called in the event loop) and accept 24 kHz
int16 PCM for playback. ``flush()`` silences immediately (barge-in), ``hold()``/``resume()`` pause and
continue without losing anything (a barge-in that may be Jarvis' own voice), ``drained(tag)`` resolves once
everything queued before it has actually been heard.
"""

from __future__ import annotations

import abc
import asyncio
import contextlib
import json
import logging
import struct
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from jarvis.voice.audio import pcm_to_wav

logger = logging.getLogger("jarvis.voice.audio")
MIC_RATE = 16000
PLAY_RATE = 24000
MicCallback = Callable[[npt.NDArray[np.int16]], None]


class AudioIO(abc.ABC):
    aec = False
    out_level = 0.0
    in_level = 0.0
    on_aec: MicCallback | None = None  # echo-cancelled microphone (only with ``aec``), for barge-in

    @abc.abstractmethod
    async def start(self, on_mic: MicCallback) -> None: ...

    @abc.abstractmethod
    async def stop(self) -> None: ...

    @abc.abstractmethod
    async def play(self, pcm: bytes) -> None:
        """Queue 24 kHz int16 mono PCM for playback (returns immediately)."""

    @abc.abstractmethod
    async def flush(self) -> None:
        """Stop playback now and drop everything queued."""

    async def hold(self) -> bool:
        """Silence playback now but keep what is queued, so ``resume`` continues mid-sentence (a barge-in that may
        turn out to be Jarvis' own voice). Returns False if the backend cannot pause – then it flushed instead."""
        await self.flush()
        return False

    async def resume(self) -> None:  # noqa: B027 - only meaningful after a successful ``hold``
        pass

    @abc.abstractmethod
    async def drained(self, wait_s: float = 120) -> bool:
        """Wait until all queued audio was played; False if flushed or timed out."""

    @property
    @abc.abstractmethod
    def playing(self) -> bool: ...

    async def mute(self, muted: bool) -> None:  # noqa: B027 - optional
        pass


class NativeAudio(AudioIO):
    def __init__(self, helper: Path, aec: bool = True) -> None:
        self.helper = helper
        self.want_aec = aec
        self.proc: asyncio.subprocess.Process | None = None
        self._on_mic: MicCallback | None = None
        self._tasks: list[asyncio.Task[Any]] = []
        self._ready = asyncio.Event()
        self._drain_waiters: dict[int, asyncio.Future[bool]] = {}
        self._tag = 0
        self._queued = 0  # bytes queued since the last drain/flush
        self._playing = False
        self.can_hold = False  # helper understands 'S'/'C' (an older build ignores them → flush instead)
        self.error: str | None = None

    async def start(self, on_mic: MicCallback) -> None:
        self._on_mic = on_mic
        args = [str(self.helper)] + ([] if self.want_aec else ["--no-aec"])
        self.proc = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        self._tasks = [asyncio.create_task(self._read_mic()), asyncio.create_task(self._read_events())]
        try:
            await asyncio.wait_for(self._ready.wait(), 8)
        except TimeoutError:
            raise RuntimeError(self.error or "Audiohelfer meldet sich nicht") from None
        if self.error and not self._ready.is_set():
            raise RuntimeError(self.error)

    async def _read_mic(self) -> None:
        """Frames: kind 'R' (raw microphone) → ``on_mic``; 'A' (echo-cancelled) → ``on_aec``."""
        assert self.proc and self.proc.stdout
        out = self.proc.stdout
        try:
            while True:
                head = await out.readexactly(5)
                kind, size = head[0], struct.unpack("<I", head[1:])[0]
                payload = await out.readexactly(size) if size else b""
                pcm = np.frombuffer(payload, dtype=np.int16).copy()
                if kind == ord("R") and self._on_mic:
                    self._on_mic(pcm)
                elif kind == ord("A") and self.on_aec:
                    self.on_aec(pcm)
        except asyncio.IncompleteReadError:
            pass
        logger.warning("audio helper microphone stream ended")

    async def _read_events(self) -> None:
        assert self.proc and self.proc.stderr
        async for raw in self.proc.stderr:
            line = raw.decode(errors="replace").strip()
            if not line.startswith("EV "):
                if line:
                    logger.debug("jarvis-audio: %s", line)
                continue
            try:
                ev = json.loads(line[3:])
            except json.JSONDecodeError:
                continue
            kind = ev.get("event")
            if kind == "ready":
                self.aec = bool(ev.get("aec"))
                self.can_hold = bool(ev.get("hold"))
                logger.info(
                    "Native audio ready (echo cancellation: %s, %s Hz × %s)",
                    self.aec,
                    ev.get("in_rate"),
                    ev.get("in_channels"),
                )
                self._ready.set()
            elif kind == "level":
                self.in_level = float(ev.get("in", 0))
                self.out_level = float(ev.get("out", 0))
                self._playing = bool(ev.get("playing"))
            elif kind == "drained":
                fut = self._drain_waiters.pop(int(ev.get("tag", -1)), None)
                if fut and not fut.done():
                    fut.set_result(True)
            elif kind == "error":
                self.error = str(ev.get("detail"))
                logger.error("jarvis-audio: %s", self.error)
            elif kind in ("warning", "config_change"):
                logger.warning("jarvis-audio %s: %s", kind, ev.get("detail", ""))

    async def _send(self, kind: bytes, payload: bytes = b"") -> None:
        if self.proc is None or self.proc.stdin is None or self.proc.returncode is not None:
            raise RuntimeError("Audiohelfer läuft nicht")
        self.proc.stdin.write(kind + struct.pack("<I", len(payload)) + payload)
        await self.proc.stdin.drain()

    async def play(self, pcm: bytes) -> None:
        if pcm:
            self._queued += len(pcm)
            self._playing = True
            await self._send(b"P", pcm)

    async def flush(self) -> None:
        for fut in self._drain_waiters.values():
            if not fut.done():
                fut.set_result(False)
        self._drain_waiters.clear()
        self._queued = 0
        self._playing = False
        with contextlib.suppress(RuntimeError, ConnectionError):
            await self._send(b"F")

    async def hold(self) -> bool:
        if not self.can_hold:
            return await super().hold()
        try:
            await self._send(b"S")
        except (RuntimeError, ConnectionError):
            return await super().hold()
        return True

    async def resume(self) -> None:
        with contextlib.suppress(RuntimeError, ConnectionError):
            await self._send(b"C")

    async def drained(self, wait_s: float = 120) -> bool:
        self._tag = (self._tag + 1) % 256
        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._drain_waiters[self._tag] = fut
        await self._send(b"E", bytes([self._tag]))
        try:
            ok = await asyncio.wait_for(fut, wait_s)
        except TimeoutError:
            ok = False
        self._queued = 0
        self._playing = False
        return ok

    @property
    def playing(self) -> bool:
        return self._playing

    async def stop(self) -> None:
        with contextlib.suppress(Exception):
            await self._send(b"Q")
        for t in self._tasks:
            t.cancel()
        if self.proc is not None and self.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.proc.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.proc.wait(), 3)


class FallbackAudio(AudioIO):
    """sounddevice microphone + afplay playback (sentence-sized buffers, fades against clicks)."""

    def __init__(self) -> None:
        self.stream: Any = None
        self._q: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._player: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._proc: asyncio.subprocess.Process | None = None

    async def start(self, on_mic: MicCallback) -> None:
        import sounddevice as sd

        loop = asyncio.get_running_loop()

        def cb(indata: Any, frames: int, time_info: Any, status: Any) -> None:
            pcm = indata[:, 0].copy() if indata.dtype == np.int16 else (indata[:, 0] * 32767).astype(np.int16)
            loop.call_soon_threadsafe(on_mic, pcm)

        self.stream = sd.InputStream(samplerate=MIC_RATE, channels=1, dtype="int16", blocksize=1280, callback=cb)
        self.stream.start()
        self._player = asyncio.create_task(self._play_loop())

    async def mute(self, muted: bool) -> None:
        if self.stream is None:
            return
        if muted:
            self.stream.stop()  # closes the microphone (macOS indicator turns off)
        else:
            self.stream.start()

    async def _play_loop(self) -> None:
        buf = bytearray()
        while True:
            item = await self._q.get()
            if item is not None:
                buf.extend(item)
            # play in ~0.6 s blocks (or what is there when the stream pauses)
            if item is None or len(buf) >= PLAY_RATE * 2 * 0.6 or self._q.empty():
                if buf and not self._stop.is_set():
                    await self._afplay(bytes(buf))
                buf.clear()
            if self._q.empty():
                self._idle.set()

    async def _afplay(self, pcm: bytes) -> None:
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        fade = min(int(PLAY_RATE * 0.006), len(audio) // 2)
        if fade > 0:
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
            audio[:fade] *= ramp
            audio[-fade:] *= ramp[::-1]
        self.out_level = float(min(1.0, np.sqrt(np.mean(audio**2)) / 8000)) if audio.size else 0.0
        with tempfile.NamedTemporaryFile(prefix="jarvis-tts-", suffix=".wav", delete=False) as f:
            f.write(pcm_to_wav(np.clip(audio, -32768, 32767).astype(np.int16), PLAY_RATE))
            path = f.name
        try:
            self._proc = await asyncio.create_subprocess_exec("afplay", path)
            await self._proc.wait()
        finally:
            self._proc = None
            self.out_level = 0.0
            Path(path).unlink(missing_ok=True)

    async def play(self, pcm: bytes) -> None:
        self._stop.clear()
        self._idle.clear()
        self._q.put_nowait(pcm)

    async def flush(self) -> None:
        self._stop.set()
        while not self._q.empty():
            self._q.get_nowait()
        if self._proc is not None and self._proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._proc.terminate()
        self._idle.set()

    async def drained(self, wait_s: float = 120) -> bool:
        self._q.put_nowait(None)
        try:
            await asyncio.wait_for(self._idle.wait(), wait_s)
        except TimeoutError:
            return False
        return not self._stop.is_set()

    @property
    def playing(self) -> bool:
        return not self._idle.is_set()

    async def stop(self) -> None:
        await self.flush()
        if self._player:
            self._player.cancel()
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()


class SounddeviceAudio(FallbackAudio):
    """Windows/Linux: microphone *and* speaker over sounddevice (PortAudio) – ``afplay`` only exists on macOS.

    Same block-wise playback with fades as ``FallbackAudio``; ``flush`` stops the output stream at once."""

    async def _afplay(self, pcm: bytes) -> None:
        import sounddevice as sd

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        fade = min(int(PLAY_RATE * 0.006), len(audio) // 2)
        if fade > 0:
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
            audio[:fade] *= ramp
            audio[-fade:] *= ramp[::-1]
        self.out_level = float(min(1.0, np.sqrt(np.mean(audio**2)) / 8000)) if audio.size else 0.0
        try:
            sd.play(np.clip(audio, -32768, 32767).astype(np.int16), PLAY_RATE, blocking=False)
            await asyncio.to_thread(sd.wait)
        finally:
            self.out_level = 0.0

    async def flush(self) -> None:
        self._stop.set()
        while not self._q.empty():
            self._q.get_nowait()
        with contextlib.suppress(Exception):
            import sounddevice as sd

            sd.stop()
        self._idle.set()


def helper_path(data_dir: Path) -> Path:
    return data_dir / "bin" / "jarvis-audio"


async def open_audio(data_dir: Path, on_mic: MicCallback, *, native: bool = True, aec: bool = False) -> AudioIO:
    """Native helper (optionally with echo cancellation) if available, otherwise the fallback."""
    helper = helper_path(data_dir)
    if native and helper.exists():
        audio = NativeAudio(helper, aec=aec)
        try:
            await audio.start(on_mic)
            return audio
        except Exception as e:
            logger.warning("Native audio unavailable (%s); falling back to sounddevice/afplay", e)
            await audio.stop()
    fb = FallbackAudio() if sys.platform == "darwin" else SounddeviceAudio()
    await fb.start(on_mic)
    return fb
