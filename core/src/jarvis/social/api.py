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
from jarvis.social.companies import COLOR_KEYS, CompanyError
from jarvis.social.platforms import PLATFORMS
from jarvis.social.repo import publication_dict, video_dict
from jarvis.social.service import SocialMediaService
from jarvis.tools.executor import ToolUserError
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


def _company(svc: SocialMediaService, ref: str | None) -> dict[str, Any]:
    try:
        return svc.company(ref)
    except ToolUserError as e:
        raise HTTPException(404, str(e)) from e


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
CompanyQuery = Query(default=None, max_length=80, description="Unternehmen (ID oder Name); leer = aktives Profil")


@router.get("/status")
async def status(company: str | None = CompanyQuery, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    svc = _svc(st)
    profile = _company(svc, company)
    out = await svc.status(profile["id"])
    out["platforms"] = svc.platforms_status(profile)
    return out


# ------------------------------------------------------------------ companies
class Colors(BaseModel):
    accent: str | None = Field(default=None, max_length=7)
    ink: str | None = Field(default=None, max_length=7)
    canvas: str | None = Field(default=None, max_length=7)
    accent_deep: str | None = Field(default=None, max_length=7)
    accent_on_dark: str | None = Field(default=None, max_length=7)
    graphite: str | None = Field(default=None, max_length=7)


class CompanyBody(BaseModel):
    name: str | None = Field(default=None, max_length=60)
    description: str | None = Field(default=None, max_length=400)
    audience: str | None = Field(default=None, max_length=300)
    website: str | None = Field(default=None, max_length=80)
    cta_button: str | None = Field(default=None, max_length=22)
    offer: str | None = Field(default=None, max_length=200)
    price: str | None = Field(default=None, max_length=40)
    colors: Colors | None = None
    tone: str | None = Field(default=None, max_length=400)
    hashtags: list[str] | None = Field(default=None, max_length=12)
    accounts: dict[Literal["instagram", "facebook", "tiktok", "youtube"], str] | None = None
    uptik_accounts: list[str] | None = Field(default=None, max_length=24)
    recipients: list[str] | None = Field(default=None, max_length=20)
    autopost: bool | None = None
    official_api: bool | None = None
    hint: str | None = Field(default=None, max_length=500)

    def changes(self) -> dict[str, Any]:
        out = {k: v for k, v in self.model_dump(exclude_unset=True).items() if v is not None}
        if self.colors is not None:
            out["colors"] = {k: v for k, v in self.colors.model_dump(exclude_unset=True).items() if k in COLOR_KEYS}
        return out


@router.get("/companies")
async def companies(st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    return await _svc(st).companies_info()


@router.post("/companies", status_code=201)
async def create_company(body: CompanyBody, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    try:
        return await _svc(st).create_company(body.changes(), p.label)
    except CompanyError as e:
        raise HTTPException(400, str(e)) from e


@router.put("/companies/{cid}")
async def update_company(cid: str, body: CompanyBody, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    svc = _svc(st)
    profile = _company(svc, cid)
    try:
        return await svc.update_company(profile["id"], body.changes(), p.label)
    except CompanyError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/companies/{cid}")
async def delete_company(cid: str, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    svc = _svc(st)
    profile = _company(svc, cid)
    try:
        return await svc.delete_company(profile["id"], p.label)
    except CompanyError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/companies/{cid}/activate")
async def activate_company(cid: str, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    svc = _svc(st)
    profile = _company(svc, cid)
    svc.companies.set_active(profile["id"])
    await audit(p.label, "social.company.activate", target=profile["id"])
    return await svc.companies_info()


@router.post("/dependencies/check")
async def check_dependencies(st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    return await _svc(st).refresh_dependencies()


# ------------------------------------------------------------------ library
@router.get("/videos")
async def videos(
    limit: int = Query(default=30, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None, pattern="^(producing|ready|failed)$"),
    company: str | None = CompanyQuery,
    all_companies: bool = Query(default=False, alias="all"),
    st: AppState = StateDep,
    p: Principal = Sir,
) -> dict[str, Any]:
    """The library of one company (default: the active one); ``all=true`` lists every company's videos."""
    svc = _svc(st)
    cid = None if all_companies else _company(svc, company)["id"]
    rows = await svc.repo.list_videos(limit=limit, offset=offset, status=status, company_id=cid)
    pubs = await svc.repo.publications(limit=500)
    by_video: dict[str, list[dict[str, Any]]] = {}
    for pub in pubs:
        by_video.setdefault(str(pub.video_id), []).append(publication_dict(pub))
    items = []
    names = {c["id"]: c["name"] for c in svc.companies.all()}
    for v in rows:
        d = video_dict(v, full=False)
        d["company_name"] = names.get(v.company_id, v.company_id)
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
    out["company_name"] = svc.profile_of(v)["name"]
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
    company: str | None = Field(default=None, max_length=80)


async def _produce(
    svc: SocialMediaService, p: Principal, body: ProduceBody, parent: uuid.UUID | None = None
) -> dict[str, Any]:
    try:
        return await svc.produce(p, hint=body.hint, parent_id=parent, company=body.company)
    except ToolUserError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/produce")
async def produce(body: ProduceBody, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    svc = _svc(st)
    res = await _produce(svc, p, body)
    await audit(p.label, "social.produce", target=res["task_id"], details={"company": res["company"]})
    return res


@router.post("/videos/{vid}/regenerate")
async def regenerate(vid: str, body: ProduceBody, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    svc = _svc(st)
    v = await svc.repo.get(_vid(vid))
    if v is None:
        raise HTTPException(404, "not found")
    res = await _produce(svc, p, body, parent=v.id)
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
async def get_settings(
    company: str | None = CompanyQuery, st: AppState = StateDep, p: Principal = Sir
) -> dict[str, Any]:
    """Global switches plus those of one company (autopost, standing wish, recipients, its daily schedule)."""
    svc = _svc(st)
    profile = _company(svc, company)
    return {
        **svc.runtime_settings(profile),
        "schedule": await svc.schedule_info(profile["id"]),
        "owner_email": svc.cfg.owner_email,
    }


class PlatformToggle(BaseModel):
    autopost: bool


class SettingsBody(BaseModel):
    company: str | None = Field(default=None, max_length=80)  # whose schedule/autopost/wish (default: active)
    daily_enabled: bool | None = None
    production_time: str | None = Field(default=None, pattern=TIME_RE)
    email_enabled: bool | None = None
    email_recipient: str | None = Field(default=None, max_length=254)
    publish_time: str | None = Field(default=None, pattern=f"({TIME_RE})|^$")
    autopost_enabled: bool | None = None
    platforms: dict[str, PlatformToggle] | None = None
    voiceover: bool | None = None
    hint: str | None = Field(default=None, max_length=500)
    recipients: list[str] | None = Field(default=None, max_length=20)


@router.put("/settings")
async def put_settings(body: SettingsBody, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    svc = _svc(st)
    profile = _company(svc, body.company)
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
    if body.recipients is not None:
        changes["recipients"] = body.recipients
    try:
        saved = svc.save_settings(changes, profile) if changes else svc.runtime_settings(profile)
    except ToolUserError as e:
        raise HTTPException(400, str(e)) from e
    schedule = await svc.schedule_info(profile["id"])
    if body.daily_enabled is not None or body.production_time is not None:
        schedule = await svc.set_production(
            p.label, enabled=body.daily_enabled, time=body.production_time, company_id=profile["id"]
        )
    await audit(
        p.label,
        "social.settings",
        details={
            "company": profile["id"],
            "keys": sorted(changes) + [k for k in ("daily_enabled", "production_time") if getattr(body, k) is not None],
        },
    )
    return {**saved, "schedule": schedule, "owner_email": svc.cfg.owner_email}


# ------------------------------------------------------------------ platforms (connector)
@router.get("/platforms")
async def platforms(
    company: str | None = CompanyQuery, st: AppState = StateDep, p: Principal = Sir
) -> list[dict[str, Any]]:
    svc = _svc(st)
    return svc.platforms_status(_company(svc, company))


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
