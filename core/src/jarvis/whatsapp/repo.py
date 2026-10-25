"""Persistence of the WhatsApp connector (tables ``whatsapp_*``, migration b6c7d8e9f0a1).

Every write is idempotent: a message is identified by (chat, message id), so live delivery, history sync, a
replayed event after a reconnect and the echo of our own send all end in the same row. Delivery status only
moves forward (pending → server_ack → delivered → read → played). The code is plain SQLAlchemy without dialect
specific upserts, so the unit tests run it unchanged on SQLite; on PostgreSQL the full-text search uses the GIN
indexes of the migration.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from jarvis.db.models import (
    WhatsAppChat,
    WhatsAppContact,
    WhatsAppFile,
    WhatsAppMessage,
    WhatsAppSend,
    WhatsAppSuggestion,
)

STATUS_NAMES = ["error", "pending", "server_ack", "delivered", "read", "played"]
STATUS_ORDER = {name: i for i, name in enumerate(STATUS_NAMES)}
SEND_ORDER = {"pending": 0, "uploading": 1, "sent": 2, "server_ack": 3, "delivered": 4, "read": 5, "played": 6}
Scope = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def status_name(code: int | None) -> str:
    if code is None or not 0 <= int(code) < len(STATUS_NAMES):
        return ""
    return STATUS_NAMES[int(code)]


def _later(old: str, new: str) -> bool:
    """Is ``new`` a step forward from ``old``? (an error after a delivery does not undo it)"""
    if not new:
        return False
    if not old:
        return True
    if new == "error":
        return STATUS_ORDER.get(old, 0) <= STATUS_ORDER["pending"]
    return STATUS_ORDER.get(new, 0) > STATUS_ORDER.get(old, 0)


def _ts(seconds: float | int | None) -> datetime:
    if not seconds:
        return datetime.now(UTC)
    return datetime.fromtimestamp(float(seconds), UTC)


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def message_dict(m: WhatsAppMessage, names: dict[str, str] | None = None) -> dict[str, Any]:
    sender = m.sender_name or (names or {}).get(m.sender_jid, "")
    out: dict[str, Any] = {
        "id": m.msg_id,
        "chat_jid": m.chat_jid,
        "from_me": m.from_me,
        "sender_jid": m.sender_jid,
        "sender_name": "Sir" if m.from_me else sender,
        "ts": m.ts.isoformat(),
        "kind": m.kind,
        "text": "" if m.deleted else m.text,
        "status": m.status,
        "deleted": m.deleted,
        "edited": m.edited_at is not None,
        "forwarded": m.forwarded,
        "quoted_id": m.quoted_msg_id,
    }
    if m.media:
        out["media"] = {k: v for k, v in m.media.items() if k != "thumbnail"}
        out["has_thumbnail"] = bool(m.media.get("thumbnail"))
    if m.extra:
        for key in ("location", "contact", "poll", "reactions", "system"):
            if m.extra.get(key):
                out[key] = m.extra[key]
    return out


def file_dict(f: WhatsAppFile) -> dict[str, Any]:
    return {
        "file_id": str(f.id),
        "chat_jid": f.chat_jid,
        "message_id": f.msg_id,
        "sender_name": f.sender_name,
        "chat_name": f.chat_name,
        "name": f.file_name,
        "location": f.location,
        "path": f.path,
        "mime": f.mime,
        "type": f.category,
        "size": f.size,
        "sha256": f.sha256,
        "duplicate_of": str(f.duplicate_of) if f.duplicate_of else None,
        "stored_at": f.created_at.isoformat(),
    }


def send_dict(s: WhatsAppSend) -> dict[str, Any]:
    return {
        "send_key": s.send_key,
        "chat_jid": s.chat_jid,
        "chat_name": s.chat_name,
        "kind": s.kind,
        "files": s.files,
        "text_len": s.text_len,
        "status": s.status,
        "message_ids": s.msg_ids,
        "error": s.error,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


def suggestion_dict(s: WhatsAppSuggestion) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "chat_jid": s.chat_jid,
        "message_id": s.msg_id,
        "kind": s.kind,
        "title": s.title,
        "detail": s.detail,
        "due_at": s.due_at.isoformat() if s.due_at else None,
        "status": s.status,
        "applied": s.applied,
        "created_at": s.created_at.isoformat(),
    }


class WhatsAppRepo:
    def __init__(self, scope: Scope | None = None) -> None:
        if scope is None:
            from jarvis.db import session_scope

            scope = session_scope
        self.scope = scope

    def _session(self) -> AbstractAsyncContextManager[AsyncSession]:
        return self.scope()

    @staticmethod
    def _postgres(s: AsyncSession) -> bool:
        return s.bind is not None and s.bind.dialect.name == "postgresql"

    # ------------------------------------------------------------------ chats & contacts
    async def upsert_chats(self, chats: Iterable[dict[str, Any]]) -> int:
        n = 0
        async with self._session() as s:
            for c in chats:
                jid = str(c.get("jid") or "")
                if not jid:
                    continue
                row = await s.get(WhatsAppChat, jid)
                if row is None:
                    row = WhatsAppChat(jid=jid, is_group=bool(c.get("isGroup")), meta={})
                    s.add(row)
                if c.get("name"):
                    row.name = str(c["name"])[:300]
                if c.get("unread") is not None:
                    row.unread_count = max(0, int(c["unread"]))
                if c.get("timestamp"):
                    ts = _ts(c["timestamp"])
                    if row.last_message_at is None or ts > row.last_message_at:
                        row.last_message_at = ts
                if c.get("archived") is not None:
                    row.archived = bool(c["archived"])
                if c.get("mutedUntil") is not None:
                    mu = int(c["mutedUntil"])
                    row.muted_until = _ts(mu / 1000 if mu > 10**11 else mu) if mu > 0 else None
                n += 1
        return n

    async def set_chat_name(self, jid: str, name: str) -> None:
        async with self._session() as s:
            row = await s.get(WhatsAppChat, jid)
            if row is not None and name:
                row.name = name[:300]

    async def upsert_contacts(self, contacts: Iterable[dict[str, Any]]) -> int:
        n = 0
        async with self._session() as s:
            for c in contacts:
                jid = str(c.get("jid") or "")
                if not jid:
                    continue
                row = await s.get(WhatsAppContact, jid)
                if row is None:
                    row = WhatsAppContact(jid=jid)
                    s.add(row)
                for src, dst in (
                    ("lid", "lid"),
                    ("name", "name"),
                    ("notify", "notify"),
                    ("verifiedName", "verified_name"),
                ):
                    if c.get(src):
                        setattr(row, dst, str(c[src])[:300])
                n += 1
        return n

    async def chat(self, jid: str) -> WhatsAppChat | None:
        async with self._session() as s:
            return await s.get(WhatsAppChat, jid)

    async def names(self, jids: Iterable[str] | None = None) -> dict[str, str]:
        """Best display name per JID: address book name, else chat/group name, else the contact's own name."""
        async with self._session() as s:
            cq = select(WhatsAppContact)
            hq = select(WhatsAppChat.jid, WhatsAppChat.name)
            if jids is not None:
                ids = list(set(jids))
                cq = cq.where(WhatsAppContact.jid.in_(ids))
                hq = hq.where(WhatsAppChat.jid.in_(ids))
            contacts = (await s.execute(cq)).scalars().all()
            chats = (await s.execute(hq)).all()
        out: dict[str, str] = {}
        for jid, name in chats:
            if name:
                out[jid] = name
        for c in contacts:
            best = c.name or out.get(c.jid) or c.verified_name or c.notify
            if best:
                out[c.jid] = best
        return out

    async def list_chats(
        self, q: str = "", limit: int = 50, include_archived: bool = True, excluded: Iterable[str] = ()
    ) -> list[dict[str, Any]]:
        async with self._session() as s:
            query = select(WhatsAppChat).order_by(WhatsAppChat.last_message_at.desc().nullslast()).limit(500)
            if not include_archived:
                query = query.where(WhatsAppChat.archived.is_(False))
            ex = list(excluded)
            if ex:
                query = query.where(WhatsAppChat.jid.not_in(ex))
            rows = (await s.execute(query)).scalars().all()
        names = await self.names([r.jid for r in rows])
        out: list[dict[str, Any]] = []
        needle = q.casefold().strip()
        for r in rows:
            name = names.get(r.jid) or r.name or ""
            if needle and needle not in name.casefold() and needle not in r.jid:
                continue
            out.append(
                {
                    "jid": r.jid,
                    "name": name,
                    "is_group": r.is_group,
                    "unread": r.unread_count,
                    "last_message_at": r.last_message_at.isoformat() if r.last_message_at else None,
                    "archived": r.archived,
                    "muted": bool(r.muted_until and r.muted_until > datetime.now(UTC)),
                }
            )
            if len(out) >= limit:
                break
        if out:
            last = await self._last_messages([str(c["jid"]) for c in out])
            for c in out:
                c["last_message"] = last.get(str(c["jid"]))
        return out

    async def _last_messages(self, jids: list[str]) -> dict[str, dict[str, Any]]:
        async with self._session() as s:
            sub = (
                select(WhatsAppMessage.chat_jid, func.max(WhatsAppMessage.ts).label("mts"))
                .where(WhatsAppMessage.chat_jid.in_(jids), WhatsAppMessage.kind != "system")
                .group_by(WhatsAppMessage.chat_jid)
                .subquery()
            )
            rows = (
                (
                    await s.execute(
                        select(WhatsAppMessage).join(
                            sub, and_(WhatsAppMessage.chat_jid == sub.c.chat_jid, WhatsAppMessage.ts == sub.c.mts)
                        )
                    )
                )
                .scalars()
                .all()
            )
        out: dict[str, dict[str, Any]] = {}
        for m in rows:
            out[m.chat_jid] = {
                "kind": m.kind,
                "text": ("" if m.deleted else m.text)[:160],
                "from_me": m.from_me,
                "ts": m.ts.isoformat(),
                "deleted": m.deleted,
                "file_name": (m.media or {}).get("fileName"),
            }
        return out

    async def contact_candidates(self) -> list[dict[str, Any]]:
        """Everything a name can refer to: contacts and chats (groups by subject) with all their names."""
        async with self._session() as s:
            contacts = (await s.execute(select(WhatsAppContact))).scalars().all()
            chats = (await s.execute(select(WhatsAppChat))).scalars().all()
        by_jid: dict[str, dict[str, Any]] = {}
        for c in chats:
            by_jid[c.jid] = {
                "jid": c.jid,
                "names": [c.name] if c.name else [],
                "is_group": c.is_group,
                "last_ts": c.last_message_at.timestamp() if c.last_message_at else 0.0,
            }
        for ct in contacts:
            entry = by_jid.setdefault(ct.jid, {"jid": ct.jid, "names": [], "is_group": False, "last_ts": 0.0})
            for n in (ct.name, ct.verified_name, ct.notify):
                if n and n not in entry["names"]:
                    entry["names"].append(n)
        return list(by_jid.values())

    async def contacts(self, q: str = "", limit: int = 200) -> list[dict[str, Any]]:
        async with self._session() as s:
            rows = (await s.execute(select(WhatsAppContact))).scalars().all()
        needle = q.casefold().strip()
        out = []
        for c in rows:
            name = c.name or c.verified_name or c.notify
            if needle and not any(needle in (n or "").casefold() for n in (c.name, c.notify, c.verified_name)):
                continue
            out.append(
                {"jid": c.jid, "name": name, "saved_name": c.name, "own_name": c.notify, "business": c.verified_name}
            )
        out.sort(key=lambda x: (not x["saved_name"], (x["name"] or "").casefold()))
        return out[:limit]

    # ------------------------------------------------------------------ messages
    async def insert_messages(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Stores messages; returns the ones that were new. Existing rows only gain missing data."""
        if not rows:
            return []
        new: list[dict[str, Any]] = []
        async with self._session() as s:
            keys = {(r["chat_jid"], r["msg_id"]) for r in rows}
            existing: dict[tuple[str, str], WhatsAppMessage] = {}
            for chat_jid in {k[0] for k in keys}:
                ids = [k[1] for k in keys if k[0] == chat_jid]
                for i in range(0, len(ids), 500):
                    found = (
                        await s.execute(
                            select(WhatsAppMessage).where(
                                WhatsAppMessage.chat_jid == chat_jid, WhatsAppMessage.msg_id.in_(ids[i : i + 500])
                            )
                        )
                    ).scalars()
                    for m in found:
                        existing[(m.chat_jid, m.msg_id)] = m
            seen: set[tuple[str, str]] = set()
            for r in rows:
                key = (r["chat_jid"], r["msg_id"])
                if key in seen:
                    continue
                seen.add(key)
                row = existing.get(key)
                if row is None:
                    s.add(WhatsAppMessage(**r))
                    new.append(r)
                    continue
                if not row.media_ref and r.get("media_ref"):
                    row.media_ref = r["media_ref"]
                if not row.sender_name and r.get("sender_name"):
                    row.sender_name = r["sender_name"]
                if not row.media and r.get("media"):
                    row.media = r["media"]
                if _later(row.status, r.get("status") or ""):
                    row.status = r["status"]
            # chats: create missing ones and move their "last message" forward
            for chat_jid in {k[0] for k in seen}:
                chat = await s.get(WhatsAppChat, chat_jid)
                latest = max((_r["ts"] for _r in rows if _r["chat_jid"] == chat_jid), default=None)
                if chat is None:
                    chat = WhatsAppChat(jid=chat_jid, is_group=chat_jid.endswith("@g.us"), meta={}, unread_count=0)
                    s.add(chat)
                if latest is not None and (chat.last_message_at is None or latest > chat.last_message_at):
                    chat.last_message_at = latest
                unread_new = sum(
                    1
                    for _r in new
                    if _r["chat_jid"] == chat_jid
                    and not _r["from_me"]
                    and _r.get("source") == "live"
                    and _r.get("kind") != "system"
                )
                if unread_new:
                    chat.unread_count = (chat.unread_count or 0) + unread_new
        return new

    async def apply_updates(self, updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Status, edits, deletions and reactions; returns the changes that hit a stored message."""
        applied: list[dict[str, Any]] = []
        async with self._session() as s:
            for u in updates:
                row = (
                    await s.execute(
                        select(WhatsAppMessage).where(
                            WhatsAppMessage.chat_jid == u["chatJid"], WhatsAppMessage.msg_id == u["id"]
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    if u.get("status") is not None and u.get("fromMe"):
                        applied.append({**u, "stored": False})  # e.g. a send whose echo has not arrived yet
                    continue
                change: dict[str, Any] = {"chatJid": row.chat_jid, "id": row.msg_id, "fromMe": row.from_me}
                if u.get("status") is not None:
                    name = status_name(u["status"])
                    if _later(row.status, name):
                        row.status = name
                        change["status"] = name
                if u.get("edit"):
                    row.text = str(u["edit"].get("text") or "")[:65536]
                    row.edited_at = _ts(u["edit"].get("timestamp"))
                    change["edited"] = True
                if u.get("deleted"):
                    row.deleted = True
                    change["deleted"] = True
                if u.get("reaction"):
                    extra = dict(row.extra or {})
                    reactions = {r["sender"]: r for r in extra.get("reactions", []) if isinstance(r, dict)}
                    sender = str(u["reaction"].get("senderJid") or "")
                    emoji = str(u["reaction"].get("emoji") or "")
                    if emoji:
                        reactions[sender] = {"sender": sender, "emoji": emoji}
                    else:
                        reactions.pop(sender, None)
                    extra["reactions"] = list(reactions.values())
                    row.extra = extra
                    change["reaction"] = True
                if len(change) > 3:
                    applied.append(change)
        return applied

    async def messages(self, chat_jid: str, limit: int = 30, before: datetime | None = None) -> list[dict[str, Any]]:
        async with self._session() as s:
            q = select(WhatsAppMessage).where(WhatsAppMessage.chat_jid == chat_jid)
            if before is not None:
                q = q.where(WhatsAppMessage.ts < before)
            rows = (
                (await s.execute(q.order_by(WhatsAppMessage.ts.desc(), WhatsAppMessage.id.desc()).limit(limit)))
                .scalars()
                .all()
            )
        names = await self.names({r.sender_jid for r in rows})
        return [message_dict(r, names) for r in reversed(rows)]

    async def oldest_message(self, chat_jid: str) -> WhatsAppMessage | None:
        async with self._session() as s:
            return (
                await s.execute(
                    select(WhatsAppMessage)
                    .where(WhatsAppMessage.chat_jid == chat_jid)
                    .order_by(WhatsAppMessage.ts.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()

    async def message(self, msg_id: str, chat_jid: str | None = None) -> WhatsAppMessage | None:
        async with self._session() as s:
            q = select(WhatsAppMessage).where(WhatsAppMessage.msg_id == msg_id)
            if chat_jid:
                q = q.where(WhatsAppMessage.chat_jid == chat_jid)
            return (await s.execute(q.order_by(WhatsAppMessage.ts.desc()).limit(1))).scalar_one_or_none()

    async def set_media_ref(self, chat_jid: str, msg_id: str, media_ref: str) -> None:
        async with self._session() as s:
            await s.execute(
                update(WhatsAppMessage)
                .where(WhatsAppMessage.chat_jid == chat_jid, WhatsAppMessage.msg_id == msg_id)
                .values(media_ref=media_ref)
            )

    async def unread(
        self, limit_chats: int = 20, per_chat: int = 20, excluded: Iterable[str] = ()
    ) -> list[dict[str, Any]]:
        ex = list(excluded)
        async with self._session() as s:
            q = select(WhatsAppChat).where(WhatsAppChat.unread_count > 0)
            if ex:
                q = q.where(WhatsAppChat.jid.not_in(ex))
            chats = (
                (await s.execute(q.order_by(WhatsAppChat.last_message_at.desc().nullslast()).limit(limit_chats)))
                .scalars()
                .all()
            )
            out = []
            for c in chats:
                n = max(1, min(per_chat, c.unread_count))
                rows = (
                    (
                        await s.execute(
                            select(WhatsAppMessage)
                            .where(
                                WhatsAppMessage.chat_jid == c.jid,
                                WhatsAppMessage.from_me.is_(False),
                                WhatsAppMessage.kind != "system",
                            )
                            .order_by(WhatsAppMessage.ts.desc())
                            .limit(n)
                        )
                    )
                    .scalars()
                    .all()
                )
                out.append((c, list(reversed(rows))))
        names = await self.names([c.jid for c, _ in out] + [m.sender_jid for _, ms in out for m in ms])
        return [
            {
                "chat_jid": c.jid,
                "chat_name": names.get(c.jid) or c.name,
                "is_group": c.is_group,
                "unread": c.unread_count,
                "messages": [message_dict(m, names) for m in ms],
            }
            for c, ms in out
        ]

    async def mark_chat_read(self, chat_jid: str) -> list[dict[str, Any]]:
        """Resets the unread counter; returns the keys of the incoming messages to confirm to WhatsApp."""
        async with self._session() as s:
            chat = await s.get(WhatsAppChat, chat_jid)
            n = max(chat.unread_count if chat else 0, 1)
            rows = (
                (
                    await s.execute(
                        select(WhatsAppMessage)
                        .where(
                            WhatsAppMessage.chat_jid == chat_jid,
                            WhatsAppMessage.from_me.is_(False),
                            WhatsAppMessage.kind != "system",
                        )
                        .order_by(WhatsAppMessage.ts.desc())
                        .limit(min(n, 200))
                    )
                )
                .scalars()
                .all()
            )
            keys = [
                {
                    "chatJid": m.chat_jid,
                    "id": m.msg_id,
                    "fromMe": False,
                    **({"participant": m.sender_jid} if m.chat_jid.endswith("@g.us") else {}),
                }
                for m in rows
                if not m.read_by_me
            ]
            for m in rows:
                m.read_by_me = True
            if chat is not None:
                chat.unread_count = 0
        return keys

    async def search_messages(
        self,
        query: str,
        *,
        chat_jid: str | None = None,
        sender_jid: str | None = None,
        kinds: list[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        from_me: bool | None = None,
        limit: int = 30,
        excluded: Iterable[str] = (),
    ) -> list[dict[str, Any]]:
        q = query.strip()
        async with self._session() as s:
            stmt = select(WhatsAppMessage).where(WhatsAppMessage.deleted.is_(False))
            if q:
                text_match = WhatsAppMessage.text.ilike(_like(q), escape="\\")
                name_match = func.coalesce(WhatsAppMessage.media["fileName"].as_string(), "").ilike(
                    _like(q), escape="\\"
                )
                if self._postgres(s):
                    fts = func.to_tsvector("simple", WhatsAppMessage.text).op("@@")(func.plainto_tsquery("simple", q))
                    stmt = stmt.where(or_(fts, text_match, name_match))
                else:
                    stmt = stmt.where(or_(text_match, name_match))
            if chat_jid:
                stmt = stmt.where(WhatsAppMessage.chat_jid == chat_jid)
            if sender_jid:
                stmt = stmt.where(WhatsAppMessage.sender_jid == sender_jid)
            if kinds:
                stmt = stmt.where(WhatsAppMessage.kind.in_(kinds))
            if since:
                stmt = stmt.where(WhatsAppMessage.ts >= since)
            if until:
                stmt = stmt.where(WhatsAppMessage.ts <= until)
            if from_me is not None:
                stmt = stmt.where(WhatsAppMessage.from_me.is_(from_me))
            ex = list(excluded)
            if ex:
                stmt = stmt.where(WhatsAppMessage.chat_jid.not_in(ex))
            rows = (await s.execute(stmt.order_by(WhatsAppMessage.ts.desc()).limit(limit))).scalars().all()
        names = await self.names({r.sender_jid for r in rows} | {r.chat_jid for r in rows})
        out = []
        for r in rows:
            d = message_dict(r, names)
            d["chat_name"] = names.get(r.chat_jid, "")
            out.append(d)
        return out

    async def attachments(
        self, chat_jid: str | None, limit: int = 30, kinds: list[str] | None = None, excluded: Iterable[str] = ()
    ) -> list[dict[str, Any]]:
        media_kinds = kinds or ["image", "video", "audio", "voice", "document", "sticker"]
        async with self._session() as s:
            stmt = select(WhatsAppMessage).where(
                WhatsAppMessage.kind.in_(media_kinds), WhatsAppMessage.deleted.is_(False)
            )
            if chat_jid:
                stmt = stmt.where(WhatsAppMessage.chat_jid == chat_jid)
            ex = list(excluded)
            if ex:
                stmt = stmt.where(WhatsAppMessage.chat_jid.not_in(ex))
            rows = (await s.execute(stmt.order_by(WhatsAppMessage.ts.desc()).limit(limit))).scalars().all()
            stored = (
                (await s.execute(select(WhatsAppFile).where(WhatsAppFile.msg_id.in_([r.msg_id for r in rows]))))
                .scalars()
                .all()
                if rows
                else []
            )
        by_msg: dict[str, list[WhatsAppFile]] = {}
        for f in stored:
            by_msg.setdefault(f.msg_id, []).append(f)
        names = await self.names({r.sender_jid for r in rows} | {r.chat_jid for r in rows})
        out = []
        for r in rows:
            d = message_dict(r, names)
            d["chat_name"] = names.get(r.chat_jid, "")
            d["stored"] = [file_dict(f) for f in by_msg.get(r.msg_id, [])]
            d["downloadable"] = bool(r.media_ref)
            out.append(d)
        return out

    # ------------------------------------------------------------------ files
    async def add_file(self, **fields: Any) -> WhatsAppFile:
        async with self._session() as s:
            row = WhatsAppFile(**fields)
            s.add(row)
            await s.flush()
            return row

    async def file(self, file_id: uuid.UUID) -> WhatsAppFile | None:
        async with self._session() as s:
            return await s.get(WhatsAppFile, file_id)

    async def files_for_message(self, chat_jid: str, msg_id: str) -> list[WhatsAppFile]:
        async with self._session() as s:
            return list(
                (
                    await s.execute(
                        select(WhatsAppFile)
                        .where(WhatsAppFile.chat_jid == chat_jid, WhatsAppFile.msg_id == msg_id)
                        .order_by(WhatsAppFile.created_at)
                    )
                ).scalars()
            )

    async def file_by_hash(self, sha256: str, location: str = "server") -> WhatsAppFile | None:
        async with self._session() as s:
            return (
                await s.execute(
                    select(WhatsAppFile)
                    .where(
                        WhatsAppFile.sha256 == sha256,
                        WhatsAppFile.location == location,
                        WhatsAppFile.duplicate_of.is_(None),
                    )
                    .order_by(WhatsAppFile.created_at)
                    .limit(1)
                )
            ).scalar_one_or_none()

    async def set_excerpt(self, file_id: uuid.UUID, text: str) -> None:
        async with self._session() as s:
            row = await s.get(WhatsAppFile, file_id)
            if row is not None:
                row.text_excerpt = text[:20000]

    async def search_files(
        self,
        query: str = "",
        *,
        chat_jid: str | None = None,
        category: str | None = None,
        location: str | None = None,
        since: datetime | None = None,
        limit: int = 30,
        excluded: Iterable[str] = (),
    ) -> list[dict[str, Any]]:
        q = query.strip()
        async with self._session() as s:
            stmt = select(WhatsAppFile)
            if q:
                like = _like(q)
                plain = or_(
                    WhatsAppFile.file_name.ilike(like, escape="\\"),
                    WhatsAppFile.chat_name.ilike(like, escape="\\"),
                    WhatsAppFile.sender_name.ilike(like, escape="\\"),
                    WhatsAppFile.text_excerpt.ilike(like, escape="\\"),
                )
                if self._postgres(s):
                    doc = func.to_tsvector(
                        "simple",
                        WhatsAppFile.file_name
                        + " "
                        + WhatsAppFile.chat_name
                        + " "
                        + WhatsAppFile.sender_name
                        + " "
                        + WhatsAppFile.text_excerpt,
                    )
                    stmt = stmt.where(or_(doc.op("@@")(func.plainto_tsquery("simple", q)), plain))
                else:
                    stmt = stmt.where(plain)
            if chat_jid:
                stmt = stmt.where(WhatsAppFile.chat_jid == chat_jid)
            if category:
                stmt = stmt.where(WhatsAppFile.category == category)
            if location:
                stmt = stmt.where(WhatsAppFile.location == location)
            if since:
                stmt = stmt.where(WhatsAppFile.created_at >= since)
            ex = list(excluded)
            if ex:
                stmt = stmt.where(WhatsAppFile.chat_jid.not_in(ex))
            rows = (await s.execute(stmt.order_by(WhatsAppFile.created_at.desc()).limit(limit))).scalars().all()
        return [file_dict(f) for f in rows]

    async def delete_file_row(self, file_id: uuid.UUID) -> None:
        async with self._session() as s:
            await s.execute(delete(WhatsAppFile).where(WhatsAppFile.id == file_id))

    # ------------------------------------------------------------------ sends
    async def send_by_key(self, send_key: str) -> WhatsAppSend | None:
        async with self._session() as s:
            return await s.get(WhatsAppSend, send_key)

    async def recent_send(self, fingerprint: str, within: timedelta) -> WhatsAppSend | None:
        cutoff = datetime.now(UTC) - within
        async with self._session() as s:
            return (
                await s.execute(
                    select(WhatsAppSend)
                    .where(
                        WhatsAppSend.fingerprint == fingerprint,
                        WhatsAppSend.created_at >= cutoff,
                        WhatsAppSend.status != "failed",
                    )
                    .order_by(WhatsAppSend.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

    async def claim_send(self, **fields: Any) -> tuple[WhatsAppSend, bool]:
        """(row, created). An existing, not failed row with the same key is returned instead of a new one."""
        async with self._session() as s:
            row = await s.get(WhatsAppSend, fields["send_key"])
            if row is not None and row.status != "failed":
                return row, False
            if row is not None:
                row.status, row.error, row.msg_ids = "pending", None, []
                for k, v in fields.items():
                    setattr(row, k, v)
                row.created_at = datetime.now(UTC)
                return row, True
            row = WhatsAppSend(**fields)
            s.add(row)
            await s.flush()
            return row, True

    async def update_send(
        self, send_key: str, *, status: str | None = None, msg_ids: list[str] | None = None, error: str | None = None
    ) -> WhatsAppSend | None:
        async with self._session() as s:
            row = await s.get(WhatsAppSend, send_key)
            if row is None:
                return None
            if status and (status == "failed" or SEND_ORDER.get(status, 0) > SEND_ORDER.get(row.status, -1)):
                row.status = status
            if msg_ids is not None:
                row.msg_ids = list(dict.fromkeys([*row.msg_ids, *msg_ids]))
            if error is not None:
                row.error = error[:1000]
            row.updated_at = datetime.now(UTC)
            return row

    async def sends_for_message(self, msg_id: str) -> list[WhatsAppSend]:
        async with self._session() as s:
            rows = (
                (
                    await s.execute(
                        select(WhatsAppSend).where(WhatsAppSend.created_at >= datetime.now(UTC) - timedelta(days=7))
                    )
                )
                .scalars()
                .all()
            )
        return [r for r in rows if msg_id in (r.msg_ids or [])]

    async def recent_sends(self, limit: int = 30) -> list[dict[str, Any]]:
        async with self._session() as s:
            rows = (
                (await s.execute(select(WhatsAppSend).order_by(WhatsAppSend.created_at.desc()).limit(limit)))
                .scalars()
                .all()
            )
        return [send_dict(r) for r in rows]

    # ------------------------------------------------------------------ suggestions
    async def add_suggestion(self, **fields: Any) -> WhatsAppSuggestion:
        async with self._session() as s:
            row = WhatsAppSuggestion(**fields)
            s.add(row)
            await s.flush()
            return row

    async def suggestions(self, status: str | None = "pending", limit: int = 50) -> list[dict[str, Any]]:
        async with self._session() as s:
            q = select(WhatsAppSuggestion)
            if status:
                q = q.where(WhatsAppSuggestion.status == status)
            rows = (await s.execute(q.order_by(WhatsAppSuggestion.created_at.desc()).limit(limit))).scalars().all()
        return [suggestion_dict(r) for r in rows]

    async def suggestion(self, sid: uuid.UUID) -> WhatsAppSuggestion | None:
        async with self._session() as s:
            return await s.get(WhatsAppSuggestion, sid)

    async def decide_suggestion(
        self, sid: uuid.UUID, status: str, applied: dict[str, Any] | None = None, *, remind_at: datetime | None = None
    ) -> WhatsAppSuggestion | None:
        async with self._session() as s:
            row = await s.get(WhatsAppSuggestion, sid)
            if row is None or row.status != "pending":
                return row
            row.status = status
            row.decided_at = datetime.now(UTC)
            if applied is not None:
                row.applied = applied
            if remind_at is not None:
                row.remind_at = remind_at
            return row

    async def due_reminders(self, now: datetime) -> list[WhatsAppSuggestion]:
        async with self._session() as s:
            rows = (
                (
                    await s.execute(
                        select(WhatsAppSuggestion).where(
                            WhatsAppSuggestion.status == "accepted",
                            WhatsAppSuggestion.remind_at.is_not(None),
                            WhatsAppSuggestion.remind_at <= now,
                            WhatsAppSuggestion.reminded_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for r in rows:
                r.reminded_at = now
            return list(rows)

    # ------------------------------------------------------------------ retention
    async def purge_before(self, cutoff: datetime) -> list[str]:
        """Deletes messages, stored file records and finished sends older than ``cutoff``; returns server file
        paths that are no longer referenced (the caller removes them from disk)."""
        async with self._session() as s:
            files = (await s.execute(select(WhatsAppFile).where(WhatsAppFile.created_at < cutoff))).scalars().all()
            doomed = {f.id for f in files}
            keep_paths = {
                f.path
                for f in (await s.execute(select(WhatsAppFile).where(WhatsAppFile.created_at >= cutoff))).scalars()
            }
            paths = sorted({f.path for f in files if f.location == "server" and f.path not in keep_paths})
            if doomed:
                await s.execute(delete(WhatsAppFile).where(WhatsAppFile.id.in_(doomed)))
            await s.execute(delete(WhatsAppMessage).where(WhatsAppMessage.ts < cutoff))
            await s.execute(delete(WhatsAppSend).where(WhatsAppSend.created_at < cutoff))
            await s.execute(
                delete(WhatsAppSuggestion).where(
                    WhatsAppSuggestion.created_at < cutoff, WhatsAppSuggestion.status != "pending"
                )
            )
        return paths

    async def forget_chat(self, chat_jid: str) -> list[str]:
        """Removes a chat completely (messages, file records, suggestions); returns server paths to delete."""
        async with self._session() as s:
            files = (await s.execute(select(WhatsAppFile).where(WhatsAppFile.chat_jid == chat_jid))).scalars().all()
            paths = sorted({f.path for f in files if f.location == "server"})
            await s.execute(delete(WhatsAppFile).where(WhatsAppFile.chat_jid == chat_jid))
            await s.execute(delete(WhatsAppMessage).where(WhatsAppMessage.chat_jid == chat_jid))
            await s.execute(delete(WhatsAppSuggestion).where(WhatsAppSuggestion.chat_jid == chat_jid))
            await s.execute(delete(WhatsAppChat).where(WhatsAppChat.jid == chat_jid))
        return paths

    async def counts(self) -> dict[str, int]:
        async with self._session() as s:
            out = {}
            for name, model in (("chats", WhatsAppChat), ("messages", WhatsAppMessage), ("files", WhatsAppFile)):
                out[name] = int((await s.execute(select(func.count()).select_from(model))).scalar_one())
            out["unread_chats"] = int(
                (
                    await s.execute(select(func.count()).select_from(WhatsAppChat).where(WhatsAppChat.unread_count > 0))
                ).scalar_one()
            )
            out["pending_suggestions"] = int(
                (
                    await s.execute(
                        select(func.count())
                        .select_from(WhatsAppSuggestion)
                        .where(WhatsAppSuggestion.status == "pending")
                    )
                ).scalar_one()
            )
        return out
