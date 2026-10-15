"""Hermes profile provisioning and gateway supervision.

Only documented Hermes configuration surfaces are used: the profile's ``config.yaml``
(model, auxiliary, mcp_servers, platform_toolsets), its ``.env`` (API server settings,
secrets referenced by config) and ``SOUL.md`` (persona). The gateway runs as a supervised
child process of the core so Jarvis can restart it when its tool set changes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx
import yaml

from jarvis.agent.persona import render_soul
from jarvis.secrets import secret_store

if TYPE_CHECKING:
    from jarvis.app_state import AppState

logger = logging.getLogger(__name__)
MCP_TOKEN_SECRET = "hermes-mcp-token"  # noqa: S105 - Keychain account name


def profile_dir(profile: str) -> Path:
    return Path.home() / ".hermes" / "profiles" / profile


def hermes_binary() -> str | None:
    return shutil.which("hermes") or (
        str(Path.home() / ".local/bin/hermes") if (Path.home() / ".local/bin/hermes").exists() else None
    )


def _update_env_file(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in values:
            out.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in values.items() if k not in seen]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    path.chmod(0o600)


def provision_profile(state: AppState, mcp_token: str) -> dict[str, Any]:
    """Write config/.env/SOUL.md for the Hermes profile. Idempotent."""
    s = state.settings
    pdir = profile_dir(s.hermes.profile)
    if not pdir.exists():
        raise RuntimeError(f"Hermes-Profil {s.hermes.profile} fehlt: `hermes profile create {s.hermes.profile}`")
    api_key = secret_store.ensure_random(s.hermes.api_key_secret)
    port = str(urlparse(s.hermes.api_url).port or 8642)
    _update_env_file(
        pdir / ".env",
        {
            "API_SERVER_ENABLED": "true",
            "API_SERVER_KEY": api_key,
            "API_SERVER_HOST": "127.0.0.1",
            "API_SERVER_PORT": port,
            "JARVIS_MCP_TOKEN": mcp_token,
            "OPENAI_API_KEY": "ollama",
        },
    )
    cfg_path = pdir / "config.yaml"
    cfg: dict[str, Any] = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    cfg = cfg or {}
    cfg.setdefault("model", {}).update(
        {
            "provider": "custom",
            "base_url": s.llm.ollama_url.rstrip("/") + "/v1",
            "default": s.llm.agent_model,
            "context_length": 65536,
        }
    )
    cfg.setdefault("agent", {})["reasoning_effort"] = "none"
    cfg.setdefault("memory", {}).update({"memory_enabled": False, "user_profile_enabled": False})
    main_aux = {"provider": "main", "model": s.llm.agent_model, "reasoning_effort": "none"}
    aux = cfg.setdefault("auxiliary", {})
    for task in ("vision", "web_extract", "compression", "session_search", "curator", "background_review"):
        aux[task] = dict(main_aux)
    aux["title_generation"] = {"enabled": False, "model_upgrade_enabled": False, **main_aux}
    cfg["mcp_servers"] = {
        "jarvis": {
            "url": f"http://127.0.0.1:{s.server.port}/mcp",
            "headers": {"Authorization": "Bearer ${env:JARVIS_MCP_TOKEN}"},
            "timeout": 600,
            "connect_timeout": 30,
            "supports_parallel_tool_calls": False,
            "enabled": True,
        }
    }
    cfg.setdefault("platform_toolsets", {})["api_server"] = ["mcp-jarvis"]
    # A 4B model handles a direct tool list far better than deferred tool_search indirection.
    cfg.setdefault("tools", {})["tool_search"] = {"enabled": "off"}
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    soul = render_soul(state.personality)
    (pdir / "SOUL.md").write_text(soul, encoding="utf-8")
    return {"profile_dir": str(pdir), "api_port": port}


def sync_soul(state: AppState) -> bool:
    pdir = profile_dir(state.settings.hermes.profile)
    if not pdir.exists():
        return False
    soul = render_soul(state.personality)
    path = pdir / "SOUL.md"
    if not path.exists() or path.read_text(encoding="utf-8") != soul:
        path.write_text(soul, encoding="utf-8")
        return True
    return False


class HermesSupervisor:
    """Runs the Hermes host gateway (``hermes gateway run --replace``) as a supervised child.

    Hermes serves all profiles from one multiplexing host gateway; the ``jarvis`` profile is
    reachable under ``/p/jarvis``. The gateway is (re)started only after the core's MCP endpoint
    is reachable, so Hermes picks up the current Jarvis tool set; ``restart()`` refreshes it after
    connector changes.
    """

    def __init__(self, state: AppState) -> None:
        self.state = state
        self.proc: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self.restarts = 0
        self.log_path = state.settings.paths.log_dir / "hermes-gateway.log"

    async def start(self) -> None:
        if not self.state.settings.hermes.enabled or hermes_binary() is None:
            return
        self._task = asyncio.create_task(self._run())

    async def _wait_core_ready(self) -> None:
        url = f"http://127.0.0.1:{self.state.settings.server.port}/api/health"
        async with httpx.AsyncClient(timeout=2) as c:
            for _ in range(120):
                try:
                    if (await c.get(url)).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)

    async def _spawn(self) -> asyncio.subprocess.Process:
        exe = hermes_binary()
        assert exe is not None
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = self.log_path.open("ab")
        env = {k: v for k, v in os.environ.items() if not k.startswith("JARVIS_SECRET")}
        env["PATH"] = f"{Path.home() / '.local/bin'}:{env.get('PATH', '')}"
        return await asyncio.create_subprocess_exec(
            exe,
            "gateway",
            "run",
            "--replace",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            env=env,
            start_new_session=True,
        )

    async def _run(self) -> None:
        await self._wait_core_ready()
        backoff = 2.0
        while not self._stopping:
            try:
                self.proc = await self._spawn()
                logger.info("Hermes host gateway started (pid %s)", self.proc.pid)
                started = asyncio.get_running_loop().time()
                rc = await self.proc.wait()
                if self._stopping:
                    return
                if asyncio.get_running_loop().time() - started > 60:
                    backoff = 2.0
                logger.warning("Hermes gateway exited with %s; restarting in %.0fs", rc, backoff)
                self.restarts += 1
            except Exception:
                logger.exception("Hermes gateway failed to start")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done() and not self._stopping

    async def restart(self) -> None:
        """Restart to refresh Hermes' MCP tool cache (e.g. after a connector was enabled)."""
        if self.proc and self.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.proc.terminate()

    async def stop(self) -> None:
        self._stopping = True
        if self.proc and self.proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 15)
            except TimeoutError:
                self.proc.kill()
        if self._task:
            self._task.cancel()
