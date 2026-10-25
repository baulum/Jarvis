"""``whatsapp`` connector: manifest, settings, health and tools around the WhatsAppService."""

from __future__ import annotations

from typing import Any

from jarvis.connectors.base import Connector, Health, Manifest
from jarvis.tools.registry import ToolSpec
from jarvis.whatsapp.service import DEFAULTS
from jarvis.whatsapp.tools import whatsapp_tools

MEDIA_TYPES = ["image", "voice", "audio", "document", "video"]


class WhatsAppConnector(Connector):
    manifest = Manifest(
        id="whatsapp",
        name="WhatsApp",
        description="WhatsApp lesen, durchsuchen, Dateien empfangen, verstehen und senden – über eine verknüpfte "
        "Geräte-Sitzung (wie WhatsApp Web, aber ohne Browser). Jede Sendung wird Sir vorher zur Bestätigung vorgelegt.",
        category="communication",
        auth="local",
        settings_schema={
            "type": "object",
            "properties": {
                "phone_number": {
                    "type": "string",
                    "maxLength": 32,
                    "description": "Eigene Nummer mit Ländervorwahl – nur für die Anmeldung per Kopplungscode",
                },
                "download_dir": {
                    "type": "string",
                    "maxLength": 1024,
                    "description": "Ordner für empfangene Dateien (leer = Jarvis-Datenordner/whatsapp/downloads)",
                },
                "allowed_dirs": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 1024},
                    "maxItems": 50,
                    "description": "Weitere Ordner, in die gespeichert bzw. aus denen gesendet werden darf",
                },
                "auto_download": {"type": "boolean", "description": "Erlaubte Dateitypen automatisch herunterladen"},
                "auto_download_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": MEDIA_TYPES},
                    "uniqueItems": True,
                },
                "max_download_mb": {"type": "integer", "minimum": 1, "maximum": 2048},
                "max_upload_mb": {"type": "integer", "minimum": 1, "maximum": 2048},
                "notify_incoming": {"type": "boolean", "description": "Neue Nachrichten melden"},
                "notify_groups": {"type": "boolean", "description": "Auch Gruppennachrichten melden"},
                "notify_preview": {"type": "boolean", "description": "Nachrichtentext in der Meldung zeigen"},
                "send_receipts_notify": {
                    "type": "boolean",
                    "description": "Melden, wenn eine Sendung nicht zugestellt wird",
                },
                "history_sync": {
                    "type": "boolean",
                    "description": "Verfügbaren Chatverlauf bei der Anmeldung übernehmen",
                },
                "retention_days": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 3650,
                    "description": "Nachrichten und Downloads nach so vielen Tagen löschen (0 = behalten)",
                },
                "excluded_chats": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 200},
                    "maxItems": 500,
                    "description": "Chats, die Jarvis weder speichert noch liest",
                },
                "auto_suggestions": {
                    "type": "boolean",
                    "description": "Einzelchats automatisch auf Aufgaben/Termine/Rechnungen prüfen (nur Vorschläge)",
                },
            },
        },
        setup_instructions="Aktivieren, dann „Mit QR-Code verbinden“ wählen und den Code in WhatsApp auf dem Handy "
        "scannen: Einstellungen › Verknüpfte Geräte › Gerät hinzufügen. Alternativ Kopplungscode mit der eigenen "
        "Nummer.",
        docs_url="",
        mcp_note="Kein offizieller WhatsApp-MCP-Server für private Konten; Anbindung über Baileys (inoffizielle "
        "WhatsApp-Web-Schnittstelle) in einem lokalen Sidecar-Prozess.",
    )

    def __init__(self, state: Any) -> None:
        self._settings: dict[str, Any] = {}
        super().__init__(state)

    @property
    def svc(self) -> Any:
        return self.state.whatsapp

    @property
    def settings(self) -> dict[str, Any]:
        return self._settings

    @settings.setter
    def settings(self, value: dict[str, Any]) -> None:
        self._settings = value
        if getattr(self.state, "whatsapp", None) is not None:
            self.state.whatsapp.apply_settings(value)

    async def start(self) -> None:
        self.svc.apply_settings(self._settings)
        await self.svc.start()

    async def stop(self) -> None:
        await self.svc.stop()

    async def health(self) -> Health:
        st = self.svc.public_status()
        side = st["sidecar"]
        if not side["running"]:
            return Health("error" if side["error"] else "degraded", side["error"] or "WhatsApp-Dienst startet …")
        state = st["state"]
        me = st.get("me") or {}
        details = {"state": state, "baileys": side.get("baileys")}
        if state == "open":
            who = " ".join(x for x in (me.get("name"), f"({me['phone']})" if me.get("phone") else "") if x)
            return Health("ok", f"Verbunden{' als ' + who if who else ''}", details=details)
        if state in ("qr", "pairing"):
            return Health("degraded", "Wartet auf die Anmeldung (QR-Code bzw. Kopplungscode)", details=details)
        if state in ("idle", "logged_out", "starting", "connecting"):
            msg = st.get("error") or (
                "Verbindet …" if state in ("starting", "connecting") else "Nicht verbunden – per QR-Code verbinden"
            )
            return Health("degraded", msg, details=details)
        if state in ("reconnecting", "restarting"):
            return Health("degraded", st.get("error") or "Verbindung wird wiederhergestellt", details=details)
        return Health("error", st.get("error") or f"Status {state}", details=details)

    def tools(self) -> list[ToolSpec]:
        return whatsapp_tools(self.state.whatsapp)


__all__ = ["DEFAULTS", "WhatsAppConnector"]
