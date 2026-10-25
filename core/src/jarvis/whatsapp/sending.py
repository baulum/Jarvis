"""Sending through WhatsApp – prepared before Sir is asked, executed exactly once after he approved.

1. **prepare** (runs before the approval, see ``ToolSpec.prepare``): the recipient is resolved (candidates instead
   of guesses), every file is located (server path, a device via its Device Agent, an upload from the desktop app or
   a stored WhatsApp file), checked (real type from the content, size, no programs) and copied into a staging folder
   under its SHA-256. A send identical to one of the last 10 minutes is refused unless Sir explicitly wants it again.
2. **approval**: always – whatever the approval mode. It shows recipient, file names, types, sizes, origin and
   the full text (``summarize``). Forwarding a received WhatsApp file is flagged.
3. **execute**: the staged files must still have the approved hashes; a send ledger keyed by the approval makes a
   second execution impossible, and the WhatsApp message ids are derived from it (the sidecar returns the first
   result for a repeated key). Afterwards Jarvis waits briefly for WhatsApp's server acknowledgement and reports
   the real status.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jarvis.events import bus
from jarvis.security.audit import audit
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext
from jarvis.whatsapp.files import (
    OUTGOING_AUDIO_EXTS,
    OUTGOING_IMAGE_EXTS,
    OUTGOING_VIDEO_EXTS,
    FileRejected,
    human_size,
    safe_file_name,
    sha256_file,
    sniff_path,
)
from jarvis.whatsapp.repo import STATUS_ORDER
from jarvis.whatsapp.service import WhatsAppService, mask_phone
from jarvis.whatsapp.sidecar import SidecarError
from jarvis.whatsapp.transfer import TransferError, pull_file

DUPLICATE_WINDOW = timedelta(minutes=10)
ACK_WAIT_S = 20.0
MAX_FILES = 10
MEDIA_LIMITS = {"image": 16 * 1024 * 1024, "audio": 16 * 1024 * 1024, "video": 64 * 1024 * 1024}
TOOL_KINDS = {
    "whatsapp_send_message": "text",
    "whatsapp_reply_to_message": "text",
    "whatsapp_send_file": "auto",
    "whatsapp_send_image": "image",
    "whatsapp_send_video": "video",
    "whatsapp_send_audio": "audio",
    "whatsapp_send_document": "document",
    "whatsapp_send_multiple_files": "auto",
}
DELIVERY_LABEL = {
    "pending": "gesendet, Bestätigung des WhatsApp-Servers steht noch aus",
    "sent": "gesendet, Bestätigung des WhatsApp-Servers steht noch aus",
    "server_ack": "vom WhatsApp-Server angenommen",
    "delivered": "zugestellt",
    "read": "zugestellt und gelesen",
    "played": "zugestellt und abgespielt",
    "error": "WhatsApp meldet einen Fehler",
}


def message_id_for(idempotency_key: str) -> str:
    """Same derivation as the sidecar (connection.ts ``messageIdFor``): the id of a send is known in advance."""
    return "3EB0" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:18].upper()


def fingerprint(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


class Sender:
    def __init__(self, svc: WhatsAppService) -> None:
        self.svc = svc

    # ------------------------------------------------------------------ files
    def _send_as(self, want: str, ext: str, category: str, size: int, name: str) -> str:
        if want == "document":
            return "document"
        if want == "auto":
            if ext in OUTGOING_IMAGE_EXTS and size <= MEDIA_LIMITS["image"]:
                return "image"
            if ext in OUTGOING_VIDEO_EXTS and size <= MEDIA_LIMITS["video"]:
                return "video"
            if ext in OUTGOING_AUDIO_EXTS and ext != "wav" and size <= MEDIA_LIMITS["audio"]:
                return "audio"
            return "document"
        allowed = {"image": OUTGOING_IMAGE_EXTS, "video": OUTGOING_VIDEO_EXTS, "audio": OUTGOING_AUDIO_EXTS - {"wav"}}[
            want
        ]
        label = {
            "image": "Bild (JPG, PNG, WebP)",
            "video": "Video (MP4, 3GP, MOV)",
            "audio": "Audio (OGG, MP3, M4A, AMR)",
        }[want]
        if ext not in allowed:
            raise ToolUserError(
                f"„{name}“ ist kein unterstütztes {label} – mit whatsapp_send_document als Dokument senden."
            )
        if size > MEDIA_LIMITS[want]:
            raise ToolUserError(
                f"„{name}“ ist {human_size(size)} groß – WhatsApp nimmt als {label.split(' ')[0]} höchstens "
                f"{human_size(MEDIA_LIMITS[want])}. Als Dokument senden?"
            )
        return want

    async def stage(self, ref: str, *, device: str | None, want: str) -> dict[str, Any]:
        svc = self.svc
        origin: str
        forwarded_from: str | None = None
        cleanup: Path | None = None
        display: str
        ref = (ref or "").strip()
        if not ref:
            raise ToolUserError("Dateipfad fehlt.")
        if ref.startswith("staged:"):
            token = ref.removeprefix("staged:")
            folder = svc.staging_dir / "uploads" / token
            files = [p for p in folder.iterdir() if p.is_file()] if token.isalnum() and folder.is_dir() else []
            if len(files) != 1:
                raise ToolUserError("Die hochgeladene Datei ist nicht mehr vorhanden – bitte erneut hochladen.")
            src, display, origin = files[0], files[0].name, "aus der Desktop-App hochgeladen"
        elif ref.startswith("wa-file:"):
            try:
                fid = uuid.UUID(ref.removeprefix("wa-file:"))
            except ValueError as e:
                raise ToolUserError("Ungültige Datei-Referenz.") from e
            f = await svc.repo.file(fid)
            if f is None or f.location != "server" or not Path(f.path).is_file() or f.chat_jid in svc.excluded:
                raise ToolUserError("Die WhatsApp-Datei liegt nicht (mehr) auf dem Server.")
            src, display = Path(f.path), f.file_name
            forwarded_from = f.chat_jid
            origin = f"empfangene WhatsApp-Datei aus dem Chat mit {f.chat_name or 'unbekannt'}"
        elif device:
            hub = svc.state.device_hub

            async def progress(done: int, total: int) -> None:
                await bus.publish("whatsapp.transfer", direction="from_device", device=device, done=done, total=total)

            try:
                pulled = await pull_file(hub, device, ref, svc.tmp_dir, max_bytes=svc.max_upload(), progress=progress)
            except (TransferError, FileRejected) as e:
                raise ToolUserError(str(e)) from e
            except Exception as e:
                raise ToolUserError(
                    f"Die Datei konnte nicht von {device} geholt werden: {getattr(e, 'message', e)}"
                ) from e
            src, display, cleanup = pulled.path, pulled.name, pulled.path
            origin = f"Gerät {device}: {pulled.remote_path}"
        else:
            policy = svc.policy()
            try:
                path = policy.resolve(ref)
            except FileRejected as e:
                raise ToolUserError(str(e)) from e
            if not path.is_file():
                raise ToolUserError(
                    f"Auf dem Jarvis-Server gibt es „{path}“ nicht. Liegt die Datei auf einem anderen Rechner, "
                    "device angeben (whatsapp_search_files bzw. device_files_search finden sie)."
                )
            if not policy.is_allowed(path):
                raise ToolUserError(
                    f"„{path}“ liegt außerhalb der freigegebenen Ordner – Jarvis versendet nur Dateien aus "
                    f"{', '.join(policy.describe()[:6])} … (weitere Ordner unter Einstellungen › WhatsApp)."
                )
            src, display, origin = path, path.name, f"Jarvis-Server: {path}"
        try:
            size = src.stat().st_size
            if size == 0:
                raise ToolUserError(f"„{display}“ ist leer.")
            if size > svc.max_upload():
                raise ToolUserError(
                    f"„{display}“ ist {human_size(size)} groß – das Versandlimit liegt bei "
                    f"{human_size(svc.max_upload())}."
                )
            det = sniff_path(src, display)
            if det.executable:
                raise ToolUserError(f"„{display}“: {det.reason or 'Programmdateien werden nicht versendet.'}")
            ext = det.kind.ext if det.kind else (display.rsplit(".", 1)[-1].lower() if "." in display else "")
            name = safe_file_name(display, det.kind.ext if det.kind else "")
            send_as = self._send_as(want, det.kind.ext if det.kind else "", det.category, size, name)
            digest = await asyncio.to_thread(sha256_file, src)
            folder = svc.staging_dir / digest[:32]
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            staged = folder / name
            if not staged.is_file() or sha256_file(staged) != digest:
                await asyncio.to_thread(shutil.copyfile, src, staged)
                staged.chmod(0o600)
        finally:
            if cleanup is not None:
                cleanup.unlink(missing_ok=True)
        info: dict[str, Any] = {
            "name": name,
            "ext": ext,
            "mime": det.mime,
            "label": det.label,
            "category": det.category,
            "size": size,
            "size_label": human_size(size),
            "sha256": digest,
            "staged": str(staged),
            "send_as": send_as,
            "origin": origin,
        }
        if send_as == "audio" and ext == "ogg":
            info["ptt"] = True  # an Opus/OGG file goes out as a voice note
        if det.mismatch:
            info["warning"] = "Dateiendung passte nicht zum Inhalt – korrigiert"
        if forwarded_from:
            info["forwarded_from"] = forwarded_from
        return info

    # ------------------------------------------------------------------ prepare (before the approval)
    async def prepare(self, tool: str, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        svc = self.svc
        clean = {k: v for k, v in args.items() if k != "resolved"}  # never trust a caller-supplied resolution
        want = TOOL_KINDS[tool]
        quoted: dict[str, Any] | None = None
        if tool == "whatsapp_reply_to_message":
            msg = await svc.find_message(str(clean["messageId"]), ctx.session_id)
            names = await svc.repo.names([msg.chat_jid, msg.sender_jid])
            target: dict[str, Any] = {
                "jid": msg.chat_jid,
                "label": names.get(msg.chat_jid) or msg.chat_jid,
                "is_group": msg.chat_jid.endswith("@g.us"),
            }
            q: dict[str, Any] = {"id": msg.msg_id, "fromMe": msg.from_me, "text": (msg.text or "")[:300]}
            if msg.chat_jid.endswith("@g.us") and not msg.from_me:
                q["participant"] = msg.sender_jid
            q["author"] = "Sir" if msg.from_me else (names.get(msg.sender_jid) or msg.sender_name or "Kontakt")
            quoted = q
        else:
            cand = await svc.resolve_chat(str(clean["contactId"]), for_send=True)
            target = {"jid": cand.jid, "label": cand.label, "is_group": cand.is_group}
        refs: list[str] = []
        if tool == "whatsapp_send_multiple_files":
            refs = [str(p) for p in clean.get("filePaths") or []]
            if not refs or len(refs) > MAX_FILES:
                raise ToolUserError(f"1 bis {MAX_FILES} Dateien angeben.")
        elif "filePath" in clean:
            refs = [str(clean["filePath"])]
        files = [await self.stage(r, device=clean.get("device"), want=want) for r in refs]
        text = str(clean.get("text") or "").strip()
        caption = str(clean.get("caption") or "").strip()
        if want == "text" and not text:
            raise ToolUserError("Die Nachricht ist leer.")
        warnings: list[str] = []
        for f in files:
            if f.get("forwarded_from") and f["forwarded_from"] != target["jid"]:
                warnings.append(f"Weiterleitung: „{f['name']}“ ist eine empfangene WhatsApp-Datei ({f['origin']}).")
            if f.get("warning"):
                warnings.append(f"„{f['name']}“: {f['warning']}.")
        if ctx.session_id and await svc.state.executor.is_tainted(ctx.session_id):
            warnings.append(
                "Das Gespräch enthält fremde Inhalte (z. B. WhatsApp-Nachrichten) – nur freigeben, wenn Sie den "
                "Versand selbst wollen."
            )
        kind = "text" if not files else ("files" if len(files) > 1 else files[0]["send_as"])
        fp = fingerprint(target["jid"], kind, text, caption, *(f["sha256"] for f in files))
        recent = await svc.repo.recent_send(fp, DUPLICATE_WINDOW)
        if recent is not None and not clean.get("force"):
            minutes = max(1, int((datetime.now(UTC) - recent.created_at).total_seconds() // 60))
            raise ToolUserError(
                f"Genau diese Nachricht ging vor {minutes} Minute(n) bereits an {target['label']} "
                f"(Status: {DELIVERY_LABEL.get(recent.status, recent.status)}). Nicht erneut senden – nur wenn Sir es "
                "ausdrücklich wünscht, mit force=true."
            )
        resolved: dict[str, Any] = {
            "chat_id": target["jid"],
            "recipient": target["label"],
            "is_group": target["is_group"],
            "phone": mask_phone(target["jid"]),
            "kind": kind,
            "files": files,
            "text": text,
            "caption": caption,
            "fingerprint": fp,
            "warnings": warnings,
        }
        if quoted:
            resolved["quoted"] = quoted
        return {**clean, "resolved": resolved}

    # ------------------------------------------------------------------ what Sir sees
    @staticmethod
    def summarize(args: dict[str, Any]) -> str:
        r = args.get("resolved") or {}
        if not r:
            return f"WhatsApp an {args.get('contactId', '?')}"
        head = f"WhatsApp an {r.get('recipient')}{' (Gruppe)' if r.get('is_group') else ''}"
        if r.get("phone"):
            head += f" · {r['phone']}"
        lines = [head]
        if r.get("quoted"):
            q = r["quoted"]
            lines.append(f"Antwort auf {q.get('author', '')}: „{str(q.get('text') or '…')[:120]}“")
        for f in r.get("files") or []:
            as_ = {
                "image": "als Foto",
                "video": "als Video",
                "audio": "als Sprachnachricht" if f.get("ptt") else "als Audio",
                "document": "als Dokument",
            }.get(f.get("send_as"), "")
            lines.append(f"Datei: {f['name']} – {f['label']}, {f['size_label']} {as_} (Quelle: {f['origin']})")
        if r.get("caption"):
            lines.append(f"Bildunterschrift: „{r['caption']}“")
        if r.get("text"):
            lines.append(f"Text: „{r['text']}“")
        for w in r.get("warnings") or []:
            lines.append(f"⚠ {w}")
        return "\n".join(lines)

    @staticmethod
    def audit_summary(args: dict[str, Any]) -> str:
        r = args.get("resolved") or {}
        files = r.get("files") or []
        return (
            f"WhatsApp an {r.get('recipient', '?')}: {len(files)} Datei(en)"
            f"{' (' + ', '.join(str(f.get('label')) for f in files) + ')' if files else ''}, "
            f"Text {len(r.get('text') or '') + len(r.get('caption') or '')} Zeichen"
        )

    # ------------------------------------------------------------------ execute (after the approval)
    async def execute(self, tool: str, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        svc = self.svc
        r = args.get("resolved")
        if not isinstance(r, dict) or not r.get("chat_id"):
            raise ToolUserError("Der Versand wurde nicht vorbereitet – bitte erneut anstoßen.")
        svc.require_connected()
        staging = svc.staging_dir.resolve()
        for f in r.get("files") or []:
            p = Path(str(f.get("staged") or ""))
            if not p.resolve().is_relative_to(staging) or not p.is_file():
                raise ToolUserError(f"„{f.get('name')}“ ist nicht mehr bereit – bitte den Versand neu anstoßen.")
            if await asyncio.to_thread(sha256_file, p) != f.get("sha256"):
                raise ToolUserError(f"„{f.get('name')}“ wurde nach der Freigabe verändert – Versand abgebrochen.")
        basis = ctx.idempotency_key or f"{r['fingerprint']}:{ctx.session_id or ctx.principal.label}"
        send_key = "wa:" + hashlib.sha256(basis.encode()).hexdigest()[:40]
        files = r.get("files") or []
        parts: list[dict[str, Any]] = []
        quoted = r.get("quoted")
        if not files:
            parts.append({"kind": "text", "text": r.get("text") or "", **({"quoted": quoted} if quoted else {})})
        else:
            if r.get("text"):
                parts.append({"kind": "text", "text": r["text"]})
            for i, f in enumerate(files):
                part: dict[str, Any] = {
                    "kind": f["send_as"],
                    "filePath": f["staged"],
                    "fileName": f["name"],
                    "mimetype": f["mime"],
                    "ptt": bool(f.get("ptt")),
                }
                if i == 0 and r.get("caption") and f["send_as"] in ("image", "video", "document"):
                    part["caption"] = r["caption"]
                parts.append(part)
        ids = [message_id_for(f"{send_key}:{i}") for i in range(len(parts))]
        row, created = await svc.repo.claim_send(
            send_key=send_key,
            fingerprint=r["fingerprint"],
            chat_jid=r["chat_id"],
            chat_name=str(r.get("recipient") or ""),
            kind=r.get("kind") or "text",
            files=[{"name": f["name"], "type": f["label"], "size": f["size"], "sha256": f["sha256"]} for f in files],
            text_len=len(r.get("text") or "") + len(r.get("caption") or ""),
            actor=ctx.principal.label,
            approval_id=ctx.approval_id,
            msg_ids=ids,
        )
        if not created:
            return {
                "status": DELIVERY_LABEL.get(row.status, row.status),
                "recipient": r.get("recipient"),
                "message_ids": row.msg_ids,
                "note": "Diese Sendung wurde bereits ausgeführt – nichts doppelt gesendet.",
            }
        waiters = {mid: asyncio.Event() for mid in ids}
        svc._ack_waiters.update(waiters)
        sent: list[str] = []
        try:
            for i, part in enumerate(parts):
                await bus.publish(
                    "whatsapp.send_progress",
                    send_key=send_key,
                    stage="uploading" if part["kind"] != "text" else "sending",
                    index=i + 1,
                    total=len(parts),
                    file=part.get("fileName"),
                )
                try:
                    res = await svc.sidecar.request(
                        "send",
                        {
                            **part,
                            "jid": r["chat_id"],
                            "idempotencyKey": f"{send_key}:{i}",
                            "maxBytes": svc.max_upload(),
                        },
                        timeout=900,
                    )
                except SidecarError as e:
                    await svc.repo.update_send(send_key, status="failed", error=e.message)
                    await bus.publish("whatsapp.send_progress", send_key=send_key, stage="failed", error=e.message)
                    done = f" ({len(sent)} von {len(parts)} Teilen waren bereits gesendet)" if sent else ""
                    raise ToolUserError(f"WhatsApp-Versand fehlgeschlagen{done}: {e.message}") from e
                mid = str(res.get("messageId") or ids[i])
                sent.append(mid)
                if res.get("message"):
                    await svc.ingest([res["message"]], "sent")
                await svc.repo.update_send(send_key, status="sent", msg_ids=[mid])
            try:
                await asyncio.wait_for(asyncio.gather(*(w.wait() for w in waiters.values())), ACK_WAIT_S)
            except TimeoutError:
                pass
        finally:
            for mid in ids:
                svc._ack_waiters.pop(mid, None)
        final = await svc.repo.send_by_key(send_key)
        status = final.status if final else "sent"
        for mid in sent:  # receipts that arrived before our own copy was stored
            if status in STATUS_ORDER:
                await svc.repo.apply_updates(
                    [{"chatJid": r["chat_id"], "id": mid, "fromMe": True, "status": STATUS_ORDER[status]}]
                )
        await bus.publish("whatsapp.send_progress", send_key=send_key, stage="done", status=status)
        await audit(
            ctx.principal.label,
            "whatsapp.send",
            target=send_key,
            details={"kind": r.get("kind"), "files": len(files), "status": status},
        )
        out: dict[str, Any] = {
            "status": DELIVERY_LABEL.get(status, status),
            "confirmed_by_server": status in ("server_ack", "delivered", "read", "played"),
            "recipient": r.get("recipient"),
            "message_ids": sent,
        }
        if files:
            out["files"] = [f["name"] for f in files]
        return out
