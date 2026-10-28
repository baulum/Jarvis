"""REST API consumed by the desktop app, the voice daemon and future clients (iPhone, VPS)."""

from __future__ import annotations

import asyncio
import html
import shutil
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select

from jarvis.agent import attachments as att
from jarvis.agent.persona import time_of_day_greeting
from jarvis.agent.timeline import redact_for_mobile
from jarvis.api.deps import current_principal, get_state, require, require_owner
from jarvis.app_state import VERSION, AppState
from jarvis.coding.runner import CodingError
from jarvis.commands import command_list
from jarvis.config import REPO_ROOT, RISK_ORDER
from jarvis.connectors.kie_creative import MEDIA_TYPES, resolve_media_file
from jarvis.db import session_scope
from jarvis.db.models import Approval, AuditEntry, Call, ChatSession, CodingJob, Task
from jarvis.desktop_build import desktop_build_status
from jarvis.events import bus, spawn
from jarvis.logging_setup import ring
from jarvis.platform import get_adapter
from jarvis.security.audit import audit, verify_chain
from jarvis.security.auth import Principal, create_device, list_devices, revoke_device
from jarvis.security.guards import rate_limiter
from jarvis.voice import wakeup
from jarvis.voice.audio import pcm_to_wav
from jarvis.voice.commands import is_stop_command
from jarvis.voice.speech import speakable

router = APIRouter(prefix="/api")
Owner = Depends(require_owner)
Reader = Depends(require("read"))
Actor = Depends(require("low"))
StateDep = Depends(get_state)


def _uuid(v: str) -> uuid.UUID:
    try:
        return uuid.UUID(v)
    except ValueError as e:
        raise HTTPException(400, "invalid id") from e


# ------------------------------------------------------------------ health / system
@router.get("/health")
async def health(st: AppState = StateDep) -> dict[str, Any]:
    return {"status": "degraded" if st.degraded else "ok", "version": VERSION, "detail": st.degraded}


@router.get("/system/status")
async def system_status(st: AppState = StateDep, p: Principal = Reader) -> dict[str, Any]:
    return await st.system_status()


@router.get("/system/desktop-build")
async def desktop_build(commit: str = Query(default="", max_length=40), p: Principal = Reader) -> dict[str, Any]:
    """Is the desktop app older than the UI code in the repository the core runs from? (see desktop_build)"""
    return await desktop_build_status(REPO_ROOT, commit.strip().lower())


@router.get("/system/capabilities")
async def system_capabilities(
    refresh: bool = False, st: AppState = StateDep, p: Principal = Reader
) -> dict[str, Any]:
    """What this Core host can do (capability flags with reasons), which tools are unavailable here and why,
    and the connected Jarvis Device Agents with their own capabilities."""
    adapter = get_adapter()
    report = adapter.capabilities(refresh=refresh)
    if refresh:
        st.registry.set_capabilities(report)
    unavailable = [
        {"name": t.name, "connector": t.connector, "reason": why}
        for t in st.registry.all(include_disabled=True)
        if (why := st.registry.unavailable_reason(t)) is not None
    ]
    return {
        "platform": adapter.info(),
        "capabilities": report.to_dict(),
        "unavailable_tools": unavailable,
        "devices": st.device_hub.describe(),
    }


@router.get("/system/greeting")
async def greeting(st: AppState = StateDep, p: Principal = Reader) -> dict[str, str]:
    return {"text": time_of_day_greeting(st.personality)}


class KillSwitchBody(BaseModel):
    engaged: bool
    reason: str = ""


@router.post("/system/killswitch")
async def killswitch(
    body: KillSwitchBody, st: AppState = StateDep, p: Principal = Depends(current_principal)
) -> dict[str, Any]:
    if body.engaged:  # any authenticated device may STOP
        await st.kill_switch.engage(body.reason or "manuell", p.label)
    else:  # only the owner may release
        if p.role != "owner":
            raise HTTPException(403, "only the owner can release the kill switch")
        await st.kill_switch.release(p.label)
    return {"engaged": st.kill_switch.engaged, "reason": st.kill_switch.reason()}


# ------------------------------------------------------------------ chat
class AttachmentRef(BaseModel):
    file: str = Field(min_length=4, max_length=110)  # stored name from POST /api/attachments
    name: str = Field(default="", max_length=255)  # display name (sanitised again)


class ChatBody(BaseModel):
    message: str = Field(default="", max_length=40000)
    session_id: str | None = None
    channel: str = "desktop"
    wait: bool = False
    images: list[str] | None = None
    attachments: list[AttachmentRef] | None = Field(default=None, max_length=att.MAX_PER_MESSAGE)

    @model_validator(mode="after")
    def _not_empty(self) -> ChatBody:
        if not self.message and not self.attachments:
            raise ValueError("Die Nachricht ist leer.")
        return self


@router.post("/chat")
async def chat(body: ChatBody, st: AppState = StateDep, p: Principal = Actor) -> dict[str, Any]:
    channel = body.channel if body.channel in ("desktop", "voice", "api", "mobile") else "desktop"
    sid = _uuid(body.session_id) if body.session_id else None
    try:  # every attachment must exist in the attachments folder before anything starts
        files = [att.load_attachment(att.attachments_dir(st), a.file, a.name) for a in body.attachments or []]
    except att.AttachmentError as e:
        raise HTTPException(e.status, str(e)) from e
    try:
        sess = await st.orchestrator.get_or_create_session(p, channel, sid)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    turn = st.orchestrator.handle(
        p, channel, body.message, session_id=sess.id, images=body.images, attachments=files or None
    )
    if body.wait:
        res = await turn
        return {"session_id": str(res.session_id), "text": res.text, "error": res.error, "tools": res.tools}
    spawn(turn, name="chat")
    return {"session_id": str(sess.id), "accepted": True}


@router.post("/attachments", status_code=201)
async def upload_attachment(
    request: Request,
    name: str = Query(min_length=1, max_length=255),
    st: AppState = StateDep,
    p: Principal = Actor,
) -> dict[str, Any]:
    """One chat attachment as the raw request body (``?name=`` = original file name). Size, type and file
    signature are checked; the file is stored under a new unique name – existing files are never replaced."""
    shown = att.display_name(name)
    too_big = f"„{shown}“ ist zu groß – erlaubt sind höchstens {att.format_size(att.MAX_BYTES)} je Datei."
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > att.MAX_BYTES:
        raise HTTPException(413, too_big)
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > att.MAX_BYTES:
            raise HTTPException(413, too_big)
    try:
        info = await asyncio.to_thread(att.save_attachment, att.attachments_dir(st), name, bytes(data))
    except att.AttachmentError as e:
        raise HTTPException(e.status, str(e)) from e
    await audit(p.label, "chat.attachment", target=info.file, details={"type": info.ext, "size": info.size})
    return info.public()


@router.get("/commands")
async def commands(p: Principal = Reader) -> dict[str, Any]:
    """Slash commands for the chat input – one list for Desktop and Mobile."""
    return command_list()


class ModelBody(BaseModel):
    model: str | None = None  # haiku | sonnet | opus | None (= automatic)
    scope: str = "default"  # default | session
    session_id: str | None = None
    auto_downgrade: bool | None = None
    effort: str | None = None  # low | medium | high | xhigh | max | "auto" (= Claude Code default)


@router.get("/agent/model")
async def agent_model(session_id: str | None = None, st: AppState = StateDep, p: Principal = Reader) -> dict[str, Any]:
    return st.model_router.status(session_id)


@router.post("/agent/model")
async def agent_model_set(body: ModelBody, st: AppState = StateDep, p: Principal = Actor) -> dict[str, Any]:
    if body.scope not in ("default", "session"):
        raise HTTPException(400, "scope must be default or session")
    try:
        if body.auto_downgrade is not None:
            st.model_router.set_auto_downgrade(body.auto_downgrade)
        if body.effort is not None:
            st.model_router.set_effort(None if body.effort == "auto" else body.effort)
        if body.model is not None or (body.auto_downgrade is None and body.effort is None):
            st.model_router.set(body.model, body.scope, body.session_id)  # type: ignore[arg-type]
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    await audit(p.label, "agent.model", details=body.model_dump())
    status: dict[str, Any] = st.model_router.status(body.session_id)
    await bus.publish("agent.model", **status)
    return status


@router.get("/agent/usage")
async def agent_usage(st: AppState = StateDep, p: Principal = Reader) -> dict[str, Any]:
    """Claude subscription quota (5 h / 7 d windows as reported by Claude Code) and local token counters."""
    return st.usage.status()


@router.post("/agent/usage/refresh")
async def agent_usage_refresh(st: AppState = StateDep, p: Principal = Actor) -> dict[str, Any]:
    from jarvis.agent.claude_agent import ClaudeUnavailable

    if not rate_limiter.check("usage-refresh", 6, 600):
        raise HTTPException(429, "Kontingent wurde gerade erst abgefragt")
    try:
        await st.oneshot.probe_limits()
    except ClaudeUnavailable as e:
        raise HTTPException(503, str(e)) from e
    return st.usage.status()


@router.post("/chat/interrupt")
async def chat_interrupt(st: AppState = StateDep, p: Principal = Actor) -> dict[str, bool]:
    """Stopp button: drops the running answer and silences the Mac voice daemon too (it may be reading it out)."""
    interrupted = await st.orchestrator.interrupt()
    await bus.publish("voice.command", action="interrupt", text=None)
    return {"interrupted": interrupted}


@router.get("/sessions")
async def sessions(st: AppState = StateDep, p: Principal = Reader, limit: int = 30) -> list[dict[str, Any]]:
    async with session_scope() as s:
        q = select(ChatSession).order_by(ChatSession.last_activity_at.desc()).limit(min(limit, 100))
        if p.role not in ("owner", "mobile_owner"):
            q = q.where(ChatSession.actor_id == p.id)
        rows = (await s.execute(q)).scalars().all()
    return [
        {
            "id": str(r.id),
            "title": r.title,
            "channel": r.channel,
            "tainted": r.tainted,
            "last_activity_at": r.last_activity_at.isoformat(),
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@router.get("/sessions/{sid}/messages")
async def session_messages(sid: str, st: AppState = StateDep, p: Principal = Reader) -> list[dict[str, Any]]:
    session_id = _uuid(sid)
    if p.role not in ("owner", "mobile_owner"):
        async with session_scope() as s:
            sess = await s.get(ChatSession, session_id)
        if sess is None or sess.actor_id != p.id:
            raise HTTPException(404, "not found")
    return await st.orchestrator.history(session_id, limit=200)


async def _own_session(session_id: uuid.UUID, p: Principal) -> None:
    async with session_scope() as s:
        sess = await s.get(ChatSession, session_id)
    if sess is None or (p.role not in ("owner", "mobile_owner") and sess.actor_id != p.id):
        raise HTTPException(404, "not found")


@router.get("/sessions/{sid}/events")
async def session_events(
    sid: str,
    st: AppState = StateDep,
    p: Principal = Reader,
    turns: int = Query(80, ge=1, le=500),
    before_seq: int | None = None,
) -> dict[str, Any]:
    """Chronological timeline (messages, tool calls, process steps) sorted by ``seq`` – survives reloads."""
    session_id = _uuid(sid)
    await _own_session(session_id, p)
    out = await st.orchestrator.timeline(session_id, turns=turns, before_seq=before_seq)
    if p.role == "mobile_owner":
        out["events"] = [redact_for_mobile(e) for e in out["events"]]
    return out


def _chat_file(st: AppState, kind: str, name: str, *, images_only: bool) -> Path:
    """A file the chat shows: generated images/videos or an attachment – by bare file name only."""
    if kind == "attachments":
        path = att.resolve_attachment(att.attachments_dir(st), name, images_only=images_only)
    else:
        path = resolve_media_file(st, kind, name)
    if path is None:
        raise HTTPException(404, "not found")
    return path


@router.get("/media/{kind}/{name}")
async def media_file(kind: str, name: str, st: AppState = StateDep, p: Principal = Reader) -> FileResponse:
    """A generated image/video or an image attachment shown in the chat. Only a bare file name from
    generated-images/-videos or chat-attachments is accepted; everything else – paths, other folders, other
    types – is 404. Document attachments are not served (they are opened in the Finder)."""
    path = _chat_file(st, kind, name, images_only=True)
    ext = path.suffix.lower()
    known = att.TYPES.get(ext[1:])
    return FileResponse(
        path,
        media_type=MEDIA_TYPES.get(ext) or (known.mime if known else "application/octet-stream"),
        headers={
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.post("/media/{kind}/{name}/reveal")
async def media_reveal(kind: str, name: str, st: AppState = StateDep, p: Principal = Owner) -> dict[str, bool]:
    """Shows a generated file or an attachment in the Finder (``open -R``) – same name-only rule as above."""
    path = _chat_file(st, kind, name, images_only=False)
    if sys.platform != "darwin":
        return await _reveal_elsewhere(path)
    exe = shutil.which("open")
    if exe is None:
        raise HTTPException(501, "„Im Finder zeigen“ gibt es nur auf dem Mac.")
    proc = await asyncio.create_subprocess_exec(
        exe, "-R", str(path), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        code = await asyncio.wait_for(proc.wait(), 10)
    except TimeoutError:
        proc.kill()
        code = -1
    if code != 0:
        raise HTTPException(500, "Der Finder konnte die Datei nicht zeigen.")
    return {"ok": True}


async def _reveal_elsewhere(path: Path) -> dict[str, bool]:
    """Windows (Explorer ``/select``) and Linux (FileManager1 over D-Bus, else the folder via xdg-open)."""
    adapter = get_adapter()
    report = adapter.capabilities()
    argv = adapter.reveal_command(path)
    if argv is None or not report.usable("desktop.reveal"):
        why = report.reason("desktop.reveal") or "kein Dateimanager gefunden"
        raise HTTPException(501, f"„Im {adapter.file_manager} zeigen“ ist auf diesem System nicht verfügbar: {why}")
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        code = await asyncio.wait_for(proc.wait(), 10)
    except TimeoutError:
        proc.kill()
        code = -1
    # explorer.exe answers 1 even when it worked – only a timeout counts as failure there
    if code != 0 and not (adapter.id == "windows" and code == 1):
        raise HTTPException(500, f"Der {adapter.file_manager} konnte die Datei nicht zeigen.")
    return {"ok": True}


class CheckBody(BaseModel):
    index: int = Field(ge=0, le=10000)
    checked: bool


@router.post("/sessions/{sid}/events/{event_id}/check")
async def session_event_check(
    sid: str, event_id: str, body: CheckBody, st: AppState = StateDep, p: Principal = Actor
) -> dict[str, Any]:
    """Toggle a task-list item of a message; the state is stored in the message and synced via ``chat.check``."""
    session_id = _uuid(sid)
    await _own_session(session_id, p)
    if not await st.orchestrator.set_check(session_id, event_id[:40], body.index, body.checked):
        raise HTTPException(404, "not found")
    return {"ok": True, "index": body.index, "checked": body.checked}


# ------------------------------------------------------------------ approvals
@router.get("/approvals")
async def approvals(status: str = "pending", st: AppState = StateDep, p: Principal = Reader) -> list[dict[str, Any]]:
    async with session_scope() as s:
        q = select(Approval).order_by(Approval.created_at.desc()).limit(100)
        if status != "all":
            q = q.where(Approval.status == status)
        rows = (await s.execute(q)).scalars().all()
    return [
        {
            "id": str(a.id),
            "tool": a.tool,
            "summary": a.summary,
            "risk": a.risk,
            "reason": a.reason,
            "status": a.status,
            "args": a.args,
            "requested_by": a.requested_by,
            "channel": a.channel,
            "created_at": a.created_at.isoformat(),
            "expires_at": a.expires_at.isoformat(),
            "decided_by": a.decided_by,
            "result": a.result,
        }
        for a in rows
    ]


class DecisionBody(BaseModel):
    approve: bool
    note: str | None = None


class ApprovalModeBody(BaseModel):
    mode: Literal["yolo", "block_dangerous", "block_everything", "custom"] | None = None
    custom_levels: dict[str, Literal["auto", "ask"]] | None = None
    tool_overrides: dict[str, Literal["auto", "ask", "deny"]] | None = None
    taint_strict: bool | None = None


@router.get("/approvals/mode")
async def approval_mode(st: AppState = StateDep, p: Principal = Reader) -> dict[str, Any]:
    return st.approval_policy.to_dict()


@router.put("/approvals/mode")
async def set_approval_mode(body: ApprovalModeBody, st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    before = st.approval_policy.mode
    try:
        result = st.approval_policy.update(body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    await audit(p.label, "approvals.mode", details={"from": before, **body.model_dump(exclude_none=True)})
    await bus.publish("approval.mode", **result)
    return result


@router.post("/approvals/{aid}")
async def decide(
    aid: str, body: DecisionBody, st: AppState = StateDep, p: Principal = Depends(current_principal)
) -> dict[str, Any]:
    try:
        return await st.executor.decide(_uuid(aid), body.approve, p, body.note)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except LookupError as e:
        raise HTTPException(404, str(e)) from e


# ------------------------------------------------------------------ tasks
def _task_dict(t: Task) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "title": t.title,
        "kind": t.kind,
        "status": t.status,
        "params": t.params,
        "error": t.error,
        "question": t.pending_question,
        "result": t.result,
        "attempts": t.attempts,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
        "steps": [
            {
                "idx": s.idx,
                "name": s.name,
                "status": s.status,
                "error": s.error,
                "output": s.output,
                "approval_id": str(s.approval_id) if s.approval_id else None,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "finished_at": s.finished_at.isoformat() if s.finished_at else None,
            }
            for s in t.steps
        ],
    }


@router.get("/tasks")
async def tasks(status: str | None = None, st: AppState = StateDep, p: Principal = Reader) -> list[dict[str, Any]]:
    return [_task_dict(t) for t in await st.tasks.list_tasks(status, limit=100)]


@router.get("/tasks/{tid}")
async def task(tid: str, st: AppState = StateDep, p: Principal = Reader) -> dict[str, Any]:
    t = await st.tasks.get_task(_uuid(tid))
    if t is None:
        raise HTTPException(404, "not found")
    return _task_dict(t)


class AgentMessage(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    immediate: bool = False  # stop the current agent run now and continue the session with the message


class TaskCreate(BaseModel):
    playbook: str
    params: dict[str, Any]


@router.post("/tasks")
async def task_create(body: TaskCreate, st: AppState = StateDep, p: Principal = Actor) -> dict[str, Any]:
    try:
        t = await st.tasks.create(body.playbook, body.params, p)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _task_dict(t)


class TaskInput(BaseModel):
    values: dict[str, Any]


@router.post("/tasks/{tid}/input")
async def task_input(tid: str, body: TaskInput, st: AppState = StateDep, p: Principal = Actor) -> dict[str, Any]:
    try:
        t = await st.tasks.provide_input(_uuid(tid), body.values, p.label)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return _task_dict(t) if t else {}


@router.post("/tasks/{tid}/cancel")
async def task_cancel(tid: str, st: AppState = StateDep, p: Principal = Actor) -> dict[str, bool]:
    return {"cancelled": await st.tasks.cancel(_uuid(tid), p.label)}


@router.post("/tasks/{tid}/pause")
async def task_pause(tid: str, st: AppState = StateDep, p: Principal = Actor) -> dict[str, bool]:
    return {"paused": await st.tasks.pause(_uuid(tid), p.label)}


@router.post("/tasks/{tid}/resume")
async def task_resume(tid: str, st: AppState = StateDep, p: Principal = Actor) -> dict[str, bool]:
    return {"resumed": await st.tasks.resume(_uuid(tid), p.label)}


@router.post("/tasks/{tid}/message")
async def task_message(
    tid: str, body: AgentMessage, st: AppState = StateDep, p: Principal = Actor
) -> dict[str, Any]:
    """Message to the agent working for this task: forwarded to its running coding job."""
    async with session_scope() as s:
        job_id = (
            await s.execute(
                select(CodingJob.id)
                .where(CodingJob.task_id == _uuid(tid), CodingJob.status.in_(["queued", "running", "paused"]))
                .order_by(CodingJob.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if job_id is None:
        raise HTTPException(
            409, "Diese Aufgabe hat gerade keinen laufenden Agenten – Rückfragen beantwortest du über /input"
        )
    try:
        return {"job_id": str(job_id), **await st.coding.send_message(job_id, body.text, p.label, body.immediate)}
    except CodingError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/tasks/{tid}/retry")
async def task_retry(tid: str, st: AppState = StateDep, p: Principal = Actor) -> dict[str, bool]:
    return {"retried": await st.tasks.retry(_uuid(tid), p.label)}


@router.get("/playbooks")
async def playbooks(st: AppState = StateDep, p: Principal = Reader) -> list[dict[str, Any]]:
    return [
        {
            "name": pb.name,
            "title": pb.title,
            "description": pb.description,
            "params_schema": pb.params_schema,
            "steps": [s.name for s in pb.steps],
        }
        for pb in st.tasks.playbooks.values()
    ]


# ------------------------------------------------------------------ tools / skills / connectors
@router.get("/tools")
async def tools(st: AppState = StateDep, p: Principal = Reader) -> list[dict[str, Any]]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "connector": t.connector,
            "risk": st.registry.effective_risk(t),
            "enabled": st.registry.is_enabled(t),
            "writes": t.writes,
            "untrusted_output": t.untrusted_output,
            "parameters": t.parameters,
            "unavailable_reason": st.registry.unavailable_reason(t),
        }
        for t in st.registry.all(include_disabled=True)
    ]


@router.get("/skills")
async def skills(st: AppState = StateDep, p: Principal = Reader) -> dict[str, Any]:
    hermes_skills: list[dict[str, Any]] = []
    error = None
    if st.hermes is not None:
        try:
            hermes_skills = await st.hermes.skills()
        except Exception as e:
            error = str(e)[:200]
    if not hermes_skills:
        # Fallback: read SKILL.md frontmatter from the profile directly — Hermes' /v1/skills
        # currently fails upstream (TypeError in _find_all_skills, Hermes v0.22.1).
        hermes_skills = _read_skill_dirs(st.settings.hermes.profile)
    return {
        "claude": st.skills.items(),
        "hermes": hermes_skills,
        "hermes_error": error,
        "playbooks": [
            {"name": pb.name, "title": pb.title, "description": pb.description} for pb in st.tasks.playbooks.values()
        ],
    }


class SkillToggle(BaseModel):
    id: str
    enabled: bool


@router.post("/skills/claude")
async def skills_toggle(body: SkillToggle, st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    try:
        st.skills.set_enabled(body.id, body.enabled)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    if st.agent is not None:
        await st.agent.reload()  # running conversations pick the change up via --resume
    await audit(p.label, "skills.toggle", target=body.id, details={"enabled": body.enabled})
    return {"id": body.id, "enabled": body.enabled, "active": len(st.skills.enabled_names())}


def _read_skill_dirs(profile: str) -> list[dict[str, Any]]:
    import yaml

    from jarvis.agent.hermes_setup import profile_dir

    out: list[dict[str, Any]] = []
    for md in sorted(profile_dir(profile).joinpath("skills").glob("**/SKILL.md")):
        text = md.read_text(encoding="utf-8", errors="replace")
        meta: dict[str, Any] = {}
        if text.startswith("---"):
            try:
                loaded = yaml.safe_load(text.split("---", 2)[1]) or {}
                meta = loaded if isinstance(loaded, dict) else {}
            except yaml.YAMLError:
                meta = {}
        out.append(
            {
                "name": str(meta.get("name") or md.parent.name),
                "description": str(meta.get("description") or "")[:300],
                "category": md.parent.parent.name if md.parent.parent.name != "skills" else "",
            }
        )
    return out


@router.get("/connectors")
async def connectors(st: AppState = StateDep, p: Principal = Reader) -> dict[str, Any]:
    return {"connectors": st.connectors.describe(), "mcp_servers": st.mcp_hub.status()}


class ConnectorConfigBody(BaseModel):
    enabled: bool | None = None
    settings: dict[str, Any] | None = None
    secrets: dict[str, str] | None = None


@router.post("/connectors/{cid}")
async def connector_configure(
    cid: str, body: ConnectorConfigBody, st: AppState = StateDep, p: Principal = Owner
) -> dict[str, Any]:
    if cid not in st.connectors.connectors:
        raise HTTPException(404, "unknown connector")
    try:
        await st.connectors.configure(
            cid, actor=p.label, enabled=body.enabled, settings=body.settings, secrets=body.secrets
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return next(c for c in st.connectors.describe() if c["id"] == cid)


@router.post("/connectors/{cid}/test")
async def connector_test(cid: str, st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    if cid not in st.connectors.connectors:
        raise HTTPException(404, "unknown connector")
    return (await st.connectors.check(cid)).to_dict()


@router.post("/connectors/telegram/pair")
async def telegram_pair(st: AppState = StateDep, p: Principal = Owner) -> dict[str, str]:
    tg = st.connectors.connectors["telegram"]
    try:
        code = await tg.begin_pairing()  # type: ignore[attr-defined]
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return {"code": code, "instructions": f"Sende deinem Bot in Telegram: /pair {code} (gültig 10 Minuten)"}


@router.post("/connectors/google/oauth/start")
async def google_oauth_start(st: AppState = StateDep, p: Principal = Owner) -> dict[str, str]:
    g = st.connectors.connectors["google"]
    try:
        return {"auth_url": g.begin_oauth()}  # type: ignore[attr-defined]
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@router.get("/connectors/google/oauth/callback", response_class=HTMLResponse)
async def google_oauth_callback(
    state: str, code: str | None = None, error: str | None = None, st: AppState = StateDep
) -> str:
    # Unauthenticated by necessity (browser redirect); protected by the one-time PKCE state.
    if error or not code:
        return f"<h3>Google-Verbindung abgebrochen: {html.escape(error or 'kein Code')}</h3>"
    g = st.connectors.connectors["google"]
    try:
        await g.finish_oauth(state, code)  # type: ignore[attr-defined]
    except Exception as e:
        return f"<h3>Fehler: {html.escape(str(e)[:200])}</h3>"
    await st.connectors.configure("google", actor="oauth", enabled=True)
    return "<h3>Google ist verbunden. Du kannst dieses Fenster schließen.</h3>"


# ------------------------------------------------------------------ memory
@router.get("/memory/search")
async def memory_search(q: str, limit: int = 8, st: AppState = StateDep, p: Principal = Reader) -> list[dict[str, Any]]:
    return await st.memory.search(q, limit=min(limit, 30), include_sensitive=p.role == "owner")


@router.get("/memory/items")
async def memory_items(
    kind: str | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
    st: AppState = StateDep,
    p: Principal = Reader,
) -> dict[str, Any]:
    return await st.memory.list_items(kind, status, min(limit, 500), offset, q, include_sensitive=p.role == "owner")


@router.get("/memory/items/{mid}")
async def memory_item(mid: str, st: AppState = StateDep, p: Principal = Reader) -> dict[str, Any]:
    item = await st.memory.get(_uuid(mid))
    if item is None:
        raise HTTPException(404, "not found")
    if item["sensitivity"] == "sensitive" and p.role != "owner":
        raise HTTPException(403, "sensitive")
    return item


class MemoryCreate(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    content: str = Field(min_length=2)
    kind: str = "note"
    entities: list[str] = []
    sensitive: bool | None = None


@router.post("/memory/items")
async def memory_create(body: MemoryCreate, st: AppState = StateDep, p: Principal = Actor) -> dict[str, Any]:
    return await st.memory.remember(
        body.title,
        body.content,
        body.kind,
        entities=body.entities,
        sensitive=body.sensitive,
        source={"type": "manual", "actor": p.label},
        actor=p.label,
        confirmed=p.role == "owner",
    )


class MemoryCorrect(BaseModel):
    content: str
    title: str | None = None


@router.post("/memory/items/{mid}/correct")
async def memory_correct(
    mid: str, body: MemoryCorrect, st: AppState = StateDep, p: Principal = Owner
) -> dict[str, Any]:
    try:
        return await st.memory.correct(_uuid(mid), body.content, p.label, body.title)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e


class MemoryEdit(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, max_length=50000)
    kind: str | None = None


@router.post("/memory/items/{mid}/tidy")
async def memory_tidy(mid: str, st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    try:
        return await st.memory.tidy(_uuid(mid))
    except LookupError as e:
        raise HTTPException(404, str(e)) from e


@router.put("/memory/items/{mid}")
async def memory_edit(mid: str, body: MemoryEdit, st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    try:
        return await st.memory.edit(_uuid(mid), p.label, title=body.title, content=body.content, kind=body.kind)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e


@router.delete("/memory/items/{mid}")
async def memory_delete(mid: str, st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    try:
        return await st.memory.forget(_uuid(mid), p.label)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/memory/pending/{mid}")
async def memory_pending(mid: str, body: DecisionBody, st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    try:
        return await st.memory.confirm_pending(_uuid(mid), body.approve, p.label)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/memory/graph")
async def memory_graph(st: AppState = StateDep, p: Principal = Reader) -> dict[str, Any]:
    return await st.memory.graph(include_sensitive=p.role == "owner")


@router.get("/memory/duplicates")
async def memory_duplicates(st: AppState = StateDep, p: Principal = Owner) -> list[list[dict[str, Any]]]:
    return await st.memory.find_duplicates()


@router.post("/memory/consolidate")
async def memory_consolidate(st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    """Merge notes that describe the same entity; merged notes stay in the vault as superseded."""
    merged = await st.memory.consolidate_duplicates(actor=p.label)
    return {"merged": merged}


# ------------------------------------------------------------------ live knowledge graph (3D interface)
@router.get("/graph")
async def graph(limit: int = 1500, st: AppState = StateDep, p: Principal = Reader) -> dict[str, Any]:
    data = await st.graph.build(min(max(limit, 50), 5000))
    if p.role != "owner":  # non-owner devices never see sensitive titles
        hidden = {n["id"] for n in data["nodes"] if n["meta"].get("sensitivity") == "sensitive"}
        data = {
            **data,
            "nodes": [n for n in data["nodes"] if n["id"] not in hidden],
            "edges": [e for e in data["edges"] if e["source"] not in hidden and e["target"] not in hidden],
        }
    return data


@router.get("/graph/cluster/{kind}")
async def graph_cluster(
    kind: str, offset: int = 0, limit: int = 300, st: AppState = StateDep, p: Principal = Reader
) -> dict[str, Any]:
    return await st.graph.cluster(kind, max(0, offset), min(limit, 1000), include_sensitive=p.role == "owner")


@router.get("/memory/export")
async def memory_export(st: AppState = StateDep, p: Principal = Owner) -> Response:
    data = await st.memory.export_zip()
    await audit(p.label, "memory.export", details={"bytes": len(data)})
    name = f"jarvis-memory-{datetime.now(UTC).strftime('%Y%m%d-%H%M')}.zip"
    return Response(
        data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


@router.post("/memory/reindex")
async def memory_reindex(st: AppState = StateDep, p: Principal = Owner) -> dict[str, int]:
    return await st.memory.reindex_all()


# ------------------------------------------------------------------ backups
@router.get("/backup")
async def backup_status(st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    return st.backup.status()


@router.post("/backup/run")
async def backup_run(st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    await audit(p.label, "backup.run")
    try:
        return await st.backup.run("manual")
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e


# ------------------------------------------------------------------ scheduler
class ScheduleBody(BaseModel):
    name: str
    cron: str
    action: dict[str, Any]


@router.get("/schedules")
async def schedules(st: AppState = StateDep, p: Principal = Reader) -> list[dict[str, Any]]:
    return [
        {
            "id": str(s.id),
            "name": s.name,
            "cron": s.cron,
            "timezone": s.timezone,
            "action": s.action,
            "enabled": s.enabled,
            "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
            "next_run_at": s.next_run_at.isoformat() if s.next_run_at else None,
            "last_status": s.last_status,
        }
        for s in await st.scheduler.list()
    ]


@router.post("/schedules")
async def schedule_create(body: ScheduleBody, st: AppState = StateDep, p: Principal = Owner) -> dict[str, str]:
    try:
        s = await st.scheduler.create(body.name, body.cron, body.action, p.label)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"id": str(s.id)}


@router.post("/schedules/{sid}/enabled")
async def schedule_enable(
    sid: str, body: DecisionBody, st: AppState = StateDep, p: Principal = Owner
) -> dict[str, bool]:
    return {"ok": await st.scheduler.set_enabled(_uuid(sid), body.approve, p.label)}


@router.post("/schedules/{sid}/run")
async def schedule_run(sid: str, st: AppState = StateDep, p: Principal = Owner) -> dict[str, bool]:
    spawn(st.scheduler.run_now(_uuid(sid)), name="schedule-run")
    return {"started": True}


@router.delete("/schedules/{sid}")
async def schedule_delete(sid: str, st: AppState = StateDep, p: Principal = Owner) -> dict[str, bool]:
    return {"deleted": await st.scheduler.delete(_uuid(sid), p.label)}


# ------------------------------------------------------------------ notifications
@router.get("/notifications")
async def notifications(unread: bool = False, st: AppState = StateDep, p: Principal = Reader) -> list[dict[str, Any]]:
    return [
        {
            "id": str(n.id),
            "event": n.event,
            "level": n.level,
            "title": n.title,
            "body": n.body,
            "read": n.read,
            "delivered": n.delivered,
            "created_at": n.created_at.isoformat(),
            "data": n.data,
        }
        for n in await st.notifier.recent(unread)
    ]


class ReadBody(BaseModel):
    ids: list[str] | None = None


@router.post("/notifications/read")
async def notifications_read(body: ReadBody, st: AppState = StateDep, p: Principal = Reader) -> dict[str, bool]:
    await st.notifier.mark_read(body.ids)
    return {"ok": True}


# ------------------------------------------------------------------ logs / audit
@router.get("/logs")
async def logs(limit: int = 300, level: str | None = None, p: Principal = Reader) -> list[dict[str, Any]]:
    return ring.recent(min(limit, 2000), level)


@router.get("/audit")
async def audit_log(
    limit: int = 200, offset: int = 0, action: str | None = None, p: Principal = Owner
) -> dict[str, Any]:
    async with session_scope() as s:
        q = select(AuditEntry).order_by(AuditEntry.id.desc()).limit(min(limit, 1000)).offset(offset)
        if action:
            q = q.where(AuditEntry.action.ilike(f"%{action}%"))
        rows = (await s.execute(q)).scalars().all()
        total = (await s.execute(select(func.count()).select_from(AuditEntry))).scalar_one()
    return {
        "total": total,
        "entries": [
            {
                "id": r.id,
                "ts": r.ts.isoformat(),
                "actor": r.actor,
                "action": r.action,
                "target": r.target,
                "outcome": r.outcome,
                "details": r.details,
            }
            for r in rows
        ],
    }


@router.get("/audit/verify")
async def audit_verify(p: Principal = Owner) -> dict[str, Any]:
    return await verify_chain()


# ------------------------------------------------------------------ permissions / devices / settings
@router.get("/permissions")
async def permissions(st: AppState = StateDep, p: Principal = Reader) -> dict[str, Any]:
    macos: dict[str, Any] = {}
    try:
        h = await st.bridge.health()
        macos = {"bridge": "ok", **h.get("permissions", {}), "kill_switch": h.get("killSwitch")}
    except Exception:
        macos = {"bridge": "offline"}
    macos["microphone"] = st.voice_state.get("mic_permission")
    perms = st.permissions
    return {
        "roles": {k: v.model_dump() for k, v in perms.roles.items()},
        "taint_requires_approval": perms.taint_requires_approval,
        "shell_allowlist": perms.shell_allowlist,
        "rate_limits": perms.rate_limits.model_dump(),
        "tools": [
            {
                "name": t.name,
                "connector": t.connector,
                "risk": st.registry.effective_risk(t),
                "enabled": st.registry.is_enabled(t),
                "auto_for_owner": RISK_ORDER[st.registry.effective_risk(t)]
                <= RISK_ORDER[perms.roles["owner"].auto_approve],
                "unavailable_reason": st.registry.unavailable_reason(t),
            }
            for t in st.registry.all(include_disabled=True)
        ],
        "macos": macos,
        "platform": get_adapter().id,
    }


@router.post("/permissions/macos/{kind}")
async def macos_permission(kind: str, st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    if kind not in ("accessibility", "screenRecording"):
        raise HTTPException(400, "unknown permission")
    try:
        return dict(await st.bridge.request("POST", "/permissions/request", {"kind": kind}))
    except Exception as e:
        raise HTTPException(503, str(e)) from e


@router.get("/devices")
async def devices(p: Principal = Owner) -> list[dict[str, Any]]:
    return [
        {
            "id": str(d.id),
            "name": d.name,
            "role": d.role,
            "revoked": d.revoked,
            "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
            "created_at": d.created_at.isoformat(),
        }
        for d in await list_devices()
    ]


class DeviceBody(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    role: str = "operator"


@router.post("/devices")
async def device_create(body: DeviceBody, st: AppState = StateDep, p: Principal = Owner) -> dict[str, str]:
    if body.role not in st.permissions.roles or body.role == "owner":
        raise HTTPException(400, "invalid role (owner devices are created with jarvisctl)")
    device, token = await create_device(body.name, body.role)
    await audit(p.label, "device.create", target=str(device.id), details={"role": body.role})
    return {"id": str(device.id), "token": token}


@router.delete("/devices/{did}")
async def device_revoke(did: str, p: Principal = Owner) -> dict[str, bool]:
    ok = await revoke_device(_uuid(did))
    await audit(p.label, "device.revoke", target=did)
    return {"revoked": ok}


@router.get("/settings")
async def settings(st: AppState = StateDep, p: Principal = Reader) -> dict[str, Any]:
    s = st.settings
    return {
        "personality": st.personality.model_dump(),
        "personas": st.personas.to_dict(),
        "voice": s.voice.model_dump(),
        "llm": s.llm.model_dump(),
        "notifications": s.notifications.model_dump(),
        "session": s.session.model_dump(),
        "paths": {k: str(v) for k, v in s.paths.model_dump().items()},
        "telephony": s.telephony.model_dump(),
        "coding": s.coding.model_dump(),
        "server": {"host": s.server.host, "port": s.server.port, "public_base_url": s.server.public_base_url},
    }


# ------------------------------------------------------------------ personality
class PersonaBody(BaseModel):
    id: str = Field(min_length=2, max_length=32)


class PersonaPreviewBody(BaseModel):
    id: str | None = Field(default=None, max_length=32)  # None = the active persona
    text: str | None = Field(default=None, max_length=400)  # None = the persona's own Hörprobe


async def _persona_status(st: AppState) -> dict[str, Any]:
    """The personas for the settings, each with ``voice_ready``: its voice is loaded by the local voice server
    (otherwise it speaks with the system voice until that server is restarted)."""
    out = st.personas.to_dict()
    for item in out["personas"]:
        item["voice_ready"] = await st.tts.local_for(item["voice"]).available()
    return out


@router.get("/persona")
async def persona(st: AppState = StateDep, p: Principal = Reader) -> dict[str, Any]:
    return await _persona_status(st)


@router.post("/persona")
async def persona_set(body: PersonaBody, st: AppState = StateDep, p: Principal = Actor) -> dict[str, Any]:
    """Switch the personality (prompt + voice) without a restart; desktop and phone follow ``persona.changed``."""
    before = st.personas.active_id
    try:
        await st.set_persona(body.id)
    except KeyError as e:
        raise HTTPException(404, f"Persönlichkeit {body.id!r} gibt es nicht") from e
    await audit(p.label, "persona.set", target=body.id, details={"from": before})
    return await _persona_status(st)


@router.post("/persona/preview")
async def persona_preview(body: PersonaPreviewBody, st: AppState = StateDep, p: Principal = Reader) -> Response:
    """Hörprobe: the persona's sample sentence in its voice, as WAV (24 kHz mono) – the same local voice the
    replies use, without switching anything."""
    persona = st.personas.get(body.id) if body.id else st.personality
    if persona is None:
        raise HTTPException(404, f"Persönlichkeit {body.id!r} gibt es nicht")
    if not rate_limiter.check(f"persona-preview:{p.label}", 20, 60):
        raise HTTPException(429, "zu viele Hörproben – bitte kurz warten")
    text = speakable(body.text or persona.sample or time_of_day_greeting(persona))
    rate = 24000
    pcm = await st.tts.synthesize(text, rate, persona.voice)
    if not pcm:
        raise HTTPException(503, "Auf diesem Rechner ist keine Stimme verfügbar")
    return Response(pcm_to_wav(pcm, rate), media_type="audio/wav", headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------ voice
class SpeakBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    sample_rate: int = 24000


@router.post("/voice/tts")
async def voice_tts(body: SpeakBody, st: AppState = StateDep, p: Principal = Reader) -> StreamingResponse:
    """Streams raw 16-bit mono PCM (used by the voice daemon for low-latency playback)."""
    text = speakable(body.text)
    return StreamingResponse(
        st.tts.stream(text, body.sample_rate), media_type="audio/L16", headers={"X-Sample-Rate": str(body.sample_rate)}
    )


@router.post("/voice/stt")
async def voice_stt(request: Request, st: AppState = StateDep, p: Principal = Reader) -> dict[str, str]:
    """Transcript of a recording. ``command: "stop"`` when it is nothing but a stop command ("Stopp", "Jarvis, stopp"):
    the client then silences Jarvis and sends nothing to the agent (one definition for Mac, phone and overlay)."""
    limit = 20_000_000
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, "too large")
    buf = bytearray()
    async for chunk in request.stream():  # enforce the limit while reading, not after buffering everything
        buf += chunk
        if len(buf) > limit:
            raise HTTPException(413, "too large")
    wav = bytes(buf)
    text = await st.stt.transcribe_wav(wav)
    return {"text": text, "command": "stop"} if is_stop_command(text) else {"text": text}


class VoiceCommand(BaseModel):
    action: str  # ptt_start | ptt_stop | mute | unmute | interrupt | say
    text: str | None = None


@router.post("/voice/command")
async def voice_command(body: VoiceCommand, p: Principal = Actor) -> dict[str, bool]:
    if body.action not in ("ptt_start", "ptt_stop", "mute", "unmute", "interrupt", "say"):
        raise HTTPException(400, "unknown action")
    await bus.publish("voice.command", action=body.action, text=body.text)
    if body.action == "say" and body.text:
        await bus.publish("voice.say", text=body.text)
    return {"ok": True}


class WakeupBody(BaseModel):
    enabled: bool | None = None
    sound: str | None = Field(default=None, max_length=1024)  # "" removes the MP3
    volume: float | None = Field(default=None, ge=0.0, le=1.0)
    apps: list[str] | None = Field(default=None, max_length=wakeup.MAX_APPS)


class WakeupRunBody(BaseModel):
    play_sound: bool = False  # desktop "Testen"; the voice daemon plays the sound itself


def _require_mac() -> None:
    if not wakeup.supported():
        raise HTTPException(409, "Die Weckroutine gibt es nur auf macOS.")


@router.get("/voice/wakeup")
async def voice_wakeup_settings(st: AppState = StateDep, p: Principal = Reader) -> dict[str, Any]:
    """Wake-up routine settings plus ``sound_error`` (MP3 missing/invalid), ``supported`` (macOS) and ``say``, the
    line the voice daemon speaks after the sound (the active persona's own, else ``voice.wakeup.say``)."""
    own = st.personality.wakeup_say
    return {**st.wakeup.to_dict(), "say": own if own is not None else st.settings.voice.wakeup.say}


@router.put("/voice/wakeup")
async def set_voice_wakeup(body: WakeupBody, st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    _require_mac()
    patch = body.model_dump(exclude_none=True)
    try:
        st.wakeup.update(patch)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    await audit(p.label, "voice.wakeup.settings", details=patch)
    return st.wakeup.to_dict()


@router.post("/voice/wakeup")
async def voice_wakeup(
    body: WakeupRunBody | None = None, st: AppState = StateDep, p: Principal = Actor
) -> dict[str, Any]:
    """ "Hey Jarvis, wake up": open the configured programs one after the other (``open -a``).

    Programs that cannot be found are skipped and reported, the others still start. The MP3 is checked (and
    played for the desktop test); the result goes to the desktop as ``voice.wakeup``."""
    _require_mac()
    s = st.wakeup.settings
    sound_error = wakeup.check_sound(s.sound) if s.sound else None
    if body is not None and body.play_sound and s.sound and sound_error is None:
        sound_error = await wakeup.play_sound(s.sound, s.volume, max_wait_s=0.6)
    if st.kill_switch.engaged:
        opened: list[str] = []
        failed = [{"app": a, "reason": "Not-Aus aktiv – nicht gestartet"} for a in s.apps]
    else:
        opened, failed = await wakeup.open_apps(s.apps, delay_s=st.settings.voice.wakeup.app_delay_s)
    result: dict[str, Any] = {
        "enabled": s.enabled,
        "sound": s.sound,
        "sound_error": sound_error,
        "opened": opened,
        "failed": failed,
    }
    await audit(p.label, "voice.wakeup", details={"opened": opened, "failed": failed, "sound_error": sound_error})
    await bus.publish("voice.wakeup", **result)
    return result


class VoiceStatus(BaseModel):
    state: str
    muted: bool = False
    wake_word: bool = True
    mic_permission: str | None = None
    level: float | None = None
    detail: str | None = None


@router.post("/voice/status")
async def voice_status(body: VoiceStatus, st: AppState = StateDep, p: Principal = Reader) -> dict[str, bool]:
    st.voice_state.update({**body.model_dump(), "connected": True, "updated_at": datetime.now(UTC).isoformat()})
    await bus.publish("voice.status", **body.model_dump())
    return {"ok": True}


# ------------------------------------------------------------------ coding jobs
def _job_dict(j: CodingJob) -> dict[str, Any]:
    return {
        "id": str(j.id),
        "title": j.title,
        "status": j.status,
        "repo_path": j.repo_path,
        "branch": j.branch,
        "worktree_path": j.worktree_path,
        "result": j.result,
        "test_command": j.test_command,
        "model": j.model,
        "effort": j.effort,
        "created_at": j.created_at.isoformat(),
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
    }


@router.get("/coding/jobs")
async def coding_jobs(p: Principal = Reader) -> list[dict[str, Any]]:
    async with session_scope() as s:
        rows = (await s.execute(select(CodingJob).order_by(CodingJob.created_at.desc()).limit(50))).scalars().all()
    return [_job_dict(j) for j in rows]


@router.get("/coding/jobs/{jid}/log")
async def coding_log(
    jid: str, lines: int = 300, st: AppState = StateDep, p: Principal = Reader
) -> list[dict[str, Any]]:
    return await st.coding.log_tail(_uuid(jid), min(lines, 2000))


@router.post("/coding/jobs/{jid}/cancel")
async def coding_cancel(jid: str, st: AppState = StateDep, p: Principal = Actor) -> dict[str, bool]:
    return {"cancelled": await st.coding.cancel(_uuid(jid), p.label)}


@router.get("/coding/jobs/{jid}/monitor")
async def coding_monitor(jid: str, st: AppState = StateDep, p: Principal = Reader) -> dict[str, Any]:
    try:
        return await st.coding.monitor(_uuid(jid))
    except CodingError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/coding/jobs/{jid}/pause")
async def coding_pause(jid: str, st: AppState = StateDep, p: Principal = Actor) -> dict[str, bool]:
    try:
        return {"paused": await st.coding.pause(_uuid(jid), p.label)}
    except CodingError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/coding/jobs/{jid}/resume")
async def coding_resume(jid: str, st: AppState = StateDep, p: Principal = Actor) -> dict[str, bool]:
    try:
        return {"resumed": await st.coding.resume(_uuid(jid), p.label)}
    except CodingError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/coding/jobs/{jid}/message")
async def coding_message(
    jid: str, body: AgentMessage, st: AppState = StateDep, p: Principal = Actor
) -> dict[str, Any]:
    try:
        return await st.coding.send_message(_uuid(jid), body.text, p.label, body.immediate)
    except CodingError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/coding/jobs/{jid}/retry")
async def coding_retry(jid: str, st: AppState = StateDep, p: Principal = Owner) -> dict[str, bool]:
    try:
        await st.coding.retry(_uuid(jid))
    except Exception as e:
        raise HTTPException(409, str(e)) from e
    return {"retried": True}


# ------------------------------------------------------------------ telephony
@router.get("/telephony/status")
async def telephony_status(st: AppState = StateDep, p: Principal = Reader) -> dict[str, Any]:
    return st.telephony.status() if st.telephony else {"enabled": False}


@router.get("/telephony/calls")
async def telephony_calls(p: Principal = Reader) -> list[dict[str, Any]]:
    async with session_scope() as s:
        rows = (await s.execute(select(Call).order_by(Call.created_at.desc()).limit(50))).scalars().all()
    return [
        {
            "id": str(c.id),
            "provider": c.provider,
            "direction": c.direction,
            "from": c.from_number,
            "to": c.to_number,
            "status": c.status,
            "authenticated": c.authenticated,
            "purpose": c.purpose,
            "transcript": c.transcript,
            "created_at": c.created_at.isoformat(),
            "ended_at": c.ended_at.isoformat() if c.ended_at else None,
        }
        for c in rows
    ]


@router.post("/telephony/call-me")
async def telephony_call_me(st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    """Real outbound test call from the Twilio number to Sir's own number (button in the app)."""
    if st.telephony is None:
        raise HTTPException(400, "Telefonie ist deaktiviert")
    try:
        result = await st.telephony.call_owner(
            "Guten Tag, Sir. Dies ist ein Testanruf von Jarvis. Die Telefonie funktioniert.", reason="test"
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    await audit(p.label, "telephony.call_me", details={"to": st.settings.telephony.owner_numbers[:1]})
    return result


@router.post("/telephony/test-call")
async def telephony_test_call(st: AppState = StateDep, p: Principal = Owner) -> dict[str, str]:
    """LOCAL TEST MODE: returns credentials for a simulated call over the media-stream protocol."""
    if st.telephony is None:
        raise HTTPException(400, "telephony disabled")
    call_id, token = await st.telephony.start_local_test_call()
    return {
        "call_id": str(call_id),
        "stream_token": token,
        "ws_url": f"ws://127.0.0.1:{st.settings.server.port}/api/telephony/stream",
        "notice": "LOKALER TESTMODUS – kein echtes Telefonat",
    }


@router.get("/stats/overview")
async def stats_overview(p: Principal = Reader, days: int = Query(7, ge=1, le=90)) -> dict[str, Any]:
    async with session_scope() as s:
        tasks_by_status = dict((await s.execute(select(Task.status, func.count()).group_by(Task.status))).all())
        approvals_by_status = dict(
            (await s.execute(select(Approval.status, func.count()).group_by(Approval.status))).all()
        )
    return {"tasks": tasks_by_status, "approvals": approvals_by_status}
