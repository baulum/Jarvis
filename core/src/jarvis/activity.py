"""Live activity pipeline: what the 3D brain shows, derived from real bus events only.

Raw bus events carry everything a client needs to render a conversation (user text, streamed answer, tool
arguments, memory titles and queries). The brain does not need any of that. This pipeline folds the raw
events into one compact, privacy-filtered stream of *activity steps* (bus type ``activity.step``):

  request          a request came in (chat.user)                      → input region
  llm              the language model works on the turn (chat.model)  → activity spreads from the core
  response         first streamed token / answer finished (chat.*)    → output region
  tool             a tool call starts / ends (executor tool.*)        → impulse to the tool node and back
  memory.retrieve  memory lookup with the hits it actually returned   → those clusters light up
  memory.store     a memory was added / updated / merged / deleted    → new connections grow
  agent            coding jobs, multi-step tasks, scheduler runs      → the agent's own region
  approval         an action waits for / got Sir's approval
  system           voice state, kill switch

Every step is built field by field from a whitelist – nothing is passed through. Steps carry ids (graph node
ids, call ids), tool names, statuses, durations and German status labels; never user text, answers, tool
arguments, memory titles or search queries. Error messages are redacted (credentials, tokens, e-mail
addresses, phone numbers, long opaque strings) and shortened. Model reasoning ("thinking") is ignored.

Clients resynchronise after a dropped connection with ``snapshot(since)``: steps carry a per-process ``seq``
and the pipeline's ``boot`` id, the snapshot returns what is running right now plus the steps they missed.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from jarvis.events import Event, EventBus

logger = logging.getLogger(__name__)

BUFFER = 400
"""Steps kept for resynchronisation."""
CODING_PROGRESS_EVERY_S = 1.5
"""At most one progress step per coding job in this interval (the runner reports every tool call)."""

# ------------------------------------------------------------------ privacy filter
_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\b(bearer|basic)\s+[^\s,;]+"), r"\1 ***"),
    # key=value / key: value credentials
    (
        re.compile(
            r"(?i)\b(bearer|token|access[_-]?token|api[_-]?key|apikey|secret|client[_-]?secret|password|passwort|"
            r"passwd|pwd|pin|authorization|auth|cookie|session[_-]?id)\b(\s*[:=]\s*|\s+)[^\s,;]+"
        ),
        r"\1=***",
    ),
    (re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s/]+@"), "://***@"),  # credentials in URLs
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"), "***"),  # JWT
    (re.compile(r"\b(?:sk|pk|rk|ghp|gho|ghs|github_pat|glpat|xox[abprs]|AKIA|AIza)[-_A-Za-z0-9]{10,}"), "***"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "***@***"),  # e-mail addresses
    (re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}\b"), "***"),  # IBAN
    (re.compile(r"(?<![\w.])\+?\d[\d ()/-]{7,}\d"), "***"),  # phone numbers
    (re.compile(r"[A-Za-z0-9+/_-]{32,}={0,2}"), "***"),  # long opaque strings (keys, hashes, tokens)
]


def redact(text: object, limit: int = 160) -> str:
    """Error text safe for the activity stream: credentials and personal identifiers removed, shortened."""
    if text is None:
        return ""
    out = " ".join(str(text).split())
    for pattern, repl in _REDACTIONS:
        out = pattern.sub(repl, out)
    return out if len(out) <= limit else out[: limit - 1].rstrip() + "…"


_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


def _id(v: object) -> str | None:
    """Identifiers only (uuids, call ids, tool names) – anything else is dropped."""
    s = str(v) if v is not None else ""
    return s if _ID.match(s) else None


# ------------------------------------------------------------------ labels
_TOOL_LABELS: list[tuple[str, str]] = [
    ("memory_search", "Durchsuche Erinnerungen"),
    ("memory_get", "Lese Erinnerung"),
    ("memory_save", "Speichere neue Erinnerung"),
    ("memory_correct", "Korrigiere Erinnerung"),
    ("memory_forget", "Vergesse Erinnerung"),
    ("browser_screenshot", "Fotografiere Webseite"),
    ("browser_", "Starte Browser"),
    ("chrome_task", "Steuere Chrome"),
    ("device_browser_automate", "Starte Browser"),
    ("web_search", "Suche im Web"),
    ("http_", "Rufe Webadresse ab"),
    ("webhook_send", "Sende Webhook"),
    ("device_shell_run", "Führe Befehl auf Gerät aus"),
    ("shell_run", "Führe Befehl aus"),
    ("python", "Führe Python aus"),
    ("gmail_send", "Sende E-Mail"),
    ("gmail_", "Lese E-Mails"),
    ("calendar_create", "Lege Termin an"),
    ("calendar_", "Prüfe Kalender"),
    ("drive_", "Durchsuche Google Drive"),
    ("lexware_", "Arbeite in Lexware"),
    ("github_", "Arbeite mit GitHub"),
    ("git_", "Arbeite mit Git"),
    ("vercel_", "Arbeite mit Vercel"),
    ("coding_start_job", "Starte Coding-Agent"),
    ("coding_", "Steuere Coding-Agent"),
    ("project_create", "Lege Projekt an"),
    ("task_start_playbook", "Starte Aufgabe"),
    ("task_", "Prüfe Aufgaben"),
    ("schedule_", "Plane Auftrag"),
    ("whatsapp_", "Arbeite mit WhatsApp"),
    ("telegram_", "Sende Telegram-Nachricht"),
    ("mac_screenshot", "Erstelle Bildschirmfoto"),
    ("device_screenshot", "Erstelle Bildschirmfoto"),
    ("phone_screenshot", "Erstelle Bildschirmfoto"),
    ("mac_", "Steuere den Mac"),
    ("device_", "Steuere Gerät"),
    ("phone_", "Steuere Handy"),
    ("mobile_", "Kontaktiere Handy"),
    ("notify_owner", "Benachrichtige Sir"),
    ("image_generate", "Erzeuge Bild"),
    ("video_generate", "Erzeuge Video"),
    ("route_", "Plane Route"),
    ("attachment_read", "Lese Anhang"),
    ("data_export", "Exportiere Daten"),
    ("system", "Prüfe System"),
    ("agent_", "Prüfe Agent-Status"),
]


def tool_label(tool: str) -> str:
    for prefix, label in _TOOL_LABELS:
        if tool.startswith(prefix):
            return label
    return f"Nutze Werkzeug {tool}"


_CODING_LABELS = {
    "queued": "Coding-Auftrag eingereiht",
    "running": "Coding-Agent arbeitet",
    "paused": "Coding-Agent pausiert",
    "completed": "Coding-Auftrag abgeschlossen",
    "failed": "Coding-Auftrag fehlgeschlagen",
    "cancelled": "Coding-Auftrag abgebrochen",
}
_TASK_LABELS = {
    "queued": "Aufgabe eingereiht",
    "running": "Aufgabe läuft",
    "paused": "Aufgabe pausiert",
    "waiting_approval": "Aufgabe wartet auf Freigabe",
    "waiting_input": "Aufgabe wartet auf Eingabe",
    "completed": "Aufgabe abgeschlossen",
    "failed": "Aufgabe fehlgeschlagen",
    "cancelled": "Aufgabe abgebrochen",
}
_MEMORY_LABELS = {
    "added": "Speichere neue Erinnerung",
    "updated": "Aktualisiere Erinnerung",
    "corrected": "Korrigiere Erinnerung",
    "merged": "Führe Erinnerungen zusammen",
    "deleted": "Erinnerung gelöscht",
    "created": "Neue Erinnerung im Vault",
    "modified": "Erinnerung im Vault geändert",
}
_DONE = {"completed", "failed", "cancelled"}
_STATUS = {"completed": "ok", "failed": "error", "cancelled": "cancelled"}


# ------------------------------------------------------------------ state
@dataclass(slots=True)
class _Turn:
    started: float
    channel: str
    llm: bool = False
    streaming: bool = False
    tools: set[str] = field(default_factory=set)


@dataclass(slots=True)
class ToolStat:
    calls: int = 0
    errors: int = 0
    total_ms: int = 0
    last_ms: int | None = None
    last_status: str | None = None
    last_at: float | None = None
    running: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "errors": self.errors,
            "avg_ms": round(self.total_ms / self.calls) if self.calls else None,
            "last_ms": self.last_ms,
            "last_status": self.last_status,
            "last_at": self.last_at,
            "running": self.running,
        }


class ActivityPipeline:
    """Subscribes to the bus, publishes ``activity.step`` events and keeps a resync buffer."""

    def __init__(self, bus: EventBus, buffer: int = BUFFER) -> None:
        self.bus = bus
        self.boot = uuid.uuid4().hex[:12]
        self.seq = 0
        self.recent: deque[dict[str, Any]] = deque(maxlen=buffer)
        self.running: dict[str, dict[str, Any]] = {}
        self.tool_stats: dict[str, ToolStat] = {}
        self.agent_stats: dict[str, dict[str, int]] = {}
        self._turns: dict[str, _Turn] = {}
        self._calls: dict[str, tuple[str, str]] = {}  # call id -> (parent key, tool)
        self._coding_progress: dict[str, float] = {}
        self._voice: str | None = None
        bus.subscribe("*", self._on_event)

    def close(self) -> None:
        self.bus.unsubscribe("*", self._on_event)

    # ---------------------------------------------------------------- bus
    async def _on_event(self, ev: Event) -> None:
        if ev.type.startswith("activity.") or ev.type in ("voice.level", "log"):
            return
        try:
            steps = self.translate(ev)
        except Exception:  # a malformed event must never break the bus
            logger.debug("activity translation failed for %s", ev.type, exc_info=True)
            return
        for step in steps:
            self.record(step)
            data = {k: v for k, v in step.items() if k != "nodes"}  # the nodes travel in the envelope
            await self.bus.publish("activity.step", nodes=list(step["nodes"]), **data)

    def record(self, step: dict[str, Any]) -> dict[str, Any]:
        """Assigns seq/boot, keeps the step for resync and tracks what is running."""
        self.seq += 1
        step["seq"] = self.seq
        step["boot"] = self.boot
        self.recent.append(step)
        key = step["key"]
        if step["state"] == "end":
            self.running.pop(key, None)
        elif step["status"] in ("running", "waiting"):
            self.running[key] = step
        return step

    def snapshot(self, since: int = 0, boot: str | None = None, limit: int = 200) -> dict[str, Any]:
        """What a (re)connecting client needs: running work, missed steps, per-tool statistics.

        ``complete`` is false when the client's last seq is older than the buffer (or from a previous core
        process) – it must then replace its state with ``running`` instead of merging the missed steps.
        """
        same = boot == self.boot
        oldest = self.recent[0]["seq"] if self.recent else self.seq + 1
        complete = same and since + 1 >= oldest
        missed = [s for s in self.recent if s["seq"] > since] if same else list(self.recent)
        return {
            "boot": self.boot,
            "seq": self.seq,
            "complete": complete,
            "running": list(self.running.values()),
            "steps": missed[-limit:],
            "tools": {k: v.to_dict() for k, v in self.tool_stats.items()},
            "agents": self.agent_stats,
        }

    # ---------------------------------------------------------------- translation
    def _step(
        self,
        phase: str,
        state: str,
        status: str,
        key: str,
        label: str,
        *,
        nodes: Sequence[str | None] | None = None,
        parent: str | None = None,
        session_id: str | None = None,
        task_id: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        step: dict[str, Any] = {
            "phase": phase,
            "state": state,
            "status": status,
            "key": key,
            "label": label,
            "parent": parent,
            "session_id": session_id,
            "task_id": task_id,
            "nodes": [n for n in (nodes or []) if n],
            "t": round(time.time() * 1000),
        }
        step.update({k: v for k, v in extra.items() if v is not None})
        return step

    def _parent(self, sid: str | None, task_id: str | None) -> str | None:
        # (a session's memory lookup may arrive just before its chat.user – it still belongs to that turn)
        if task_id:
            return f"task:{task_id}"
        return f"turn:{sid}" if sid else None

    def translate(self, ev: Event) -> list[dict[str, Any]]:
        d = ev.data
        t = ev.type
        sid = _id(ev.session_id)
        tid = _id(ev.task_id)
        family = t.split(".", 1)[0]
        if family == "chat":
            return self._chat(t, d, sid)
        if t in ("tool.started", "tool.finished"):
            return self._tool(t, d, sid, tid)
        if t == "memory.recalled":
            return self._recall(ev, d, sid, tid)
        if t in ("memory.changed", "memory.pending"):
            return self._memory_store(t, d, sid, tid)
        if t in ("approval.requested", "approval.decided"):
            return self._approval(t, d, sid)
        if family == "coding":
            return self._coding(t, d)
        if family == "task":
            return self._task(t, d, tid)
        if t == "schedule.ran":
            return self._schedule(d)
        if t == "voice.status":
            return self._voice_status(d)
        if t == "system.killswitch":
            on = bool(d.get("engaged"))
            return [
                self._step(
                    "system",
                    "end",
                    "error" if on else "ok",
                    "system:killswitch",
                    "Not-Aus aktiviert" if on else "Not-Aus aufgehoben",
                    nodes=["agent:jarvis"],
                )
            ]
        return []

    def _chat(self, t: str, d: dict[str, Any], sid: str | None) -> list[dict[str, Any]]:
        if not sid:
            return []
        key = f"turn:{sid}"
        nodes = ["agent:jarvis", f"session:{sid}"]
        if t == "chat.user":
            channel = _id(d.get("channel")) or "desktop"
            self._turns[sid] = _Turn(started=time.monotonic(), channel=channel)
            return [
                self._step(
                    "request",
                    "start",
                    "running",
                    key,
                    "Analysiere Anfrage",
                    nodes=nodes,
                    session_id=sid,
                    channel=channel,
                )
            ]
        turn = self._turns.get(sid)
        if turn is None:
            return []
        out: list[dict[str, Any]] = []
        if t == "chat.model" or (not turn.llm and t in ("chat.delta", "chat.tool_started", "chat.run_started")):
            if not turn.llm:
                turn.llm = True
                model = _id(d.get("model"))
                out.append(
                    self._step(
                        "llm",
                        "progress",
                        "running",
                        key,
                        "Sprachmodell verarbeitet die Anfrage",
                        nodes=["agent:jarvis"],
                        session_id=sid,
                        model=model,
                        channel=turn.channel,
                    )
                )
        if t == "chat.delta" and not turn.streaming:
            turn.streaming = True
            out.append(
                self._step(
                    "response",
                    "progress",
                    "running",
                    key,
                    "Generiere Antwort",
                    nodes=["agent:jarvis"],
                    session_id=sid,
                    channel=turn.channel,
                )
            )
        if t in ("chat.done", "chat.interrupted"):
            self._turns.pop(sid, None)
            err = d.get("error")
            cancelled = t == "chat.interrupted" or err == "cancelled"
            status = "cancelled" if cancelled else "error" if err else "ok"
            label = {"ok": "Antwort gesendet", "error": "Antwort fehlgeschlagen", "cancelled": "Antwort abgebrochen"}
            out.append(
                self._step(
                    "response",
                    "end",
                    status,
                    key,
                    label[status],
                    nodes=nodes,
                    session_id=sid,
                    channel=turn.channel,
                    duration_ms=round((time.monotonic() - turn.started) * 1000),
                    error=redact(err) if status == "error" else None,
                )
            )
        return out

    def _tool(self, t: str, d: dict[str, Any], sid: str | None, tid: str | None) -> list[dict[str, Any]]:
        tool = _id(d.get("tool"))
        call = _id(d.get("call_id"))
        if not tool or not call:
            return []
        connector = _id(d.get("connector"))
        nodes = [f"tool:{tool}", f"connector:{connector}" if connector else None]
        stat = self.tool_stats.setdefault(tool, ToolStat())
        key = f"call:{call}"
        if t == "tool.started":
            parent = self._parent(sid, tid)
            self._calls[call] = (parent or "", tool)
            stat.running += 1
            turn = self._turns.get(sid or "")
            if turn is not None and not tid:
                turn.tools.add(call)
                turn.streaming = False  # the model continues writing after the result
            parallel = sum(1 for p, _ in self._calls.values() if p == (parent or ""))
            return [
                self._step(
                    "tool",
                    "start",
                    "running",
                    key,
                    tool_label(tool),
                    nodes=nodes,
                    parent=parent,
                    session_id=sid,
                    task_id=tid,
                    tool=tool,
                    connector=connector,
                    risk=_id(d.get("risk")),
                    parallel=parallel,
                )
            ]
        parent_key, _tool = self._calls.pop(call, (self._parent(sid, tid) or "", tool))
        turn = self._turns.get(sid or "")
        if turn is not None:
            turn.tools.discard(call)
        ok = d.get("status") == "ok"
        duration = d.get("duration_ms")
        duration_ms = int(duration) if isinstance(duration, int | float) else None
        stat.running = max(0, stat.running - 1)
        stat.calls += 1
        stat.errors += 0 if ok else 1
        stat.total_ms += duration_ms or 0
        stat.last_ms = duration_ms
        stat.last_status = "ok" if ok else "error"
        stat.last_at = round(time.time() * 1000)
        return [
            self._step(
                "tool",
                "end",
                "ok" if ok else "error",
                key,
                f"Ergebnis von {tool}" if ok else f"{tool} fehlgeschlagen",
                nodes=nodes,
                parent=parent_key or None,
                session_id=sid,
                task_id=tid,
                tool=tool,
                connector=connector,
                duration_ms=duration_ms,
                error=None if ok else redact(d.get("message")),
            )
        ]

    def _recall(self, ev: Event, d: dict[str, Any], sid: str | None, tid: str | None) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        for h in d.get("hits") or []:
            if not isinstance(h, dict) or not _id(h.get("id")):
                continue
            score = h.get("score")
            hits.append(
                {
                    "id": str(h["id"]),
                    "kind": _id(h.get("kind")),
                    "score": round(float(score), 3) if isinstance(score, int | float) else None,
                }
            )
        purpose = _id(d.get("purpose"))
        via = _id(d.get("via"))
        duration = d.get("duration_ms")
        label = "Gleiche mit vorhandenen Erinnerungen ab" if purpose == "extraction" else "Durchsuche Erinnerungen"
        return [
            self._step(
                "memory.retrieve",
                "end",
                "ok",
                f"recall:{ev.id[:12]}",
                label,
                nodes=[f"memory:{h['id']}" for h in hits],
                parent=self._recall_parent(via, sid, tid),
                session_id=sid,
                task_id=tid,
                purpose=purpose,
                via=via,
                hits=hits,
                count=len(hits),
                semantic=bool(d.get("semantic")),
                duration_ms=round(float(duration)) if isinstance(duration, int | float) else None,
            )
        ]

    def _recall_parent(self, via: str | None, sid: str | None, tid: str | None) -> str | None:
        """A lookup inside a tool call (memory_search …) belongs to that call, otherwise to the turn/task."""
        parent = self._parent(sid, tid) or ""
        if via:
            for call, (p, tool) in reversed(self._calls.items()):
                if tool == via and p == parent:
                    return f"call:{call}"
        return parent or None

    def _memory_store(self, t: str, d: dict[str, Any], sid: str | None, tid: str | None) -> list[dict[str, Any]]:
        mid = _id(d.get("id"))
        if not mid:
            return []
        if t == "memory.pending":
            return [
                self._step(
                    "memory.store",
                    "end",
                    "waiting",
                    f"memory:{mid}",
                    "Erinnerung vorgemerkt – wartet auf Bestätigung",
                    session_id=sid,
                    kind=_id(d.get("kind")),
                    change="pending",
                )
            ]
        change = _id(d.get("change")) or "updated"
        status = "cancelled" if change == "deleted" else "ok"
        return [
            self._step(
                "memory.store",
                "end",
                status,
                f"memory:{mid}",
                _MEMORY_LABELS.get(change, "Aktualisiere Erinnerung"),
                nodes=[f"memory:{mid}"],
                parent=self._parent(sid, tid),
                session_id=sid,
                task_id=tid,
                change=change,
                origin=_id(d.get("origin")),
            )
        ]

    def _approval(self, t: str, d: dict[str, Any], sid: str | None) -> list[dict[str, Any]]:
        aid = _id(d.get("approval_id"))
        tool = _id(d.get("tool"))
        if not aid or not tool:
            return []
        key = f"approval:{aid}"
        if t == "approval.requested":
            return [
                self._step(
                    "approval",
                    "start",
                    "waiting",
                    key,
                    "Wartet auf Freigabe",
                    nodes=[f"tool:{tool}"],
                    parent=self._parent(sid, None),
                    session_id=sid,
                    tool=tool,
                    risk=_id(d.get("risk")),
                )
            ]
        st = str(d.get("status") or "")
        status = "ok" if st == "executed" else "error" if st == "failed" else "cancelled"
        label = {
            "ok": "Freigegeben und ausgeführt",
            "error": "Freigegeben, aber fehlgeschlagen",
            "cancelled": "Abgelehnt",
        }
        return [
            self._step("approval", "end", status, key, label[status], nodes=[f"tool:{tool}"], session_id=sid, tool=tool)
        ]

    def _bump_agent(self, agent: str, status: str) -> None:
        s = self.agent_stats.setdefault(agent, {"runs": 0, "errors": 0})
        if status in _DONE:
            s["runs"] += 1
            s["errors"] += 1 if status == "failed" else 0

    def _coding(self, t: str, d: dict[str, Any]) -> list[dict[str, Any]]:
        job = _id(d.get("job_id"))
        if not job:
            return []
        key = f"coding:{job}"
        nodes = ["agent:coding", key]
        if t == "coding.deleted":
            return [
                self._step("agent", "end", "cancelled", key, "Coding-Auftrag gelöscht", nodes=nodes, agent="coding")
            ]
        if t == "coding.progress":
            tool = _id(d.get("tool"))
            now = time.monotonic()
            if not tool or now - self._coding_progress.get(job, 0.0) < CODING_PROGRESS_EVERY_S:
                return []
            self._coding_progress[job] = now
            return [
                self._step(
                    "agent",
                    "progress",
                    "running",
                    key,
                    f"Coding-Agent nutzt {tool}",
                    nodes=nodes,
                    agent="coding",
                    tool=tool,
                )
            ]
        status = str(d.get("status") or "")
        if status not in _CODING_LABELS:
            return []
        self._bump_agent("coding", status)
        done = status in _DONE
        if done:
            self._coding_progress.pop(job, None)
        state = "end" if done else "start" if key not in self.running else "progress"
        return [
            self._step(
                "agent",
                state,
                _STATUS.get(status, "waiting" if status in ("queued", "paused") else "running"),
                key,
                _CODING_LABELS[status],
                nodes=nodes,
                agent="coding",
                task_id=_id(d.get("task_id")),
            )
        ]

    def _task(self, t: str, d: dict[str, Any], tid: str | None) -> list[dict[str, Any]]:
        task = tid or _id(d.get("task_id"))
        if not task:
            return []
        key = f"task:{task}"
        nodes = ["agent:jarvis", key]
        if t == "task.created":
            return [
                self._step(
                    "agent", "start", "running", key, "Aufgabe gestartet", nodes=nodes, agent="tasks", task_id=task
                )
            ]
        if t == "task.step":
            name = redact(d.get("name"), 60)
            st = str(d.get("status") or "")
            if st != "running" or not name:
                return []
            return [
                self._step(
                    "agent", "progress", "running", key, f"Schritt: {name}", nodes=nodes, agent="tasks", task_id=task
                )
            ]
        if t == "task.input_required":
            return [
                self._step(
                    "agent",
                    "progress",
                    "waiting",
                    key,
                    _TASK_LABELS["waiting_input"],
                    nodes=nodes,
                    agent="tasks",
                    task_id=task,
                )
            ]
        status = str(d.get("status") or "")
        if status not in _TASK_LABELS:
            return []
        self._bump_agent("tasks", status)
        done = status in _DONE
        waiting = status.startswith("waiting") or status in ("queued", "paused")
        return [
            self._step(
                "agent",
                "end" if done else "progress",
                _STATUS[status] if done else "waiting" if waiting else "running",
                key,
                _TASK_LABELS[status],
                nodes=nodes,
                agent="tasks",
                task_id=task,
            )
        ]

    def _schedule(self, d: dict[str, Any]) -> list[dict[str, Any]]:
        sched = _id(d.get("id"))
        if not sched:
            return []
        ok = str(d.get("status") or "ok") in ("ok", "completed", "success")
        self._bump_agent("scheduler", "completed" if ok else "failed")
        return [
            self._step(
                "agent",
                "end",
                "ok" if ok else "error",
                f"schedule:{sched}",
                "Zeitplan ausgeführt" if ok else "Zeitplan fehlgeschlagen",
                nodes=["agent:scheduler", f"schedule:{sched}"],
                agent="scheduler",
            )
        ]

    def _voice_status(self, d: dict[str, Any]) -> list[dict[str, Any]]:
        state = _id(d.get("state"))
        if state == self._voice:
            return []
        self._voice = state
        labels = {
            "listening": "Hört zu",
            "recording": "Hört zu",
            "speaking": "Spricht",
            "thinking": "Verarbeitet Sprache",
        }
        if state in labels:
            return [
                self._step(
                    "system", "progress", "running", "voice", labels[state], nodes=["agent:voice"], agent="voice"
                )
            ]
        return [self._step("system", "end", "ok", "voice", "Stimme bereit", nodes=["agent:voice"], agent="voice")]
