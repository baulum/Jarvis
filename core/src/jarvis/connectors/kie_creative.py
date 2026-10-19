"""Bildgenerierung über KIE.ai (Werkzeug ``image_generate``).

Spricht die KIE.ai-Market-API direkt an (createTask → recordInfo-Polling) und legt das Ergebnis unter
``<data_dir>/generated-images`` ab. Der API-Key liegt ausschließlich im Keychain (``kie-api-key``) und
taucht weder in Logs noch in Fehlermeldungen oder Ergebnissen auf. Das Ergebnis ist ein lokaler
Dateipfad; der Versand an Sir läuft getrennt über ``telegram_send_photo`` (mit Freigabe).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from jarvis.connectors.base import Connector, Health, Manifest, SecretField
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext, ToolSpec

logger = logging.getLogger(__name__)

DEFAULT_API = "https://api.kie.ai"
DEFAULT_UPLOAD_API = "https://kieai.redpandaai.co"
DEFAULT_MODEL = "google/nano-banana"
DEFAULT_EDIT_MODEL = "google/nano-banana-edit"
ASPECT_RATIOS = ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"]
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MAX_REFERENCE_BYTES = 10 * 1024 * 1024
POLL_INTERVAL_S = 3.0
POLL_TIMEOUT_S = 240.0


def generated_dir(state: Any) -> Path:
    return Path(state.settings.paths.data_dir) / "generated-images"


def allowed_image_roots(state: Any) -> list[Path]:
    """Ordner, aus denen Bilder gelesen bzw. an Dritte gesendet werden dürfen."""
    p = state.settings.paths
    return [generated_dir(state).resolve(), Path(p.workspaces_dir).resolve(), Path(p.projects_dir).resolve()]


def resolve_image_path(state: Any, raw: str, roots: list[Path] | None = None) -> Path:
    """Validiert einen Bildpfad: existiert, ist ein Bild und liegt in einem erlaubten Ordner."""
    path = Path(raw).expanduser().resolve()
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        raise ToolUserError("Nur PNG-, JPG- oder WebP-Bilder sind erlaubt.")
    if not any(path.is_relative_to(r) for r in (roots or allowed_image_roots(state))):
        raise ToolUserError("Das Bild liegt außerhalb der erlaubten Ordner (generierte Bilder, Workspaces, Projekte).")
    if not path.is_file():
        raise ToolUserError(f"Bilddatei nicht gefunden: {path.name}")
    return path


def _obj(props: dict[str, Any], req: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": req or [], "additionalProperties": False}


class KieCreativeConnector(Connector):
    manifest = Manifest(
        id="kie_creative",
        name="KIE.ai Bildgenerierung",
        description="Erzeugt Bilder per Prompt (optional mit Referenzbild) über KIE.ai und legt sie lokal ab. "
        "Der Versand an Sir läuft über den Telegram-Connector (mit Freigabe).",
        category="productivity",
        auth="api_key",
        secrets=[SecretField("kie-api-key", "KIE.ai API-Key", help="https://kie.ai/api-key › Create New Key")],
        settings_schema={
            "type": "object",
            "properties": {
                "model": {"type": "string", "default": DEFAULT_MODEL, "description": "Modell für Text → Bild"},
                "edit_model": {
                    "type": "string",
                    "default": DEFAULT_EDIT_MODEL,
                    "description": "Modell bei Referenzbild",
                },
                "api_base": {"type": "string", "default": DEFAULT_API},
                "upload_base": {"type": "string", "default": DEFAULT_UPLOAD_API},
            },
        },
        docs_url="https://docs.kie.ai/",
        setup_instructions="API-Key unter https://kie.ai/api-key erstellen und hier hinterlegen "
        "(oder `jarvisctl secret set kie-api-key`). Modelle sind optional einstellbar.",
        mcp_note="Das Projekt pje-creative-mcp nutzt dieselbe KIE.ai-API; Jarvis ruft sie hier direkt auf, "
        "damit Risiko, Freigabe und Audit über die Jarvis-Pipeline laufen.",
    )

    # ------------------------------------------------------------------ KIE.ai API
    def _cfg(self, key: str, default: str) -> str:
        return str(self.settings.get(key) or default).rstrip("/")

    def _headers(self) -> dict[str, str]:
        key = self.secret("kie-api-key")
        if not key:
            raise ToolUserError("KIE.ai ist nicht eingerichtet (Secret 'kie-api-key' fehlt).")
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    @staticmethod
    def _check(r: httpx.Response) -> dict[str, Any]:
        """Prüft HTTP- und KIE-Statuscode; Meldungen enthalten nie Header oder Schlüssel."""
        try:
            data = r.json()
        except ValueError:
            data = {}
        code = data.get("code", r.status_code) if isinstance(data, dict) else r.status_code
        if r.status_code == 401 or code == 401:
            raise ToolUserError("KIE.ai lehnt den API-Key ab (401).")
        if r.status_code == 402 or code == 402:
            raise ToolUserError("KIE.ai: Guthaben aufgebraucht (402).")
        if r.status_code >= 400 or code != 200:
            msg = str(data.get("msg", "")) if isinstance(data, dict) else ""
            raise ToolUserError(f"KIE.ai-Fehler {code}: {msg[:200]}")
        return data

    async def health(self) -> Health:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(f"{self._cfg('api_base', DEFAULT_API)}/api/v1/chat/credit", headers=self._headers())
            data = self._check(r)
        except ToolUserError as e:
            return Health("error", str(e))
        except httpx.HTTPError as e:
            return Health("error", f"KIE.ai nicht erreichbar ({type(e).__name__})")
        return Health("ok", f"Guthaben: {data.get('data')}")

    async def _upload_reference(self, path: Path) -> str:
        size = path.stat().st_size
        if size > MAX_REFERENCE_BYTES:
            raise ToolUserError("Das Referenzbild ist größer als 10 MB.")
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        payload = {
            "base64Data": f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}",
            "uploadPath": "jarvis/references",
            "fileName": f"{uuid.uuid4().hex}{path.suffix.lower()}",
        }
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                f"{self._cfg('upload_base', DEFAULT_UPLOAD_API)}/api/file-base64-upload",
                headers=self._headers(),
                json=payload,
            )
        data = self._check(r).get("data") or {}
        url = data.get("downloadUrl") or data.get("fileUrl")
        if not url:
            raise ToolUserError("KIE.ai: Upload des Referenzbilds lieferte keine URL.")
        return str(url)

    async def _run_task(self, model: str, inputs: dict[str, Any]) -> list[str]:
        base = self._cfg("api_base", DEFAULT_API)
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                f"{base}/api/v1/jobs/createTask", headers=self._headers(), json={"model": model, "input": inputs}
            )
            task_id = (self._check(r).get("data") or {}).get("taskId")
            if not task_id:
                raise ToolUserError("KIE.ai: Kein taskId erhalten.")
            deadline = time.monotonic() + POLL_TIMEOUT_S
            while time.monotonic() < deadline:
                await asyncio.sleep(POLL_INTERVAL_S)
                r = await c.get(f"{base}/api/v1/jobs/recordInfo", headers=self._headers(), params={"taskId": task_id})
                info = self._check(r).get("data") or {}
                state = info.get("state")
                if state == "success":
                    try:
                        urls = json.loads(info.get("resultJson") or "{}").get("resultUrls") or []
                    except ValueError:
                        urls = []
                    if not urls:
                        raise ToolUserError("KIE.ai: Aufgabe erfolgreich, aber ohne Ergebnisbild.")
                    return [str(u) for u in urls]
                if state == "fail":
                    raise ToolUserError(f"KIE.ai: Erzeugung fehlgeschlagen: {str(info.get('failMsg', ''))[:200]}")
        raise ToolUserError("KIE.ai: Zeitüberschreitung bei der Bilderzeugung.")

    async def _download(self, url: str) -> Path:
        out_dir = generated_dir(self.state)
        out_dir.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
            r = await c.get(url)  # Ergebnis-URL ist öffentlich; bewusst ohne Authorization-Header
        if r.status_code >= 400 or not r.content:
            raise ToolUserError(f"Bild konnte nicht geladen werden (HTTP {r.status_code}).")
        ctype = r.headers.get("content-type", "").split(";")[0]
        suffix = {"image/jpeg": ".jpg", "image/webp": ".webp"}.get(ctype, ".png")
        path = out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}{suffix}"
        path.write_bytes(r.content)
        path.chmod(0o600)
        return path

    # ------------------------------------------------------------------ tools
    def tools(self) -> list[ToolSpec]:
        async def image_generate(ctx: ToolContext, a: dict[str, Any]) -> Any:
            ref_raw = a.get("reference_image_path")
            inputs: dict[str, Any] = {
                "prompt": a["prompt"],
                "image_size": a.get("aspect_ratio", "1:1"),
                "output_format": "png",
            }
            if ref_raw:
                ref = resolve_image_path(self.state, ref_raw)
                inputs["image_urls"] = [await self._upload_reference(ref)]
                model = self._cfg("edit_model", DEFAULT_EDIT_MODEL)
            else:
                model = self._cfg("model", DEFAULT_MODEL)
            urls = await self._run_task(model, inputs)
            path = await self._download(urls[0])
            return {
                "image_path": str(path),
                "model": model,
                "hint": "Zum Zusenden an Sir telegram_send_photo mit diesem Pfad verwenden (braucht Freigabe).",
            }

        return [
            ToolSpec(
                "image_generate",
                "Erzeugt ein Bild über KIE.ai aus einem Prompt (optional mit Referenzbild) und speichert es lokal. "
                "Gibt den Dateipfad zurück; versendet nichts.",
                _obj(
                    {
                        "prompt": {"type": "string", "minLength": 3, "maxLength": 4000},
                        "aspect_ratio": {"type": "string", "enum": ASPECT_RATIOS, "default": "1:1"},
                        "reference_image_path": {
                            "type": "string",
                            "description": "Optionaler Pfad eines Referenzbilds (generierte Bilder, Workspaces, Projekte)",
                        },
                    },
                    ["prompt"],
                ),
                image_generate,
                "medium",  # verbraucht kostenpflichtiges KIE-Guthaben, aber keine Außenwirkung auf Dritte
                "kie_creative",
                writes=True,
                timeout_s=POLL_TIMEOUT_S + 60,
                summarize=lambda a: f"Bild erzeugen ({a.get('aspect_ratio', '1:1')}): {str(a.get('prompt', ''))[:120]}",
            )
        ]
