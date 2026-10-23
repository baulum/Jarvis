"""Connection to the Jarvis Core Server: pairing (HTTPS) and the command WebSocket.

Trust: the Core's gateway uses a self-signed certificate whose SHA-256 fingerprint comes with the pairing code.
The agent checks that fingerprint on every TLS connection *before* it sends anything secret – the pairing code
and the device token travel only after the check (the token in the first WebSocket frame, not in a header that
would leave before the certificate could be inspected). Without a fingerprint (Tailscale serve / reverse proxy
with a real certificate) normal CA verification applies.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import http.client
import json
import logging
import platform
import random
import socket
import ssl
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from jarvis_device import __version__
from jarvis_device.config import AgentConfig
from jarvis_device.dispatcher import CommandError, Dispatcher
from jarvis_device.protocol import MAX_FRAME_BYTES, PROTOCOL_VERSION, REGISTER_PATH, WS_PATH

logger = logging.getLogger("jarvis_device")

# actions that drive the screen/keyboard run one at a time (two clicks racing would hit the wrong window)
SERIAL_PREFIXES = ("input.", "apps.", "windows.", "clipboard.", "audio.")


class PairingFailed(RuntimeError):
    pass


class Unauthorized(RuntimeError):
    """The Core rejected the token (revoked device) – the agent stops instead of hammering the Core."""


def normalize_fingerprint(fp: str) -> str:
    return fp.replace(":", "").replace(" ", "").strip().lower()


def ssl_context(fingerprint: str) -> ssl.SSLContext:
    if fingerprint:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # replaced by the pin check below (self-signed gateway certificate)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        return ctx
    return ssl.create_default_context()


def check_pin(der: bytes | None, fingerprint: str) -> None:
    if not fingerprint:
        return
    if not der:
        raise PairingFailed("Kein Zertifikat vom Core erhalten")
    got = hashlib.sha256(der).hexdigest()
    if got != normalize_fingerprint(fingerprint):
        raise PairingFailed(
            f"Zertifikat des Core passt nicht zum Fingerabdruck (erwartet {normalize_fingerprint(fingerprint)[:16]}…, "
            f"erhalten {got[:16]}…) – Verbindung abgebrochen"
        )


def ws_url(core_url: str) -> str:
    parts = urlsplit(core_url.rstrip("/"))
    scheme = "wss" if parts.scheme == "https" else "ws"
    return f"{scheme}://{parts.netloc}{parts.path}{WS_PATH}"


def device_info(backend_platform: str, session: str) -> dict[str, str]:
    return {
        "platform": backend_platform,
        "os": f"{platform.system()} {platform.release()}",
        "hostname": socket.gethostname(),
        "version": __version__,
        "arch": platform.machine(),
        "session": session,
    }


# --------------------------------------------------------------------------- pairing
def register(
    core_url: str, code: str, name: str, fingerprint: str, platform_id: str, timeout: float = 20
) -> dict[str, Any]:
    """Trades the one-time code for the device token (POST /api/devices/agents/register)."""
    parts = urlsplit(core_url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise PairingFailed("Core-Adresse muss mit https:// beginnen (z. B. https://mac.tailnet.ts.net:8767)")
    body = json.dumps({"code": code, "name": name, "platform": platform_id, "hostname": socket.gethostname()})
    conn: http.client.HTTPConnection
    if parts.scheme == "https":
        conn = http.client.HTTPSConnection(
            parts.hostname, parts.port or 443, timeout=timeout, context=ssl_context(fingerprint)
        )
    else:
        if parts.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise PairingFailed("Unverschlüsseltes http:// nur für 127.0.0.1 – sonst https:// verwenden")
        conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    try:
        conn.connect()
        if isinstance(conn, http.client.HTTPSConnection):
            sock = conn.sock
            check_pin(sock.getpeercert(binary_form=True) if isinstance(sock, ssl.SSLSocket) else None, fingerprint)
        conn.request("POST", REGISTER_PATH, body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        raw = resp.read()
    except (OSError, ssl.SSLError) as e:
        raise PairingFailed(f"Core nicht erreichbar: {e}") from e
    finally:
        conn.close()
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        data = {}
    if resp.status != 200:
        raise PairingFailed(f"Kopplung abgelehnt ({resp.status}): {data.get('detail', raw[:200])}")
    if data.get("protocol") != PROTOCOL_VERSION:
        raise PairingFailed(f"Core spricht Protokoll {data.get('protocol')}, der Agent {PROTOCOL_VERSION}")
    return dict(data)


# --------------------------------------------------------------------------- command channel
class AgentConnection:
    def __init__(
        self,
        cfg: AgentConfig,
        dispatcher: Dispatcher,
        on_state: Callable[[str, str], None] | None = None,
        reprobe_s: float = 300,
    ) -> None:
        self.cfg = cfg
        self.dispatcher = dispatcher
        self.on_state = on_state or (lambda state, detail: None)
        self.reprobe_s = reprobe_s
        self._serial = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._caps: dict[str, dict[str, str]] = {}
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                await self.session()
            except Unauthorized as e:
                self.on_state("unauthorized", str(e))
                raise
            except PairingFailed as e:  # certificate mismatch: possibly an attacker – do not retry quickly
                self.on_state("error", str(e))
                logger.error("%s", e)
                attempt = max(attempt, 6)
            except (TimeoutError, OSError, ConnectionError) as e:
                self.on_state("offline", f"{type(e).__name__}: {e}")
                logger.warning("Verbindung zum Core fehlgeschlagen: %s", e)
            except Exception as e:  # websockets.ConnectionClosed and friends
                self.on_state("offline", f"{type(e).__name__}: {e}")
                logger.warning("Verbindung zum Core beendet: %s", e)
            if self._stop.is_set():
                break
            attempt = 0 if time.monotonic() - started > 60 else attempt + 1
            delay = min(60.0, 2 ** min(attempt, 6)) * (0.5 + random.random() / 2)  # noqa: S311 - jitter only
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), delay)

    async def session(self) -> None:
        from websockets.exceptions import ConnectionClosed

        try:
            await self._session()
        except ConnectionClosed as e:
            code = e.rcvd.code if e.rcvd is not None else None
            if code in (4401, 4403):
                raise Unauthorized("Der Core hat den Geräte-Token abgelehnt – neu koppeln (jarvis-device pair)") from e
            raise

    async def _session(self) -> None:
        from websockets.asyncio.client import connect

        url = ws_url(self.cfg.core_url)
        ctx = ssl_context(self.cfg.fingerprint) if url.startswith("wss://") else None
        self.on_state("connecting", url)
        async with connect(
            url,
            ssl=ctx,
            max_size=MAX_FRAME_BYTES,
            open_timeout=15,
            ping_interval=20,
            ping_timeout=30,
            user_agent_header=f"jarvis-device/{__version__}",
        ) as ws:
            if ctx is not None:
                ssl_obj = ws.transport.get_extra_info("ssl_object")
                check_pin(ssl_obj.getpeercert(binary_form=True) if ssl_obj else None, self.cfg.fingerprint)
            self._caps = await asyncio.to_thread(self.dispatcher.capabilities)
            await ws.send(
                json.dumps(
                    {
                        "type": "hello",
                        "protocol": PROTOCOL_VERSION,
                        "token": self.cfg.token,
                        "device": device_info(self.dispatcher.backend.platform, self.dispatcher.backend.session()),
                        "capabilities": self._caps,
                    }
                )
            )
            welcome = json.loads(await asyncio.wait_for(ws.recv(), 15))
            if welcome.get("type") == "error":
                raise Unauthorized(str(welcome.get("message") or "abgelehnt"))
            if welcome.get("type") != "welcome":
                raise ConnectionError("Unerwartete Antwort des Core")
            self.dispatcher.kill.remote = bool(welcome.get("kill_switch"))
            self.on_state("online", f"verbunden als {self.cfg.name}")
            logger.info("Mit dem Core verbunden (%s)", url)

            async def send(frame: dict[str, Any]) -> None:
                async with self._send_lock:
                    await ws.send(json.dumps(frame, ensure_ascii=False, default=str))

            reprobe = asyncio.create_task(self._reprobe_loop(send))
            running: set[asyncio.Task[None]] = set()
            try:
                async for raw in ws:
                    msg = json.loads(raw)
                    kind = msg.get("type")
                    if kind == "command":
                        t = asyncio.create_task(self._handle(msg, send))
                        running.add(t)
                        t.add_done_callback(running.discard)
                    elif kind == "killswitch":
                        self.dispatcher.kill.remote = bool(msg.get("engaged"))
                        logger.warning(
                            "Not-Aus %s (vom Core)", "aktiv" if self.dispatcher.kill.remote else "aufgehoben"
                        )
                    elif kind == "ping":
                        await send({"type": "pong"})
            finally:
                reprobe.cancel()
                for t in running:
                    t.cancel()

    async def _handle(self, msg: dict[str, Any], send: Callable[[dict[str, Any]], Any]) -> None:
        cid = str(msg.get("id"))
        action = str(msg.get("action"))
        args = msg.get("args") or {}
        timeout = float(msg.get("timeout_s") or 60)
        started = time.monotonic()
        try:
            if action.startswith(SERIAL_PREFIXES):
                async with self._serial:
                    data = await asyncio.wait_for(self.dispatcher.run(action, args, self._caps), timeout)
            else:
                data = await asyncio.wait_for(self.dispatcher.run(action, args, self._caps), timeout)
            frame: dict[str, Any] = {"type": "result", "id": cid, "ok": True, "data": data}
        except CommandError as e:
            frame = {"type": "result", "id": cid, "ok": False, "error": {"code": e.code, "message": e.message}}
        except TimeoutError:
            frame = {
                "type": "result",
                "id": cid,
                "ok": False,
                "error": {"code": "timeout", "message": f"Aktion nach {int(timeout)} s abgebrochen"},
            }
        logger.info(
            "%s → %s (%.1f s)", action, "ok" if frame["ok"] else frame["error"]["code"], time.monotonic() - started
        )
        await send(frame)

    async def _reprobe_loop(self, send: Callable[[dict[str, Any]], Any]) -> None:
        while True:
            await asyncio.sleep(self.reprobe_s)
            caps = await asyncio.to_thread(self.dispatcher.capabilities)
            if caps != self._caps:
                self._caps = caps
                await send({"type": "capabilities", "capabilities": caps})
