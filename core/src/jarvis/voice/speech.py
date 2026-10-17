"""Speech-to-text (whisper.cpp server) and text-to-speech provider adapters.

TTS providers implement ``TTSProvider``; new providers (ElevenLabs, Piper, ...) only need
``synthesize`` (and optionally ``stream``) returning 16-bit mono PCM at the requested rate.
"""

from __future__ import annotations

import abc
import asyncio
import logging
import re
import shutil
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import numpy as np

from jarvis.config import TtsCfg
from jarvis.secrets import secret_store
from jarvis.voice.audio import pcm_to_wav
from jarvis.voice.fish_stream import FishStreamSession

logger = logging.getLogger(__name__)


class STTService:
    """Client for a local ``whisper-server`` (whisper.cpp) process."""

    def __init__(self, server_url: str, language: str = "de") -> None:
        self.server_url = server_url.rstrip("/")
        self.language = language
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=3))

    async def healthy(self) -> bool:
        try:
            r = await self._http.get(self.server_url + "/", timeout=3)
            return r.status_code < 500
        except httpx.HTTPError:
            return False

    async def transcribe_wav(self, wav: bytes, language: str | None = None) -> str:
        """whisper-server runs with a reduced audio context (``-ac 768`` ≈ 15 s, half the latency of the full
        30 s window); longer utterances are split at quiet points and transcribed piece by piece."""
        import io
        import wave

        try:
            with wave.open(io.BytesIO(wav)) as w:
                rate, frames = w.getframerate(), w.readframes(w.getnframes())
                mono16 = w.getnchannels() == 1 and w.getsampwidth() == 2
        except (wave.Error, EOFError):
            mono16, rate, frames = False, 16000, b""
        if mono16 and len(frames) / 2 / rate > MAX_SEGMENT_S:
            pcm = np.frombuffer(frames, dtype=np.int16)
            parts = [await self._transcribe_one(pcm_to_wav(seg, rate), language) for seg in split_long(pcm, rate)]
            return " ".join(p for p in parts if p)
        return await self._transcribe_one(wav, language)

    async def _transcribe_one(self, wav: bytes, language: str | None = None) -> str:
        files = {"file": ("audio.wav", wav, "audio/wav")}
        data = {
            "response_format": "json",
            "temperature": "0.0",
            "language": language or self.language,
            # Biases recognition towards domain vocabulary ("Jarvis" was heard as "Travis").
            "prompt": "Jarvis, Sir, Lexware, Telegram, Obsidian, Claude Code, GitHub.",
        }
        r = await self._http.post(self.server_url + "/inference", files=files, data=data)
        r.raise_for_status()
        text = str(r.json().get("text", "")).strip()
        return _clean_transcript(text)

    async def transcribe_pcm(self, pcm: np.ndarray, rate: int = 16000, language: str | None = None) -> str:
        return await self.transcribe_wav(pcm_to_wav(pcm.astype(np.int16), rate), language)

    async def transcribe_file(self, path: Path) -> str:
        """Any audio format (ogg/opus from Telegram, mp3 ...) via ffmpeg → 16 kHz mono WAV."""
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise RuntimeError("ffmpeg fehlt")
        proc = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-f",
            "wav",
            "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg: {err.decode()[:200]}")
        return await self.transcribe_wav(out)


MAX_SEGMENT_S = 14.0


def split_long(pcm: np.ndarray, rate: int, max_s: float = MAX_SEGMENT_S) -> list[np.ndarray]:
    """Split audio into ≤ max_s segments, cutting at the quietest 100 ms between 60 % and 100 % of max_s."""
    out: list[np.ndarray] = []
    rest = pcm
    hop = rate // 10
    while len(rest) / rate > max_s:
        lo, hi = int(rate * max_s * 0.6), int(rate * max_s)
        window = rest[lo:hi].astype(np.float32)
        n = len(window) // hop
        energies = [float(np.mean(window[i * hop : (i + 1) * hop] ** 2)) for i in range(n)] or [0.0]
        cut = lo + int(np.argmin(energies)) * hop + hop // 2
        out.append(rest[:cut])
        rest = rest[cut:]
    out.append(rest)
    return out


_NOISE = re.compile(r"\[(?:BLANK_AUDIO|Musik|Music|MUSIK|Applaus|Stille|silence)\]|\((?:Musik|music)\)", re.I)


def _collapse_repeats(text: str) -> str:
    """whisper loops on (digital) silence: "Wie spät ist es?\n Wie spät ist es?\n …" → once."""
    parts = [p.strip() for p in re.split(r"\n+|(?<=[.!?])\s+", text) if p.strip()]
    out: list[str] = []
    for p in parts:
        if not out or p.lower() != out[-1].lower():
            out.append(p)
    return " ".join(out)


def _clean_transcript(text: str) -> str:
    text = _collapse_repeats(_NOISE.sub("", text).strip())
    # whisper hallucinations on silence
    if text.lower().strip(" .!") in (
        "",
        "untertitel im auftrag des zdf",
        "vielen dank",
        "untertitel von stephanie geiges",
        "copyright wdr 2021",
        "thank you",
        "thanks for watching",
    ):
        return ""
    return text


class TTSProvider(abc.ABC):
    name: str

    @abc.abstractmethod
    async def synthesize(self, text: str, sample_rate: int) -> bytes:
        """Return 16-bit little-endian mono PCM at ``sample_rate``."""

    async def stream(self, text: str, sample_rate: int) -> AsyncIterator[bytes]:
        yield await self.synthesize(text, sample_rate)

    async def available(self) -> bool:
        return True


class MacSayTTS(TTSProvider):
    name = "macos"

    def __init__(self, voice: str, rate: int) -> None:
        self.voice = voice
        self.rate = rate

    async def available(self) -> bool:
        return shutil.which("say") is not None and shutil.which("ffmpeg") is not None

    async def synthesize(self, text: str, sample_rate: int) -> bytes:
        with tempfile.TemporaryDirectory() as td:
            aiff = Path(td) / "out.aiff"
            proc = await asyncio.create_subprocess_exec(
                "say",
                "-v",
                self.voice,
                "-r",
                str(self.rate),
                "-o",
                str(aiff),
                "--",
                text,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"say: {err.decode()[:200]}")
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-nostdin",
                "-loglevel",
                "error",
                "-i",
                str(aiff),
                "-ar",
                str(sample_rate),
                "-ac",
                "1",
                "-f",
                "s16le",
                "-",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"ffmpeg: {err.decode()[:200]}")
            return out


class FishAudioTTS(TTSProvider):
    name = "fish_audio"
    SUPPORTED_RATES = (8000, 16000, 24000, 32000, 44100)

    def __init__(self, api_key_secret: str, reference_id: str, model: str, chunk_length: int = 100) -> None:
        self.api_key_secret = api_key_secret
        self.reference_id = reference_id
        self.model = model
        self.chunk_length = chunk_length
        # One pooled client: a warm TLS connection saves ~200 ms per request (measured 480 → 260 ms TTFB).
        self._http: httpx.AsyncClient | None = None
        self._warmed = 0.0

    def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url="https://api.fish.audio",
                timeout=httpx.Timeout(60, connect=5),
                limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=120),
            )
        return self._http

    def api_key(self) -> str | None:
        return secret_store.get(self.api_key_secret)

    async def warm(self) -> None:
        """Open (or keep) the TLS connection before the first sentence is ready."""
        if time.monotonic() - self._warmed < 60:
            return
        key = self.api_key()
        if not key or not self.reference_id:
            return
        try:
            await self._client().get(f"/model/{self.reference_id}", headers={"Authorization": f"Bearer {key}"})
            self._warmed = time.monotonic()
        except httpx.HTTPError:
            logger.debug("Fish Audio warm-up failed", exc_info=True)

    async def available(self) -> bool:
        return bool(secret_store.get(self.api_key_secret)) and bool(self.reference_id)

    async def stream(self, text: str, sample_rate: int) -> AsyncIterator[bytes]:
        key = secret_store.get(self.api_key_secret)
        if not key:
            raise RuntimeError("Fish-Audio-API-Key fehlt")
        rate = sample_rate if sample_rate in self.SUPPORTED_RATES else 16000
        body = {
            "text": text,
            "format": "pcm",
            "sample_rate": rate,
            "latency": "balanced",
            "normalize": True,
            "chunk_length": self.chunk_length,
        }
        if self.reference_id:
            body["reference_id"] = self.reference_id
        async with self._client().stream(
            "POST", "/v1/tts", json=body, headers={"Authorization": f"Bearer {key}", "model": self.model}
        ) as r:
            if r.status_code >= 400:
                detail = (await r.aread()).decode(errors="replace")[:200]
                raise RuntimeError(f"Fish Audio {r.status_code}: {detail}")
            carry = b""
            async for chunk in r.aiter_bytes():
                data = carry + chunk
                cut = len(data) - (len(data) % 2)  # keep 16-bit alignment
                carry = data[cut:]
                if cut:
                    out = data[:cut]
                    if rate != sample_rate:
                        from jarvis.voice.audio import resample

                        out = resample(np.frombuffer(out, dtype=np.int16), rate, sample_rate).tobytes()
                    yield out

    async def synthesize(self, text: str, sample_rate: int) -> bytes:
        return b"".join([chunk async for chunk in self.stream(text, sample_rate)])


def _fish_error(text: str) -> str:
    if "402" in text:
        return "Kein Fish-Audio-API-Guthaben (API-Guthaben ist getrennt vom Plattform-Guthaben: https://fish.audio/app/developers)"
    if "401" in text:
        return "Fish Audio lehnt den API-Key ab"
    return text[:200]


class TTSService:
    """Selects the configured provider and falls back to macOS ``say`` when it is unavailable."""

    def __init__(self, cfg: TtsCfg) -> None:
        self.cfg = cfg
        self.macos = MacSayTTS(cfg.macos_voice, cfg.rate)
        self.fish = FishAudioTTS(
            cfg.fish_audio.api_key_secret,
            cfg.fish_audio.reference_id,
            cfg.fish_audio.model,
            cfg.fish_audio.chunk_length,
        )
        # Set by the Fish Audio connector: when it is active, its settings win over config/jarvis.yaml.
        self.fish_connector_active = False
        self.fish_last_error: str | None = None

    def configure_fish(self, active: bool, reference_id: str | None = None, model: str | None = None) -> None:
        self.fish_connector_active = active
        if reference_id is not None:
            self.fish.reference_id = reference_id
        if model:
            self.fish.model = model

    @property
    def wanted(self) -> str:
        return "fish_audio" if self.fish_connector_active or self.cfg.provider == "fish_audio" else "macos"

    async def provider(self) -> TTSProvider:
        if self.wanted == "fish_audio" and await self.fish.available():
            return self.fish
        return self.macos

    async def status(self) -> dict[str, object]:
        p = await self.provider()
        return {
            "configured": self.wanted,
            "active": p.name,
            "fish_audio_ready": await self.fish.available(),
            "fallback": p.name != self.wanted or (self.wanted == "fish_audio" and self.fish_last_error is not None),
            "fish_audio_error": self.fish_last_error,
        }

    async def stream(self, text: str, sample_rate: int) -> AsyncIterator[bytes]:
        p = await self.provider()
        try:
            async for chunk in p.stream(text, sample_rate):
                yield chunk
            if p is self.fish:
                self.fish_last_error = None
        except Exception as e:
            if p is self.macos:
                raise
            if p is self.fish:
                self.fish_last_error = _fish_error(str(e))
            logger.warning("TTS provider %s failed; falling back to macOS say", p.name, exc_info=True)
            yield await self.macos.synthesize(text, sample_rate)

    async def synthesize(self, text: str, sample_rate: int) -> bytes:
        return b"".join([c async for c in self.stream(text, sample_rate)])

    async def warm(self) -> None:
        if (await self.provider()) is self.fish:
            await self.fish.warm()

    async def live_session(self, sample_rate: int) -> FishStreamSession | None:
        """A Fish Audio WebSocket session for streamed replies, or None (provider is not Fish / disabled)."""
        fcfg = self.cfg.fish_audio
        if not fcfg.streaming or (await self.provider()) is not self.fish or self.fish_last_error:
            return None
        key = self.fish.api_key()
        if not key:
            return None
        return FishStreamSession(
            key,
            self.fish.reference_id,
            self.fish.model,
            sample_rate=sample_rate,
            latency=fcfg.latency,
            chunk_length=fcfg.chunk_length,
        )


_SENTENCE_END = re.compile(r"(?<=[.!?…:;])\s+|\n+")


def split_sentences(buffer: str) -> tuple[list[str], str]:
    """Split streamed text into complete sentences + remainder (for low-latency TTS)."""
    parts = _SENTENCE_END.split(buffer)
    if len(parts) <= 1:
        return [], buffer
    complete = [p.strip() for p in parts[:-1] if p.strip()]
    return complete, parts[-1]


def speakable(text: str) -> str:
    """Strip markdown and symbols that TTS would read out literally."""
    text = re.sub(r"```.*?```", " (Code ausgelassen) ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "Link", text)
    text = re.sub(r"[*_#>|]+", " ", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()
