"""Streaming speech output for one spoken reply: LLM tokens in → ordered PCM chunks out.

* ``TextChunker`` cuts the token stream into speakable pieces. The first piece is cut early (at a
  clause boundary) so synthesis starts before the first sentence is complete; ordinals ("15.") and
  abbreviations ("z. B.") never end a piece.
* ``CommitmentGuard`` holds sentences that announce an action ("Ich kümmere mich darum", "ist
  angelegt") until a tool call of this turn has actually succeeded; without one they are dropped –
  Jarvis must not claim to have started something that was never accepted.
* ``SpeechTurn`` synthesises pieces in order with overlap. Measured on Sir's Mac (2026-10-16): Fish
  HTTP on a warm pooled connection gives the first audio ~260 ms after the text, the live WebSocket
  ~440 ms (+ ~550 ms connect). So the default ``hybrid`` transport speaks the first piece via HTTP
  and streams the rest through a WebSocket that was opened while Sir was still talking (continuous
  prosody, no per-sentence requests). ``http`` = pipelined HTTP per piece (prefetching ahead);
  ``websocket`` = everything through the live socket. Any socket failure falls back to HTTP, any
  Fish failure to the macOS voice (inside ``TTSService``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from jarvis.events import spawn
from jarvis.voice.speech import speakable

if TYPE_CHECKING:
    from jarvis.voice.fish_stream import FishStreamSession
    from jarvis.voice.speech import TTSService

logger = logging.getLogger(__name__)
Transport = Literal["hybrid", "http", "websocket"]

_ABBREV = re.compile(
    r"(?:\b(?:bzw|ca|nr|dr|hr|fr|evtl|ggf|inkl|usw|vgl|bspw|mio|mrd|tel|str|"
    r"jan|feb|mär|apr|jun|jul|aug|sep|sept|okt|nov|dez|st|ff|max|min|zzgl|abs)\.)$",
    re.I,
)
_END = re.compile(r"[.!?…:;](?=\s)|\n+")
_CLAUSE = re.compile(r"(?:,|\s[–—-])\s")


class TextChunker:
    def __init__(self, first_min: int = 14, first_clause_at: int = 48) -> None:
        self.buf = ""
        self.emitted = 0
        self.first_min = first_min
        self.first_clause_at = first_clause_at

    def _boundary(self) -> int | None:
        for m in _END.finditer(self.buf):
            end = m.end()
            head = self.buf[:end].rstrip()
            if head.endswith(".") and (
                re.search(r"\d\.$", head) or re.search(r"(?:^|\s)\w\.$", head) or _ABBREV.search(head[-12:])
            ):
                continue  # "15. Oktober", "z. B.", initials – not a sentence end
            if len(head.strip()) < 2:
                continue
            return end
        if self.emitted == 0 and len(self.buf) >= self.first_clause_at:
            cut = None
            for m in _CLAUSE.finditer(self.buf):
                if m.start() >= self.first_min:
                    cut = m.end()
                    break
            return cut
        return None

    def feed(self, delta: str) -> list[str]:
        self.buf += delta
        out: list[str] = []
        while (cut := self._boundary()) is not None:
            piece, self.buf = self.buf[:cut].strip(), self.buf[cut:]
            if piece:
                out.append(piece)
                self.emitted += 1
        return out

    def finish(self) -> list[str]:
        rest, self.buf = self.buf.strip(), ""
        if rest:
            self.emitted += 1
            return [rest]
        return []


_COMMIT = re.compile(
    r"\b(?:ich\s+kümmere\s+mich|kümmere\s+mich\s+darum|(?:ich\s+)?(?:starte|beginne|lege|erstelle|schicke|sende|"
    r"verschicke|buche|trage|richte|baue)\s+(?:\w+\s+){0,4}?(?:jetzt|sofort|gleich|an|ein|los|auf)\b|"
    r"(?:wird|wurde|ist|sind|habe|hab)\s+(?:\w+\s+){0,4}?(?:erledigt|gestartet|angelegt|erstellt|gesendet|"
    r"verschickt|eingetragen|gebucht|eingerichtet)\b|läuft\s+(?:jetzt\s+)?im\s+hintergrund)",
    re.I,
)


def claims_action(sentence: str) -> bool:
    return bool(_COMMIT.search(sentence))


@dataclass
class CommitmentGuard:
    held: list[tuple[str, bool]] = field(default_factory=list)  # (sentence, is_claim)
    tool_ok: bool = False
    dropped: list[str] = field(default_factory=list)

    def admit(self, sentence: str) -> list[str]:
        claim = claims_action(sentence)
        if self.tool_ok or (not claim and not self.held):
            return [sentence]
        self.held.append((sentence, claim))  # keep order behind a held claim
        return []

    def on_tool(self, ok: bool) -> list[str]:
        if not ok:
            return []
        self.tool_ok = True
        out, self.held = [s for s, _ in self.held], []
        return out

    def finish(self) -> list[str]:
        out = []
        for s, claim in self.held:
            if claim:
                self.dropped.append(s)
                logger.warning("Dropped unbacked action claim from speech: %r", s)
            else:
                out.append(s)
        self.held = []
        return out


@dataclass
class _HttpSeg:
    text: str
    queue: asyncio.Queue[bytes | None] = field(default_factory=asyncio.Queue)
    task: asyncio.Task[None] | None = None

    async def audio(self) -> AsyncIterator[bytes]:
        while (chunk := await self.queue.get()) is not None:
            yield chunk


@dataclass
class _WsSeg:
    texts: list[str] = field(default_factory=list)
    forwarded: int = 0  # bytes already played (decides whether a failure can be retried via HTTP)


class SpeechTurn:
    def __init__(
        self,
        tts: TTSService,
        on_audio: Callable[[bytes], Awaitable[None]],
        *,
        sample_rate: int = 24000,
        transport: Transport = "hybrid",
        on_piece: Callable[[str], Awaitable[None]] | None = None,
        max_prefetch: int = 3,
    ) -> None:
        self.tts = tts
        self.on_audio = on_audio
        self.on_piece = on_piece
        self.rate = sample_rate
        self.transport: Transport = transport
        self.chunker = TextChunker()
        self.guard = CommitmentGuard()
        self.segments: list[_HttpSeg | _WsSeg] = []
        self._ws_seg: _WsSeg | None = None
        self._ws_future: asyncio.Task[FishStreamSession | None] | None = None
        self._ws: FishStreamSession | None = None
        self._ws_q: asyncio.Queue[str | None] = asyncio.Queue()
        self._ws_writer: asyncio.Task[None] | None = None
        self.ws_failed = transport == "http"
        self._changed = asyncio.Event()
        self._finished = False
        self._cancelled = False
        self._sem = asyncio.Semaphore(max_prefetch)
        self._sender: asyncio.Task[None] | None = None
        self.metrics: dict[str, Any] = {"transport": transport, "pieces": 0, "chars": 0}
        self._t0 = time.perf_counter()

    # ------------------------------------------------------------------ lifecycle
    def _ms(self) -> float:
        return round((time.perf_counter() - self._t0) * 1000, 1)

    async def prepare(self) -> None:
        """Warm the HTTP connection and open the live socket while Sir is still speaking."""
        self._t0 = time.perf_counter()
        warm = asyncio.ensure_future(self.tts.warm())
        if self.transport != "http":
            self._ws_future = asyncio.ensure_future(self._open_ws())
        self._sender = asyncio.ensure_future(self._send_loop())
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.shield(warm), 0.05)

    async def _open_ws(self) -> FishStreamSession | None:
        try:
            sess = await self.tts.live_session(self.rate)
            if sess is None:
                self.ws_failed = True
                return None
            await sess.open()
            self.metrics["ws_connect_ms"] = self._ms()
            self._ws = sess
            return sess
        except Exception as e:
            logger.warning("Fish live TTS unavailable (%s); using HTTP", e)
            self.ws_failed = True
            self.metrics["ws_error"] = str(e)[:200]
            return None

    # ------------------------------------------------------------------ input from the agent
    def text(self, delta: str) -> None:
        for piece in self.chunker.feed(delta):
            for s in self.guard.admit(piece):
                self._enqueue(s)

    def tool_finished(self, ok: bool) -> None:
        for s in self.guard.on_tool(ok):
            self._enqueue(s)

    def say(self, text: str) -> None:
        """Speak a fixed text (notifications, confirmations) – no commitment guard."""
        for piece in [*self.chunker.feed(text + " "), *self.chunker.finish()]:
            self._enqueue(piece)

    def _enqueue(self, piece: str) -> None:
        if self._cancelled:
            return
        text = speakable(piece)
        if not text:
            return
        self.metrics["pieces"] += 1
        self.metrics["chars"] += len(text)
        self.metrics.setdefault("first_piece_ms", self._ms())
        if self.on_piece:
            spawn(self.on_piece(text), name="speech-piece")
        use_http = self.ws_failed or (self.transport == "hybrid" and not self.segments)
        if use_http:
            self._ws_seg = None
            self.segments.append(self._http_seg(text))
        else:
            if self._ws_seg is None:
                self._ws_seg = _WsSeg()
                self.segments.append(self._ws_seg)
            self._ws_seg.texts.append(text)
            self._ws_q.put_nowait(text)
            if self._ws_writer is None:
                self._ws_writer = asyncio.ensure_future(self._ws_write())
        self._changed.set()

    def _http_seg(self, text: str) -> _HttpSeg:
        seg = _HttpSeg(text)

        async def run() -> None:
            try:
                async with self._sem:
                    async for chunk in self.tts.stream(text, self.rate):
                        if self._cancelled:
                            break
                        seg.queue.put_nowait(chunk)
            except Exception:
                logger.warning("TTS failed for %r", text[:60], exc_info=True)
            finally:
                seg.queue.put_nowait(None)

        seg.task = asyncio.ensure_future(run())
        return seg

    async def _ws_write(self) -> None:
        """Single writer: text pieces reach the socket strictly in order, each flushed at its boundary."""
        sess = await self._ws_future if self._ws_future else None
        while True:
            text = await self._ws_q.get()
            if text is None or sess is None:
                if sess is not None and text is None:
                    with contextlib.suppress(Exception):
                        await sess.stop()
                return
            try:
                await sess.send_text(text + " ")
                await sess.flush()
            except Exception as e:
                logger.warning("Fish live TTS send failed: %s", e)
                self.ws_failed = True
                return

    async def finish(self) -> dict[str, Any]:
        """No more text: speak the rest, wait until every chunk has been handed to ``on_audio``."""
        if self._cancelled:
            self.metrics["cancelled"] = True
            return self.metrics
        for piece in self.chunker.finish():
            for s in self.guard.admit(piece):
                self._enqueue(s)
        for s in self.guard.finish():
            self._enqueue(s)
        self.metrics["dropped_claims"] = len(self.guard.dropped)
        self._finished = True
        self._changed.set()
        if self._ws_future is not None:
            sess = await self._ws_future
            if sess is not None:
                if self._ws_writer is not None:
                    self._ws_q.put_nowait(None)  # the writer sends "stop" after the last piece
                    with contextlib.suppress(Exception):
                        await self._ws_writer
                else:
                    await sess.close()  # opened but not needed (short reply)
        if self._sender is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._sender
        self.metrics["total_ms"] = self._ms()
        return self.metrics

    async def cancel(self) -> None:
        """Barge-in: stop generating and forwarding immediately; stale chunks are discarded."""
        self._cancelled = True
        self._finished = True
        self._changed.set()
        if self._sender is not None:
            self._sender.cancel()
        for seg in self.segments:
            if isinstance(seg, _HttpSeg) and seg.task is not None:
                seg.task.cancel()
        for t in (self._ws_future, self._ws_writer):
            if t is not None:
                t.cancel()
        if self._ws is not None:
            await self._ws.close()

    # ------------------------------------------------------------------ output
    async def _forward(self, chunk: bytes) -> None:
        if self._cancelled or not chunk:
            return
        self.metrics.setdefault("first_audio_ms", self._ms())
        await self.on_audio(chunk)

    async def _send_loop(self) -> None:
        i = 0
        while True:
            while i >= len(self.segments):
                if self._finished:
                    return
                self._changed.clear()
                await self._changed.wait()
            seg = self.segments[i]
            if isinstance(seg, _HttpSeg):
                async for chunk in seg.audio():
                    await self._forward(chunk)
            else:
                await self._play_ws(seg, i)
            i += 1

    async def _play_ws(self, seg: _WsSeg, index: int) -> None:
        sess = await self._ws_future if self._ws_future else None
        if sess is not None:
            try:
                async for chunk in sess.audio():
                    seg.forwarded += len(chunk)
                    await self._forward(chunk)
                return
            except Exception as e:
                logger.warning("Fish live TTS stream failed: %s", e)
                self.metrics["ws_error"] = str(e)[:200]
        self.ws_failed = True
        if self._ws_seg is seg:
            self._ws_seg = None
        if seg.forwarded == 0:  # nothing was spoken yet → say these pieces via HTTP instead
            self.metrics["ws_fallback"] = True
            self.segments[index + 1 : index + 1] = [self._http_seg(t) for t in seg.texts]
            self._changed.set()
