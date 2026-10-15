"""Audio helpers: G.711 µ-law codec, resampling, WAV packing, simple energy VAD."""

from __future__ import annotations

import io
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
