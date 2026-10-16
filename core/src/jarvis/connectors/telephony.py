"""Phone calls via Twilio, set up in the desktop app instead of config files and the terminal.

Secrets (Keychain): Twilio Account SID, Auth Token and Sir's phone PIN. Settings: the Twilio number,
Sir's own number for outbound calls and, optionally, a fixed public HTTPS base URL. Without one,
Jarvis opens a free Cloudflare Quick Tunnel itself and points the Twilio number's webhooks at it
(again on every restart, because the quick-tunnel address changes). Values set here override
``config/jarvis.yaml``; empty fields keep the YAML value.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import httpx

from jarvis.connectors.base import Connector, Health, Manifest, SecretField
from jarvis.telephony.tunnel import QuickTunnel
from jarvis.tools.registry import ToolSpec

if TYPE_CHECKING:
    from jarvis.app_state import AppState

logger = logging.getLogger(__name__)
_E164 = re.compile(r"^\+[1-9]\d{6,14}$")


class TelephonyConnector(Connector):
    manifest = Manifest(
        id="telephony",
        name="Telefon (Twilio)",
        description="Echte ein- und ausgehende Anrufe: Sir ruft Jarvis an (PIN-Prüfung) oder Jarvis ruft Sir an.",
        category="communication",
        auth="api_key",
        secrets=[
            SecretField(
                "twilio-account-sid", "Twilio Account SID", help="console.twilio.com › Account Info (beginnt mit AC)"
            ),
            SecretField(
                "twilio-auth-token", "Twilio Auth Token", help="console.twilio.com › Account Info › Auth Token"
            ),
            SecretField(
                "phone-pin",
                "Telefon-PIN (4–8 Ziffern)",
                help="Fragt Jarvis bei jedem Anruf ab; die Rufnummer allein reicht nie",
            ),
        ],
        settings_schema={
            "type": "object",
            "properties": {
                "from_number": {"type": "string", "description": "Deine Twilio-Rufnummer im Format +49…"},
                "owner_number": {
                    "type": "string",
                    "description": "Deine eigene Handynummer (+49…), für Anrufe von Jarvis an dich",
                },
                "public_base_url": {
                    "type": "string",
                    "description": "Leer lassen = automatisch (kostenloser Cloudflare-Tunnel). "
                    "Nur bei eigener Domain ausfüllen.",
                },
            },
        },
        docs_url="https://console.twilio.com/",
        setup_instructions="Schritt für Schritt: docs/ANLEITUNG-TWILIO.md. Die Webhooks der Twilio-Nummer "
        "trägt Jarvis selbst ein.",
    )

    def __init__(self, state: AppState) -> None:
        super().__init__(state)
        self.tunnel: QuickTunnel | None = None
        self.webhook_error: str | None = None
        self.webhook_url: str | None = None

    @property
    def fixed_url(self) -> str:
        return str(self.settings.get("public_base_url") or "").rstrip("/")

    def _apply(self) -> None:
        tel = self.state.settings.telephony
        if self.settings.get("from_number"):
            tel.twilio.from_number = str(self.settings["from_number"]).replace(" ", "")
        if self.settings.get("owner_number"):
            tel.owner_numbers = [str(self.settings["owner_number"]).replace(" ", "")]
        if self.fixed_url:
            self.state.settings.server.public_base_url = self.fixed_url
        elif self.tunnel and self.tunnel.url:
            self.state.settings.server.public_base_url = self.tunnel.url
        tel.provider = "twilio"

    async def start(self) -> None:
        self._apply()
        if self.fixed_url:
            await self._sync_webhook(self.fixed_url)
        else:
            self.tunnel = QuickTunnel(self.state.settings.server.port, self._on_tunnel_url)
            self.tunnel.start()

    async def stop(self) -> None:
        if self.tunnel:
            await self.tunnel.stop()
            self.tunnel = None
            if not self.fixed_url:
                self.state.settings.server.public_base_url = ""

    async def _on_tunnel_url(self, url: str) -> None:
        self.state.settings.server.public_base_url = url
        await self._sync_webhook(url)

    async def _sync_webhook(self, base: str) -> None:
        """Point the Twilio number's voice + status webhooks at this Jarvis."""
        sid, token = self.secret("twilio-account-sid"), self.secret("twilio-auth-token")
        number = self.state.settings.telephony.twilio.from_number
        api = f"https://api.twilio.com/2010-04-01/Accounts/{sid}"
        try:
            async with httpx.AsyncClient(timeout=20, auth=(sid or "", token or "")) as c:
                r = await c.get(f"{api}/IncomingPhoneNumbers.json", params={"PhoneNumber": number})
                r.raise_for_status()
                found = r.json().get("incoming_phone_numbers") or []
                if not found:
                    raise RuntimeError(f"{number} gehört nicht zu diesem Twilio-Konto")
                voice_url = f"{base}/api/telephony/twilio/voice"
                u = await c.post(
                    f"{api}/IncomingPhoneNumbers/{found[0]['sid']}.json",
                    data={
                        "VoiceUrl": voice_url,
                        "VoiceMethod": "POST",
                        "StatusCallback": f"{base}/api/telephony/twilio/status",
                        "StatusCallbackMethod": "POST",
                    },
                )
                u.raise_for_status()
            self.webhook_url, self.webhook_error = voice_url, None
            logger.info("Twilio webhook for %s set to %s", number, voice_url)
        except (httpx.HTTPError, RuntimeError) as e:
            self.webhook_error = f"Webhook nicht gesetzt: {e}"
            logger.warning(self.webhook_error)

    async def health(self) -> Health:
        self._apply()
        problems: list[str] = []
        pin = self.secret("phone-pin") or ""
        if not re.fullmatch(r"\d{4,8}", pin):
            problems.append("PIN muss aus 4–8 Ziffern bestehen")
        tel = self.state.settings.telephony
        number = tel.twilio.from_number
        if not _E164.match(number or ""):
            problems.append("Twilio-Rufnummer fehlt oder nicht im Format +49…")
        if tel.owner_numbers and not _E164.match(tel.owner_numbers[0]):
            problems.append("Eigene Rufnummer nicht im Format +49…")
        base = self.state.settings.server.public_base_url
        if not base.startswith("https://"):
            if self.tunnel and self.tunnel.error:
                problems.append(self.tunnel.error)
            elif self.tunnel:
                problems.append("Tunnel startet noch …")
            else:
                problems.append("Keine öffentliche Adresse (Connector aktivieren startet den Tunnel)")
        if self.webhook_error:
            problems.append(self.webhook_error)

        sid, token = self.secret("twilio-account-sid"), self.secret("twilio-auth-token")
        api = f"https://api.twilio.com/2010-04-01/Accounts/{sid}"
        async with httpx.AsyncClient(timeout=15, auth=(sid or "", token or "")) as c:
            r = await c.get(f"{api}.json")
            if r.status_code == 401:
                return Health("error", "Twilio lehnt Account SID / Auth Token ab (401)")
            if r.status_code >= 400:
                return Health("error", f"Twilio antwortet mit {r.status_code}")
            account = r.json()
            if number and _E164.match(number):
                nums = await c.get(f"{api}/IncomingPhoneNumbers.json", params={"PhoneNumber": number})
                if nums.status_code < 400 and not nums.json().get("incoming_phone_numbers"):
                    problems.append(f"{number} gehört nicht zu diesem Twilio-Konto")
            if base.startswith("https://"):
                # The webhook only accepts POST: a 405 proves the request reached this Jarvis.
                try:
                    reach = await c.get(f"{base}/api/telephony/twilio/voice", auth=None, timeout=10)
                    if reach.status_code != 405:
                        problems.append(f"Öffentliche Adresse erreicht Jarvis nicht (HTTP {reach.status_code})")
                except httpx.HTTPError:
                    problems.append("Öffentliche Adresse noch nicht erreichbar (Tunnel startet?)")
        trial = " · Testkonto (Trial)" if account.get("type") == "Trial" else ""
        if problems:
            return Health("degraded", "; ".join(problems) + trial)
        via = "Cloudflare-Tunnel" if self.tunnel else "eigene Adresse"
        return Health(
            "ok", f"Konto „{account.get('friendly_name', sid)}“, Nummer {number}, erreichbar über {via}{trial}"
        )

    def tools(self) -> list[ToolSpec]:
        return []
