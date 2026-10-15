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
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import numpy as np

from jarvis.config import TtsCfg
from jarvis.secrets import secret_store
from jarvis.voice.audio import pcm_to_wav

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


_NOISE = re.compile(r"\[(?:BLANK_AUDIO|Musik|Music|MUSIK|Applaus|Stille|silence)\]|\((?:Musik|music)\)", re.I)


def _clean_transcript(text: str) -> str:
    text = _NOISE.sub("", text).strip()
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

    def __init__(self, api_key_secret: str, reference_id: str, model: str) -> None:
        self.api_key_secret = api_key_secret
        self.reference_id = reference_id
        self.model = model

    async def available(self) -> bool:
        return bool(secret_store.get(self.api_key_secret)) and bool(self.reference_id)

    async def stream(self, text: str, sample_rate: int) -> AsyncIterator[bytes]:
        key = secret_store.get(self.api_key_secret)
        if not key:
            raise RuntimeError("Fish-Audio-API-Key fehlt")
        rate = sample_rate if sample_rate in self.SUPPORTED_RATES else 16000
        body = {"text": text, "format": "pcm", "sample_rate": rate, "latency": "balanced", "normalize": True}
        if self.reference_id:
            body["reference_id"] = self.reference_id
        async with (
            httpx.AsyncClient(timeout=httpx.Timeout(60, connect=5)) as c,
            c.stream(
                "POST",
                "https://api.fish.audio/v1/tts",
                json=body,
                headers={"Authorization": f"Bearer {key}", "model": self.model},
            ) as r,
        ):
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


class TTSService:
    """Selects the configured provider and falls back to macOS ``say`` when it is unavailable."""

    def __init__(self, cfg: TtsCfg) -> None:
        self.cfg = cfg
        self.macos = MacSayTTS(cfg.macos_voice, cfg.rate)
        self.fish = FishAudioTTS(cfg.fish_audio.api_key_secret, cfg.fish_audio.reference_id, cfg.fish_audio.model)

    async def provider(self) -> TTSProvider:
        if self.cfg.provider == "fish_audio" and await self.fish.available():
            return self.fish
        return self.macos

    async def status(self) -> dict[str, object]:
        p = await self.provider()
        return {
            "configured": self.cfg.provider,
            "active": p.name,
            "fish_audio_ready": await self.fish.available(),
            "fallback": p.name != self.cfg.provider,
        }

    async def stream(self, text: str, sample_rate: int) -> AsyncIterator[bytes]:
        p = await self.provider()
        try:
            async for chunk in p.stream(text, sample_rate):
                yield chunk
        except Exception:
            if p is self.macos:
                raise
            logger.warning("TTS provider %s failed; falling back to macOS say", p.name, exc_info=True)
            yield await self.macos.synthesize(text, sample_rate)

    async def synthesize(self, text: str, sample_rate: int) -> bytes:
        return b"".join([c async for c in self.stream(text, sample_rate)])


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
