"""Device hub: the Core Server's side of every connected Jarvis Device Agent.

Remote-command routing lives here: a tool (or the owner's client) asks ``DeviceHub.call(device, action, args)``;
the hub resolves the device (id, name or host name – or the only one online), checks that the action's capability
is available there (otherwise "nicht verfügbar" with the device's own reason), sends the command over the device's
WebSocket and waits for the matching result. The kill switch is mirrored to every device.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from jarvis.devices.protocol import ACTIONS, CAPABILITY_LABELS, PROTOCOL_VERSION, READ_ONLY_ACTIONS
from jarvis.events import bus

logger = logging.getLogger(__name__)
Sender = Callable[[dict[str, Any]], Awaitable[None]]


class DeviceError(Exception):
    """A device command failed; ``code`` is one of ``protocol.ERROR_CODES``, ``message`` is German and safe to show."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class DeviceConnection:
    device_id: uuid.UUID
    name: str
    info: dict[str, Any]
    capabilities: dict[str, dict[str, str]]
    send: Sender
    close: Callable[[], Awaitable[None]]
    connected_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    pending: dict[str, asyncio.Future[Any]] = field(default_factory=dict)

    def capability(self, cap: str) -> tuple[bool, str]:
        st = self.capabilities.get(cap)
        if st is None:
            return False, "vom Gerät nicht gemeldet (ältere Agent-Version?)"
        return st.get("status") != "unavailable", str(st.get("reason") or "")

    def matches(self, query: str) -> bool:
        q = query.strip().lower()
        return q in (str(self.device_id), self.name.lower(), str(self.info.get("hostname", "")).lower())

    def describe(self) -> dict[str, Any]:
        return {
            "id": str(self.device_id),
            "name": self.name,
            "online": True,
            "platform": self.info.get("platform"),
            "os": self.info.get("os"),
            "hostname": self.info.get("hostname"),
            "agent_version": self.info.get("version"),
            "session": self.info.get("session"),
            "connected_at": self.connected_at,
            "last_seen": self.last_seen,
            "capabilities": {
                cap: {**st, "label": CAPABILITY_LABELS.get(cap, cap)} for cap, st in sorted(self.capabilities.items())
            },
        }


class DeviceHub:
    def __init__(self, default_timeout_s: float = 60.0, kill_switch_engaged: Callable[[], bool] | None = None) -> None:
        self.default_timeout_s = default_timeout_s
        self.kill_switch_engaged = kill_switch_engaged or (lambda: False)
        self._online: dict[uuid.UUID, DeviceConnection] = {}
        self.paired: dict[uuid.UUID, str] = {}  # device_id → name of every non-revoked device agent
        self.on_paired_change: Callable[[], Awaitable[None]] | None = None

    # ------------------------------------------------------------------ pairing state
    @property
    def has_paired(self) -> bool:
        return bool(self.paired)

    async def load_paired(self) -> None:
        from sqlalchemy import select

        from jarvis.db import session_scope
        from jarvis.db.models import Device
        from jarvis.devices.protocol import DEVICE_ROLE

        async with session_scope() as s:
            rows = (
                await s.execute(select(Device).where(Device.role == DEVICE_ROLE, Device.revoked.is_(False)))
            ).scalars()
            self.paired = {d.id: d.name for d in rows}

    async def set_paired(self, device_id: uuid.UUID, name: str | None) -> None:
        """Add (name) or remove (None) a paired device; tells the app state when the tool set appears/disappears."""
        before = self.has_paired
        if name is None:
            self.paired.pop(device_id, None)
            conn = self._online.get(device_id)
            if conn is not None:
                await self.detach(conn)
                with contextlib.suppress(Exception):
                    await conn.close()
        else:
            self.paired[device_id] = name
        if before != self.has_paired and self.on_paired_change is not None:
            await self.on_paired_change()

    # ------------------------------------------------------------------ connections
    def online(self) -> list[DeviceConnection]:
        return sorted(self._online.values(), key=lambda c: c.name.lower())

    async def attach(self, conn: DeviceConnection) -> None:
        old = self._online.get(conn.device_id)
        if old is not None and old is not conn:  # the same agent reconnected: the old socket is stale
            self._fail_pending(old, "offline", f"{old.name} hat die Verbindung neu aufgebaut")
            with contextlib.suppress(Exception):
                await old.close()
        self._online[conn.device_id] = conn
        await bus.publish(
            "device.connected",
            device_id=str(conn.device_id),
            name=conn.name,
            platform=conn.info.get("platform"),
            nodes=[f"device:{conn.device_id}"],
        )
        logger.info("Device agent %s connected (%s)", conn.name, conn.info.get("platform"))

    async def detach(self, conn: DeviceConnection) -> None:
        if self._online.get(conn.device_id) is not conn:
            return
        del self._online[conn.device_id]
        self._fail_pending(conn, "offline", f"{conn.name} hat die Verbindung verloren")
        await bus.publish(
            "device.disconnected", device_id=str(conn.device_id), name=conn.name, nodes=[f"device:{conn.device_id}"]
        )
        logger.info("Device agent %s disconnected", conn.name)

    @staticmethod
    def _fail_pending(conn: DeviceConnection, code: str, message: str) -> None:
        for fut in conn.pending.values():
            if not fut.done():
                fut.set_exception(DeviceError(code, message))
        conn.pending.clear()

    def handle_message(self, conn: DeviceConnection, msg: dict[str, Any]) -> dict[str, Any] | None:
        """One frame from a device; returns a frame to send back (pong) or None."""
        conn.last_seen = time.time()
        kind = msg.get("type")
        if kind == "result":
            fut = conn.pending.pop(str(msg.get("id")), None)
            if fut is None or fut.done():
                return None
            if msg.get("ok"):
                fut.set_result(msg.get("data"))
            else:
                err = msg.get("error") or {}
                fut.set_exception(DeviceError(str(err.get("code") or "failed"), str(err.get("message") or "Fehler")))
            return None
        if kind == "capabilities" and isinstance(msg.get("capabilities"), dict):
            conn.capabilities = _clean_caps(msg["capabilities"])
            return None
        if kind == "ping":
            return {"type": "pong"}
        return None

    # ------------------------------------------------------------------ routing
    def resolve(self, query: str | None) -> DeviceConnection:
        online = self.online()
        if query:
            for conn in online:
                if conn.matches(query):
                    return conn
            known = [n for d, n in self.paired.items() if query.strip().lower() in (str(d), n.lower())]
            if known:
                raise DeviceError("offline", f"{known[0]} ist gekoppelt, aber gerade nicht verbunden.")
            names = ", ".join(c.name for c in online) or "keins"
            raise DeviceError("invalid", f"Unbekanntes Gerät „{query}“ (verbunden: {names}).")
        if len(online) == 1:
            return online[0]
        if not online:
            raise DeviceError("offline", "Kein Jarvis Device Agent verbunden.")
        raise DeviceError(
            "invalid", "Mehrere Geräte verbunden – bitte „device“ angeben: " + ", ".join(c.name for c in online)
        )

    async def call(
        self, device: str | None, action: str, args: dict[str, Any] | None = None, timeout_s: float | None = None
    ) -> Any:
        cap = ACTIONS.get(action)
        if cap is None:
            raise DeviceError("unknown_action", f"Unbekannte Geräteaktion {action}")
        if self.kill_switch_engaged() and action not in READ_ONLY_ACTIONS:
            raise DeviceError("kill_switch", "Not-Aus ist aktiv – nur lesende Geräteaktionen sind erlaubt.")
        conn = self.resolve(device)
        ok, reason = conn.capability(cap)
        if not ok:
            label = CAPABILITY_LABELS.get(cap, cap)
            raise DeviceError(
                "unavailable", f"{label} ist auf {conn.name} nicht verfügbar" + (f": {reason}" if reason else ".")
            )
        timeout = float(timeout_s or self.default_timeout_s)
        cid = uuid.uuid4().hex
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        conn.pending[cid] = fut
        try:
            await conn.send({"type": "command", "id": cid, "action": action, "args": args or {}, "timeout_s": timeout})
        except Exception as e:
            conn.pending.pop(cid, None)
            raise DeviceError("offline", f"{conn.name} ist nicht erreichbar ({type(e).__name__}).") from e
        try:
            return await asyncio.wait_for(fut, timeout + 5)
        except TimeoutError:
            conn.pending.pop(cid, None)
            raise DeviceError("timeout", f"{conn.name} hat nicht rechtzeitig geantwortet ({int(timeout)} s).") from None

    async def broadcast_killswitch(self, engaged: bool) -> None:
        for conn in self.online():
            with contextlib.suppress(Exception):
                await conn.send({"type": "killswitch", "engaged": engaged})

    def describe(self) -> list[dict[str, Any]]:
        online = {c.device_id for c in self._online.values()}
        out = [c.describe() for c in self.online()]
        for did, name in sorted(self.paired.items(), key=lambda kv: kv[1].lower()):
            if did not in online:
                out.append({"id": str(did), "name": name, "online": False, "capabilities": {}})
        return out


def _clean_caps(raw: dict[str, Any]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for cap, st in raw.items():
        if not isinstance(cap, str) or len(cap) > 64 or not isinstance(st, dict):
            continue
        status = st.get("status")
        out[cap] = {
            "status": status if status in ("available", "degraded", "unavailable") else "unavailable",
            "reason": str(st.get("reason") or "")[:300],
        }
    return out


def parse_hello(msg: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    """Validates the first frame of a device; raises DeviceError("invalid") when it is not a usable hello."""
    if msg.get("type") != "hello":
        raise DeviceError("invalid", "Erstes Paket muss „hello“ sein")
    if msg.get("protocol") != PROTOCOL_VERSION:
        raise DeviceError(
            "invalid", f"Protokollversion {msg.get('protocol')} wird nicht unterstützt (Core: {PROTOCOL_VERSION})"
        )
    device = msg.get("device")
    if not isinstance(device, dict):
        raise DeviceError("invalid", "Geräteangaben fehlen")
    info = {k: str(device.get(k, ""))[:120] for k in ("platform", "os", "hostname", "version", "arch", "session")}
    caps = msg.get("capabilities")
    return info, _clean_caps(caps if isinstance(caps, dict) else {})
