"""Agent brain via the official Claude Code CLI (Sir's Claude subscription).

* One long-lived ``claude -p --input-format stream-json`` process per conversation: the first
  token of follow-up turns arrives in ~1 s instead of paying the ~15 s CLI start-up each turn.
  A pre-spawned standby process makes the first turn of a new conversation fast as well.
* Idle processes are stopped; the conversation continues later with ``--resume <session>``.
* Isolation: ``--restricted`` (no user/project settings, hooks or plugins), ``--tools ""`` (no
  built-in file/shell tools), ``--strict-mcp-config`` with ONLY the Jarvis MCP server, and
  ``--allowedTools mcp__jarvis`` — every action still passes Jarvis' policy/approval engine.
* Authentication: the unmodified CLI with Sir's own login. API-key variables are stripped so
  the subscription is used; no OAuth tokens are read or reused anywhere.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jarvis.agent.llm import AgentEvent
from jarvis.agent.model_router import MODELS
from jarvis.agent.persona import render_soul
from jarvis.events import spawn

if TYPE_CHECKING:
    from jarvis.app_state import AppState

logger = logging.getLogger(__name__)
_STRIP_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_PROFILE",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)
MCP_PREFIX = "mcp__jarvis__"


class ClaudeUnavailable(RuntimeError):
    pass


def claude_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _STRIP_ENV and not k.startswith("JARVIS_SECRET")}
    env["PATH"] = env.get("PATH", "") + ":/opt/homebrew/bin:/usr/local/bin"
    return env


def friendly_error(text: str) -> str:
    low = text.lower()
    if "limit" in low and ("reset" in low or "usage" in low or "session" in low or "rate" in low):
        return f"Das Claude-Kontingent der Subscription ist ausgeschöpft ({text.strip()[:120]})."
    if "auth" in low or "login" in low or "401" in low:
        return "Claude Code ist nicht angemeldet (im Terminal `claude` → /login)."
    return text.strip()[:300] or "Unbekannter Fehler von Claude Code"


@dataclass
class _Proc:
    proc: asyncio.subprocess.Process
    claude_session_id: str | None = None
    model: str = ""
    last_used: float = field(default_factory=time.monotonic)
    busy: bool = False
    stderr_tail: list[str] = field(default_factory=list)

    @property
    def alive(self) -> bool:
        return self.proc.returncode is None


class ClaudeCodeAgent:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self.cfg = state.settings.agent
        self.home = state.settings.paths.data_dir / "agent-home"
        self.mcp_config = state.settings.paths.data_dir / "agent-mcp.json"
        self._procs: dict[uuid.UUID, _Proc] = {}
        self._standby: _Proc | None = None
        self._standby_lock = asyncio.Lock()
        self._reaper: asyncio.Task[None] | None = None
        self.last_error: str | None = None

    # ------------------------------------------------------------------ lifecycle
    def write_mcp_config(self, token: str) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        cfg = {
            "mcpServers": {
                "jarvis": {
                    "type": "http",
                    "url": f"http://127.0.0.1:{self.state.settings.server.port}/mcp",
                    "headers": {"Authorization": f"Bearer {token}"},
                }
            }
        }
        tmp = self.mcp_config.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.mcp_config)

    async def start(self) -> None:
        self._reaper = asyncio.create_task(self._reap_idle())
        spawn(self._ensure_standby(), name="claude-standby")

    async def stop(self) -> None:
        if self._reaper:
            self._reaper.cancel()
        for p in [*self._procs.values(), *([self._standby] if self._standby else [])]:
            await self._kill(p)
        self._procs.clear()
        self._standby = None

    def available(self) -> bool:
        return shutil.which(self.cfg.claude_binary) is not None and self.mcp_config.exists()

    def system_prompt(self) -> str:
        skills = self.state.skills.enabled_names()
        skill_note = (
            "\n\n## Skills\nDu kannst Sirs aktivierte Claude-Skills mit dem Skill-Werkzeug laden, wenn sie zur Aufgabe "
            "passen (Wissen, Vorgehen, Designrichtlinien). Du selbst hast keinen Datei- oder Shell-Zugriff: Aufgaben, "
            "die Dateien erstellen oder Code schreiben (z. B. Websites), gibst du per `coding_start_job` an "
            "Claude Code weiter und nennst im Auftrag die passenden Skills – dort stehen dieselben Skills bereit.\n"
            f"Aktivierte Skills: {', '.join(skills[:80]) or 'keine'}."
        )
        return render_soul(self.state.personality) + (
            "\n\n## Werkzeuge\nDeine Werkzeuge sind die Jarvis-Werkzeuge (Namen beginnen mit `mcp__jarvis__`) und "
            "das Skill-Werkzeug." + skill_note
        )

    async def reload(self) -> None:
        """Apply changed skills/persona: idle processes and the standby are restarted, busy ones finish first."""
        for sid, p in list(self._procs.items()):
            if not p.busy:
                await self._kill(p)
                self._procs.pop(sid, None)
        if self._standby is not None:
            await self._kill(self._standby)
            self._standby = None
        spawn(self._ensure_standby(), name="claude-standby")

    async def _spawn(self, resume: str | None = None) -> _Proc:
        exe = shutil.which(self.cfg.claude_binary)
        if exe is None:
            raise ClaudeUnavailable("Claude Code CLI nicht gefunden")
        argv = [
            exe,
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--restricted",
            "--tools",
            "Skill",
            "--plugin-dir",
            str(self.state.skills.plugin_dir),
            "--strict-mcp-config",
            "--mcp-config",
            str(self.mcp_config),
            "--allowedTools",
            "mcp__jarvis",
            "Skill",
            "--permission-prompts",
            "none",
            "--model",
            self._model_id(self.cfg.model),
            "--append-system-prompt",
            self.system_prompt(),
        ]
        if resume:
            argv += ["--resume", resume]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=self.home,
            env=claude_env(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
            start_new_session=True,
        )
        p = _Proc(proc=proc, model=self._model_id(self.cfg.model))
        spawn(self._drain_stderr(p), name="claude-stderr")
        return p

    async def _drain_stderr(self, p: _Proc) -> None:
        assert p.proc.stderr is not None
        async for raw in p.proc.stderr:
            line = raw.decode(errors="replace").strip()
            if line:
                p.stderr_tail = [*p.stderr_tail[-9:], line[:300]]

    async def _ensure_standby(self) -> None:
        async with self._standby_lock:
            if self._standby is None or not self._standby.alive:
                try:
                    self._standby = await self._spawn()
                except Exception as e:
                    self.last_error = str(e)
                    logger.warning("Claude standby spawn failed: %s", e)

    async def _kill(self, p: _Proc) -> None:
        if p.alive:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(p.proc.pid, 15)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(p.proc.wait(), 5)

    async def _reap_idle(self) -> None:
        idle = self.cfg.idle_timeout_minutes * 60
        while True:
            await asyncio.sleep(30)
            now = time.monotonic()
            for sid, p in list(self._procs.items()):
                if not p.busy and (now - p.last_used > idle or not p.alive):
                    await self._kill(p)
                    self._procs.pop(sid, None)

    async def _proc_for(self, session_id: uuid.UUID, resume: str | None) -> _Proc:
        p = self._procs.get(session_id)
        if p is not None and p.alive:
            return p
        if resume:
            p = await self._spawn(resume=resume)
        else:
            async with self._standby_lock:
                p, self._standby = self._standby, None
            if p is None or not p.alive:
                p = await self._spawn()
            spawn(self._ensure_standby(), name="claude-standby")
        self._procs[session_id] = p
        return p

    # ------------------------------------------------------------------ turns
    @staticmethod
    def _model_id(alias: str) -> str:
        return MODELS.get(alias, {"id": alias})["id"]

    async def _switch_model(self, p: _Proc, model_id: str) -> None:
        """Switch the model of a running session in place (stream-json control request, no restart)."""
        assert p.proc.stdin is not None and p.proc.stdout is not None
        rid = f"model-{uuid.uuid4().hex[:8]}"
        req = {"type": "control_request", "request_id": rid, "request": {"subtype": "set_model", "model": model_id}}
        p.proc.stdin.write((json.dumps(req) + "\n").encode())
        await p.proc.stdin.drain()
        async with asyncio.timeout(30):
            while True:
                line = await p.proc.stdout.readline()
                if not line:
                    raise ClaudeUnavailable("Claude Code wurde beim Modellwechsel beendet")
                ev = json.loads(line)
                if ev.get("type") == "control_response" and ev.get("response", {}).get("request_id") == rid:
                    if ev["response"].get("subtype") != "success":
                        raise ClaudeUnavailable(f"Modellwechsel fehlgeschlagen: {ev['response']}")
                    p.model = model_id
                    return

    async def chat_stream(
        self, session_id: uuid.UUID, resume_id: str | None, text: str, context: str, model: str | None = None
    ) -> AsyncIterator[AgentEvent]:
        p = await self._proc_for(session_id, resume_id)
        p.busy = True
        p.last_used = time.monotonic()
        model_id = self._model_id(model or self.cfg.model)
        if p.model != model_id:
            try:
                await self._switch_model(p, model_id)
            except (ClaudeUnavailable, TimeoutError, json.JSONDecodeError) as e:
                p.busy = False
                yield AgentEvent("failed", text=f"Modellwechsel fehlgeschlagen: {e}")
                return
        content = f"<kontext>\n{context}\n</kontext>\n\n{text}" if context else text
        msg = {"type": "user", "message": {"role": "user", "content": content}}
        assert p.proc.stdin is not None and p.proc.stdout is not None
        try:
            p.proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode())
            await p.proc.stdin.drain()
            while True:
                line = await p.proc.stdout.readline()
                if not line:
                    err = friendly_error(" ".join(p.stderr_tail) or "Claude Code wurde beendet")
                    self._procs.pop(session_id, None)
                    yield AgentEvent("failed", text=err)
                    return
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for out in self._translate(ev, p):
                    yield out
                    if out.type in ("completed", "failed"):
                        return
        finally:
            p.busy = False
            p.last_used = time.monotonic()

    def _translate(self, ev: dict[str, Any], p: _Proc) -> list[AgentEvent]:
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            p.claude_session_id = ev.get("session_id") or p.claude_session_id
            return [AgentEvent("run_started", data={"run_id": p.claude_session_id})]
        if t == "system" and ev.get("subtype") == "api_retry":
            logger.warning("Claude API retry: %s", ev.get("error"))
            return []
        if t == "stream_event":
            inner = ev.get("event", {})
            if inner.get("type") == "content_block_delta" and inner.get("delta", {}).get("type") == "text_delta":
                return [AgentEvent("delta", text=str(inner["delta"].get("text", "")))]
            return []
        if t == "assistant":
            out = []
            for block in ev.get("message", {}).get("content", []) or []:
                if block.get("type") == "tool_use":
                    name = str(block.get("name", "")).removeprefix(MCP_PREFIX)
                    out.append(
                        AgentEvent("tool_started", data={"name": name, "args": json.dumps(block.get("input"))[:300]})
                    )
            return out
        if t == "user":
            out = []
            for block in ev.get("message", {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    out.append(
                        AgentEvent("tool_failed" if block.get("is_error") else "tool_completed", data={"name": "tool"})
                    )
            return out
        if t == "result":
            p.claude_session_id = ev.get("session_id") or p.claude_session_id
            if ev.get("is_error"):
                detail = str(ev.get("result") or "; ".join(map(str, ev.get("errors") or [])) or ev.get("subtype"))
                self.last_error = detail
                return [AgentEvent("failed", text=friendly_error(detail), data={"session_id": p.claude_session_id})]
            self.last_error = None
            return [
                AgentEvent("assistant_completed", text=str(ev.get("result") or "")),
                AgentEvent(
                    "completed",
                    data={
                        "session_id": p.claude_session_id,
                        "duration_ms": ev.get("duration_ms"),
                        "num_turns": ev.get("num_turns"),
                    },
                ),
            ]
        return []

    def session_id_for(self, session_id: uuid.UUID) -> str | None:
        p = self._procs.get(session_id)
        return p.claude_session_id if p else None

    async def interrupt(self, session_id: uuid.UUID) -> None:
        p = self._procs.pop(session_id, None)
        if p is not None:
            await self._kill(p)  # the conversation continues later via --resume

    def status(self) -> dict[str, Any]:
        return {
            "provider": "claude_code",
            "model": self.cfg.model,
            "active_sessions": len(self._procs),
            "standby_ready": bool(self._standby and self._standby.alive),
            "last_error": self.last_error,
        }


class ClaudeOneShot:
    """Single structured/text completions for utility tasks (extraction, vision) via ``claude -p``."""

    def __init__(self, state: AppState) -> None:
        self.state = state
        self.cfg = state.settings.agent
        self.home = state.settings.paths.data_dir / "agent-home"
        self._sem = asyncio.Semaphore(2)

    async def run(
        self,
        prompt: str,
        *,
        schema: dict[str, Any] | None = None,
        system: str | None = None,
        read_dir: Path | None = None,
        timeout_s: float = 120,
    ) -> Any:
        exe = shutil.which(self.cfg.claude_binary)
        if exe is None:
            raise ClaudeUnavailable("Claude Code CLI nicht gefunden")
        argv = [
            exe,
            "-p",
            prompt,
            "--output-format",
            "json",
            "--restricted",
            "--strict-mcp-config",
            "--permission-prompts",
            "none",
            "--disable-slash-commands",
            "--model",
            MODELS.get(self.cfg.utility_model, {"id": self.cfg.utility_model})["id"],
        ]
        if read_dir is not None:
            argv += ["--tools", "Read", "--allowedTools", "Read", "--add-dir", str(read_dir)]
        else:
            argv += ["--tools", ""]
        if schema is not None:
            argv += ["--json-schema", json.dumps(schema)]
        if system:
            argv += ["--append-system-prompt", system]
        self.home.mkdir(parents=True, exist_ok=True)
        async with self._sem:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=self.home,
                env=claude_env(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
            except TimeoutError:
                proc.kill()
                raise ClaudeUnavailable("Zeitüberschreitung bei Claude Code") from None
        try:
            data = json.loads(out.decode())
        except json.JSONDecodeError as e:
            raise ClaudeUnavailable(friendly_error(err.decode() or out.decode())) from e
        if isinstance(data, list):
            data = data[-1]
        if data.get("is_error"):
            raise ClaudeUnavailable(friendly_error(str(data.get("result") or data.get("errors"))))
        return data.get("structured_output") if schema is not None else str(data.get("result") or "")
