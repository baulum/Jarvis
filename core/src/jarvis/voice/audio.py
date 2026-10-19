"""Audio helpers: G.711 µ-law codec, resampling, WAV packing, simple energy VAD."""

from __future__ import annotations

import io
import re
import wave
from typing import Any

import numpy as np
import numpy.typing as npt

_BIAS = 0x84
_CLIP = 32635


def _build_ulaw_decode() -> npt.NDArray[np.int16]:
    table = np.zeros(256, dtype=np.int16)
    for i in range(256):
        u = ~i & 0xFF
        sign = u & 0x80
        exponent = (u >> 4) & 0x07
        mantissa = u & 0x0F
        sample = ((mantissa << 3) + _BIAS) << exponent
        sample -= _BIAS
        table[i] = -sample if sign else sample
    return table


_ULAW_DECODE = _build_ulaw_decode()


def ulaw_decode(data: bytes) -> npt.NDArray[np.int16]:
    return _ULAW_DECODE[np.frombuffer(data, dtype=np.uint8)]


def ulaw_encode(pcm: npt.NDArray[np.int16]) -> bytes:
    x = pcm.astype(np.int32)
    sign = np.where(x < 0, 0x80, 0)
    x = np.minimum(np.abs(x), _CLIP) + _BIAS
    exponent = np.floor(np.log2(np.maximum(x, 1))).astype(np.int32) - 7
    exponent = np.clip(exponent, 0, 7)
    mantissa = (x >> (exponent + 3)) & 0x0F
    u = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return bytes(u.astype(np.uint8).tobytes())


def resample(pcm: npt.NDArray[np.int16], src_rate: int, dst_rate: int) -> npt.NDArray[np.int16]:
    if src_rate == dst_rate or pcm.size == 0:
        return pcm
    x: npt.NDArray[np.floating[Any]] = pcm.astype(np.float32)
    if dst_rate < src_rate:  # crude anti-aliasing: moving average over the decimation factor
        k = max(1, round(src_rate / dst_rate))
        if k > 1:
            x = np.convolve(x, np.ones(k, dtype=np.float32) / k, mode="same")
    n_out = round(len(x) * dst_rate / src_rate)
    src_idx = np.linspace(0, len(x) - 1, n_out, dtype=np.float32)
    out = np.interp(src_idx, np.arange(len(x), dtype=np.float32), x)
    result: npt.NDArray[np.int16] = np.clip(out, -32768, 32767).astype(np.int16)
    return result


def pcm_to_wav(pcm: npt.NDArray[np.int16] | bytes, rate: int) -> bytes:
    raw = pcm.tobytes() if isinstance(pcm, np.ndarray) else pcm
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(raw)
    return buf.getvalue()


def rms(pcm: npt.NDArray[np.int16]) -> float:
    if pcm.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)))


class EnergyVAD:
    """Adaptive energy-based voice activity detector with end-of-utterance detection.

    Tracks a noise floor; a frame is speech when its RMS exceeds ``floor * ratio`` and an
    absolute minimum. An utterance ends after ``silence_ms`` of non-speech.
    """

    def __init__(
        self,
        rate: int,
        frame_ms: int = 20,
        silence_ms: int = 800,
        ratio: float = 3.0,
        min_rms: float = 300.0,
        min_speech_ms: int = 250,
        max_utterance_s: float = 30.0,
    ) -> None:
        self.rate = rate
        self.frame = int(rate * frame_ms / 1000)
        self.frame_ms = frame_ms
        self.silence_frames = silence_ms // frame_ms
        self.min_speech_frames = min_speech_ms // frame_ms
        self.max_frames = int(max_utterance_s * 1000 / frame_ms)
        self.ratio = ratio
        self.min_rms = min_rms
        self.floor = 200.0
        self.reset()

    def reset(self) -> None:
        self.active = False
        self.buffer: list[npt.NDArray[np.int16]] = []
        self.speech_frames = 0
        self.silent_run = 0
        self._pending = np.zeros(0, dtype=np.int16)

    def is_speech(self, frame: npt.NDArray[np.int16]) -> bool:
        level = rms(frame)
        speech = level > max(self.min_rms, self.floor * self.ratio)
        if not speech:
            self.floor = 0.95 * self.floor + 0.05 * level
        return speech

    def feed(self, pcm: npt.NDArray[np.int16]) -> list[npt.NDArray[np.int16]]:
        """Feed audio; returns completed utterances."""
        done: list[npt.NDArray[np.int16]] = []
        data = np.concatenate([self._pending, pcm])
        n = len(data) // self.frame
        self._pending = data[n * self.frame :]
        for i in range(n):
            frame = data[i * self.frame : (i + 1) * self.frame]
            speech = self.is_speech(frame)
            if speech:
                if not self.active:
                    self.active = True
                    self.buffer = []
                    self.speech_frames = 0
                self.speech_frames += 1
                self.silent_run = 0
                self.buffer.append(frame)
            elif self.active:
                self.silent_run += 1
                self.buffer.append(frame)
                if self.silent_run >= self.silence_frames:
                    if self.speech_frames >= self.min_speech_frames:
                        done.append(np.concatenate(self.buffer))
                    self.active = False
                    self.buffer = []
            if self.active and len(self.buffer) >= self.max_frames:
                done.append(np.concatenate(self.buffer))
                self.active = False
                self.buffer = []
        return done


_INCOMPLETE_TAIL = frozenset(
    """und oder aber also dann weil dass ob wenn sodass ähm äh öhm hm mhm mit für von zu zum zur der die das den dem
    des ein eine einen einem einer im am an auf in ich wir du er sie es noch bitte sowie bzw beziehungsweise sondern
    nämlich sprich quasi irgendwie halt""".split()
)


def looks_complete(text: str) -> bool:
    """Heuristic: does a transcript sound like a finished turn (vs. a thinking pause mid-sentence)?"""
    t = text.strip()
    if not t:
        return False
    words = re.findall(r"[\wäöüß]+", t.lower())
    if not words:
        return False
    if words[-1] in _INCOMPLETE_TAIL or t.endswith((",", "-", "–", "…", "...")):
        return False
    return t.endswith(("?", "!", ".")) or len(words) >= 3


class Endpointer:
    """Streaming end-of-turn detection.

    Emits ``"start"`` when speech begins, ``"pause"`` once a pause reaches ``spec_ms`` (the moment to start a
    speculative transcription) and ``"resume"`` when speech continues after such a pause. The caller ends the
    turn based on ``silence_ms`` and the speculative transcript (``looks_complete``): short pause when the
    sentence is finished, the long one when it clearly is not.
    """

    def __init__(
        self,
        rate: int = 16000,
        frame_ms: int = 20,
        min_rms: float = 180,
        ratio: float = 2.5,
        min_speech_ms: int = 250,
        spec_ms: int = 280,
        max_utterance_s: float = 30,
        preroll_ms: int = 240,
    ) -> None:
        self.rate = rate
        self.frame = int(rate * frame_ms / 1000)
        self.frame_ms = frame_ms
        self.min_rms = min_rms
        self.ratio = ratio
        self.min_speech_frames = max(1, min_speech_ms // frame_ms)
        self.spec_frames = max(1, spec_ms // frame_ms)
        self.max_frames = int(max_utterance_s * 1000 / frame_ms)
        self.preroll_frames = preroll_ms // frame_ms
        self.floor = 200.0
        self._pending = np.zeros(0, dtype=np.int16)
        self._preroll: list[npt.NDArray[np.int16]] = []
        self.reset()

    def reset(self) -> None:
        self.active = False
        self.frames: list[npt.NDArray[np.int16]] = []
        self.speech_frames = 0
        self.silent_run = 0
        self.paused = False
        self.last_speech_frame = 0

    def prime(self, audio: npt.NDArray[np.int16]) -> None:
        """Start an utterance with audio that was already captured (e.g. the speech that caused a barge-in)."""
        self.reset()
        n = len(audio) // self.frame
        if n:
            self.active = True
            self.frames = [audio[i * self.frame : (i + 1) * self.frame] for i in range(n)]
            self.speech_frames = n
            self.last_speech_frame = n

    @property
    def silence_ms(self) -> int:
        return self.silent_run * self.frame_ms if self.active else 0

    @property
    def too_long(self) -> bool:
        return len(self.frames) >= self.max_frames

    @property
    def has_speech(self) -> bool:
        return self.speech_frames >= self.min_speech_frames

    def audio(self) -> npt.NDArray[np.int16]:
        """The utterance so far, trailing silence trimmed to 200 ms."""
        keep = min(len(self.frames), self.last_speech_frame + 10)
        return np.concatenate(self.frames[:keep]) if keep else np.zeros(0, dtype=np.int16)

    def _is_speech(self, frame: npt.NDArray[np.int16]) -> bool:
        level = rms(frame)
        speech = level > max(self.min_rms, self.floor * self.ratio)
        if not speech:
            self.floor = 0.95 * self.floor + 0.05 * level
        return speech

    def feed(self, pcm: npt.NDArray[np.int16]) -> list[str]:
        events: list[str] = []
        data = np.concatenate([self._pending, pcm])
        n = len(data) // self.frame
        self._pending = data[n * self.frame :]
        for i in range(n):
            frame = data[i * self.frame : (i + 1) * self.frame]
            speech = self._is_speech(frame)
            if not self.active:
                self._preroll = [*self._preroll[-(self.preroll_frames - 1) :], frame] if self.preroll_frames else []
                if speech:
                    self.active = True
                    self.frames = list(self._preroll)
                    self.speech_frames = 1
                    self.silent_run = 0
                    self.last_speech_frame = len(self.frames)
                    events.append("start")
                continue
            self.frames.append(frame)
            if speech:
                self.speech_frames += 1
                if self.paused:
                    events.append("resume")
                    self.paused = False
                self.silent_run = 0
                self.last_speech_frame = len(self.frames)
            else:
                self.silent_run += 1
                if self.silent_run == self.spec_frames and self.has_speech:
                    self.paused = True
                    events.append("pause")
        return events


class BargeInDetector:
    """Sustained speech of Sir while Jarvis talks (echo-cancelled microphone).

    The residual echo sets an adaptive floor; speech must exceed ``max(min_rms, floor * ratio)`` for
    ``need_ms`` within a short window. The recent audio is kept so the interrupting words are not lost.
    """

    def __init__(
        self,
        rate: int = 16000,
        frame_ms: int = 20,
        need_ms: int = 320,
        min_rms: float = 450,
        ratio: float = 3.0,
        peak_hold: bool = False,
    ) -> None:
        # peak_hold: raw microphone without echo cancellation – the floor follows the peaks of Jarvis' own
        # voice coming out of the speakers, so only something clearly louder (Sir) counts as an interruption.
        self.peak_hold = peak_hold
        self.frame = int(rate * frame_ms / 1000)
        self.frame_ms = frame_ms
        self.need = max(1, need_ms // frame_ms)
        self.window = int(self.need * 1.6)
        self.min_rms = min_rms
        self.ratio = ratio
        self.reset()

    def reset(self) -> None:
        self.floor = self.min_rms / self.ratio
        self.hits: list[bool] = []
        self.recent: list[npt.NDArray[np.int16]] = []
        self._pending = np.zeros(0, dtype=np.int16)
        self.warmup = 10  # ~200 ms for the echo canceller to settle after playback starts

    def feed(self, pcm: npt.NDArray[np.int16]) -> bool:
        data = np.concatenate([self._pending, pcm])
        n = len(data) // self.frame
        self._pending = data[n * self.frame :]
        fired = False
        for i in range(n):
            frame = data[i * self.frame : (i + 1) * self.frame]
            self.recent = [*self.recent[-(self.window + 10) :], frame]
            level = rms(frame)
            if self.warmup > 0:
                self.warmup -= 1
                self.floor = max(self.floor, 0.5 * self.floor + 0.5 * level)
                continue
            speech = level > max(self.min_rms, self.floor * self.ratio)
            if not speech:
                self.floor = max(level, self.floor * 0.995) if self.peak_hold else 0.97 * self.floor + 0.03 * level
            self.hits = [*self.hits[-(self.window - 1) :], speech]
            if sum(self.hits) >= self.need:
                fired = True
        return fired

    def captured(self) -> npt.NDArray[np.int16]:
        return np.concatenate(self.recent) if self.recent else np.zeros(0, dtype=np.int16)
