"""Jarvis operates Sir's Android phone: commands go out over the existing push channel, results come back over HTTP.

Flow (same rail as ``location_request``): ``PhoneControl.run`` sends an end-to-end encrypted push
``phone_command`` {cmd_id, action, args} to the paired phone; the app's AccessibilityService executes it and
answers with ``POST /api/mobile/phone/result``. ``run`` waits for exactly that answer and turns every failure
(no phone, no push, no answer, service not enabled, password field …) into a plain German ``ToolUserError``
so Jarvis can tell Sir honestly what is missing. Nothing of the result (screen contents!) is logged or stored.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jarvis.mobile.push import PushService

logger = logging.getLogger(__name__)

ACTIONS = frozenset({"open_app", "list_apps", "ui_tree", "tap", "type_text", "scroll", "key", "screenshot"})
DEFAULT_TIMEOUT_S = 25.0
MAX_COMMAND_BYTES = 2000  # push plaintext limit is 2600 (see push.MAX_PLAINTEXT); leave room for the envelope fields

ERROR_TEXTS = {
    "accessibility_disabled": (
        "Bedienungshilfe in der Jarvis-App nicht aktiviert. Sir muss sie auf dem Handy einschalten: "
        "J.A.R.V.I.S.-App › Einstellungen › „Handy-Steuerung durch Jarvis“ › Bedienungshilfen."
    ),
    "password_field": "Das ist ein Passwortfeld – Passwortfelder lese und befülle ich grundsätzlich nicht.",
    "app_not_found": "Diese App ist auf dem Handy nicht installiert oder nicht startbar.",
    "app_ambiguous": "Der App-Name ist nicht eindeutig.",
    "element_not_found": "Das Bedienelement gibt es nicht (mehr). Erst phone_ui_tree aufrufen.",
    "stale_element": "Der Bildschirm hat sich seit phone_ui_tree geändert. phone_ui_tree erneut aufrufen.",
    "no_input_field": "Kein Eingabefeld ausgewählt. Mit phone_tap ein Feld antippen oder element_id angeben.",
    "unsupported": "Das unterstützt dieses Handy bzw. diese Android-Version nicht.",
    "action_failed": "Das Handy konnte die Aktion nicht ausführen.",
    "bad_request": "Ungültiger Befehl an das Handy.",
}


class PhoneError(Exception):
    """User-facing failure (German text). The tools wrap it into ``ToolUserError``."""


@dataclass(slots=True)
class _Pending:
    future: asyncio.Future[dict[str, Any]]
    devices: set[str] | None  # None while the push is still going out (any paired phone may already answer)
    action: str = field(default="")


class PhoneControl:
    def __init__(self, push: PushService) -> None:
        self.push = push
        self._pending: dict[str, _Pending] = {}
        self._lock = asyncio.Lock()  # one UI command at a time: parallel taps/typing would trample each other

    async def run(
        self, action: str, args: dict[str, Any] | None = None, *, timeout_s: float = DEFAULT_TIMEOUT_S
    ) -> dict[str, Any]:
        """Executes one command on the phone and returns its ``data``. Raises PhoneError with an honest reason."""
        if action not in ACTIONS:
            raise PhoneError(f"Unbekannter Handy-Befehl: {action}")
        args = args or {}
        cmd_id = uuid.uuid4().hex
        payload = {"cmd_id": cmd_id, "action": action, "args": args}
        if len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()) > MAX_COMMAND_BYTES:
            raise PhoneError("Der Befehl ist zu lang für den Push-Kanal zum Handy (Text kürzen oder in Teilen senden).")
        async with self._lock:
            return await self._run_locked(cmd_id, action, payload, timeout_s)

    async def _run_locked(self, cmd_id: str, action: str, payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        if not self.push.fcm.configured():
            raise PhoneError("Handy nicht verbunden: Push (Firebase) ist auf dem Jarvis-Server nicht eingerichtet.")
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        pending = _Pending(fut, None, action)
        self._pending[cmd_id] = pending
        try:
            try:
                results = await self.push.send_all(
                    "phone_command", payload, high_priority=True, ttl_s=max(30, int(timeout_s) + 5)
                )
            except Exception as e:  # push problems must not leak details
                logger.warning("Phone command push failed: %s", type(e).__name__)
                raise PhoneError("Handy nicht verbunden: Der Push an das Handy ist fehlgeschlagen.") from e
            pending.devices = {r.device_id for r in results if r.ok}
            if not pending.devices:
                if not results:
                    raise PhoneError(
                        "Handy nicht verbunden: kein gekoppeltes Handy mit Push-Registrierung "
                        "(J.A.R.V.I.S.-App koppeln und Push einrichten)."
                    )
                raise PhoneError(
                    "Handy nicht verbunden: Der Push wurde nicht zugestellt "
                    f"({', '.join(sorted({r.error or '?' for r in results}))})."
                )
            try:
                result = await asyncio.wait_for(fut, timeout_s)
            except TimeoutError as e:
                raise PhoneError(
                    "Handy nicht erreichbar: keine Antwort der J.A.R.V.I.S.-App (Handy offline, "
                    "Energiesparmodus oder App beendet)."
                ) from e
        finally:
            self._pending.pop(cmd_id, None)
        return self._unwrap(result)

    @staticmethod
    def _unwrap(result: dict[str, Any]) -> dict[str, Any]:
        if result.get("ok"):
            data = result.get("data")
            return data if isinstance(data, dict) else {}
        code = str(result.get("error") or "action_failed")
        text = ERROR_TEXTS.get(code, ERROR_TEXTS["action_failed"])
        detail = str(result.get("message") or "").strip()
        raise PhoneError(f"{text} {detail}".strip() if detail else text)

    def complete(self, device_id: uuid.UUID | str, cmd_id: str, result: dict[str, Any]) -> bool:
        """Called by the result endpoint. Only a phone the command was pushed to may answer, and only once."""
        pending = self._pending.get(cmd_id)
        if pending is None or pending.future.done():
            return False
        if pending.devices is not None and str(device_id) not in pending.devices:
            return False
        pending.future.set_result(result)
        return True
