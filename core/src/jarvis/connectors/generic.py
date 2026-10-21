"""Generic connectors: local shell (allowlisted), HTTP API and webhooks."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import shlex
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from jarvis.connectors.base import Connector, Health, Manifest, SecretField
from jarvis.connectors.browser import check_public_url
from jarvis.security.guards import wrap_untrusted
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext, ToolSpec

_FORBIDDEN_ARG_CHARS = set(";|&`$<>\n")


def _obj(props: dict[str, Any], req: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": req or [], "additionalProperties": False}


class ShellConnector(Connector):
    manifest = Manifest(
        id="shell",
        name="Lokale Shell",
        description="Führt ausschließlich freigegebene Programme (permissions.yaml: shell_allowlist) als "
        "Argumentliste aus – ohne Shell-Interpretation.",
        category="system",
        auth="local",
        default_enabled=True,
    )

    async def health(self) -> Health:
        allow = self.state.permissions.shell_allowlist
        missing = [b for b in allow if shutil.which(b, path=self._search_path()) is None]
        return Health("ok", f"{len(allow) - len(missing)} Programme erlaubt", details={"not_installed": missing})

    def _search_path(self) -> str:
        """PATH for allowlisted programs: the core runs under launchd without a shell profile, so Flutter, Homebrew and
        the Android SDK are added (``coding.extra_path`` is shared with the coding runner)."""
        extra = [os.path.expanduser(str(p)) for p in getattr(self.state.settings.coding, "extra_path", None) or []]
        fixed = ["/opt/homebrew/bin", "/usr/local/bin", os.path.expanduser("~/Library/Android/sdk/platform-tools")]
        return ":".join([os.environ.get("PATH", ""), *extra, *fixed])

    def tools(self) -> list[ToolSpec]:
        async def run(ctx: ToolContext, a: dict[str, Any]) -> Any:
            argv = a.get("argv") or shlex.split(a.get("command", ""))
            if not argv:
                raise ToolUserError("Kein Befehl angegeben")
            exe = argv[0]
            if exe not in self.state.permissions.shell_allowlist or "/" in exe:
                raise ToolUserError(f"'{exe}' ist nicht in der Shell-Allowlist")
            if any(set(arg) & _FORBIDDEN_ARG_CHARS for arg in argv[1:]):
                raise ToolUserError("Argumente mit Shell-Sonderzeichen sind nicht erlaubt")
            search_path = self._search_path()
            path = shutil.which(exe, path=search_path)
            if path is None:
                raise ToolUserError(f"{exe} ist nicht installiert")
            cwd = Path(os.path.expanduser(a.get("cwd") or "~")).resolve()
            home = Path.home().resolve()
            if not cwd.is_relative_to(home):
                raise ToolUserError("Arbeitsverzeichnis muss im Benutzerordner liegen")
            env = {k: v for k, v in os.environ.items() if not k.startswith(("JARVIS_SECRET", "AWS_", "GITHUB_TOKEN"))}
            env["PATH"] = search_path
            proc = await asyncio.create_subprocess_exec(
                path,
                *argv[1:],
                cwd=cwd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=float(a.get("timeout_s", 60)))
            except TimeoutError:
                proc.kill()
                raise ToolUserError("Befehl hat das Zeitlimit überschritten") from None
            return {
                "exit_code": proc.returncode,
                "stdout": out.decode(errors="replace")[-8000:],
                "stderr": err.decode(errors="replace")[-4000:],
            }

        return [
            ToolSpec(
                "shell_run",
                "Führt ein freigegebenes Kommandozeilenprogramm aus (argv-Liste, kein Shell-Syntax).",
                _obj(
                    {
                        "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                        "command": {"type": "string"},
                        "cwd": {"type": "string"},
                        "timeout_s": {"type": "integer", "minimum": 1, "maximum": 600},
                    }
                ),
                run,
                "high",
                "shell",
                writes=False,
                timeout_s=620,
                summarize=lambda a: "Shell: " + (" ".join(a.get("argv") or []) or a.get("command", "")),
            )
        ]


class HttpApiConnector(Connector):
    manifest = Manifest(
        id="http",
        name="HTTP API",
        description="Ruft frei konfigurierbare HTTP-APIs auf. Nur Hosts aus der Allowlist; GET ist lesend, "
        "schreibende Methoden benötigen eine Freigabe.",
        category="developer",
        auth="token",
        secrets=[SecretField("http-api-token", "Optionaler Bearer-Token", required=False)],
        settings_schema={
            "type": "object",
            "properties": {"allowed_hosts": {"type": "array", "items": {"type": "string"}, "default": []}},
            "required": ["allowed_hosts"],
        },
        default_enabled=False,
    )

    def _check(self, url: str) -> str:
        host = urlparse(url).hostname or ""
        allowed = self.settings.get("allowed_hosts") or []
        if not any(host == h or host.endswith("." + h) for h in allowed):
            raise ToolUserError(f"Host {host} ist nicht in allowed_hosts")
        return check_public_url(url)

    def tools(self) -> list[ToolSpec]:
        async def call(method: str, a: dict[str, Any]) -> Any:
            url = await asyncio.to_thread(self._check, a["url"])
            headers = {"User-Agent": "Jarvis/1.0"}
            token = self.secret("http-api-token")
            if token and a.get("use_token"):
                headers["Authorization"] = f"Bearer {token}"
            async with httpx.AsyncClient(timeout=30, follow_redirects=False) as c:
                r = await c.request(method, url, json=a.get("json"), params=a.get("params"), headers=headers)
            body = r.text[:20000]
            return {"status": r.status_code, **wrap_untrusted(body, url, 12000)}

        async def get(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await call("GET", a)

        async def send(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await call(a.get("method", "POST"), a)

        props = {"url": {"type": "string"}, "params": {"type": "object"}, "use_token": {"type": "boolean"}}
        return [
            ToolSpec(
                "http_get",
                "HTTP-GET auf eine freigegebene API.",
                _obj(props, ["url"]),
                get,
                "read",
                "http",
                untrusted_output=True,
            ),
            ToolSpec(
                "http_send",
                "HTTP POST/PUT/PATCH/DELETE auf eine freigegebene API.",
                _obj(
                    {
                        **props,
                        "method": {"type": "string", "enum": ["POST", "PUT", "PATCH", "DELETE"]},
                        "json": {"type": "object"},
                    },
                    ["url"],
                ),
                send,
                "high",
                "http",
                writes=True,
                untrusted_output=True,
                summarize=lambda a: f"HTTP {a.get('method', 'POST')} {a['url']}",
            ),
        ]


class WebhookConnector(Connector):
    """Outbound webhooks (tool) and inbound webhooks (``POST /api/webhooks/{name}``, HMAC-SHA256)."""

    manifest = Manifest(
        id="webhook",
        name="Webhooks",
        description="Eingehende Webhooks (HMAC-signiert) starten Jarvis-Anweisungen; ausgehende Webhooks "
        "senden Ereignisse an konfigurierte Ziele.",
        category="developer",
        auth="token",
        secrets=[
            SecretField(
                "webhook-signing-secret",
                "HMAC-Secret für eingehende Webhooks",
                help="Absender signieren den Body mit HMAC-SHA256 im Header X-Jarvis-Signature (Format: sha256=<hex>).",
            )
        ],
        settings_schema={
            "type": "object",
            "properties": {
                "outbound_targets": {"type": "object", "additionalProperties": {"type": "string"}, "default": {}},
                "inbound_routes": {"type": "object", "additionalProperties": {"type": "string"}, "default": {}},
            },
        },
        setup_instructions="inbound_routes: {name: 'Anweisung an Jarvis, {payload} wird ersetzt'}; "
        "outbound_targets: {name: 'https://...'}",
    )

    def verify(self, body: bytes, signature: str | None) -> bool:
        secret = self.secret("webhook-signing-secret")
        if not secret or not signature or not signature.startswith("sha256="):
            return False
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature.removeprefix("sha256="))

    def tools(self) -> list[ToolSpec]:
        async def send(ctx: ToolContext, a: dict[str, Any]) -> Any:
            targets = self.settings.get("outbound_targets") or {}
            url = targets.get(a["target"])
            if not url:
                raise ToolUserError(f"Unbekanntes Webhook-Ziel. Konfiguriert: {', '.join(targets) or '–'}")
            await asyncio.to_thread(check_public_url, url)
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(url, json=a.get("payload") or {})
            return {"status": r.status_code}

        return [
            ToolSpec(
                "webhook_send",
                "Sendet ein Ereignis an ein konfiguriertes Webhook-Ziel.",
                _obj({"target": {"type": "string"}, "payload": {"type": "object"}}, ["target"]),
                send,
                "high",
                "webhook",
                writes=True,
                summarize=lambda a: f"Webhook an '{a['target']}' senden",
            )
        ]
