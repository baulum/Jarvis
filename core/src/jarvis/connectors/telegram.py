"""Telegram bot: chat with Jarvis, start tasks, receive notifications, approve actions.

Uses Bot API long polling (no public URL needed). Only the paired owner chat is accepted;
pairing requires a one-time code shown in the desktop app (``/pair <code>``) — a chat id
alone is never trusted implicitly. Critical approvals are refused here by the executor.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from jarvis.connectors.base import Connector, Health, Manifest, SecretField
from jarvis.events import Event, bus, spawn
from jarvis.security.audit import audit
from jarvis.security.auth import Principal
from jarvis.security.guards import rate_limiter
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext, ToolSpec

logger = logging.getLogger(__name__)
API = "https://api.telegram.org"


class TelegramConnector(Connector):
    manifest = Manifest(
        id="telegram",
        name="Telegram",
        description="Mit Jarvis per Telegram schreiben oder sprechen, Aufgaben starten, Statusmeldungen und "
        "Freigaben per Knopfdruck.",
        category="communication",
        auth="token",
        secrets=[
            SecretField(
                "telegram-bot-token",
                "Bot-Token von @BotFather",
                help="In Telegram @BotFather › /newbot. Danach in der App 'Koppeln' wählen und dem Bot "
                "/pair <Code> senden.",
            )
        ],
        settings_schema={
            "type": "object",
            "properties": {
                "owner_chat_id": {"type": "integer"},
                "pairing_code": {"type": ["string", "null"]},
                "pairing_expires": {"type": ["number", "null"]},
            },
            "required": ["owner_chat_id"],
        },
        mcp_note="Kein offizieller Telegram-MCP-Server; direkte Nutzung der offiziellen Bot API.",
    )

    def __init__(self, state: Any) -> None:
        super().__init__(state)
        self._poll_task: asyncio.Task[None] | None = None
        self._pair_task: asyncio.Task[None] | None = None
        self._offset = 0
        self._sessions: dict[int, uuid.UUID] = {}

    # ------------------------------------------------------------------ Bot API
    async def _call(self, method: str, http_timeout: float = 30, **params: Any) -> Any:
        token = self.secret("telegram-bot-token")
        if not token:
            raise ToolUserError("Telegram ist nicht eingerichtet (Bot-Token fehlt).")
        async with httpx.AsyncClient(timeout=http_timeout + 10) as c:
            r = await c.post(f"{API}/bot{token}/{method}", json=params)
        data = r.json()
        if not data.get("ok"):
            raise ToolUserError(f"Telegram: {data.get('description', r.status_code)}")
        return data["result"]

    async def send(
        self, text: str, chat_id: int | None = None, buttons: list[list[dict[str, str]]] | None = None
    ) -> Any:
        chat = chat_id or self.settings.get("owner_chat_id")
        if not chat:
            raise ToolUserError("Telegram ist noch nicht mit Sir gekoppelt.")
        params: dict[str, Any] = {"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": buttons}
        return await self._call("sendMessage", **params)

    async def health(self) -> Health:
        try:
            me = await self._call("getMe", http_timeout=10)
            return Health("ok", f"@{me.get('username')} gekoppelt mit Chat {self.settings.get('owner_chat_id')}")
        except ToolUserError as e:
            return Health("error", str(e))

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        bus.subscribe("approval.requested", self._on_approval_requested)
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

    async def begin_pairing(self) -> str:
        """Generate a one-time code; polling runs until the owner sends /pair <code>."""
        if not self.secret("telegram-bot-token"):
            raise ToolUserError("Zuerst den Bot-Token hinterlegen.")
        code = f"{secrets.randbelow(10**6):06d}"
        self.settings["pairing_code"] = code
        self.settings["pairing_expires"] = time.time() + 600
        await self.state.connectors.configure(
            "telegram",
            actor="pairing",
            settings={"pairing_code": code, "pairing_expires": self.settings["pairing_expires"]},
        )
        if self._poll_task is None and self._pair_task is None:
            self._pair_task = asyncio.create_task(self._poll_loop(pairing_only=True))
        return code

    async def _poll_loop(self, pairing_only: bool = False) -> None:
        backoff = 1.0
        while True:
            try:
                updates = await self._call(
                    "getUpdates",
                    http_timeout=35,
                    offset=self._offset,
                    timeout=25,
                    allowed_updates=["message", "callback_query"],
                )
                backoff = 1.0
                for upd in updates:
                    self._offset = max(self._offset, int(upd["update_id"]) + 1)
                    spawn(self._handle_update(upd), name="telegram-update")
                if pairing_only and self.settings.get("owner_chat_id"):
                    self._pair_task = None
                    return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Telegram polling error: %s", e)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    # ------------------------------------------------------------------ inbound
    async def _handle_update(self, upd: dict[str, Any]) -> None:
        try:
            if "callback_query" in upd:
                await self._handle_callback(upd["callback_query"])
                return
            msg = upd.get("message") or {}
            chat_id = int(msg.get("chat", {}).get("id", 0))
            text = (msg.get("text") or "").strip()
            if text.startswith("/pair"):
                await self._handle_pair(chat_id, text)
                return
            owner = self.settings.get("owner_chat_id")
            if not owner or chat_id != int(owner):
                await audit(
                    f"telegram:{chat_id}",
                    "telegram.rejected",
                    outcome="denied",
                    details={"from": msg.get("from", {}).get("username")},
                )
                return
            if not rate_limiter.check(f"telegram:{chat_id}", 20, 60):
                await self.send("Langsam, Sir. Zu viele Nachrichten in kurzer Zeit.", chat_id)
                return
            if "voice" in msg or "audio" in msg:
                text = await self._transcribe(msg.get("voice") or msg.get("audio"))
                if not text:
                    await self.send("Die Sprachnachricht konnte ich leider nicht verstehen, Sir.", chat_id)
                    return
                await self.send(f"🎙 {text}", chat_id)
            if not text:
                return
            if await self._handle_command(chat_id, text):
                return
            await self._call("sendChatAction", chat_id=chat_id, action="typing")
            principal = Principal(id=str(chat_id), role="telegram_owner", channel="telegram")
            result = await self.state.orchestrator.handle(
                principal, "telegram", text, session_id=self._sessions.get(chat_id)
            )
            self._sessions[chat_id] = result.session_id
            await self.send(result.text or "(keine Antwort)", chat_id)
        except Exception:
            logger.exception("Telegram update handling failed")

    async def _handle_pair(self, chat_id: int, text: str) -> None:
        code = text.split(maxsplit=1)[1].strip() if " " in text else ""
        expected = self.settings.get("pairing_code")
        expires = float(self.settings.get("pairing_expires") or 0)
        if not rate_limiter.check(f"telegram-pair:{chat_id}", 5, 3600):
            return
        if expected and secrets.compare_digest(code, str(expected)) and time.time() < expires:
            await self.state.connectors.configure(
                "telegram",
                actor=f"telegram:{chat_id}",
                enabled=True,
                settings={"owner_chat_id": chat_id, "pairing_code": None, "pairing_expires": None},
            )
            await audit(f"telegram:{chat_id}", "telegram.paired")
            await self.send("Kopplung erfolgreich. Zu Ihren Diensten, Sir.", chat_id)
        else:
            await audit(f"telegram:{chat_id}", "telegram.pair_failed", outcome="denied")
            await self._call("sendMessage", chat_id=chat_id, text="Ungültiger oder abgelaufener Code.")

    async def _handle_command(self, chat_id: int, text: str) -> bool:
        cmd = text.split()[0].lower()
        if cmd in ("/status", "/start"):
            s = await self.state.system_status(brief=True)
            lines = [f"{k}: {v}" for k, v in s.get("components", {}).items()]
            await self.send("Systemstatus:\n" + "\n".join(lines), chat_id)
            return True
        if cmd == "/tasks":
            tasks = await self.state.tasks.list_tasks(limit=8)
            await self.send("\n".join(f"• {t.title} – {t.status}" for t in tasks) or "Keine Aufgaben.", chat_id)
            return True
        if cmd == "/stop":
            await self.state.kill_switch.engage("Telegram /stop", f"telegram:{chat_id}")
            await self.send("Not-Aus aktiviert. Freigabe nur über die Desktop-App.", chat_id)
            return True
        if cmd == "/new":
            self._sessions.pop(chat_id, None)
            await self.send("Neue Unterhaltung, Sir.", chat_id)
            return True
        return False

    async def _handle_callback(self, cq: dict[str, Any]) -> None:
        chat_id = int(cq.get("message", {}).get("chat", {}).get("id", 0))
        owner = self.settings.get("owner_chat_id")
        data = str(cq.get("data", ""))
        if not owner or chat_id != int(owner) or not data.startswith("ap:"):
            return
        _, aid, decision = data.split(":", 2)
        principal = Principal(id=str(chat_id), role="telegram_owner", channel="telegram")
        try:
            res = await self.state.executor.decide(uuid.UUID(aid), decision == "y", principal)
            note = {
                "executed": "✅ Freigegeben und ausgeführt",
                "rejected": "❌ Abgelehnt",
                "failed": "⚠️ Freigegeben, Ausführung fehlgeschlagen",
                "expired": "⌛ Abgelaufen",
            }.get(res["status"], res["status"])
        except PermissionError as e:
            note = f"⛔ {e}"
        await self._call("answerCallbackQuery", callback_query_id=cq["id"], text=note[:180])
        await self._call(
            "editMessageReplyMarkup",
            chat_id=chat_id,
            message_id=cq["message"]["message_id"],
            reply_markup={"inline_keyboard": []},
        )
        await self.send(note, chat_id)

    async def _on_approval_requested(self, ev: Event) -> None:
        if not self.active:
            return
        risk = ev.data.get("risk")
        text = f"Freigabe erforderlich ({risk}):\n{ev.data.get('summary')}\nGrund: {ev.data.get('reason')}"
        if risk == "critical":
            await self.send(text + "\n\nKritische Aktion – bitte in der Desktop-App freigeben.")
            return
        aid = ev.data["approval_id"]
        await self.send(
            text,
            buttons=[
                [
                    {"text": "✅ Freigeben", "callback_data": f"ap:{aid}:y"},
                    {"text": "❌ Ablehnen", "callback_data": f"ap:{aid}:n"},
                ]
            ],
        )

    async def _transcribe(self, media: dict[str, Any] | None) -> str:
        if not media:
            return ""
        info = await self._call("getFile", file_id=media["file_id"])
        token = self.secret("telegram-bot-token")
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(f"{API}/file/bot{token}/{info['file_path']}")
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "in.oga"
            src.write_bytes(r.content)
            return await self.state.stt.transcribe_file(src)

    # ------------------------------------------------------------------ tools
    def tools(self) -> list[ToolSpec]:
        async def send(ctx: ToolContext, a: dict[str, Any]) -> Any:
            res = await self.send(a["text"])
            return {"message_id": res.get("message_id")}

        return [
            ToolSpec(
                "telegram_send_message",
                "Sendet Sir eine Telegram-Nachricht.",
                {
                    "type": "object",
                    "properties": {"text": {"type": "string", "minLength": 1}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
                send,
                "low",
                "telegram",
            )
        ]
