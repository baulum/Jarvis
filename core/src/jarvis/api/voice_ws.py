"""``/api/voice/speak``: one WebSocket per voice daemon for spoken turns.

Daemon → core (JSON text frames):
  {"type": "prepare", "tag": n}                    Sir is about to finish → warm TTS (HTTP + live socket)
  {"type": "turn", "tag": n, "text": …, "session_id": …}   run the turn; the reply is spoken while it is written
  {"type": "say", "tag": n, "text": …}             speak a fixed text (notifications)
  {"type": "cancel", "tag": n}                     barge-in: stop speech + interrupt the agent run
  {"type": "level", "in": 0..1, "out": 0..1}       live audio levels for the interface (~12 Hz)
  {"type": "metrics", …}                           end-to-end latency of a turn, measured by the daemon
Core → daemon:
  binary frames: 1 byte tag + 16-bit mono PCM (24 kHz) – the daemon drops frames of stale tags
  {"type": "session", "tag", "session_id"}, {"type": "piece", "tag", "text"} (caption),
  {"type": "speech_end", "tag", "metrics"}, {"type": "error", "tag", "detail"}
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections import deque
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from jarvis.agent.llm import AgentEvent
from jarvis.api.deps import require
from jarvis.app_state import AppState
from jarvis.events import bus, spawn
from jarvis.security.auth import Principal, authenticate_token
from jarvis.voice.pipeline import SpeechTurn

logger = logging.getLogger(__name__)
router = APIRouter()
VOICE_ROLES = ("owner", "voice_owner")
recent_metrics: deque[dict[str, Any]] = deque(maxlen=200)


class VoiceSocket:
    def __init__(self, ws: WebSocket, state: AppState, principal: Any) -> None:
        self.ws = ws
        self.state = state
        self.principal = principal
        self._lock = asyncio.Lock()
        self.speech: dict[int, SpeechTurn] = {}
        self.runs: dict[int, asyncio.Task[None]] = {}
        self.sessions: dict[int, uuid.UUID] = {}  # tag → conversation of that turn (for a targeted cancel)
        self.rate = 24000
        cfg = state.settings.voice.tts.fish_audio
        self.transport = "hybrid" if cfg.streaming else "http"

    async def send_json(self, obj: dict[str, Any]) -> None:
        async with self._lock:
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):  # daemon went away mid-turn
                await self.ws.send_text(json.dumps(obj, ensure_ascii=False, default=str))

    def _speech(self, tag: int) -> SpeechTurn:
        st = self.speech.get(tag)
        if st is None:

            async def audio(pcm: bytes, tag: int = tag) -> None:
                async with self._lock:
                    with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                        await self.ws.send_bytes(bytes([tag & 0xFF]) + pcm)

            async def piece(text: str, tag: int = tag) -> None:
                await self.send_json({"type": "piece", "tag": tag, "text": text})

            st = SpeechTurn(
                self.state.tts,
                audio,
                sample_rate=self.rate,
                transport=self.transport,  # type: ignore[arg-type]
                on_piece=piece,
            )
            self.speech[tag] = st
        return st

    async def prepare(self, tag: int) -> None:
        for old in [t for t in self.speech if t != tag and t not in self.runs]:
            await self.speech.pop(old).cancel()  # a prepared turn that never came
        if tag not in self.speech:
            await self._speech(tag).prepare()

    async def turn(self, tag: int, text: str, session_id: str | None) -> None:
        fresh = tag not in self.speech
        speech = self._speech(tag)
        if fresh:
            await speech.prepare()
        t0 = time.perf_counter()
        orch = self.state.orchestrator
        error: str | None = None
        result_timings: dict[str, float] = {}
        try:
            sid = uuid.UUID(session_id) if session_id else None
            sess = await orch.get_or_create_session(self.principal, "voice", sid)
            self.sessions[tag] = sess.id
            await self.send_json({"type": "session", "tag": tag, "session_id": str(sess.id)})

            async def on_event(ev: AgentEvent) -> None:
                if ev.type == "delta" and ev.text:
                    speech.text(ev.text)
                elif ev.type == "tool_started":
                    speech.tool_started()
                elif ev.type == "tool_completed":
                    speech.tool_finished(True)
                elif ev.type == "tool_failed":
                    speech.tool_finished(False)

            result = await orch.handle(self.principal, "voice", text, session_id=sess.id, on_event=on_event)
            result_timings = result.timings
            error = result.error
        except Exception as e:
            logger.exception("Voice turn failed")
            error = str(e)[:300]
            speech.say("Das hat leider nicht funktioniert, Sir.")
        metrics = await speech.finish()
        metrics.update(result_timings)
        metrics["turn_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        self.speech.pop(tag, None)
        self.sessions.pop(tag, None)
        await self.send_json({"type": "speech_end", "tag": tag, "metrics": metrics, "error": error})

    async def say(self, tag: int, text: str) -> None:
        speech = self._speech(tag)
        await speech.prepare()
        speech.say(text)
        metrics = await speech.finish()
        self.speech.pop(tag, None)
        await self.send_json({"type": "speech_end", "tag": tag, "metrics": metrics})

    async def cancel(self, tag: int) -> None:
        st = self.speech.pop(tag, None)
        if st is not None:
            await st.cancel()
        sid = self.sessions.pop(tag, None)
        if tag in self.runs and sid is not None:  # the run returns once its agent turn is interrupted
            await self.state.orchestrator.interrupt(sid)

    def _run(self, tag: int, coro: Any) -> None:
        task = spawn(coro, name=f"voice-{tag}")
        self.runs[tag] = task

        def done(_t: asyncio.Task[None], tag: int = tag) -> None:
            self.runs.pop(tag, None)

        task.add_done_callback(done)

    async def handle(self, msg: dict[str, Any]) -> None:
        kind = msg.get("type")
        tag = int(msg.get("tag", 0)) & 0xFF
        if kind == "prepare":
            await self.prepare(tag)
        elif kind == "turn" and str(msg.get("text", "")).strip():
            self._run(tag, self.turn(tag, str(msg["text"]), msg.get("session_id")))
        elif kind == "say" and str(msg.get("text", "")).strip():
            self._run(tag, self.say(tag, str(msg["text"])))
        elif kind == "cancel":
            await self.cancel(tag)
        elif kind == "level":
            await bus.publish("voice.level", level=float(msg.get("in", 0)), output=float(msg.get("out", 0)))
        elif kind == "metrics":
            record = {k: v for k, v in msg.items() if k != "type"}
            recent_metrics.append(record)
            await bus.publish("voice.metrics", **record)
            _append_metrics(self.state, record)

    async def close(self) -> None:
        for tag in list(self.speech):
            await self.cancel(tag)


def _append_metrics(state: AppState, record: dict[str, Any]) -> None:
    try:
        path = state.settings.paths.log_dir / "voice-metrics.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:
        logger.debug("could not write voice metrics", exc_info=True)


@router.websocket("/api/voice/speak")
async def voice_speak(ws: WebSocket) -> None:
    principal = await authenticate_token(ws.query_params.get("token"))
    state: AppState = ws.app.state.jarvis
    if principal is None or principal.role not in VOICE_ROLES:
        await ws.close(code=4401)
        return
    await ws.accept()
    vs = VoiceSocket(ws, state, principal)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            try:
                await vs.handle(msg)
            except Exception:
                logger.exception("voice socket message failed: %s", msg.get("type"))
    except WebSocketDisconnect:
        pass
    finally:
        await vs.close()
        with contextlib.suppress(Exception):
            await ws.close()


@router.get("/api/voice/metrics")
async def voice_metrics(limit: int = 50, p: Principal = Depends(require("read"))) -> list[dict[str, Any]]:
    """Latest measured voice turns (end of speech → first audible audio), newest last."""
    return list(recent_metrics)[-max(1, min(limit, 200)) :]
