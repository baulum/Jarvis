"""REST API of the UpTik connector – desktop app, browser client and phone use the same endpoints (Sir's devices
only). Publishing, scheduling, connecting an account and cancelling run through the ToolExecutor exactly like the
agent's calls: approvals, audit log, kill switch and the idempotency ledger apply the same way."""

from __future__ import annotations

import asyncio
import re
import secrets
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from jarvis.api.deps import current_principal, get_state
from jarvis.app_state import AppState
from jarvis.db import session_scope
from jarvis.db.models import Approval
from jarvis.security.audit import audit
from jarvis.security.auth import Principal
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext
from jarvis.uptik.model import CONNECTOR_ID, FINISHED_STATUSES, OPEN_STATUSES, PLATFORMS
from jarvis.uptik.service import UptikService
from jarvis.uptik.tools import post_view

router = APIRouter(prefix="/api/uptik")
StateDep = Depends(get_state)
PrincipalDep = Depends(current_principal)
SIR_ROLES = ("owner", "mobile_owner")
UPLOAD_LIMIT = 2 * 1024**3
_NAME_BAD = re.compile(r"[^\w.\- ]+", re.UNICODE)


async def require_sir(principal: Principal = PrincipalDep) -> Principal:
    if principal.role not in SIR_ROLES:
        raise HTTPException(403, "owner only")
    return principal


Sir = Depends(require_sir)


def _svc(st: AppState, *, need_enabled: bool = True) -> UptikService:
    conn = st.connectors.connectors.get(CONNECTOR_ID)
    if need_enabled and (conn is None or not conn.enabled):
        raise HTTPException(409, "Der UpTik-Connector ist nicht aktiviert (Connectoren › UpTik Autopost).")
    return st.uptik


def _bad(e: ToolUserError) -> HTTPException:
    return HTTPException(400, str(e))


async def _run(st: AppState, p: Principal, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    res = await st.executor.execute(tool, args, ToolContext(principal=p))
    out = res.to_dict()
    if res.status == "approval_required" and res.approval_id:
        async with session_scope() as s:
            ap = await s.get(Approval, res.approval_id)
            if ap is not None:
                out["summary"] = ap.summary
                out["risk"] = ap.risk
    if res.status in ("error", "denied"):
        raise HTTPException(400 if res.status == "error" else 403, res.message or "Fehler")
    return out


# ------------------------------------------------------------------ connector
@router.get("/status")
async def status(st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    return await _svc(st, need_enabled=False).status()


class ConnectorBody(BaseModel):
    enabled: bool | None = None
    settings: dict[str, Any] | None = None


@router.put("/connector")
async def configure(body: ConnectorBody, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    """Enable/disable and settings – the same ConnectorManager path as Connectoren in the desktop app."""
    try:
        await st.connectors.configure(CONNECTOR_ID, actor=p.label, enabled=body.enabled, settings=body.settings)
    except Exception as e:  # jsonschema.ValidationError and friends
        raise HTTPException(400, getattr(e, "message", None) or str(e)[:300]) from e
    return await _svc(st, need_enabled=False).status()


# ------------------------------------------------------------------ accounts
class AccountBody(BaseModel):
    platform: Literal["tiktok", "instagram_reels", "youtube_shorts"]
    label: str = Field(min_length=1, max_length=80)
    runner: Literal["local", "device"] | None = None
    device: str | None = Field(default=None, max_length=120)


class AccountPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=80)
    runner: Literal["local", "device"] | None = None
    device: str | None = Field(default=None, max_length=120)
    autopost: bool | None = None


class ResumeBody(BaseModel):
    confirm: Literal[True]
    requeue: bool = False


@router.get("/accounts")
async def accounts(st: AppState = StateDep, p: Principal = Sir) -> list[dict[str, Any]]:
    return await _svc(st).accounts()


@router.post("/accounts", status_code=201)
async def create_account(body: AccountBody, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    try:
        return await _svc(st).create_account(body.platform, body.label, body.runner, body.device, actor=p.label)
    except ToolUserError as e:
        raise _bad(e) from e


@router.patch("/accounts/{aid}")
async def update_account(aid: str, body: AccountPatch, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    try:
        return await _svc(st).update_account(aid, p.label, **body.model_dump(exclude_none=True))
    except ToolUserError as e:
        raise _bad(e) from e


@router.delete("/accounts/{aid}")
async def delete_account(aid: str, st: AppState = StateDep, p: Principal = Sir) -> dict[str, bool]:
    try:
        await _svc(st).delete_account(aid, p.label)
    except ToolUserError as e:
        raise _bad(e) from e
    return {"ok": True}


@router.post("/accounts/{aid}/connect")
async def connect(aid: str, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    _svc(st)
    return await _run(st, p, "uptik_connect_account", {"account_id": aid})


@router.post("/accounts/{aid}/check")
async def check(aid: str, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    try:
        return await _svc(st).check_account(aid)
    except ToolUserError as e:
        raise _bad(e) from e


@router.post("/accounts/{aid}/disconnect")
async def disconnect(aid: str, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    try:
        return await _svc(st).disconnect(aid, p.label)
    except ToolUserError as e:
        raise _bad(e) from e


@router.post("/accounts/{aid}/resume")
async def resume(aid: str, body: ResumeBody, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    """Sir checked the account on the platform: close the circuit breaker (owner devices only, never the agent)."""
    try:
        return await _svc(st).resume(aid, p.label, requeue=body.requeue)
    except ToolUserError as e:
        raise _bad(e) from e


# ------------------------------------------------------------------ videos
@router.get("/inbox")
async def inbox(st: AppState = StateDep, p: Principal = Sir) -> list[dict[str, Any]]:
    svc = _svc(st)
    await svc.ensure_open()
    return await svc.store.inbox()


@router.delete("/inbox/{iid}")
async def remove_inbox(iid: str, st: AppState = StateDep, p: Principal = Sir) -> dict[str, bool]:
    svc = _svc(st)
    await svc.ensure_open()
    item = await svc.store.remove_inbox(iid)
    if item and item["source"] == "upload":  # only own uploads are deleted, never the producer's file
        target = svc.uploads_dir / item["name"]
        if target.resolve().is_relative_to(svc.uploads_dir.resolve()):
            target.unlink(missing_ok=True)
    return {"ok": item is not None}


def _safe_name(raw: str) -> str:
    name = _NAME_BAD.sub("_", raw.replace("\\", "/").rsplit("/", 1)[-1]).strip(" ._") or "video.mp4"
    return name[-120:]


@router.get("/uploads")
async def uploads(st: AppState = StateDep, p: Principal = Sir) -> list[dict[str, Any]]:
    """The uploads folder (video source „upload:<name>“): name, size, modified_at – newest first."""
    return await asyncio.to_thread(_svc(st).uploads)


@router.post("/uploads", status_code=201)
async def upload(
    request: Request, name: str = Query(min_length=1, max_length=255), st: AppState = StateDep, p: Principal = Sir
) -> dict[str, Any]:
    """A video from the app (raw body) → uploads folder + inbox entry. Checked again before every post."""
    svc = _svc(st)
    await svc.ensure_open()
    safe = _safe_name(name)
    if not safe.lower().endswith((".mp4", ".mov", ".webm")):
        raise HTTPException(415, "Nur MP4, MOV oder WebM")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > UPLOAD_LIMIT:
        raise HTTPException(413, "Datei zu groß (max. 2 GB)")
    svc.uploads_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = svc.uploads_dir / f"{secrets.token_hex(4)}-{safe}"
    size = 0
    try:
        with target.open("xb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > UPLOAD_LIMIT:
                    raise HTTPException(413, "Datei zu groß (max. 2 GB)")
                await asyncio.to_thread(out.write, chunk)
        if size == 0:
            raise HTTPException(400, "Die Datei ist leer")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    target.chmod(0o600)
    item = await svc.store.add_inbox(
        source="upload",
        source_ref=target.name,
        path=str(target),
        name=target.name,
        size=size,
        title=safe.rsplit(".", 1)[0],
    )
    await audit(p.label, "uptik.upload", details={"size": size})
    return item


# ------------------------------------------------------------------ posts
class PostBody(BaseModel):
    video: str = Field(min_length=3, max_length=2048)
    caption: str = Field(default="", max_length=5000)
    hashtags: list[str] | str = Field(default_factory=list)
    title: str = Field(default="", max_length=100)
    accounts: list[str] = Field(default_factory=list, max_length=12)
    per_platform: dict[str, dict[str, Any]] | None = None
    options: dict[str, dict[str, Any]] | None = None
    publish_at: str | None = Field(default=None, max_length=40)

    def args(self) -> dict[str, Any]:
        out = self.model_dump(exclude_none=True)
        if not out.get("publish_at"):
            out.pop("publish_at", None)
        if out.get("per_platform"):
            out["per_platform"] = {k: v for k, v in out["per_platform"].items() if k in PLATFORMS}
        return out


@router.post("/validate")
async def validate(body: PostBody, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    svc = _svc(st)
    try:
        out = await svc.prepare(body.args(), scheduled=bool(body.publish_at))
    except ToolUserError as e:
        return {"ok": False, "problems": str(e)}
    return {
        "ok": True,
        "targets": out["targets"],
        "video_name": out["video_name"],
        "video_size": out["video_size"],
        "video_duration": out.get("video_duration"),
        "publish_at_label": out.get("publish_at_label"),
    }


@router.post("/publish")
async def publish(body: PostBody, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    """Publish now or (with ``publish_at``) at a time – always as an approval unless Sir's approval mode allows it."""
    _svc(st)
    tool = "uptik_schedule_post" if body.publish_at else "uptik_publish_now"
    return await _run(st, p, tool, body.args())


@router.get("/queue")
async def queue(
    limit: int = Query(default=100, ge=1, le=500), st: AppState = StateDep, p: Principal = Sir
) -> list[dict[str, Any]]:
    svc = _svc(st)
    await svc.ensure_open()
    return [post_view(x) for x in await svc.store.posts(OPEN_STATUSES, limit=limit)]


@router.get("/history")
async def history(
    limit: int = Query(default=100, ge=1, le=500), st: AppState = StateDep, p: Principal = Sir
) -> list[dict[str, Any]]:
    svc = _svc(st)
    await svc.ensure_open()
    return [post_view(x) for x in await svc.store.posts(FINISHED_STATUSES, limit=limit, newest_first=True)]


@router.get("/posts/{pid}")
async def post(pid: str, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    svc = _svc(st)
    await svc.ensure_open()
    row = await svc.store.post(pid)
    if row is None:
        raise HTTPException(404, "not found")
    return {**post_view(row), "events": await svc.store.events(pid)}


@router.post("/posts/{pid}/cancel")
async def cancel(pid: str, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    _svc(st)
    return await _run(st, p, "uptik_cancel_post", {"post_id": pid})


class ReviewBody(BaseModel):
    outcome: Literal["published", "failed"]
    url: str | None = Field(default=None, max_length=500, pattern=r"^https://")


@router.post("/posts/{pid}/review")
async def review(pid: str, body: ReviewBody, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    try:
        return post_view(await _svc(st).resolve_review(pid, body.outcome, body.url, p.label))
    except ToolUserError as e:
        raise _bad(e) from e
