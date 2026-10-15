"""Fish Audio text-to-speech as a connector (API key + voice), used by voice, phone and the app."""

from __future__ import annotations

import httpx

from jarvis.connectors.base import Connector, Health, Manifest, SecretField
from jarvis.tools.registry import ToolSpec


class FishAudioConnector(Connector):
    manifest = Manifest(
        id="fish_audio",
        name="Fish Audio (Stimme)",
        description="Natürliche Stimme für J.A.R.V.I.S. bei Sprachausgabe, Telefon und App. "
        "Ist der Connector aus oder nicht erreichbar, spricht Jarvis mit der macOS-Stimme.",
        category="communication",
        auth="api_key",
        secrets=[
            SecretField(
                "fish-audio-api-key", "Fish Audio API-Key", help="https://fish.audio/app/api-keys › Create New Key"
            )
        ],
        settings_schema={
            "type": "object",
            "properties": {
                "reference_id": {"type": "string", "description": "Voice-ID: Zeichenkette nach /m/ in der Stimmen-URL"},
                "model": {"type": "string", "enum": ["s1", "s2-pro", "s2.1-pro"], "default": "s1"},
            },
            "required": ["reference_id"],
        },
        docs_url="https://docs.fish.audio/",
        setup_instructions="API-Key unter https://fish.audio/app/api-keys erstellen. Stimme in der Bibliothek "
        "(https://fish.audio/discovery) öffnen und die Voice-ID aus der Adresse kopieren (nach /m/).",
    )

    def _apply(self) -> None:
        self.state.tts.configure_fish(
            self.active, self.settings.get("reference_id") or "", self.settings.get("model") or "s1"
        )

    async def start(self) -> None:
        self._apply()

    async def stop(self) -> None:
        self.state.tts.configure_fish(False)

    async def health(self) -> Health:
        self._apply()
        key = self.secret("fish-audio-api-key")
        ref = self.settings.get("reference_id")
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"https://api.fish.audio/model/{ref}", headers={"Authorization": f"Bearer {key}"})
        if r.status_code == 401:
            return Health("error", "Fish Audio lehnt den API-Key ab (401)")
        if r.status_code == 404:
            return Health("error", "Voice-ID nicht gefunden")
        if r.status_code >= 400:
            return Health("error", f"Fish Audio antwortet mit {r.status_code}")
        title = r.json().get("title", ref) if r.headers.get("content-type", "").startswith("application/json") else ref
        return Health("ok", f"Stimme: {title}")

    def tools(self) -> list[ToolSpec]:
        return []
