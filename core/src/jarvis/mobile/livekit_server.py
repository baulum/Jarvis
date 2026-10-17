"""Self-hosted LiveKit server (WebRTC SFU) supervised by the core.

* Signalling listens on 127.0.0.1:7880 only – phones reach it through the TLS gateway (``/rtc`` proxy).
* Media (DTLS-SRTP, always encrypted) uses UDP ``rtc_udp_start..end`` or TCP ``rtc_tcp_port`` on the LAN /
  Tailscale interfaces. Without ``node_ip`` LiveKit offers every local interface as ICE candidate, so the
  same server works in the home network and over Tailscale. Over the open internet (mobile data without
  Tailscale) a VPS with public IP + TURN is needed (see docs/MOBILE.md).
* API key/secret are generated once and stored in the Keychain.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import yaml

from jarvis.config import LiveKitCfg
from jarvis.secrets import secret_store

logger = logging.getLogger(__name__)


class LiveKitServer:
    def __init__(self, cfg: LiveKitCfg, data_dir: Path, log_dir: Path) -> None:
        self.cfg = cfg
        self.config_path = data_dir / "mobile" / "livekit.yaml"
        self.log_path = log_dir / "livekit.log"
        self._proc: asyncio.subprocess.Process | None = None
        self._watch: asyncio.Task[None] | None = None
        self._stopping = False
        self.error: str | None = None

    # ------------------------------------------------------------------ credentials
    def credentials(self) -> tuple[str, str]:
        key = secret_store.get(self.cfg.api_key_secret)
        secret = secret_store.get(self.cfg.api_secret_secret)
        if not key or not secret:
            key = "jarvis-" + secrets.token_hex(4)
            secret = secrets.token_urlsafe(36)
            secret_store.set(self.cfg.api_key_secret, key)
            secret_store.set(self.cfg.api_secret_secret, secret)
        return key, secret

    def mint_token(self, room: str, identity: str, name: str, ttl_s: int = 900, *, agent: bool = False) -> str:
        from livekit import api

        key, secret = self.credentials()
        grants = api.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=True)
        tok = (
            api.AccessToken(key, secret)
            .with_identity(identity)
            .with_name(name)
            .with_kind("agent" if agent else "standard")
            .with_ttl(timedelta(seconds=ttl_s))
            .with_grants(grants)
        )
        return str(tok.to_jwt())

    # ------------------------------------------------------------------ process
    def write_config(self) -> None:
        key, secret = self.credentials()
        port = int(self.cfg.internal_url.rsplit(":", 1)[-1])
        rtc: dict[str, Any] = {
            "tcp_port": self.cfg.rtc_tcp_port,
            "port_range_start": self.cfg.rtc_udp_start,
            "port_range_end": self.cfg.rtc_udp_end,
            "use_external_ip": False,
        }
        if self.cfg.node_ip:
            rtc["node_ip"] = self.cfg.node_ip
        conf: dict[str, Any] = {
            "port": port,
            "bind_addresses": ["127.0.0.1"],
            "rtc": rtc,
            "keys": {key: secret},
            "room": {"empty_timeout": 60, "departure_timeout": 10, "max_participants": 3},
            "logging": {"level": "info", "json": True},
            "turn": {"enabled": self.cfg.turn_enabled},
        }
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(yaml.safe_dump(conf), encoding="utf-8")
        self.config_path.chmod(0o600)

    def binary(self) -> str | None:
        return shutil.which(self.cfg.binary) or next(
            (p for p in ("/opt/homebrew/bin/livekit-server", "/usr/local/bin/livekit-server") if Path(p).exists()),
            None,
        )

    async def start(self) -> None:
        if not self.cfg.enabled or not self.cfg.manage_process:
            return
        if await self.healthy():
            logger.info("LiveKit already running at %s", self.cfg.internal_url)
            return
        binary = self.binary()
        if binary is None:
            self.error = "livekit-server nicht installiert (brew install livekit)"
            logger.warning(self.error)
            return
        self.write_config()
        self._stopping = False
        self._watch = asyncio.create_task(self._supervise(binary), name="livekit-supervisor")

    async def _supervise(self, binary: str) -> None:
        backoff = 1.0
        while not self._stopping:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("ab") as log:
                self._proc = await asyncio.create_subprocess_exec(
                    binary, "--config", str(self.config_path), stdout=log, stderr=log
                )
                logger.info("livekit-server started (pid %s)", self._proc.pid)
                code = await self._proc.wait()
            if self._stopping:
                return
            self.error = f"livekit-server beendet (Code {code})"
            logger.warning("%s – Neustart in %.0f s", self.error, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def stop(self) -> None:
        self._stopping = True
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._proc.wait(), 5)
        if self._watch is not None:
            self._watch.cancel()

    async def healthy(self) -> bool:
        http = self.cfg.internal_url.replace("ws://", "http://").replace("wss://", "https://")
        try:
            async with httpx.AsyncClient(timeout=1.5) as c:
                r = await c.get(http + "/")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def status(self) -> dict[str, Any]:
        ok = await self.healthy()
        return {
            "enabled": self.cfg.enabled,
            "running": ok,
            "managed": self.cfg.manage_process,
            "error": None if ok else self.error,
            "internal_url": self.cfg.internal_url,
            "udp": f"{self.cfg.rtc_udp_start}-{self.cfg.rtc_udp_end}",
            "tcp": self.cfg.rtc_tcp_port,
        }
