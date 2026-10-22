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


class LocalTTS(TTSProvider):
    """The Jarvis voice synthesised on this Mac by scripts/local-tts/server.py – no API costs."""

    name = "local"

    def __init__(self, server_url: str) -> None:
        self.server_url = server_url.rstrip("/")
        self._http = httpx.AsyncClient(base_url=self.server_url, timeout=httpx.Timeout(60, connect=2))
        self._checked = (0.0, False)
        self.voice = "local"  # model + reference clip as reported by the server (phrase-cache key)

    async def available(self) -> bool:
        """Health is cached for a few seconds – this is asked once per spoken piece."""
        at, ok = self._checked
        if time.monotonic() - at < 5:
            return ok
        try:
            r = await self._http.get("/health", timeout=2)
            ok = r.status_code == 200
            if ok:
                self.voice = f"local:{r.json().get('voice', '')}"
        except (httpx.HTTPError, ValueError):
            ok = False
        self._checked = (time.monotonic(), ok)
        return ok

    async def stream(self, text: str, sample_rate: int) -> AsyncIterator[bytes]:
        async with self._http.stream("POST", "/tts", json={"text": text}) as r:
            if r.status_code >= 400:
                detail = (await r.aread()).decode(errors="replace")[:200]
                raise RuntimeError(f"Lokale TTS {r.status_code}: {detail}")
            rate = int(r.headers.get("X-Sample-Rate") or sample_rate)
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


class PhraseCache:
    """Short, recurring pieces ("Sehr wohl, Sir.", "Einen Moment, Sir.", the wake-up line) are synthesised
    once per voice and sample rate and kept on disk – every repeat is free and instant."""

    def __init__(self, directory: Path | None, max_chars: int, max_entries: int = 500) -> None:
        self.dir = directory
        self.max_chars = max_chars
        self.max_entries = max_entries
        self._mem: dict[str, bytes] = {}

    def key(self, voice: str, text: str, sample_rate: int) -> str | None:
        if not 0 < len(text) <= self.max_chars:
            return None
        import hashlib

        return hashlib.sha256(f"{voice}\0{sample_rate}\0{text}".encode()).hexdigest()[:32]

    def get(self, key: str) -> bytes | None:
        if key in self._mem:
            return self._mem[key]
        if self.dir is not None:
            path = self.dir / f"{key}.pcm"
            if path.is_file():
                data = path.read_bytes()
                path.touch()  # pruning drops the least recently used phrases
                self._mem[key] = data
                return data
        return None

    def put(self, key: str, pcm: bytes) -> None:
        if not pcm:
            return
        if len(self._mem) >= self.max_entries:
            self._mem.pop(next(iter(self._mem)))
        self._mem[key] = pcm
        if self.dir is not None:
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                (self.dir / f"{key}.pcm").write_bytes(pcm)
                files = sorted(self.dir.glob("*.pcm"), key=lambda p: p.stat().st_mtime)
                for old in files[: max(0, len(files) - self.max_entries)]:  # ≤ ~120 MB at 24 kHz
                    old.unlink(missing_ok=True)
            except OSError:
                logger.debug("TTS cache write failed", exc_info=True)


def _fish_error(text: str) -> str:
    if "402" in text:
        return "Kein Fish-Audio-API-Guthaben (API-Guthaben ist getrennt vom Plattform-Guthaben: https://fish.audio/app/developers)"
    if "401" in text:
        return "Fish Audio lehnt den API-Key ab"
    return text[:200]


class TTSService:
    """Selects the configured provider and falls back to macOS ``say`` when it is unavailable.

    ``provider: local`` wins over the Fish Audio connector; while the local server is down, Fish Audio
    (if the connector is active) and then ``say`` take over.
    """

    def __init__(self, cfg: TtsCfg, cache_dir: Path | None = None) -> None:
        self.cfg = cfg
        self.cache = PhraseCache(cache_dir, cfg.cache_max_chars)
        self.macos = MacSayTTS(cfg.macos_voice, cfg.rate)
        self.local = LocalTTS(cfg.local.server_url)
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
        if self.cfg.provider == "local":
            return "local"
        return "fish_audio" if self.fish_connector_active or self.cfg.provider == "fish_audio" else "macos"

    async def provider(self) -> TTSProvider:
        if self.wanted == "local" and await self.local.available():
            return self.local
        if (self.wanted == "fish_audio" or self.fish_connector_active) and await self.fish.available():
            return self.fish
        return self.macos

    def _voice_id(self, p: TTSProvider) -> str:
        if p is self.fish:
            return f"fish:{self.fish.reference_id}:{self.fish.model}"
        return self.local.voice if p is self.local else p.name

    async def status(self) -> dict[str, object]:
        p = await self.provider()
        return {
            "configured": self.wanted,
            "active": p.name,
            "local_ready": await self.local.available(),
            "fish_audio_ready": await self.fish.available(),
            "fallback": p.name != self.wanted or (self.wanted == "fish_audio" and self.fish_last_error is not None),
            "fish_audio_error": self.fish_last_error,
        }

    async def stream(self, text: str, sample_rate: int) -> AsyncIterator[bytes]:
        p = await self.provider()
        key = self.cache.key(self._voice_id(p), text, sample_rate) if p is not self.macos else None
        if key and (hit := self.cache.get(key)) is not None:
            yield hit
            return
        parts: list[bytes] = []
        try:
            async for chunk in p.stream(text, sample_rate):
                if key:
                    parts.append(chunk)
                yield chunk
            if key:
                self.cache.put(key, b"".join(parts))
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


# "J.A.R.V.I.S." is spelled out letter by letter (or read as "Jay Ay Ar…") by every TTS engine
_NAME_ACRONYM = re.compile(r"\bJ\s?\.\s?A\s?\.\s?R\s?\.\s?V\s?\.\s?I\s?\.\s?S\b\.?", re.I)


def pronounceable(text: str) -> str:
    """Spoken form of the assistant's name: always "Jarvis", never the dotted acronym."""
    return _NAME_ACRONYM.sub("Jarvis", text)


# Local file paths (e.g. a generated image under ~/Library/Application Support/…) are never read out. A segment
# may contain spaces only when another "/" follows, so the words after the file name stay.
_FILE_PATH = re.compile(
    r"(?<![\w.:/])(?:~|/(?:Users|private|tmp|var|Volumes|home|opt))(?:/(?:[^\s/`]+(?: [^\s/`]+)*(?=/)|[^\s/`]+))+"
)


def speakable(text: str) -> str:
    """Strip markdown and symbols that TTS would read out literally."""
    text = pronounceable(text)
    text = re.sub(r"```.*?```", " (Code ausgelassen) ", text, flags=re.S)
    text = _FILE_PATH.sub("", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "Link", text)
    text = re.sub(r"[*_#>|]+", " ", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()
