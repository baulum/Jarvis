from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from jarvis.whatsapp.repo import WhatsAppRepo

CHAT = "4917000000001@s.whatsapp.net"
GROUP = "120363000000000001@g.us"


def row(mid: str, text: str = "hi", *, chat: str = CHAT, from_me: bool = False, ts: datetime | None = None,
        source: str = "live", kind: str = "text", **kw: Any) -> dict[str, Any]:  # fmt: skip
    return {
        "chat_jid": chat, "msg_id": mid, "from_me": from_me, "sender_jid": "me" if from_me else chat,
        "sender_name": "" if from_me else "Jonathan", "ts": ts or datetime(2026, 10, 25, 12, tzinfo=UTC),
        "kind": kind, "text": text, "quoted_msg_id": None, "status": "pending" if from_me else "", "forwarded": False,
        "media": {}, "media_ref": None, "extra": {}, "source": source, "read_by_me": from_me, **kw,
    }  # fmt: skip


async def test_messages_are_stored_once(db: Any) -> None:
    repo = WhatsAppRepo()
    new = await repo.insert_messages([row("A"), row("B"), row("A")])
    assert [r["msg_id"] for r in new] == ["A", "B"]
    # replay after a reconnect + history sync + duplicate in one batch: nothing new
    again = await repo.insert_messages([row("A", source="history"), row("B")])
    assert again == []
    msgs = await repo.messages(CHAT)
    assert [m["id"] for m in msgs] == ["A", "B"]
    chat = await repo.chat(CHAT)
    assert chat is not None and chat.unread_count == 2  # only the live incoming ones, counted once


async def test_existing_rows_gain_missing_media_ref_but_keep_content(db: Any) -> None:
    repo = WhatsAppRepo()
    await repo.insert_messages([row("M", "Bild", kind="image")])
    await repo.insert_messages([row("M", "anders", kind="image", media_ref="sealed", media={"mimetype": "image/jpeg"})])
    msg = await repo.message("M")
    assert msg is not None and msg.media_ref == "sealed" and msg.text == "Bild" and msg.media == {"mimetype": "image/jpeg"}


async def test_status_only_moves_forward_and_edits_deletes_reactions(db: Any) -> None:
    repo = WhatsAppRepo()
    await repo.insert_messages([row("OUT", "Hallo", from_me=True), row("IN", "Original")])
    await repo.apply_updates([{"chatJid": CHAT, "id": "OUT", "fromMe": True, "status": 4}])  # read
    await repo.apply_updates([{"chatJid": CHAT, "id": "OUT", "fromMe": True, "status": 3}])  # late "delivered"
    await repo.apply_updates([{"chatJid": CHAT, "id": "OUT", "fromMe": True, "status": 0}])  # error after read
    assert (await repo.message("OUT")).status == "read"  # type: ignore[union-attr]
    await repo.apply_updates([{"chatJid": CHAT, "id": "IN", "fromMe": False, "edit": {"text": "Korrigiert", "timestamp": 1_760_000_100}}])
    await repo.apply_updates([{"chatJid": CHAT, "id": "IN", "fromMe": False, "reaction": {"senderJid": "me", "emoji": "👍"}}])
    msgs = {m["id"]: m for m in await repo.messages(CHAT)}
    assert msgs["IN"]["text"] == "Korrigiert" and msgs["IN"]["edited"] and msgs["IN"]["reactions"] == [{"sender": "me", "emoji": "👍"}]
    await repo.apply_updates([{"chatJid": CHAT, "id": "IN", "fromMe": False, "reaction": {"senderJid": "me", "emoji": ""}}])
    await repo.apply_updates([{"chatJid": CHAT, "id": "IN", "fromMe": False, "deleted": True}])
    msgs = {m["id"]: m for m in await repo.messages(CHAT)}
    assert msgs["IN"]["deleted"] and msgs["IN"]["text"] == "" and "reactions" not in msgs["IN"]
    unknown = await repo.apply_updates([{"chatJid": CHAT, "id": "NOPE", "fromMe": True, "status": 2}])
    assert unknown == [{"chatJid": CHAT, "id": "NOPE", "fromMe": True, "status": 2, "stored": False}]


async def test_chats_contacts_names_and_unread(db: Any) -> None:
    repo = WhatsAppRepo()
    await repo.upsert_chats([{"jid": CHAT, "name": "Jonathan", "unread": 0, "timestamp": 1_760_000_000, "isGroup": False},
                             {"jid": GROUP, "name": "Projekt Team", "isGroup": True}])  # fmt: skip
    await repo.upsert_contacts([{"jid": CHAT, "name": "Jonathan Weber", "notify": "Jo"}])
    names = await repo.names([CHAT, GROUP])
    assert names == {CHAT: "Jonathan Weber", GROUP: "Projekt Team"}
    await repo.insert_messages([row("G1", "Meeting um 10?", chat=GROUP, sender_jid="4917000000002@s.whatsapp.net")])
    chats = await repo.list_chats()
    assert chats[0]["jid"] == GROUP and chats[0]["unread"] == 1 and chats[0]["last_message"]["text"] == "Meeting um 10?"
    unread = await repo.unread()
    assert unread[0]["chat_jid"] == GROUP and unread[0]["messages"][0]["text"] == "Meeting um 10?"
    keys = await repo.mark_chat_read(GROUP)
    assert keys == [{"chatJid": GROUP, "id": "G1", "fromMe": False, "participant": "4917000000002@s.whatsapp.net"}]
    assert await repo.unread() == []
    assert await repo.mark_chat_read(GROUP) == []  # already confirmed: not sent twice
    cands = {c["jid"]: c for c in await repo.contact_candidates()}
    assert cands[CHAT]["names"][:2] == ["Jonathan", "Jonathan Weber"] and "Jo" in cands[CHAT]["names"]
    assert (await repo.list_chats(excluded=[GROUP]))[0]["jid"] == CHAT


async def test_search_and_attachments(db: Any) -> None:
    repo = WhatsAppRepo()
    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    await repo.insert_messages([
        row("T1", "Die Rechnung kommt morgen", ts=t0),
        row("T2", "100% sicher_nicht", ts=t0 + timedelta(days=1)),
        row("D1", "Bitte prüfen", kind="document", media={"fileName": "Rechnung-0815.pdf", "mimetype": "application/pdf"},
            media_ref="x", ts=t0 + timedelta(days=2)),
        row("S1", "Rechnung", chat=GROUP, ts=t0 + timedelta(days=3)),
    ])  # fmt: skip
    hits = await repo.search_messages("rechnung")
    assert {h["id"] for h in hits} == {"T1", "D1", "S1"}
    assert {h["id"] for h in await repo.search_messages("rechnung", chat_jid=CHAT, since=t0 + timedelta(hours=1))} == {"D1"}
    assert [h["id"] for h in await repo.search_messages("rechnung", kinds=["document"])] == ["D1"]
    assert [h["id"] for h in await repo.search_messages("100%")] == ["T2"]  # LIKE wildcards are escaped
    assert await repo.search_messages("_nicht") != [] and await repo.search_messages("rechnung", excluded=[CHAT, GROUP]) == []
    att = await repo.attachments(CHAT)
    assert [a["id"] for a in att] == ["D1"] and att[0]["downloadable"] and att[0]["stored"] == []


async def test_files_sends_and_suggestions(db: Any) -> None:
    repo = WhatsAppRepo()
    f = await repo.add_file(chat_jid=CHAT, msg_id="D1", file_name="Rechnung.pdf", path="/x/Rechnung.pdf", sha256="a" * 64,
                            chat_name="Jonathan", sender_name="Jonathan", text_excerpt="Gesamtbetrag 499,00 EUR")  # fmt: skip
    assert (await repo.file_by_hash("a" * 64)).id == f.id  # type: ignore[union-attr]
    assert [x["name"] for x in await repo.search_files("499")] == ["Rechnung.pdf"]  # content index
    assert [x["name"] for x in await repo.search_files("jonathan")] == ["Rechnung.pdf"]
    assert await repo.search_files("nichts") == []

    s1, created = await repo.claim_send(send_key="k1", fingerprint="fp", chat_jid=CHAT, kind="text", msg_ids=["M1"])
    assert created
    s2, created2 = await repo.claim_send(send_key="k1", fingerprint="fp", chat_jid=CHAT, kind="text", msg_ids=["M1"])
    assert not created2 and s2.send_key == "k1"
    assert (await repo.recent_send("fp", timedelta(minutes=10))) is not None
    await repo.update_send("k1", status="delivered")
    await repo.update_send("k1", status="server_ack")  # never backwards
    assert (await repo.send_by_key("k1")).status == "delivered"  # type: ignore[union-attr]
    assert [s.send_key for s in await repo.sends_for_message("M1")] == ["k1"]
    await repo.update_send("k1", status="failed", error="weg")
    _, again = await repo.claim_send(send_key="k1", fingerprint="fp", chat_jid=CHAT, kind="text", msg_ids=["M2"])
    assert again and (await repo.send_by_key("k1")).msg_ids == ["M2"]  # type: ignore[union-attr]

    sug = await repo.add_suggestion(chat_jid=CHAT, kind="appointment", title="Termin Montag", detail="",
                                    due_at=datetime.now(UTC) + timedelta(days=1))  # fmt: skip
    assert [s["title"] for s in await repo.suggestions()] == ["Termin Montag"]
    remind = datetime.now(UTC) - timedelta(minutes=1)
    await repo.decide_suggestion(sug.id, "accepted", {"memory": {}}, remind_at=remind)
    assert await repo.suggestions() == []
    due = await repo.due_reminders(datetime.now(UTC))
    assert [d.id for d in due] == [sug.id]
    assert await repo.due_reminders(datetime.now(UTC)) == []  # only once


async def test_retention_purge(db: Any) -> None:
    repo = WhatsAppRepo()
    old = datetime.now(UTC) - timedelta(days=40)
    await repo.insert_messages([row("OLD", ts=old), row("NEW", ts=datetime.now(UTC))])
    f = await repo.add_file(chat_jid=CHAT, msg_id="OLD", file_name="a.pdf", path="/dl/a.pdf", sha256="b" * 64)
    async with repo.scope() as s:
        from jarvis.db.models import WhatsAppFile

        (await s.get(WhatsAppFile, f.id)).created_at = old  # type: ignore[union-attr]
    paths = await repo.purge_before(datetime.now(UTC) - timedelta(days=30))
    assert paths == ["/dl/a.pdf"]
    assert [m["id"] for m in await repo.messages(CHAT)] == ["NEW"]
    assert await repo.counts() == {"chats": 1, "messages": 1, "files": 0, "unread_chats": 1, "pending_suggestions": 0}
