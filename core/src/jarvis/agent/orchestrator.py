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
from jarvis.db import session_scope
from jarvis.db.models import ChatMessage, ChatSession
from jarvis.events import bus
from jarvis.security.audit import audit
from jarvis.security.auth import Principal

if TYPE_CHECKING:
    from jarvis.app_state import AppState

logger = logging.getLogger(__name__)
EventCallback = Callable[[AgentEvent], Awaitable[None]]


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


class Orchestrator:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self._run_lock = asyncio.Lock()
        self.active_run: RunContext | None = None

    # ------------------------------------------------------------------ sessions
    async def get_or_create_session(
        self, principal: Principal, channel: str, session_id: uuid.UUID | None = None, title: str = ""
    ) -> ChatSession:
        timeout = timedelta(minutes=self.state.settings.session.idle_timeout_minutes)
        async with session_scope() as s:
            if session_id is not None:
                sess = await s.get(ChatSession, session_id)
                if sess is not None and not sess.closed:
                    if sess.actor_id != principal.id and principal.role != "owner":
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

    async def _store(self, session_id: uuid.UUID, role: str, content: str, meta: dict[str, Any] | None = None) -> None:
        async with session_scope() as s:
            s.add(ChatMessage(session_id=session_id, role=role, content=content, meta=meta or {}))
            sess = await s.get(ChatSession, session_id)
            if sess is not None:
                sess.last_activity_at = datetime.now(UTC)
                if not sess.title and role == "user":
                    sess.title = content[:80]

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
        await self._store(sess.id, "user", text, {"channel": channel})
        await bus.publish("chat.user", session_id=str(sess.id), text=text, channel=channel)

        memory_context = await self._recall(text, principal)
        instructions = turn_instructions(channel, memory_context=memory_context, tainted=sess.tainted)

        async def emit(ev: AgentEvent) -> None:
            await bus.publish(f"chat.{ev.type}", session_id=str(sess.id), text=ev.text, data=_safe_event_data(ev.data))
            if on_event:
                await on_event(ev)

        model, model_reason, confirmation, agent_text = await self._model_for_turn(sess, principal, channel, text)

        if confirmation is not None:
            await emit(AgentEvent("delta", text=confirmation))
            result = TurnResult(sess.id, confirmation, [], provider="system")
        elif self.state.kill_switch.engaged:
            reply = "Der Not-Aus ist aktiv, Sir. Ich führe derzeit keine Aktionen aus."
            await emit(AgentEvent("delta", text=reply))
            result = TurnResult(sess.id, reply, [], provider="system")
        elif self.state.agent is not None and self.state.agent.available():
            await bus.publish("chat.model", session_id=str(sess.id), model=model, reason=model_reason)
            result = await self._run_claude(sess, principal, agent_text, instructions, emit, model)
            result.model = model
        elif self.state.hermes is not None and await self._hermes_ready():
            result = await self._run_hermes(sess, principal, text, instructions, emit, images)
        else:
            result = await self._run_fallback(sess, text, instructions, emit)

        await self._store(
            sess.id,
            "assistant",
            result.text,
            {"tools": result.tools, "provider": result.provider, "error": result.error, "model": result.model},
        )
        await bus.publish(
            "chat.done",
            session_id=str(sess.id),
            text=result.text,
            error=result.error,
            provider=result.provider,
            model=result.model,
        )
        if (
            result.text
            and not result.error
            and principal.role in ("owner", "voice_owner", "telegram_owner", "phone_owner")
        ):
            self.state.memory.enqueue_extraction(text, result.text, {"session_id": str(sess.id), "channel": channel})
        return result

    async def _recall(self, text: str, principal: Principal) -> str:
        if len(text) < 8:
            return ""
        try:
            hits = await self.state.memory.search(text, limit=4, include_sensitive=principal.role == "owner")
        except Exception:
            logger.warning("Memory recall failed", exc_info=True)
            return ""
        lines = [f"- {h['title']}: {h['content'][:400]} ({h['path']})" for h in hits if h["score"] >= 0.55]
        return "\n".join(lines)

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
            elif principal.role in ("owner", "voice_owner", "telegram_owner", "phone_owner"):
                router.set(cmd.model, cmd.scope, str(sess.id))
                await audit(principal.label, "agent.model", details={"model": cmd.model, "scope": cmd.scope})
                await bus.publish("agent.model", **router.status(str(sess.id)))
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
        async with self._run_lock:
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

    async def interrupt(self) -> bool:
        run = self.active_run
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


def _safe_event_data(data: dict[str, Any]) -> dict[str, Any]:
    keep = ("name", "tool", "status", "error", "duration_ms", "run_id", "preview", "args")
    out = {k: data[k] for k in keep if k in data}
    if "args" in out:
        out["args"] = str(out["args"])[:300]
    return out
