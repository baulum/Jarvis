"""Fish Audio live TTS over WebSocket (``wss://api.fish.audio/v1/tts/live``, MessagePack frames).

Protocol (docs.fish.audio › api-reference › websocket/tts-live): the client sends ``start`` (same
request fields as the HTTP API: reference_id, format, sample_rate, latency, chunk_length …), then any
number of ``text`` events, optional ``flush`` (synthesise what is buffered now) and finally ``stop``.
The server answers with ``audio`` events (raw bytes in the requested format) and one ``finish``
(reason ``stop`` or ``error``), then closes the connection – so a session lives for one reply.

Latency: the session is opened (TLS + ``start``) while Sir is still finishing his sentence, text is
sent as soon as the model writes it and flushed at sentence ends, and every audio chunk is forwarded
to the speaker immediately. The voice is the configured ``reference_id`` – identical to the HTTP path.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import ssl
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any

import certifi
import msgpack
import websockets

from jarvis.secrets import secret_store

if TYPE_CHECKING:
    from jarvis.config import Settings

logger = logging.getLogger(__name__)
LIVE_URL = "wss://api.fish.audio/v1/tts/live"


class FishStreamError(RuntimeError):
    pass


@functools.cache
def _ssl_context() -> ssl.SSLContext:
    # python.org builds do not use the macOS keychain; use the same CA bundle as httpx.
    return ssl.create_default_context(cafile=certifi.where())


class FishStreamSession:
    def __init__(
        self,
        api_key: str,
        reference_id: str,
        model: str = "s1",
        *,
        sample_rate: int = 24000,
        latency: str = "balanced",
        chunk_length: int = 100,
        url: str = LIVE_URL,
        connect: Callable[..., Any] | None = None,
    ) -> None:
        self.api_key = api_key
        self.reference_id = reference_id
        self.model = model
        self.sample_rate = sample_rate
        self.latency = latency
        self.chunk_length = chunk_length
        self.url = url
        self._connect = connect or websockets.connect
        self._ws: Any = None
        self._carry = b""
        self.chars_sent = 0
        self.closed = False

    @classmethod
    def from_settings(
        cls, settings: Settings, reference_id: str | None = None, model: str | None = None
    ) -> FishStreamSession | None:
        cfg = settings.voice.tts.fish_audio
        key = secret_store.get(cfg.api_key_secret)
        ref = reference_id or cfg.reference_id
        if not key or not ref:
            return None
        return cls(key, ref, model or cfg.model, latency=cfg.latency, chunk_length=cfg.chunk_length)

    async def open(self, connect_s: float = 6.0) -> None:
        self._ws = await asyncio.wait_for(
            self._connect(
                self.url,
                additional_headers={"Authorization": f"Bearer {self.api_key}", "model": self.model},
                max_size=None,
                open_timeout=connect_s,
                ssl=_ssl_context() if self.url.startswith("wss:") else None,
                ping_interval=20,
            ),
            connect_s,
        )
        await self._send(
            {
                "event": "start",
                "request": {
                    "text": "",
                    "format": "pcm",
                    "sample_rate": self.sample_rate,
                    "reference_id": self.reference_id,
                    "latency": self.latency,
                    "chunk_length": self.chunk_length,
                    "normalize": True,
                    "language": "de",
                },
            }
        )

    async def _send(self, msg: dict[str, Any]) -> None:
        if self._ws is None or self.closed:
            raise FishStreamError("Fish-Audio-Verbindung ist nicht offen")
        await self._ws.send(msgpack.packb(msg))

    async def send_text(self, text: str) -> None:
        if text:
            self.chars_sent += len(text)
            await self._send({"event": "text", "text": text})

    async def flush(self) -> None:
        await self._send({"event": "flush"})

    async def stop(self) -> None:
        """No more text: the server finishes the remaining audio and sends ``finish``."""
        await self._send({"event": "stop"})

    async def audio(self) -> AsyncIterator[bytes]:
        """16-bit mono PCM chunks (aligned to whole samples) until ``finish``."""
        if self._ws is None:
            raise FishStreamError("Fish-Audio-Verbindung ist nicht offen")
        try:
            async for raw in self._ws:
                if isinstance(raw, str):
                    continue
                msg = msgpack.unpackb(raw, raw=False)
                event = msg.get("event")
                if event == "audio":
                    data = self._carry + bytes(msg.get("audio") or b"")
                    cut = len(data) - (len(data) % 2)
                    self._carry = data[cut:]
                    if cut:
                        yield data[:cut]
                elif event == "finish":
                    if msg.get("reason") == "error":
                        raise FishStreamError(f"Fish Audio meldet einen Fehler: {msg.get('message') or msg}")
                    return
                # unknown events are ignored (forward compatible, as the protocol docs ask)
        except websockets.ConnectionClosed as e:
            if e.rcvd is not None and e.rcvd.code not in (1000, 1001):
                raise FishStreamError(_close_reason(e.rcvd.code, e.rcvd.reason)) from e
        finally:
            self.closed = True

    async def close(self) -> None:
        """Cancel: closing the socket stops generation (used on barge-in)."""
        self.closed = True
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._ws.close(), 2)


def _close_reason(code: int, reason: str) -> str:
    low = (reason or "").lower()
    if code == 4402 or "credit" in low or "balance" in low or "402" in low:
        return "Fish Audio 402: kein API-Guthaben"
    if code in (4401, 4003) or "auth" in low or "401" in low:
        return "Fish Audio 401: API-Key abgelehnt"
    return f"Fish Audio WebSocket geschlossen ({code} {reason})".strip()
