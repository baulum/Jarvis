"""REST API of the social media feature for the desktop app, browser client and phone (Sir's devices only).

Actions with an effect outside the Core (e-mail, publishing) run through the ToolExecutor exactly like the agent's
– approvals, audit log, kill switch and the idempotency ledger apply the same way."""

from __future__ import annotations

import html
import re
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from jarvis.api.deps import current_principal, get_state
from jarvis.app_state import AppState
from jarvis.db import session_scope
from jarvis.db.models import Approval
from jarvis.security.audit import audit
from jarvis.security.auth import Principal
from jarvis.social import links
from jarvis.social.platforms import PLATFORMS
from jarvis.social.repo import publication_dict, video_dict
from jarvis.social.service import SocialMediaService
from jarvis.tools.registry import ToolContext

router = APIRouter(prefix="/api/social")
StateDep = Depends(get_state)
SIR_ROLES = ("owner", "mobile_owner")
TIME_RE = r"^([01]\d|2[0-3]):[0-5]\d$"


PrincipalDep = Depends(current_principal)


async def require_sir(principal: Principal = PrincipalDep) -> Principal:
    if principal.role not in SIR_ROLES:
        raise HTTPException(403, "owner only")
    return principal


Sir = Depends(require_sir)


def _svc(st: AppState) -> SocialMediaService:
    if not st.settings.social_media.enabled:
        raise HTTPException(409, "Social Media ist in der Konfiguration ausgeschaltet (social_media.enabled).")
    return st.social


def _vid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as e:
        raise HTTPException(400, "invalid id") from e


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


# ------------------------------------------------------------------ dashboard
@router.get("/status")
async def status(st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    svc = _svc(st)
    out = await svc.status()
    out["platforms"] = svc.platforms_status()
    return out


@router.post("/dependencies/check")
async def check_dependencies(st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    return await _svc(st).refresh_dependencies()


# ------------------------------------------------------------------ library
@router.get("/videos")
async def videos(
    limit: int = Query(default=30, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None, pattern="^(producing|ready|failed)$"),
    st: AppState = StateDep,
    p: Principal = Sir,
) -> dict[str, Any]:
    svc = _svc(st)
    rows = await svc.repo.list_videos(limit=limit, offset=offset, status=status)
    pubs = await svc.repo.publications(limit=500)
    by_video: dict[str, list[dict[str, Any]]] = {}
    for pub in pubs:
        by_video.setdefault(str(pub.video_id), []).append(publication_dict(pub))
    items = []
    for v in rows:
        d = video_dict(v, full=False)
        d["captions"] = v.captions or {}
        d["publications"] = by_video.get(str(v.id), [])
        items.append(d)
    return {"videos": items, "has_more": len(rows) == limit}


@router.get("/videos/{vid}")
async def video(vid: str, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    svc = _svc(st)
    v = await svc.repo.get(_vid(vid))
    if v is None:
        raise HTTPException(404, "not found")
    out = video_dict(v)
    out["publications"] = [publication_dict(x) for x in await svc.repo.publications(v.id)]
    out["email_preview"] = None
    if v.status == "ready":
        mail = svc.build_mail(v, attachment=v.file_name, download_url=None, publications=out["publications"])
        out["email_preview"] = {"subject": mail.subject, "text": mail.text}
    return out


def _file_response(path: Any, name: str, download: bool) -> FileResponse:
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=name,
        content_disposition_type="attachment" if download else "inline",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.get("/videos/{vid}/file")
async def video_file(vid: str, download: bool = False, st: AppState = StateDep, p: Principal = Sir) -> FileResponse:
    """The MP4 (Range requests supported – streams, also to a remote client)."""
    svc = _svc(st)
    v = await svc.repo.get(_vid(vid))
    path = svc.file_path(v.file_name) if v else None
    if v is None or path is None:
        raise HTTPException(404, "not found")
    return _file_response(path, v.file_name or "video.mp4", download)


class LinkBody(BaseModel):
    hours: int = Field(default=24, ge=1, le=24 * 30)


@router.post("/videos/{vid}/link")
async def video_link(vid: str, body: LinkBody, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    """A signed, time-limited download link (path plus, if configured, the public URL)."""
    svc = _svc(st)
    v = await svc.repo.get(_vid(vid))
    if v is None or svc.file_path(v.file_name) is None:
        raise HTTPException(404, "not found")
    token = links.sign(v.id, body.hours * 3600)
    base = svc.public_base()
    await audit(p.label, "social.link", target=str(v.id), details={"hours": body.hours})
    return {
        "path": f"/api/social/download/{token}",
        "url": f"{base}/api/social/download/{token}" if base else None,
        "hours": body.hours,
    }


@router.get("/download/{token}")
async def download(token: str, st: AppState = StateDep) -> FileResponse:
    """Unauthenticated by design – the signed token (HMAC, expiry) is the authorization."""
    svc = _svc(st)
    vid = links.verify(token[:400])
    v = await svc.repo.get(vid) if vid else None
    path = svc.file_path(v.file_name) if v else None
    if v is None or path is None:
        raise HTTPException(404, "Link ungültig oder abgelaufen")
    return _file_response(path, v.file_name or "video.mp4", True)


# ------------------------------------------------------------------ production
class ProduceBody(BaseModel):
    hint: str = Field(default="", max_length=500)


@router.post("/produce")
async def produce(body: ProduceBody, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    svc = _svc(st)
    res = await svc.produce(p, hint=body.hint)
    await audit(p.label, "social.produce", target=res["task_id"])
    return res


@router.post("/videos/{vid}/regenerate")
async def regenerate(vid: str, body: ProduceBody, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    svc = _svc(st)
    v = await svc.repo.get(_vid(vid))
    if v is None:
        raise HTTPException(404, "not found")
    res = await svc.produce(p, hint=body.hint, parent_id=v.id)
    await audit(p.label, "social.regenerate", target=str(v.id), details={"task": res["task_id"]})
    return res


class EmailBody(BaseModel):
    to: str | None = Field(default=None, max_length=254)


@router.post("/videos/{vid}/email")
async def email(vid: str, body: EmailBody, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    _svc(st)
    args: dict[str, Any] = {"video_id": str(_vid(vid))}
    if body.to:
        args["to"] = body.to.strip()
    return await _run(st, p, "social_email_video", args)


class PublishBody(BaseModel):
    platform: Literal["instagram", "facebook", "tiktok", "youtube"]


@router.post("/videos/{vid}/publish")
async def publish(vid: str, body: PublishBody, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    _svc(st)
    return await _run(st, p, "social_publish_video", {"video_id": str(_vid(vid)), "platform": body.platform})


class ManualBody(BaseModel):
    post_url: str | None = Field(default=None, max_length=500, pattern=r"^https://")


@router.post("/videos/{vid}/publications/{platform}/manual")
async def manual(
    vid: str, platform: str, body: ManualBody, st: AppState = StateDep, p: Principal = Sir
) -> dict[str, Any]:
    """Manual workflow: Sir posted himself (or will) – store the status and the link to the post."""
    svc = _svc(st)
    if platform not in PLATFORMS:
        raise HTTPException(404, "unknown platform")
    v = await svc.repo.get(_vid(vid))
    if v is None:
        raise HTTPException(404, "not found")
    return await svc.mark_manual(v.id, platform, body.post_url, p.label)


# ------------------------------------------------------------------ settings
@router.get("/settings")
async def get_settings(st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    svc = _svc(st)
    return {**svc.runtime_settings(), "schedule": await svc.schedule_info(), "owner_email": svc.cfg.owner_email}


class PlatformToggle(BaseModel):
    autopost: bool


class SettingsBody(BaseModel):
    daily_enabled: bool | None = None
    production_time: str | None = Field(default=None, pattern=TIME_RE)
    email_enabled: bool | None = None
    email_recipient: str | None = Field(default=None, max_length=254)
    publish_time: str | None = Field(default=None, pattern=f"({TIME_RE})|^$")
    autopost_enabled: bool | None = None
    platforms: dict[str, PlatformToggle] | None = None
    voiceover: bool | None = None
    hint: str | None = Field(default=None, max_length=500)


@router.put("/settings")
async def put_settings(body: SettingsBody, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    svc = _svc(st)
    changes: dict[str, Any] = {}
    for key in ("email_enabled", "autopost_enabled", "voiceover", "hint"):
        val = getattr(body, key)
        if val is not None:
            changes[key] = val
    if body.email_recipient is not None:
        to = body.email_recipient.strip()
        if to and not re.fullmatch(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+", to):
            raise HTTPException(400, "Ungültige E-Mail-Adresse")
        changes["email_recipient"] = to or svc.cfg.owner_email
    if body.publish_time is not None:
        changes["publish_time"] = body.publish_time or None
    if body.platforms is not None:
        changes["platforms"] = {k: {"autopost": v.autopost} for k, v in body.platforms.items() if k in PLATFORMS}
    saved = svc.settings.save(changes) if changes else svc.runtime_settings()
    schedule = await svc.schedule_info()
    if body.daily_enabled is not None or body.production_time is not None:
        schedule = await svc.set_production(p.label, enabled=body.daily_enabled, time=body.production_time)
    await audit(
        p.label,
        "social.settings",
        details={
            "keys": sorted(changes) + [k for k in ("daily_enabled", "production_time") if getattr(body, k) is not None]
        },
    )
    return {**saved, "schedule": schedule, "owner_email": svc.cfg.owner_email}


# ------------------------------------------------------------------ platforms (connector)
@router.get("/platforms")
async def platforms(st: AppState = StateDep, p: Principal = Sir) -> list[dict[str, Any]]:
    return _svc(st).platforms_status()


@router.post("/oauth/{platform}/start")
async def oauth_start(platform: str, st: AppState = StateDep, p: Principal = Sir) -> dict[str, str]:
    conn = _svc(st).platform(platform) if platform in PLATFORMS else None
    if conn is None:
        raise HTTPException(404, "unknown platform")
    try:
        return {"auth_url": conn.begin_oauth()}
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@router.get("/oauth/{platform}/callback", response_class=HTMLResponse)
async def oauth_callback(
    platform: str, state: str, code: str | None = None, error: str | None = None, st: AppState = StateDep
) -> str:
    # Unauthenticated by necessity (browser redirect); protected by the one-time state of begin_oauth.
    conn = st.social.platform(platform) if platform in PLATFORMS else None
    if conn is None:
        return "<h3>Unbekannte Plattform</h3>"
    if error or not code:
        return f"<h3>Verbindung abgebrochen: {html.escape(error or 'kein Code')}</h3>"
    try:
        await conn.finish_oauth(state, code)
    except Exception as e:
        return f"<h3>Fehler: {html.escape(str(e)[:200])}</h3>"
    await st.connectors.configure(conn.manifest.id, actor="oauth")
    await audit("oauth", "social.connect", target=platform)
    name = html.escape(conn.manifest.name)
    return f"<h3>{name} ist verbunden. Autopost bleibt aus, bis du ihn in Jarvis einschaltest.</h3>"


class DisconnectBody(BaseModel):
    confirm: bool = True


@router.post("/platforms/{platform}/disconnect")
async def disconnect(
    platform: str, body: DisconnectBody, st: AppState = StateDep, p: Principal = Sir
) -> dict[str, Any]:
    from jarvis.secrets import secret_store

    conn = _svc(st).platform(platform) if platform in PLATFORMS else None
    if conn is None:
        raise HTTPException(404, "unknown platform")
    secret_store.delete(conn.token_secret)
    await st.connectors.configure(conn.manifest.id, actor=p.label, enabled=False)
    await audit(p.label, "social.disconnect", target=platform)
    return {"ok": True}
