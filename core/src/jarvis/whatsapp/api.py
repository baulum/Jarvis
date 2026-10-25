"""REST API of the WhatsApp connector for the desktop app (and remote/browser clients of the same Core).

Owner devices only: chats are private. Actions with an effect go through the ToolExecutor exactly like the
agent's – a send from the app therefore also ends in an approval that shows recipient, files and text, and the
audit log, the kill switch and the double-send protection apply the same way.
"""

from __future__ import annotations

import asyncio
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from jarvis.api.deps import get_state, require_owner
from jarvis.app_state import AppState
from jarvis.db import session_scope
from jarvis.db.models import Approval
from jarvis.security.audit import audit
from jarvis.security.auth import Principal
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext
from jarvis.whatsapp.files import human_size, safe_file_name, sniff
from jarvis.whatsapp.service import WhatsAppService

router = APIRouter(prefix="/api/whatsapp")
Owner = Depends(require_owner)
StateDep = Depends(get_state)
UPLOAD_LIMIT_HARD = 2 * 1024 * 1024 * 1024


def _svc(st: AppState) -> WhatsAppService:
    conn = st.connectors.connectors.get("whatsapp")
    if conn is None or not conn.enabled:
        raise HTTPException(409, "Der WhatsApp-Connector ist nicht aktiviert (Connectoren › WhatsApp).")
    return st.whatsapp


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


def _bad(e: ToolUserError) -> HTTPException:
    return HTTPException(400, str(e))


# ------------------------------------------------------------------ connection
@router.get("/status")
async def status(st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    conn = st.connectors.connectors.get("whatsapp")
    out: dict[str, Any] = {"enabled": bool(conn and conn.enabled)}
    if conn is not None and conn.enabled:
        out.update(st.whatsapp.public_status(include_login=True))
        out["counts"] = await st.whatsapp.repo.counts()
        out["download_dir"] = str(st.whatsapp.download_dir())
    return out


class ConnectBody(BaseModel):
    method: Literal["qr", "pairing"] = "qr"
    phone: str | None = Field(default=None, max_length=32)


@router.post("/connect")
async def connect(body: ConnectBody, st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    svc = _svc(st)
    try:
        res = await svc.connect(body.method, body.phone)
    except ToolUserError as e:
        raise _bad(e) from e
    await audit(p.label, "whatsapp.connect", details={"method": body.method})
    return res


@router.post("/disconnect")
async def disconnect(st: AppState = StateDep, p: Principal = Owner) -> dict[str, bool]:
    await _svc(st).disconnect()
    await audit(p.label, "whatsapp.disconnect")
    return {"ok": True}


@router.post("/logout")
async def logout(st: AppState = StateDep, p: Principal = Owner) -> dict[str, bool]:
    try:
        await _svc(st).logout(p.label)
    except ToolUserError as e:
        raise _bad(e) from e
    return {"ok": True}


# ------------------------------------------------------------------ chats & messages
@router.get("/chats")
async def chats(
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
    st: AppState = StateDep,
    p: Principal = Owner,
) -> list[dict[str, Any]]:
    svc = _svc(st)
    return await svc.repo.list_chats(q, limit, excluded=svc.excluded)


@router.get("/chats/{jid}/messages")
async def messages(
    jid: str,
    before: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    st: AppState = StateDep,
    p: Principal = Owner,
) -> dict[str, Any]:
    svc = _svc(st)
    if jid in svc.excluded:
        raise HTTPException(404, "not found")
    when = None
    if before:
        try:
            when = datetime.fromisoformat(before)
        except ValueError as e:
            raise HTTPException(400, "before: ISO-Zeit") from e
    rows = await svc.repo.messages(jid, limit, when)
    chat = await svc.repo.chat(jid)
    names = await svc.repo.names([jid])
    stored = await svc.repo.search_files(chat_jid=jid, limit=500)
    by_msg: dict[str, list[dict[str, Any]]] = {}
    for f in stored:
        by_msg.setdefault(f["message_id"], []).append({**f, "size_label": human_size(f["size"])})
    for m in rows:
        m["files"] = by_msg.get(m["id"], [])
    return {
        "chat": {
            "jid": jid,
            "name": names.get(jid) or (chat.name if chat else jid),
            "is_group": jid.endswith("@g.us"),
            "unread": chat.unread_count if chat else 0,
        },
        "messages": rows,
        "has_more": len(rows) == limit,
    }


@router.get("/chats/{jid}/messages/{msg_id}/thumbnail")
async def thumbnail(jid: str, msg_id: str, st: AppState = StateDep, p: Principal = Owner) -> Response:
    data = await _svc(st).thumbnail(jid, msg_id)
    if data is None:
        raise HTTPException(404, "not found")
    return Response(
        data,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400", "Content-Security-Policy": "default-src 'none'; sandbox"},
    )


@router.post("/chats/{jid}/read")
async def mark_read(jid: str, st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    _svc(st)
    return await _run(st, p, "whatsapp_mark_read", {"chatId": jid})


@router.post("/chats/{jid}/messages/{msg_id}/download")
async def download(jid: str, msg_id: str, st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    _svc(st)
    return await _run(st, p, "whatsapp_download_attachment", {"messageId": f"{jid}/{msg_id}"})


@router.post("/chats/{jid}/history")
async def load_history(jid: str, st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    try:
        return await _svc(st).history_more(jid)
    except ToolUserError as e:
        raise _bad(e) from e


@router.post("/chats/{jid}/suggest")
async def suggest(jid: str, st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    try:
        return {"created": await _svc(st).detect_suggestions(jid)}
    except ToolUserError as e:
        raise _bad(e) from e


@router.delete("/chats/{jid}")
async def forget_chat(jid: str, st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    """Removes Jarvis' local copy of a chat (messages, downloads in the WhatsApp folder) – not on WhatsApp."""
    return await _svc(st).forget_chat(jid, p.label)


# ------------------------------------------------------------------ files
@router.get("/files")
async def files(
    q: str = Query(default="", max_length=200),
    chat: str | None = None,
    type: str | None = None,
    limit: int = Query(default=60, ge=1, le=200),
    st: AppState = StateDep,
    p: Principal = Owner,
) -> list[dict[str, Any]]:
    svc = _svc(st)
    rows = await svc.repo.search_files(q, chat_jid=chat, category=type, limit=limit, excluded=svc.excluded)
    return [{**r, "size_label": human_size(r["size"])} for r in rows]


@router.get("/files/{file_id}/content")
async def file_content(
    file_id: str, download: bool = False, st: AppState = StateDep, p: Principal = Owner
) -> FileResponse:
    try:
        fid = uuid.UUID(file_id)
        f, path = await _svc(st).file_for_api(fid)
    except ValueError as e:
        raise HTTPException(400, "invalid id") from e
    except ToolUserError as e:
        raise HTTPException(404, str(e)) from e
    inline = not download and f.category in ("image", "video", "audio")
    return FileResponse(
        path,
        media_type=f.mime if inline else "application/octet-stream",
        filename=f.file_name,
        content_disposition_type="inline" if inline else "attachment",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, max-age=3600",
        },
    )


class SaveBody(BaseModel):
    destination: str = Field(min_length=1, max_length=4096)
    device: str | None = Field(default=None, max_length=120)
    allow_outside: bool = False


@router.post("/files/{file_id}/save")
async def save_file(file_id: str, body: SaveBody, st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    svc = _svc(st)
    try:
        f = await svc.repo.file(uuid.UUID(file_id))
    except ValueError as e:
        raise HTTPException(400, "invalid id") from e
    if f is None:
        raise HTTPException(404, "not found")
    args: dict[str, Any] = {"messageId": f"{f.chat_jid}/{f.msg_id}", "destination": body.destination}
    if body.device:
        args["device"] = body.device
    if body.allow_outside:
        args["allow_outside"] = True
    return await _run(st, p, "whatsapp_save_attachment", args)


@router.get("/locations")
async def locations(st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    svc = _svc(st)
    pol = svc.policy()
    return {
        "download_dir": str(svc.download_dir()),
        "allowed": [{"path": str(a), "exists": a.is_dir()} for a in pol.allowed],
        "devices": [
            {"name": d["name"], "online": d["online"], "platform": d.get("platform")} for d in st.device_hub.describe()
        ],
    }


@router.post("/uploads", status_code=201)
async def upload(
    request: Request, name: str = Query(min_length=1, max_length=255), st: AppState = StateDep, p: Principal = Owner
) -> dict[str, Any]:
    """A file from the desktop app to send (raw body). Checked like every outgoing file; kept 24 h."""
    svc = _svc(st)
    limit = min(svc.max_upload(), UPLOAD_LIMIT_HARD)
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, f"Datei zu groß – erlaubt sind {human_size(limit)}.")
    token = secrets.token_hex(12)
    folder = svc.staging_dir / "uploads" / token
    folder.mkdir(parents=True, mode=0o700)
    target = folder / safe_file_name(name)
    size = 0
    head = b""
    try:
        with target.open("xb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"Datei zu groß – erlaubt sind {human_size(limit)}.")
                if len(head) < 4096:
                    head += chunk[: 4096 - len(head)]
                await asyncio.to_thread(out.write, chunk)
        det = sniff(head, name, path=target)
        if det.executable:
            raise HTTPException(415, det.reason or "Programmdateien werden nicht versendet.")
        if size == 0:
            raise HTTPException(400, "Die Datei ist leer.")
        if det.kind is not None:
            fixed = folder / safe_file_name(name, det.kind.ext)
            if fixed != target:
                target.rename(fixed)
                target = fixed
    except BaseException:
        for f in folder.glob("*"):
            f.unlink(missing_ok=True)
        folder.rmdir()
        raise
    target.chmod(0o600)
    await audit(p.label, "whatsapp.upload", details={"size": size, "type": det.category})
    return {
        "ref": f"staged:{token}",
        "name": target.name,
        "size": size,
        "size_label": human_size(size),
        "type": det.label,
        "category": det.category,
    }


# ------------------------------------------------------------------ sending
class SendBody(BaseModel):
    chat: str = Field(min_length=1, max_length=200)
    text: str = Field(default="", max_length=65536)
    files: list[str] = Field(default_factory=list, max_length=10)
    caption: str = Field(default="", max_length=4096)
    as_document: bool = False
    reply_to: str | None = Field(default=None, max_length=128)
    force: bool = False


@router.post("/send")
async def send(body: SendBody, st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    """From the app's composer: creates the same approval the agent would (recipient, files, text) – Sir confirms
    it in the approval card that opens right away."""
    _svc(st)
    args: dict[str, Any]
    if body.reply_to and not body.files:
        tool, args = "whatsapp_reply_to_message", {"messageId": f"{body.chat}/{body.reply_to}", "text": body.text}
    elif not body.files:
        tool, args = "whatsapp_send_message", {"contactId": body.chat, "text": body.text}
    elif len(body.files) == 1 and not body.text:
        tool = "whatsapp_send_document" if body.as_document else "whatsapp_send_file"
        args = {"contactId": body.chat, "filePath": body.files[0]}
        if body.caption and tool == "whatsapp_send_document":
            args["caption"] = body.caption
        elif body.caption:
            tool, args = (
                "whatsapp_send_multiple_files",
                {"contactId": body.chat, "filePaths": body.files, "caption": body.caption},
            )
    else:
        tool, args = "whatsapp_send_multiple_files", {"contactId": body.chat, "filePaths": body.files}
        if body.text:
            args["text"] = body.text
        if body.caption:
            args["caption"] = body.caption
    if body.force:
        args["force"] = True
    return await _run(st, p, tool, args)


@router.get("/sends")
async def sends(
    limit: int = Query(default=30, ge=1, le=200), st: AppState = StateDep, p: Principal = Owner
) -> list[dict[str, Any]]:
    return await _svc(st).repo.recent_sends(limit)


# ------------------------------------------------------------------ suggestions & retention
@router.get("/suggestions")
async def suggestions(status: str = "pending", st: AppState = StateDep, p: Principal = Owner) -> list[dict[str, Any]]:
    svc = _svc(st)
    rows = await svc.repo.suggestions(None if status == "all" else status)
    names = await svc.repo.names({r["chat_jid"] for r in rows})
    return [{**r, "chat_name": names.get(r["chat_jid"], "")} for r in rows if r["chat_jid"] not in svc.excluded]


class DecisionBody(BaseModel):
    decision: Literal["accept", "dismiss"]


@router.post("/suggestions/{sid}")
async def decide(sid: str, body: DecisionBody, st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    svc = _svc(st)
    try:
        suggestion_id = uuid.UUID(sid)
    except ValueError as e:
        raise HTTPException(400, "invalid id") from e
    try:
        if body.decision == "accept":  # Sir himself, at his paired desktop: that is the approval
            return await svc.apply_suggestion(suggestion_id, p.label)
        return await svc.dismiss_suggestion(suggestion_id, p.label)
    except ToolUserError as e:
        raise _bad(e) from e


class PurgeBody(BaseModel):
    older_than_days: int = Field(ge=0, le=3650)


@router.post("/purge")
async def purge(body: PurgeBody, st: AppState = StateDep, p: Principal = Owner) -> dict[str, Any]:
    """Deletes stored messages and downloads older than N days (0 = everything) from Jarvis – not from WhatsApp."""
    cutoff = datetime.now(UTC) - timedelta(days=body.older_than_days)
    return await _svc(st).purge(cutoff, p.label)
