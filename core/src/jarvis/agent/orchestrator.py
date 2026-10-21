"""Conversation orchestrator.

Entry point for every user utterance regardless of channel (desktop chat, voice, Telegram,
phone). Responsibilities: session lifecycle (incl. idle timeout), memory recall, running the
Hermes agent turn, streaming events to the bus, persisting the transcript and feeding the
memory extractor. Agent runs are serialised: a 4B local model serves one request at a time
anyway, and serialisation lets the MCP server attribute tool calls to the right session.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from jarvis.agent.claude_agent import ClaudeUnavailable
from jarvis.agent.llm import AgentEvent, HermesError
from jarvis.agent.model_router import MODELS, parse_command
from jarvis.agent.persona import turn_instructions
from jarvis.agent.timeline import (
    DbTimelineStore,
    SeqAllocator,
    TimelineStore,
    TurnRecorder,
    append_message,
    load_timeline,
)
from jarvis.db import session_scope
from jarvis.db.models import ChatMessage, ChatSession
from jarvis.events import activity, bus, spawn
from jarvis.security.audit import audit
from jarvis.security.auth import OWNER_ROLES, Principal

if TYPE_CHECKING:
    from jarvis.app_state import AppState

logger = logging.getLogger(__name__)
EventCallback = Callable[[AgentEvent], Awaitable[None]]
# logged actions of other components that become steps of the "Gedankenprozess" of the running turn
TURN_WATCH = ("memory.recalled", "approval.requested", "task.created")


@dataclass(slots=True)
class RunContext:
    principal: Principal
    session_id: uuid.UUID
    run_id: str | None = None
    started: datetime = field(default_factory=lambda: datetime.now(UTC))
    cancelled: bool = False


@dataclass(slots=True)
class TurnResult:
    session_id: uuid.UUID
    text: str
    tools: list[dict[str, Any]]
    error: str | None = None
    provider: str = "hermes"
    model: str | None = None
    timings: dict[str, float] = field(default_factory=dict)


class Orchestrator:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self._run_lock = asyncio.Lock()
        self.active_run: RunContext | None = None
        self._cancel_pending: set[uuid.UUID] = set()
        self.timeline_store: TimelineStore = DbTimelineStore()
        self.seq = SeqAllocator(self.timeline_store)

    # ------------------------------------------------------------------ sessions
    async def get_or_create_session(
        self, principal: Principal, channel: str, session_id: uuid.UUID | None = None, title: str = ""
    ) -> ChatSession:
        timeout = timedelta(minutes=self.state.settings.session.idle_timeout_minutes)
        async with session_scope() as s:
            if session_id is not None:
                sess = await s.get(ChatSession, session_id)
                if sess is not None and not sess.closed:
                    if sess.actor_id != principal.id and principal.role not in ("owner", "mobile_owner"):
                        raise PermissionError("Sitzung gehört einem anderen Akteur")
                    if datetime.now(UTC) - sess.last_activity_at <= timeout:
                        return sess
                    sess.closed = True  # expired -> start a fresh one
            sess = ChatSession(channel=channel, actor_role=principal.role, actor_id=principal.id, title=title[:200])
            s.add(sess)
            await s.flush()
        await bus.publish("session.created", session_id=str(sess.id), channel=channel)
        return sess

    async def history(self, session_id: uuid.UUID, limit: int = 100) -> list[dict[str, Any]]:
        async with session_scope() as s:
            rows = (
                (
                    await s.execute(
                        select(ChatMessage)
                        .where(ChatMessage.session_id == session_id)
                        .order_by(ChatMessage.created_at.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
        return [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "meta": m.meta,
                "created_at": m.created_at.isoformat(),
            }
            for m in reversed(rows)
        ]

    async def record_assistant(self, session_id: uuid.UUID, text: str, meta: dict[str, Any] | None = None) -> None:
        """Add a message Jarvis said outside a turn (e.g. the opening of a call) to the conversation context."""
        item = await append_message(
            self.timeline_store, self.seq, str(session_id), "assistant", text, publish=bus.publish
        )
        await self._store(session_id, "assistant", text, {**(meta or {}), "turn_id": item["turn_id"]})

    # ------------------------------------------------------------------ timeline
    async def timeline(
        self, session_id: uuid.UUID, *, turns: int | None = 80, before_seq: int | None = None
    ) -> dict[str, Any]:
        """Chronological items of a conversation (sorted by ``seq``), newest ``turns`` exchanges."""
        events, has_more = await self.timeline_store.list(str(session_id), turns=turns, before_seq=before_seq)
        messages: list[ChatMessage] = []
        if not has_more:  # all recorded turns are loaded: prepend what was stored before the timeline existed
            async with session_scope() as s:
                messages = list(
                    (
                        await s.execute(
                            select(ChatMessage)
                            .where(ChatMessage.session_id == session_id)
                            .order_by(ChatMessage.created_at)
                        )
                    )
                    .scalars()
                    .all()
                )
        return {"events": load_timeline(str(session_id), events, messages), "has_more": has_more}

    async def set_check(self, session_id: uuid.UUID, item_id: str, index: int, checked: bool) -> bool:
        """Toggle a task-list item (``- [ ]``) of a message; the state lives in the message itself."""
        if _is_uuid(item_id):  # a message stored before the timeline existed
            async with session_scope() as s:
                msg = await s.get(ChatMessage, uuid.UUID(item_id))
                if msg is None or msg.session_id != session_id:
                    return False
                meta = dict(msg.meta or {})
                meta["checks"] = {**(meta.get("checks") or {}), str(index): checked}
                msg.meta = meta
        else:
            item = await self.timeline_store.get(item_id)
            if item is None or item["session_id"] != str(session_id) or item["kind"] != "message":
                return False
            data = {**item["data"], "checks": {**(item["data"].get("checks") or {}), str(index): checked}}
            await self.timeline_store.upsert({**item, "data": data})
        await bus.publish("chat.check", session_id=str(session_id), event_id=item_id, index=index, checked=checked)
        return True

    async def _store(
        self, session_id: uuid.UUID, role: str, content: str, meta: dict[str, Any] | None = None
    ) -> uuid.UUID:
        async with session_scope() as s:
            msg = ChatMessage(session_id=session_id, role=role, content=content, meta=meta or {})
            s.add(msg)
            sess = await s.get(ChatSession, session_id)
            if sess is not None:
                sess.last_activity_at = datetime.now(UTC)
                sess.summarized_at = None  # new activity → the episode is summarised again later
                if not sess.title and role == "user":
                    sess.title = content[:80]
            await s.flush()
            return msg.id

    # ------------------------------------------------------------------ main entry
    async def handle(
        self,
        principal: Principal,
        channel: str,
        text: str,
        *,
        session_id: uuid.UUID | None = None,
        on_event: EventCallback | None = None,
        images: list[str] | None = None,
    ) -> TurnResult:
        sess = await self.get_or_create_session(principal, channel, session_id)
        with activity(session_id=str(sess.id)):
            return await self._handle_turn(sess, principal, channel, text, on_event, images)

    async def _handle_turn(
        self,
        sess: ChatSession,
        principal: Principal,
        channel: str,
        text: str,
        on_event: EventCallback | None,
        images: list[str] | None,
    ) -> TurnResult:
        t0 = time.perf_counter()
        timings: dict[str, float] = {}
        turn = TurnRecorder(
            self.timeline_store, self.seq, str(sess.id), publish=bus.publish, plan_steps=self._plan_steps
        )
        for pattern in TURN_WATCH:
            bus.subscribe(pattern, turn.on_bus_event)
        try:
            # Memory recall runs while the user message is stored and the model is chosen (critical path for voice).
            recall = asyncio.create_task(self._recall(text, principal, channel))
            user_msg_id = await self._store(sess.id, "user", text, {"channel": channel, "turn_id": turn.turn_id})
            user_item = await turn.start(text, channel)
            await bus.publish(
                "chat.user",
                session_id=str(sess.id),
                text=text,
                channel=channel,
                event_id=user_item.id if user_item else None,
                turn_id=turn.turn_id,
            )
            model, model_reason, confirmation, agent_text = await self._model_for_turn(sess, principal, channel, text)
            memory_context = await recall
            timings["recall_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            instructions = turn_instructions(channel, memory_context=memory_context, tainted=sess.tainted)
            t_agent = time.perf_counter()

            async def emit(ev: AgentEvent) -> None:
                if ev.type == "delta" and "first_token_ms" not in timings:
                    timings["first_token_ms"] = round((time.perf_counter() - t_agent) * 1000, 1)
                ref = await turn.on_agent_event(ev)
                await bus.publish(
                    f"chat.{ev.type}", session_id=str(sess.id), text=ev.text, data=_safe_event_data(ev.data), **ref
                )
                if on_event:
                    await on_event(ev)

            if confirmation is not None:
                await emit(AgentEvent("delta", text=confirmation))
                result = TurnResult(sess.id, confirmation, [], provider="system")
            elif self.state.kill_switch.engaged:
                reply = "Der Not-Aus ist aktiv, Sir. Ich führe derzeit keine Aktionen aus."
                await emit(AgentEvent("delta", text=reply))
                result = TurnResult(sess.id, reply, [], provider="system")
            elif self.state.agent is not None and self.state.agent.available():
                await bus.publish("chat.model", session_id=str(sess.id), model=model, reason=model_reason)
                await turn.set_model(model, _model_label(model), model_reason)
                result = await self._run_claude(sess, principal, agent_text, instructions, emit, model)
                result.model = model
            elif self.state.hermes is not None and await self._hermes_ready():
                result = await self._run_hermes(sess, principal, text, instructions, emit, images)
            else:
                result = await self._run_fallback(sess, text, instructions, emit)

            timings["agent_ms"] = round((time.perf_counter() - t_agent) * 1000, 1)
            result.timings = timings
            await turn.finish(error=result.error, model_label=_model_label(result.model) if result.model else None)
        finally:
            for pattern in TURN_WATCH:
                bus.unsubscribe(pattern, turn.on_bus_event)
            if not turn.finished:  # an exception escaped: close the turn honestly instead of leaving it "running"
                await turn.finish(error="Unerwarteter Abbruch der Antwort")
        await self._store(
            sess.id,
            "assistant",
            result.text,
            {
                "tools": result.tools,
                "provider": result.provider,
                "error": result.error,
                "model": result.model,
                "timings": timings,
                "turn_id": turn.turn_id,
            },
        )
        await bus.publish(
            "chat.done",
            session_id=str(sess.id),
            text=result.text,
            error=result.error,
            provider=result.provider,
            model=result.model,
            timings=timings,
            turn_id=turn.turn_id,
        )
        if result.text and not result.error and principal.role in OWNER_ROLES and not await self._is_tainted(sess.id):
            # a session that read untrusted content (web/mail) must not write "facts" into the long-term memory
            spawn(self._queue_extraction(sess.id, channel, text, result.text, user_msg_id), name="memory-extract")
        return result

    async def _plan_steps(self, task_id: str) -> list[str]:
        task = await self.state.tasks.get_task(uuid.UUID(task_id))
        return [s.name for s in task.steps] if task is not None else []

    async def _is_tainted(self, session_id: uuid.UUID) -> bool:
        async with session_scope() as s:
            row = await s.get(ChatSession, session_id)
            return bool(row is not None and row.tainted)

    async def _queue_extraction(
        self, session_id: uuid.UUID, channel: str, text: str, reply: str, user_msg_id: uuid.UUID
    ) -> None:
        recent = await self.history(session_id, limit=8)
        self.state.memory.enqueue_extraction(
            text,
            reply,
            {"session_id": str(session_id), "channel": channel},
            context=[{"role": m["role"], "content": m["content"]} for m in recent[:-2]],
            message_id=str(user_msg_id),
        )

    async def _recall(self, text: str, principal: Principal, channel: str = "desktop") -> str:
        """Hybrid memory lookup before the agent runs. Spoken turns get a tight budget (first audio matters)."""
        budget = 250.0 if channel in ("voice", "phone") else None
        try:
            return await self.state.memory.context_for(
                text, include_sensitive=principal.role == "owner", budget_ms=budget
            )
        except Exception:
            logger.warning("Memory recall failed", exc_info=True)
            return ""

    async def _run_hermes(
        self,
        sess: ChatSession,
        principal: Principal,
        text: str,
        instructions: str,
        emit: EventCallback,
        images: list[str] | None,
    ) -> TurnResult:
        hermes = self.state.hermes
        assert hermes is not None
        tools: list[dict[str, Any]] = []
        chunks: list[str] = []
        final_text = ""
        async with self._run_lock:
            ctx = RunContext(principal=principal, session_id=sess.id)
            self.active_run = ctx
            try:
                hermes_sid = await self._hermes_session(sess, hermes)
                async for ev in hermes.chat_stream(hermes_sid, text, instructions=instructions, images=images):
                    if ev.type == "run_started":
                        ctx.run_id = str(ev.data.get("run_id") or ev.data.get("id") or "") or None
                        continue
                    if ev.type == "delta":
                        chunks.append(ev.text)
                    elif ev.type == "assistant_completed":
                        final_text = ev.text or final_text
                    elif ev.type in ("tool_started", "tool_completed", "tool_failed"):
                        tools.append(
                            {
                                "event": ev.type,
                                "name": ev.data.get("name") or ev.data.get("tool"),
                                "ts": datetime.now(UTC).isoformat(),
                            }
                        )
                    elif ev.type == "failed":
                        return TurnResult(sess.id, self._error_reply(ev.text), tools, error=ev.text or "run failed")
                    elif ev.type == "cancelled":
                        text_out = ("".join(chunks) or final_text).strip() or "Abgebrochen, Sir."
                        return TurnResult(sess.id, text_out, tools, error="cancelled")
                    await emit(ev)
            except (HermesError, OSError, TimeoutError) as e:
                logger.exception("Hermes run failed")
                reply = self._error_reply(str(e))
                await emit(AgentEvent("delta", text=reply))
                return TurnResult(sess.id, reply, tools, error=str(e)[:500])
            finally:
                self.active_run = None
        return TurnResult(sess.id, (final_text or "".join(chunks)).strip(), tools)

    async def _hermes_session(self, sess: ChatSession, hermes: Any) -> str:
        if sess.hermes_session_id:
            return sess.hermes_session_id
        hsid: str = await hermes.create_session(f"jarvis:{sess.channel}:{sess.id}")
        async with session_scope() as s:
            row = await s.get(ChatSession, sess.id)
            if row is not None:
                row.hermes_session_id = hsid
        sess.hermes_session_id = hsid
        return hsid

    async def _hermes_ready(self, wait_s: float = 60.0) -> bool:
        """Healthy now, or wait while the supervised gateway is (re)starting."""
        hermes = self.state.hermes
        assert hermes is not None
        if await hermes.health():
            return True
        if not self.state.hermes_supervisor.is_running():
            return False
        deadline = asyncio.get_running_loop().time() + wait_s
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(1.5)
            if await hermes.health():
                return True
        return False

    async def _model_for_turn(
        self, sess: ChatSession, principal: Principal, channel: str, text: str
    ) -> tuple[str, str, str | None, str]:
        """Apply model-switch commands; returns (model, reason, confirmation-or-None, text for the agent)."""
        router = self.state.model_router
        cmd = parse_command(text)
        turn_model: str | None = None
        if cmd is not None:
            if cmd.scope == "turn":
                turn_model = cmd.model
            elif principal.role in OWNER_ROLES:
                router.set(cmd.model, cmd.scope, str(sess.id))
                await audit(principal.label, "agent.model", details={"model": cmd.model, "scope": cmd.scope})
                status: dict[str, Any] = router.status(str(sess.id))
                await bus.publish("agent.model", **status)
            else:  # never confirm a switch that did not happen
                return (
                    "sonnet",
                    "Befehl abgelehnt",
                    "Das Modell darf ich auf diesem Kanal nicht umschalten, Sir.",
                    text,
                )
            if not cmd.remainder:
                label = MODELS[cmd.model]["label"] if cmd.model else "automatische Modellwahl"
                where = {
                    "default": "ab sofort als Standard",
                    "session": "für dieses Gespräch",
                    "turn": "für die nächste Antwort",
                }[cmd.scope]
                if cmd.scope == "turn" and cmd.model:
                    router.next_turn[str(sess.id)] = cmd.model  # a bare "only once" means: the next answer
                return (cmd.model or "sonnet", "Befehl", f"Sehr wohl, Sir – {label} {where}.", text)
            text = cmd.remainder
        model, reason = router.choose(text, channel, str(sess.id), turn_model)
        return model, reason, None, text

    async def _run_claude(
        self,
        sess: ChatSession,
        principal: Principal,
        text: str,
        instructions: str,
        emit: EventCallback,
        model: str | None = None,
    ) -> TurnResult:
        agent = self.state.agent
        assert agent is not None
        tools: list[dict[str, Any]] = []
        chunks: list[str] = []
        final_text = ""
        if self._run_lock.locked() and sess.channel in ("voice", "phone"):
            # another channel's turn is running: say so instead of leaving Sir in silence
            busy = "Einen Moment, Sir, ich bin noch mit einer anderen Aufgabe beschäftigt. "
            await emit(AgentEvent("delta", text=busy))
        async with self._run_lock:
            if sess.id in self._cancel_pending:  # interrupted while waiting for its turn
                self._cancel_pending.discard(sess.id)
                return TurnResult(sess.id, "Abgebrochen, Sir.", tools, error="cancelled", provider="claude_code")
            ctx = RunContext(principal=principal, session_id=sess.id)
            self.active_run = ctx
            try:
                async for ev in agent.chat_stream(sess.id, sess.hermes_session_id, text, instructions, model):
                    if ev.type == "run_started":
                        ctx.run_id = str(ev.data.get("run_id") or "") or None
                        await self._remember_agent_session(sess, ctx.run_id)
                        continue
                    if ev.type == "delta":
                        chunks.append(ev.text)
                    elif ev.type == "assistant_completed":
                        final_text = ev.text or final_text
                        continue
                    elif ev.type in ("tool_started", "tool_completed", "tool_failed"):
                        tools.append(
                            {"event": ev.type, "name": ev.data.get("name"), "ts": datetime.now(UTC).isoformat()}
                        )
                    elif ev.type == "failed":
                        reply = self._error_reply(ev.text)
                        await emit(AgentEvent("delta", text=reply))
                        return TurnResult(sess.id, reply, tools, error=ev.text, provider="claude_code")
                    elif ev.type == "completed":
                        await self._remember_agent_session(sess, ev.data.get("session_id"))
                        break
                    await emit(ev)
            except (ClaudeUnavailable, OSError) as e:
                logger.exception("Claude Code run failed")
                reply = self._error_reply(str(e))
                await emit(AgentEvent("delta", text=reply))
                return TurnResult(sess.id, reply, tools, error=str(e)[:500], provider="claude_code")
            finally:
                self.active_run = None
        if ctx.cancelled:
            return TurnResult(
                sess.id,
                ("".join(chunks)).strip() or "Abgebrochen, Sir.",
                tools,
                error="cancelled",
                provider="claude_code",
            )
        return TurnResult(sess.id, (final_text or "".join(chunks)).strip(), tools, provider="claude_code")

    async def _remember_agent_session(self, sess: ChatSession, agent_session_id: str | None) -> None:
        if not agent_session_id or sess.hermes_session_id == agent_session_id:
            return
        sess.hermes_session_id = agent_session_id  # column stores the agent provider's session id
        async with session_scope() as s:
            row = await s.get(ChatSession, sess.id)
            if row is not None:
                row.hermes_session_id = agent_session_id

    async def _run_fallback(self, sess: ChatSession, text: str, instructions: str, emit: EventCallback) -> TurnResult:
        """No agent available: answer honestly and deterministically — never pretend to have acted."""
        reason = (self.state.agent.last_error if self.state.agent is not None else None) or "nicht erreichbar"
        reply = (
            f"Mein Sprachmodell (Claude Code) ist gerade nicht verfügbar, Sir ({reason[:160]}). "
            "Ich kann deshalb im Moment weder antworten noch Aktionen ausführen – es wurde nichts angelegt, gesendet "
            "oder gestartet."
        )
        await emit(AgentEvent("delta", text=reply))
        return TurnResult(sess.id, reply, [], error="agent_unavailable", provider="system")

    def _error_reply(self, detail: str) -> str:
        base = (self.state.personality.error_phrases or ["Das hat leider nicht funktioniert, Sir."])[0]
        return f"{base} ({detail[:160]})" if detail else base

    async def interrupt(self, session_id: uuid.UUID | None = None) -> bool:
        """Stop the running agent turn. With ``session_id`` only that conversation's turn is stopped (a voice
        barge-in must never cancel what another channel is doing); a turn of that session that is still
        waiting for its slot is skipped when it gets there."""
        run = self.active_run
        if session_id is not None and (run is None or run.session_id != session_id):
            self._cancel_pending.add(session_id)
            return False
        if run is None:
            return False
        run.cancelled = True
        if self.state.agent is not None:
            await self.state.agent.interrupt(run.session_id)
        elif run.run_id and self.state.hermes is not None:
            try:
                await self.state.hermes.stop_run(run.run_id)
            except Exception:
                logger.warning("Hermes stop failed", exc_info=True)
        await bus.publish("chat.interrupted", session_id=str(run.session_id))
        return True


def _model_label(model: str) -> str:
    return str(MODELS.get(model, {}).get("label") or model)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _safe_event_data(data: dict[str, Any]) -> dict[str, Any]:
    keep = ("name", "tool", "tool_use_id", "status", "error", "duration_ms", "run_id", "preview", "args")
    out = {k: data[k] for k in keep if k in data}
    if "args" in out:
        out["args"] = str(out["args"])[:300]
    return out
