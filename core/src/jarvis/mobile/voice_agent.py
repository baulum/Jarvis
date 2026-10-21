"""Jarvis as a participant of a LiveKit call room – the existing voice engine, over WebRTC.

Phone mic (Opus, echo-cancelled + noise-suppressed on the phone) → ``AudioStream`` 16 kHz → ``Endpointer``
(speculative Whisper in pauses, same rules as the Mac voice daemon) → orchestrator (same agent, memory and
tools as every other channel) → ``SpeechTurn`` (Fish Audio streaming TTS, commitment guard) → 24 kHz frames
→ ``AudioSource`` → phone. Sir can interrupt (barge-in): his speech while Jarvis talks stops the speech and
the agent run of this call. Because the phone cancels the echo, barge-in by voice works here.

State, transcript and real tool/memory activity go to the phone over the room's data channel
(topic ``jarvis``) and onto the event bus (``call.*``) for the desktop.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt

from jarvis.agent.llm import AgentEvent
from jarvis.agent.persona import LeadingGreetingFilter
from jarvis.events import Event, bus, spawn
from jarvis.mobile.call_context import call_context_block, is_vague_confirmation, load_call_context, open_question
from jarvis.security.auth import Principal
from jarvis.voice.audio import Endpointer, looks_complete, pcm_to_wav, rms
from jarvis.voice.pipeline import SpeechTurn

if TYPE_CHECKING:
    from jarvis.app_state import AppState

from jarvis.voice.commands import is_end_conversation

logger = logging.getLogger(__name__)
IN_RATE = 16000
OUT_RATE = 24000
OUT_FRAME = OUT_RATE // 50  # 20 ms
TOPIC = "jarvis"

_TOOL_LABELS: list[tuple[str, str]] = [
    ("calendar_", "Prüfe den Kalender …"),
    ("gmail_", "Sehe die E-Mails durch …"),
    ("memory_", "Durchsuche das Gedächtnis …"),
    ("web_", "Recherchiere im Web …"),
    ("browser_", "Recherchiere im Web …"),
    ("lexware_", "Frage Lexware ab …"),
    ("coding_", "Beauftrage Claude Code …"),
    ("task_", "Starte eine Aufgabe …"),
    ("mac_", "Bediene den Mac …"),
    ("drive_", "Durchsuche Google Drive …"),
    ("github_", "Frage GitHub ab …"),
    ("telegram_", "Telegram …"),
    ("mobile_", "Plane eine Benachrichtigung …"),
    ("system_", "Prüfe das System …"),
]


def _normalise(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def tool_label(name: str) -> str:
    short = name.rsplit("__", 1)[-1]
    for prefix, label in _TOOL_LABELS:
        if short.startswith(prefix):
            return label
    return f"Führe {short} aus …"


class CallVoiceAgent:
    def __init__(
        self,
        state: AppState,
        *,
        call_id: uuid.UUID,
        room_name: str,
        greeting: str | None,
        on_owner_joined: Callable[[str], Awaitable[None]],
        on_finished: Callable[[str, dict[str, Any]], Awaitable[None]],
        join_timeout_s: float,
    ) -> None:
        self.state = state
        self.call_id = call_id
        self.room_name = room_name
        self.greeting = greeting
        self.on_owner_joined = on_owner_joined
        self.on_finished = on_finished
        self.join_timeout_s = join_timeout_s
        self.principal: Principal | None = None
        self.session_id: uuid.UUID | None = None
        self.call_context: dict[str, Any] = {}  # the open question of this call (empty for a call Sir placed)
        self.transcript: list[dict[str, Any]] = []
        self.metrics: dict[str, Any] = {"turns": 0, "barge_ins": 0}
        self._room: Any = None
        self._source: Any = None
        self._owner_joined = asyncio.Event()
        self._owner_left = asyncio.Event()
        self._audio_q: asyncio.Queue[npt.NDArray[np.int16]] = asyncio.Queue(maxsize=500)
        self._speech: SpeechTurn | None = None
        self._turn_task: asyncio.Task[None] | None = None
        self._speaking = False
        self._out_buf = b""
        self._task: asyncio.Task[None] | None = None
        self._end_reason = "ended"
        self._ending = False
        self._started = time.monotonic()
        self._greeting_pcm: asyncio.Future[bytes] | None = None
        self._owner_track = asyncio.Event()
        self._prepared: SpeechTurn | None = None
        self._greeting_spoken = False

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"call-agent-{self.call_id}")

    def set_owner(self, principal: Principal) -> None:
        self.principal = principal

    async def hangup(self, reason: str = "agent") -> None:
        self._end_reason = reason
        self._owner_left.set()

    async def _run(self) -> None:
        from livekit import rtc

        try:
            self._room = rtc.Room()
            token = self.state.livekit.mint_token(self.room_name, "jarvis", "Jarvis", ttl_s=7200, agent=True)
            self._room.on("track_subscribed", self._on_track)
            self._room.on("participant_connected", self._on_participant)
            self._room.on("participant_disconnected", self._on_participant_left)
            self._room.on("disconnected", lambda *_: self._owner_left.set())
            if self.greeting:  # synthesised while the phone rings / connects → no wait after pick-up
                self._greeting_pcm = asyncio.ensure_future(self.state.tts.synthesize(self.greeting, OUT_RATE))
            await self._room.connect(self.state.settings.mobile.livekit.internal_url, token)
            self._source = rtc.AudioSource(OUT_RATE, 1, queue_size_ms=400)
            track = rtc.LocalAudioTrack.create_audio_track("jarvis-voice", self._source)
            opts = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            await self._room.local_participant.publish_track(track, opts)
            for p in self._room.remote_participants.values():
                self._on_participant(p)
            joined = asyncio.ensure_future(self._owner_joined.wait())
            left = asyncio.ensure_future(self._owner_left.wait())  # also set when the call is cancelled
            await asyncio.wait({joined, left}, timeout=self.join_timeout_s, return_when=asyncio.FIRST_COMPLETED)
            left.cancel()
            if not self._owner_joined.is_set():
                joined.cancel()
                self._end_reason = "no_answer" if not self._owner_left.is_set() else self._end_reason
                return
            await self._conversation()
        except Exception as e:
            logger.exception("Call agent failed")
            self._end_reason = "agent_error"
            self.metrics["error"] = str(e)[:200]
        finally:
            await self._cleanup()

    async def _cleanup(self) -> None:
        if self._prepared is not None:
            with contextlib.suppress(Exception):
                await self._prepared.cancel()
        if self._speech is not None:
            with contextlib.suppress(Exception):
                await self._speech.cancel()
        if self._turn_task is not None:
            self._turn_task.cancel()
        if self._room is not None:
            with contextlib.suppress(Exception):
                await self._room.disconnect()
        self.metrics["duration_s"] = round(time.monotonic() - self._started, 1)
        await self.on_finished(
            self._end_reason, {"transcript": self.transcript, "session_id": self.session_id, "metrics": self.metrics}
        )

    # ------------------------------------------------------------------ room callbacks
    def _on_participant(self, participant: Any) -> None:
        if participant.identity.startswith("owner-") and not self._owner_joined.is_set():
            self._owner_joined.set()
            spawn(self.on_owner_joined(participant.identity), name="call-owner-joined")

    def _on_participant_left(self, participant: Any) -> None:
        if participant.identity.startswith("owner-"):
            self._end_reason = self._end_reason if self._ending else "owner_hangup"
            self._owner_left.set()

    def _on_track(self, track: Any, publication: Any, participant: Any) -> None:
        from livekit import rtc

        if track.kind == rtc.TrackKind.KIND_AUDIO and participant.identity.startswith("owner-"):
            self._owner_track.set()
            spawn(self._read_audio(rtc.AudioStream(track, sample_rate=IN_RATE, num_channels=1)), name="call-audio-in")

    async def _read_audio(self, stream: Any) -> None:
        async for ev in stream:
            pcm = np.frombuffer(ev.frame.data, dtype=np.int16).copy()
            if self._audio_q.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    self._audio_q.get_nowait()
            self._audio_q.put_nowait(pcm)

    # ------------------------------------------------------------------ data channel
    async def send(self, msg: dict[str, Any]) -> None:
        if self._room is None:
            return
        with contextlib.suppress(Exception):
            await self._room.local_participant.publish_data(
                json.dumps(msg, ensure_ascii=False), reliable=True, topic=TOPIC
            )

    async def set_state(self, value: str, detail: str | None = None) -> None:
        await self.send({"type": "state", "state": value, "detail": detail})
        await bus.publish("call.state", call_id=str(self.call_id), state=value, detail=detail)

    async def _transcript(self, role: str, text: str) -> None:
        entry = {"role": role, "text": text, "ts": time.time()}
        self.transcript.append(entry)
        await self.send({"type": "transcript", **entry})
        await bus.publish("call.transcript", call_id=str(self.call_id), role=role, text=text)

    async def _on_bus_memory(self, ev: Event) -> None:
        if self.session_id is not None and ev.session_id == str(self.session_id):
            hits = ev.data.get("hits") or []
            await self.send(
                {
                    "type": "activity",
                    "kind": "memory",
                    "label": "Erinnerungen abgerufen" if hits else "Gedächtnis durchsucht",
                    "count": len(hits),
                    "nodes": ev.nodes[:12],
                }
            )

    # ------------------------------------------------------------------ conversation
    async def _load_call_context(self) -> None:
        """The question this call is about, stored when the call was placed. The session starts knowing it."""
        self.call_context = await load_call_context(self.call_id)
        self.metrics["open_question"] = open_question(self.call_context) is not None

    async def _conversation(self) -> None:
        assert self.principal is not None
        await self._load_call_context()
        orch = self.state.orchestrator
        sess = await orch.get_or_create_session(self.principal, "phone", None)
        self.session_id = sess.id
        await self.send({"type": "session", "session_id": str(sess.id)})
        bus.subscribe("memory.recalled", self._on_bus_memory)
        listen = asyncio.create_task(self._listen_loop())  # running during the greeting: Sir may interrupt it
        greeting: asyncio.Task[None] | None = None
        if self.greeting:  # not awaited here: a hang-up during the greeting must end the call at once
            await orch.record_assistant(sess.id, self.greeting, {"channel": "phone", "call_id": str(self.call_id)})
            greeting = asyncio.create_task(self._speak_greeting(self.greeting))
            greeting.add_done_callback(lambda _t: spawn(self.set_state("listening"), name="call-state"))
        else:
            await self.set_state("listening")
        max_s = self.state.settings.mobile.calls.max_duration_minutes * 60
        try:
            await asyncio.wait_for(self._owner_left.wait(), max_s)
        except TimeoutError:
            self._end_reason = "max_duration"
            await self._speak_fixed("Sir, wir sprechen schon eine ganze Weile. Ich lege jetzt auf.")
        finally:
            listen.cancel()
            if greeting is not None:
                greeting.cancel()
            bus.unsubscribe("memory.recalled", self._on_bus_memory)

    async def _listen_loop(self) -> None:
        vcfg = self.state.settings.voice
        ep = Endpointer(IN_RATE, min_rms=vcfg.vad_min_rms, spec_ms=vcfg.speculative_stt_ms, max_utterance_s=30)
        barge_frames = 0
        need_barge = max(1, vcfg.barge_in_ms // 20)
        spec: asyncio.Task[str] | None = None
        spec_len = 0
        while True:
            pcm = await self._audio_q.get()
            busy = self._speaking or (self._turn_task is not None and not self._turn_task.done())
            if busy:
                # Barge-in: sustained speech while Jarvis speaks or works (phone AEC removes his own voice)
                for i in range(0, len(pcm) - 319, 320):
                    if rms(pcm[i : i + 320]) > max(vcfg.vad_min_rms * 2.5, ep.floor * 3):
                        barge_frames += 1
                    else:
                        barge_frames = max(0, barge_frames - 1)
                if barge_frames >= need_barge:
                    barge_frames = 0
                    self.metrics["barge_ins"] += 1
                    await self._interrupt()
                    ep.reset()
                else:
                    continue
            for ev in ep.feed(pcm):
                if ev == "pause":
                    spec_len = ep.audio().size
                    spec = asyncio.create_task(self._stt(ep.audio()))
                    if self._prepared is None:  # warm Fish (HTTP + live socket) while Sir finishes the sentence
                        self._prepared = self._make_speech()
                        spawn(self._prepared.prepare(), name="call-tts-prepare")
                elif ev == "resume" and spec is not None:
                    spec.cancel()
                    spec = None
            if not ep.active:
                continue
            silence = ep.silence_ms
            done = ep.too_long or silence >= vcfg.end_of_turn_silence_ms
            if not done and spec is not None and spec.done() and silence >= vcfg.min_end_of_turn_silence_ms:
                with contextlib.suppress(Exception):
                    done = looks_complete(spec.result())
            if not done:
                continue
            audio = ep.audio()
            has_speech = ep.has_speech
            ep.reset()
            if not has_speech:
                continue
            text: str | None = None
            if spec is not None and spec.done() and spec_len == audio.size:
                with contextlib.suppress(Exception):
                    text = spec.result()
            elif spec is not None:
                spec.cancel()
            spec = None
            self._turn_task = asyncio.create_task(self._turn(audio, text))

    def _is_echo(self, text: str) -> bool:
        """The recognised text is (part of) something Jarvis said a moment ago, e.g. the greeting via speaker."""
        heard = _normalise(text)
        if len(heard) < 8:
            return False
        for entry in self.transcript[-4:]:
            if entry["role"] != "jarvis":
                continue
            said = _normalise(entry["text"])
            if heard in said or SequenceMatcher(None, heard, said).ratio() > 0.75:
                return True
        return False

    async def _stt(self, audio: npt.NDArray[np.int16]) -> str:
        return (await self.state.stt.transcribe_wav(pcm_to_wav(audio, IN_RATE))).strip()

    async def _interrupt(self) -> None:
        if self._speech is not None:
            await self._speech.cancel()
        if self._source is not None:
            self._source.clear_queue()
        self._out_buf = b""
        self._speaking = False
        if self._turn_task is not None and not self._turn_task.done():
            if self.session_id is not None:
                await self.state.orchestrator.interrupt(self.session_id)
            self._turn_task.cancel()
        await self.set_state("listening", "unterbrochen")

    async def _turn(self, audio: npt.NDArray[np.int16], text: str | None) -> None:
        assert self.principal is not None and self.session_id is not None
        await self.set_state("thinking")
        t0 = time.perf_counter()
        logger.info("Call utterance: %.2f s (transcript %s)", audio.size / IN_RATE, "reused" if text else "new")
        if text is None:
            text = await self._stt(audio)
        text = text.strip()
        if len(re.sub(r"\W", "", text)) < 2:
            await self.set_state("listening")
            return
        if self._is_echo(text):  # Jarvis' own voice picked up again: never answer it (that would loop)
            logger.info("Call %s: ignoring echo of Jarvis' own speech", self.call_id)
            await self.set_state("listening")
            return
        await self._transcript("user", text)
        if is_end_conversation(text, 70):
            await self._speak_fixed("Sehr wohl, Sir. Auf Wiederhören.")
            self._ending = True
            self._end_reason = "owner_goodbye"
            self._owner_left.set()
            return
        if open_question(self.call_context) is None and is_vague_confirmation(text):
            # "ja" with nothing it could refer to: ask, and do not start an agent turn that could act on it
            await self._answer_vague_confirmation(text)
            await self.set_state("listening")
            return
        self.metrics["turns"] += 1
        speech = self._prepared
        self._prepared = None
        if speech is None:
            speech = self._new_speech()
            await speech.prepare()
        else:
            self._speech = speech
        reply_parts: list[str] = []
        title = self.state.personality.owner_title
        # the greeting was said when Sir picked up: the first answer must not open with another one
        greet = LeadingGreetingFilter(title) if self.greeting and self.metrics["turns"] == 1 else None

        def say(text: str) -> None:
            if text:
                speech.text(text)

        async def on_event(ev: AgentEvent) -> None:
            if ev.type == "delta" and ev.text:
                reply_parts.append(ev.text)
                say(greet.feed(ev.text) if greet else ev.text)
            elif ev.type == "tool_started":
                name = str(ev.data.get("name", ""))
                if greet:
                    say(greet.flush())  # keep the order of text and tool start (commitment guard)
                speech.tool_started()
                await self.set_state("tool", tool_label(name))
                await self.send(
                    {
                        "type": "activity",
                        "kind": "tool",
                        "name": name,
                        "label": tool_label(name),
                        "status": "started",
                        "id": ev.data.get("tool_use_id"),
                    }
                )
            elif ev.type in ("tool_completed", "tool_failed"):
                ok = ev.type == "tool_completed"
                speech.tool_finished(ok)
                await self.send(
                    {
                        "type": "activity",
                        "kind": "tool",
                        "name": ev.data.get("name"),
                        "status": "done" if ok else "failed",
                        "id": ev.data.get("tool_use_id"),
                    }
                )

        try:
            result = await self.state.orchestrator.handle(
                self.principal,
                "phone",
                text,
                session_id=self.session_id,
                on_event=on_event,
                call_context=call_context_block(self.call_context),
            )
            if greet:
                say(greet.flush())
            if result.error and not reply_parts:
                speech.say("Das hat leider nicht funktioniert, Sir.")
            metrics = await speech.finish()
            await self._drain()
            final = result.text or "".join(reply_parts)
            if greet:  # the transcript shows what was said
                clean = LeadingGreetingFilter(title)
                final = (clean.feed(final) + clean.flush()).strip()
            if final:
                await self._transcript("jarvis", final)
            metrics["stt_to_done_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            self.metrics.setdefault("turn_metrics", []).append(metrics)
        except asyncio.CancelledError:
            await speech.cancel()
            raise
        finally:
            self._speech = None
        await self.set_state("listening")

    # ------------------------------------------------------------------ speech output
    def _make_speech(self) -> SpeechTurn:
        cfg = self.state.settings.voice.tts.fish_audio
        return SpeechTurn(
            self.state.tts, self._on_audio, sample_rate=OUT_RATE, transport="hybrid" if cfg.streaming else "http"
        )

    def _new_speech(self) -> SpeechTurn:
        speech = self._make_speech()
        self._speech = speech
        return speech

    async def _speak_greeting(self, text: str) -> None:
        """Play the pre-synthesised greeting as soon as the phone's audio is up (its subscription is ready too).
        Strictly once per call."""
        if self._greeting_spoken:
            return
        self._greeting_spoken = True
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._owner_track.wait(), 5)
        pcm = b""
        if self._greeting_pcm is not None:
            with contextlib.suppress(Exception):
                pcm = await asyncio.wait_for(self._greeting_pcm, 8)
        if not pcm:
            await self._speak_fixed(text)
            return
        self.metrics["greeting_ready_ms"] = round((time.monotonic() - self._started) * 1000)
        await self._transcript("jarvis", text)  # recorded when it starts – it is being said
        await self._on_audio(pcm)
        await self._drain()

    async def _answer_vague_confirmation(self, text: str) -> None:
        """Sir said yes without saying to what, and the call has no open question: ask instead of acting."""
        assert self.session_id is not None
        reply = (
            "Worauf beziehen Sie sich, Sir? Ich habe in diesem Gespräch keine offene Frage an Sie. "
            "Was soll ich konkret tun?"
        )
        meta = {"channel": "phone", "call_id": str(self.call_id), "guard": "vague_confirmation"}
        await self.state.orchestrator.record_turn(self.session_id, text, reply, meta)
        await self._speak_fixed(reply)

    async def _speak_fixed(self, text: str) -> None:
        speech = self._new_speech()
        await speech.prepare()
        speech.say(text)
        await speech.finish()
        self._speech = None
        await self._drain()
        await self._transcript("jarvis", text)

    async def _on_audio(self, pcm: bytes) -> None:
        from livekit import rtc

        if not self._speaking:
            self._speaking = True
            await self.set_state("speaking")
        self._out_buf += pcm
        frame_bytes = OUT_FRAME * 2
        while len(self._out_buf) >= frame_bytes and self._source is not None:
            chunk, self._out_buf = self._out_buf[:frame_bytes], self._out_buf[frame_bytes:]
            await self._source.capture_frame(rtc.AudioFrame(chunk, OUT_RATE, 1, OUT_FRAME))

    async def _drain(self) -> None:
        from livekit import rtc

        if self._source is None:
            return
        if self._out_buf:
            pad = self._out_buf + b"\x00" * (OUT_FRAME * 2 - len(self._out_buf) % (OUT_FRAME * 2))
            self._out_buf = b""
            for i in range(0, len(pad), OUT_FRAME * 2):
                await self._source.capture_frame(rtc.AudioFrame(pad[i : i + OUT_FRAME * 2], OUT_RATE, 1, OUT_FRAME))
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._source.wait_for_playout(), 30)
        self._speaking = False
