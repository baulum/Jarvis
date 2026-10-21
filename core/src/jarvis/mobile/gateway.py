"""TLS gateway for the companion app.

The core API stays on 127.0.0.1:8765. Phones reach Jarvis through a second listener (default ``0.0.0.0:8767``)
that serves the same application, but only the paths a phone needs (``MOBILE_ROUTES``) – everything else
answers 404 there, independent of authentication. TLS uses a self-signed certificate generated on first
start; the phone pins its SHA-256 fingerprint from the pairing QR code (no CA involved, survives IP changes
because the pin – not the hostname – is checked). Behind Tailscale ``serve`` or a VPS reverse proxy with a
real certificate set ``mobile.gateway.tls: external``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import logging
import re
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psutil
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from jarvis.config import Settings

logger = logging.getLogger(__name__)

# (methods, path regex) a phone may use. Anything else is 404 on the gateway listener.
MOBILE_ROUTES: list[tuple[frozenset[str], re.Pattern[str]]] = [
    (frozenset({"GET"}), re.compile(r"^/api/health$")),
    (frozenset({"GET", "POST", "PUT", "DELETE"}), re.compile(r"^/api/mobile/.*")),
    (frozenset({"POST"}), re.compile(r"^/api/chat(/interrupt)?$")),
    (frozenset({"GET"}), re.compile(r"^/api/sessions(/[0-9a-f-]{36}/(messages|events))?$")),
    (frozenset({"POST"}), re.compile(r"^/api/sessions/[0-9a-f-]{36}/events/[A-Za-z0-9_-]{1,40}/check$")),
    (frozenset({"GET"}), re.compile(r"^/api/tasks(/[0-9a-f-]{36})?$")),
    (frozenset({"POST"}), re.compile(r"^/api/tasks/[0-9a-f-]{36}/(input|cancel|retry|pause|resume|message)$")),
    (frozenset({"GET"}), re.compile(r"^/api/approvals(/mode)?$")),  # deciding goes through /api/mobile (signed)
    (frozenset({"GET", "POST"}), re.compile(r"^/api/agent/model$")),  # model picker in the app
    (frozenset({"GET"}), re.compile(r"^/api/agent/usage$")),  # Claude quota display
    (frozenset({"POST"}), re.compile(r"^/api/agent/usage/refresh$")),
    (frozenset({"GET"}), re.compile(r"^/api/notifications$")),
    (frozenset({"POST"}), re.compile(r"^/api/notifications/read$")),
    (frozenset({"GET"}), re.compile(r"^/api/graph(/cluster/[a-z_]+)?$")),
    (frozenset({"GET"}), re.compile(r"^/api/memory/(search|items)(/[0-9a-f-]{36})?$")),
    (frozenset({"GET"}), re.compile(r"^/api/system/(status|greeting)$")),
    (frozenset({"POST"}), re.compile(r"^/api/system/killswitch$")),  # engaging only; release is owner-only
    (frozenset({"GET"}), re.compile(r"^/api/coding/jobs$")),
    (frozenset({"GET"}), re.compile(r"^/api/coding/jobs/[0-9a-f-]{36}/(monitor|log)$")),  # agent monitor
    (frozenset({"POST"}), re.compile(r"^/api/coding/jobs/[0-9a-f-]{36}/(pause|resume|cancel|message)$")),
    (frozenset({"GET"}), re.compile(r"^/api/commands$")),  # slash-command list, same source as the desktop
    (frozenset({"POST"}), re.compile(r"^/api/voice/stt$")),
    (frozenset({"GET"}), re.compile(r"^/ws$")),
    (frozenset({"GET", "POST"}), re.compile(r"^/rtc(/.*)?$")),  # LiveKit signalling (proxied, see rtc_proxy)
]


def mobile_path_allowed(method: str, path: str) -> bool:
    return any(method in methods and rx.match(path) for methods, rx in MOBILE_ROUTES)


# --------------------------------------------------------------------------- addresses
def local_addresses() -> list[str]:
    """IPv4 addresses a phone may reach: Tailscale (100.64.0.0/10) first, then LAN, no loopback."""
    lan: list[str] = []
    tail: list[str] = []
    cgnat = ipaddress.ip_network("100.64.0.0/10")
    for name, addrs in psutil.net_if_addrs().items():
        for a in addrs:
            if a.family != socket.AF_INET:
                continue
            ip = ipaddress.ip_address(a.address)
            if ip.is_loopback or ip.is_link_local:
                continue
            if ip in cgnat:
                tail.append(a.address)
            elif ip.is_private and not name.startswith(("bridge", "vmnet", "docker")):
                lan.append(a.address)
    for ts_ip in _tailscale_ips():  # some utun setups are not listed by getifaddrs consumers
        if ts_ip not in tail:
            tail.append(ts_ip)
    primary = _primary_ip()
    if primary in lan:
        lan.remove(primary)
        lan.insert(0, primary)
    return [*tail, *lan]  # Tailscale first: reachable at home and on mobile data alike


def _tailscale_ips() -> list[str]:
    import shutil
    import subprocess

    cli = shutil.which("tailscale") or "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    try:
        out = subprocess.run([cli, "ip", "-4"], capture_output=True, text=True, timeout=3, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip().startswith("100.")]


def _primary_ip() -> str | None:
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))  # TEST-NET, nothing is sent for UDP connect
        return str(s.getsockname()[0])
    return None


def advertised_urls(settings: Settings) -> list[str]:
    g = settings.mobile.gateway
    if g.advertise_urls:
        return list(g.advertise_urls)
    scheme = "https"
    hosts = local_addresses()
    with contextlib.suppress(OSError):
        name = socket.gethostname()
        hosts.append(name if "." in name else name + ".local")  # "mac.fritz.box" resolves via the router
    return [f"{scheme}://{h}:{g.port}" for h in hosts]


# --------------------------------------------------------------------------- certificate
class GatewayCert:
    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.cert_path = directory / "gateway-cert.pem"
        self.key_path = directory / "gateway-key.pem"

    def ensure(self) -> None:
        if self.cert_path.exists() and self.key_path.exists():
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        self.dir.chmod(0o700)
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "J.A.R.V.I.S. Mobile Gateway")])
        sans: list[x509.GeneralName] = [x509.DNSName("localhost"), x509.DNSName(socket.gethostname())]
        for ip in local_addresses():
            sans.append(x509.IPAddress(ipaddress.ip_address(ip)))
        now = datetime.now(UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )
        self.key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
        self.key_path.chmod(0o600)
        self.cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        logger.info("Generated mobile gateway certificate (%s)", self.fingerprint())

    def fingerprint(self) -> str:
        """SHA-256 over the DER certificate, lowercase hex – what the app pins."""
        cert = x509.load_pem_x509_certificate(self.cert_path.read_bytes())
        return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


class GatewayServer:
    """Second uvicorn server inside the core process, same ASGI app and state."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cert = GatewayCert(settings.paths.data_dir / "mobile")
        self._server: Any = None
        self._task: asyncio.Task[None] | None = None
        self.error: str | None = None

    def fingerprint(self) -> str | None:
        if self.settings.mobile.gateway.tls != "self_signed":
            return None
        self.cert.ensure()
        return self.cert.fingerprint()

    async def start(self, app: Any) -> None:
        import uvicorn

        g = self.settings.mobile.gateway
        kw: dict[str, Any] = {}
        if g.tls == "self_signed":
            self.cert.ensure()
            kw = {"ssl_certfile": str(self.cert.cert_path), "ssl_keyfile": str(self.cert.key_path)}
        # tls "external": a reverse proxy / Tailscale serve terminates TLS → plain HTTP must stay on loopback
        host = g.host if g.tls == "self_signed" else "127.0.0.1"
        config = uvicorn.Config(
            app,
            host=host,
            port=g.port,
            log_level="warning",
            access_log=False,
            ws_ping_interval=20,
            proxy_headers=False,
            lifespan="off",
            **kw,
        )
        self._server = uvicorn.Server(config)
        self._server.install_signal_handlers = lambda: None
        self._task = asyncio.create_task(self._serve(), name="mobile-gateway")

    async def _serve(self) -> None:
        try:
            await self._server.serve()
        except (OSError, SystemExit) as e:
            self.error = str(e)[:200]
            logger.error("Mobile gateway could not start: %s", e)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._task, 5)

    def status(self) -> dict[str, Any]:
        g = self.settings.mobile.gateway
        running = bool(self._server is not None and self._server.started)
        return {
            "enabled": g.enabled,
            "running": running,
            "error": self.error,
            "port": g.port,
            "tls": g.tls,
            "urls": advertised_urls(self.settings),
            "fingerprint": self.fingerprint() if g.tls == "self_signed" else None,
        }
