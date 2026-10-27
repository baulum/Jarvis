"""``/api/mobile/app-update/*`` – release manifest, APK download, phone reports, Sir's controls.

Every path lies below ``/api/mobile/``, which the mobile gateway already serves (TLS, pinned certificate, LAN and
Tailscale). Phones authenticate with their device token, the desktop with the owner token. The only exception is
the install page for the very first installation (the phone is not paired yet, so it has no token): it needs a
random one-time link that Sir creates on the Mac (valid ``install_link_ttl_hours``, at most 20 downloads).

Downloads support HTTP Range requests (``FileResponse``): an interrupted download continues where it stopped.
"""

from __future__ import annotations

import html
import io
import logging
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from jarvis.api.deps import current_principal, get_state, require_owner
from jarvis.app_state import AppState
from jarvis.db import session_scope
from jarvis.db.models import Device, MobileDevice
from jarvis.mobile.gateway import advertised_urls
from jarvis.mobile.ota.releases import Release
from jarvis.mobile.ota.service import AppUpdateService, BuildRequest
from jarvis.mobile.ota.versioning import is_upgrade
from jarvis.mobile.pairing import MOBILE_ROLE
from jarvis.security.audit import audit
from jarvis.security.auth import Principal
from jarvis.security.guards import rate_limiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/mobile/app-update")
StateDep = Depends(get_state)
APK_MEDIA_TYPE = "application/vnd.android.package-archive"


Current = Depends(current_principal)


async def _owner_or_mobile(p: Principal = Current) -> Principal:
    if p.role not in ("owner", MOBILE_ROLE):
        raise HTTPException(403, "owner devices only")
    return p


async def _mobile(p: Principal = Current) -> Principal:
    if p.role != MOBILE_ROLE or p.device_id is None:
        raise HTTPException(403, "only paired phones")
    return p


OwnerOrMobile = Depends(_owner_or_mobile)
Mobile = Depends(_mobile)
Owner = Depends(require_owner)


def _svc(st: AppState) -> AppUpdateService:
    svc = getattr(st, "app_update", None)
    if svc is None:
        raise HTTPException(503, "App-Updates sind ausgeschaltet (mobile.app_update.enabled)")
    return svc  # type: ignore[no-any-return]


def _release(svc: AppUpdateService, code: int) -> Release:
    rel = svc.releases.get(code)
    if rel is None:
        raise HTTPException(404, "release not found")
    return rel


def _apk(svc: AppUpdateService, rel: Release) -> FileResponse:
    path = svc.releases.apk_path(rel)
    if not path.is_file():
        raise HTTPException(404, "release file missing")
    return FileResponse(
        path,
        media_type=APK_MEDIA_TYPE,
        filename=f"jarvis-{rel.version_name}.apk".replace("+", "-"),
        headers={"X-Jarvis-Sha256": rel.sha256, "Cache-Control": "no-store"},
    )


async def _paired_ids() -> set[str]:
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(MobileDevice.device_id)
                .join(Device, Device.id == MobileDevice.device_id)
                .where(Device.revoked.is_(False))
            )
        ).all()
    return {str(r[0]) for r in rows}


# ------------------------------------------------------------------ status (desktop + app)
@router.get("/status")
async def status(st: AppState = StateDep, p: Principal = OwnerOrMobile) -> dict[str, Any]:
    snap = _svc(st).snapshot()
    try:
        paired = await _paired_ids()
    except Exception:  # database unreachable: show what is known
        logger.debug("paired devices not readable", exc_info=True)
    else:
        snap["devices"] = [d for d in snap["devices"] if d["device_id"] in paired]
    return snap


@router.get("/latest")
async def latest(current: int | None = None, st: AppState = StateDep, p: Principal = OwnerOrMobile) -> dict[str, Any]:
    """The release the phone should run. ``available`` only for a strictly higher versionCode (no downgrade)."""
    svc = _svc(st)
    rel = svc.releases.latest()
    if rel is None:
        return {"available": False, "release": None, "reason": "Noch kein Release gebaut"}
    up = is_upgrade(rel.version_code, current)
    return {
        "available": up,
        "release": rel.public(),
        "reason": None if up else f"Version {current} ist aktuell (neuestes Release {rel.version_code})",
    }


@router.get("/releases/{code}/apk")
async def apk(code: int, request: Request, st: AppState = StateDep, p: Principal = OwnerOrMobile) -> FileResponse:
    svc = _svc(st)
    rel = _release(svc, code)
    if rel.withdrawn and p.role != "owner":
        raise HTTPException(410, "release withdrawn")
    if not request.headers.get("range"):  # count whole downloads, not every resumed piece
        await audit(p.label, "mobile.app_update.download", target=str(code))
    return _apk(svc, rel)


class Report(BaseModel):
    version_code: int = Field(ge=0, le=2_147_483_647)
    version_name: str = Field(default="", max_length=80)
    state: Literal[
        "idle", "checking", "up_to_date", "downloading", "paused", "verifying", "ready", "waiting_user",
        "installing", "installed", "failed", "unsupported",
    ]  # fmt: skip
    target_code: int | None = Field(default=None, ge=0, le=2_147_483_647)
    progress: float | None = Field(default=None, ge=0, le=1)
    bytes: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, ge=0)
    error: str | None = Field(default=None, max_length=300)
    can_install: bool | None = None  # "Install unknown apps" allowed for J.A.R.V.I.S.
    unattended: bool | None = None  # Android 12+: updates without confirmation possible
    sdk: int | None = Field(default=None, ge=0, le=1000)


@router.post("/report")
async def report(body: Report, st: AppState = StateDep, p: Principal = Mobile) -> dict[str, bool]:
    assert p.device_id is not None
    if not rate_limiter.check(f"app-update-report:{p.device_id}", 600, 3600):
        raise HTTPException(429, "too many reports")
    _svc(st).record_report(str(p.device_id), p.id, body.model_dump())
    if body.state in ("installed", "failed"):
        await audit(p.label, f"mobile.app_update.{body.state}", target=str(body.target_code or body.version_code),
                    details={"error": body.error} if body.error else None)  # fmt: skip
    return {"ok": True}


# ------------------------------------------------------------------ Sir's controls
@router.post("/check")
async def check(st: AppState = StateDep, p: Principal = OwnerOrMobile) -> dict[str, Any]:
    """Looks for app changes now (builds only if needed)."""
    svc = _svc(st)
    await svc.check_toolchain()
    return svc.request(BuildRequest("manuelle Prüfung", actor=p.label), debounce=0)


@router.post("/build")
async def build(st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    """Builds the branch head now – also to retry a failed build."""
    await audit(p.label, "mobile.app_update.build")
    return _svc(st).request(BuildRequest("manuell gestartet", force=True, actor=p.label))


@router.post("/distribute")
async def distribute(st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    """Announces the newest release to the phones again (push)."""
    return await _svc(st).distribute()


class Withdraw(BaseModel):
    withdrawn: bool = True
    reason: str | None = Field(default=None, max_length=300)


@router.post("/releases/{code}/withdraw")
async def withdraw(code: int, body: Withdraw, st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    """Hides a defective release from the phones (they keep what they have; Android allows no downgrade)."""
    svc = _svc(st)
    _release(svc, code)
    rel = svc.withdraw(code, body.withdrawn, body.reason)
    await audit(
        p.label, "mobile.app_update.withdraw" if body.withdrawn else "mobile.app_update.restore", target=str(code)
    )
    return rel.public()


@router.post("/releases/{code}/rebuild")
async def rebuild(code: int, st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    """Recovery: builds the commit of an older release again with a new, higher versionCode."""
    svc = _svc(st)
    _release(svc, code)
    await audit(p.label, "mobile.app_update.rebuild", target=str(code))
    return svc.rebuild(code, p.label)


# ------------------------------------------------------------------ first installation
@router.post("/install-link")
async def install_link(st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    """One-time link for installing the first signed APK on a phone that is not paired yet."""
    import segno

    svc = _svc(st)
    rel = svc.releases.latest()
    if rel is None:
        raise HTTPException(409, "Noch kein Release gebaut – zuerst „Jetzt bauen“.")
    token, expires = svc.create_install_link()
    urls = [f"{u.rstrip('/')}/api/mobile/app-update/install/{token}" for u in advertised_urls(st.settings)]
    buf = io.BytesIO()
    if urls:
        segno.make(urls[0], error="m").save(buf, kind="svg", scale=5, dark="#0b1a24", light="#ffffff", border=2)
    await audit(p.label, "mobile.app_update.install_link", target=str(rel.version_code))
    return {
        "urls": urls,
        "expires_at": datetime.fromtimestamp(expires, UTC).isoformat(),
        "svg": buf.getvalue().decode() if urls else None,
        "release": rel.public(),
        "certificate_fingerprint": st.gateway.fingerprint() if getattr(st, "gateway", None) else None,
    }


def _install_release(svc: AppUpdateService, token: str, request: Request, *, count: bool) -> Release:
    client = request.client.host if request.client else "?"
    if not rate_limiter.check(f"app-install:{client}", 60, 3600):
        raise HTTPException(429, "too many requests")
    if not svc.use_install_link(token, count=count):
        raise HTTPException(404, "Link ungültig oder abgelaufen – am Mac einen neuen erzeugen.")
    rel = svc.releases.latest()
    if rel is None:
        raise HTTPException(404, "kein Release")
    return rel


@router.get("/install/{token}", response_class=HTMLResponse)
async def install_page(token: str, request: Request, st: AppState = StateDep) -> HTMLResponse:
    rel = _install_release(_svc(st), token, request, count=False)
    return HTMLResponse(_page(rel, token), headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/install/{token}/jarvis.apk")
async def install_apk(token: str, request: Request, st: AppState = StateDep) -> FileResponse:
    svc = _svc(st)
    rel = _install_release(svc, token, request, count=not request.headers.get("range"))
    client = request.client.host if request.client else "?"
    await audit(f"install-link:{client}", "mobile.app_update.install_download", target=str(rel.version_code))
    return _apk(svc, rel)


def _fp(hexstr: str) -> str:
    return ":".join(hexstr[i : i + 2] for i in range(0, len(hexstr), 2)).upper()


def _page(rel: Release, token: str) -> str:
    e = html.escape
    log = "".join(f"<li>{e(c['subject'])}</li>" for c in rel.changelog[:8])
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>J.A.R.V.I.S. installieren</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;background:#05080c;color:#d8e6ee;margin:0;padding:24px;max-width:640px}}
h1{{font-size:22px;letter-spacing:.08em}}a.b{{display:block;text-align:center;background:#5ce1ff;color:#05080c;
padding:16px;border-radius:10px;font-weight:600;text-decoration:none;margin:20px 0}}code{{font-size:12px;
word-break:break-all;color:#9fb3bf}}ol li{{margin:6px 0}}small{{color:#7d919c}}</style></head><body>
<h1>J.A.R.V.I.S. für Android</h1>
<p>Version <b>{e(rel.version_name)}</b> · {rel.size / 1e6:.1f} MB · Android 8 oder neuer</p>
<a class="b" href="{e(token)}/jarvis.apk">APK herunterladen</a>
<ol>
<li>Herunterladen, dann die Datei öffnen (Benachrichtigung oder „Downloads“).</li>
<li>Android fragt einmalig, ob der Browser Apps installieren darf: <b>Einstellungen › Zulassen</b>, zurück,
 <b>Installieren</b>.</li>
<li>Ist schon eine ältere J.A.R.V.I.S.-App mit anderem Schlüssel installiert (meldet Android „App nicht installiert“
 oder „Konflikt mit vorhandenem Paket“), diese zuerst deinstallieren und danach neu koppeln.</li>
<li>Danach kommen Updates automatisch über die App (Einstellungen › App-Updates).</li>
</ol>
<p><small>Prüfsumme (SHA-256)</small><br><code>{e(rel.sha256)}</code></p>
<p><small>Signatur-Fingerabdruck (SHA-256)</small><br><code>{e(_fp(rel.signing_cert_sha256))}</code></p>
{f"<p><small>Änderungen</small></p><ul>{log}</ul>" if log else ""}
</body></html>"""
