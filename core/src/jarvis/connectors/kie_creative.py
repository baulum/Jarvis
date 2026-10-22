"""Bild- und Videogenerierung über KIE.ai (Werkzeuge ``image_generate`` und ``video_generate``).

Spricht die KIE.ai-Market-API direkt an (createTask → recordInfo-Polling) und legt das Ergebnis unter
``<data_dir>/generated-images`` bzw. ``<data_dir>/generated-videos`` ab. Der API-Key liegt ausschließlich im
Keychain (``kie-api-key``) und taucht weder in Logs noch in Fehlermeldungen oder Ergebnissen auf. Das Ergebnis
ist ein lokaler Dateipfad; der Versand an Sir läuft getrennt über ``telegram_send_photo`` (mit Freigabe).

Im Chat erscheint das Ergebnis als Anzeige-Artefakt (``jarvis.tools.artifacts``) am Tool-Ereignis; die
Oberflächen laden die Datei über ``GET /api/media/{images|videos}/{name}`` – nur per Dateiname, nur aus diesen
beiden Ordnern (``resolve_media_file``).

Die Modell-IDs stehen in ``Settings.creative`` (``config/jarvis.yaml``, ``JARVIS__CREATIVE__…``); ein Modell
in den Connector-Einstellungen der App hat Vorrang.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import re
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from jarvis.config import CreativeCfg
from jarvis.connectors.base import Connector, Health, Manifest, SecretField
from jarvis.tools.artifacts import artifacts
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext, ToolSpec

logger = logging.getLogger(__name__)

DEFAULT_API = "https://api.kie.ai"
DEFAULT_UPLOAD_API = "https://kieai.redpandaai.co"
# GPT Image 2.5 Sunburst: Schnittmenge der Seitenverhältnisse von Text→Bild und Bild→Bild (docs.kie.ai)
ASPECT_RATIOS = ["auto", "1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"]
# Wan 3.0 Video (docs.kie.ai/market/wan/3-0-video)
VIDEO_ASPECT_RATIOS = ["adaptive", "16:9", "4:3", "1:1", "3:4", "9:16"]
VIDEO_RESOLUTIONS = ["480P", "720P", "1080P"]
VIDEO_MIN_S, VIDEO_MAX_S = 2, 30
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".webm", ".mov"}
MEDIA_DIRS = {"images": "generated-images", "videos": "generated-videos"}
MEDIA_SUFFIXES = {"images": IMAGE_SUFFIXES, "videos": VIDEO_SUFFIXES}
MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
}
_MEDIA_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}\.[a-z0-9]{2,5}")
MAX_REFERENCE_BYTES = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 300 * 1024 * 1024
POLL_INTERVAL_S = 3.0
VIDEO_POLL_INTERVAL_S = 10.0
POLL_TIMEOUT_S = 240.0
_DEFAULTS = CreativeCfg()


def creative_cfg(state: Any) -> CreativeCfg:
    cfg = getattr(state.settings, "creative", None)
    return cfg if isinstance(cfg, CreativeCfg) else _DEFAULTS


def media_dir(state: Any, kind: str) -> Path:
    return Path(state.settings.paths.data_dir) / MEDIA_DIRS[kind]


def generated_dir(state: Any) -> Path:
    return media_dir(state, "images")


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


def resolve_media_file(state: Any, kind: str, name: str) -> Path | None:
    """Eine erzeugte Datei zum Anzeigen im Chat – nur ein nackter Dateiname aus generated-images/-videos.

    Kein Pfad, keine Trennzeichen, kein ``..``, nur passende Endungen; der aufgelöste Pfad (auch über
    Symlinks) muss im Ordner liegen. ``None`` heißt: gibt es nicht bzw. nicht erlaubt (→ 404).
    """
    if kind not in MEDIA_DIRS or not _MEDIA_NAME.fullmatch(name or ""):
        return None
    if Path(name).suffix.lower() not in MEDIA_SUFFIXES[kind]:
        return None
    root = media_dir(state, kind).resolve()
    path = (root / name).resolve()
    if path.parent != root or not path.is_file():
        return None
    return path


def image_inputs(model: str, prompt: str, aspect: str, resolution: str, refs: list[str]) -> dict[str, Any]:
    """Request-Eingaben je Modellfamilie (Parameternamen laut docs.kie.ai)."""
    if model.startswith("google/nano-banana"):  # älteres Schema, falls Sir es in den Einstellungen wählt
        inputs: dict[str, Any] = {
            "prompt": prompt,
            "image_size": "1:1" if aspect == "auto" else aspect,
            "output_format": "png",
        }
        if refs:
            inputs["image_urls"] = refs
        return inputs
    # GPT Image 2.5 Sunburst (Text→Bild und Bild→Bild)
    inputs = {"prompt": prompt, "aspect_ratio": aspect, "resolution": resolution}
    if refs:
        inputs["input_urls"] = refs
    return inputs


def video_inputs(
    prompt: str, aspect: str, duration: int, resolution: str, audio: bool, first_frame: str | None
) -> dict[str, Any]:
    """Wan 3.0: Referenzbild = erstes Bild des Videos (``first_frame_url``)."""
    inputs: dict[str, Any] = {
        "prompt": prompt,
        "aspect_ratio": aspect,
        "duration": duration,
        "resolution": resolution,
        "audio": audio,
    }
    if first_frame:
        inputs["first_frame_url"] = first_frame
    return inputs


def _obj(props: dict[str, Any], req: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": req or [], "additionalProperties": False}


_SHOWN_NOTE = (
    "Sir sieht das {what} bereits im Chat. Dateipfad und artifact_id weder wiederholen noch vorlesen – "
    "kurz sagen, was zu sehen ist."
)


class KieCreativeConnector(Connector):
    manifest = Manifest(
        id="kie_creative",
        name="KIE.ai Bild & Video",
        description="Erzeugt Bilder und Videos per Prompt (optional mit Referenzbild) über KIE.ai und legt sie "
        "lokal ab. Der Versand an Sir läuft über den Telegram-Connector (mit Freigabe).",
        category="productivity",
        auth="api_key",
        secrets=[SecretField("kie-api-key", "KIE.ai API-Key", help="https://kie.ai/api-key › Create New Key")],
        settings_schema={
            "type": "object",
            "properties": {
                "image_model": {
                    "type": "string",
                    "default": _DEFAULTS.image_model,
                    "description": "Modell für Text → Bild (leer = Standard aus der Konfiguration)",
                },
                "image_edit_model": {
                    "type": "string",
                    "default": _DEFAULTS.image_edit_model,
                    "description": "Modell bei Referenzbild (leer = Standard aus der Konfiguration)",
                },
                "video_model": {
                    "type": "string",
                    "default": _DEFAULTS.video_model,
                    "description": "Modell für Videos (leer = Standard aus der Konfiguration)",
                },
                "api_base": {"type": "string", "default": DEFAULT_API},
                "upload_base": {"type": "string", "default": DEFAULT_UPLOAD_API},
            },
        },
        docs_url="https://docs.kie.ai/",
        setup_instructions="API-Key unter https://kie.ai/api-key erstellen und hier hinterlegen "
        "(oder `jarvisctl secret set kie-api-key`). Standardmodelle stehen in config/jarvis.yaml unter "
        "`creative:`; hier eingetragene Modelle haben Vorrang.",
        mcp_note="Das Projekt pje-creative-mcp nutzt dieselbe KIE.ai-API; Jarvis ruft sie hier direkt auf, "
        "damit Risiko, Freigabe und Audit über die Jarvis-Pipeline laufen.",
    )

    # Tests setzen hier einen httpx.MockTransport ein; produktiv bleibt es beim Standard-Transport.
    transport: httpx.AsyncBaseTransport | None = None

    # ------------------------------------------------------------------ KIE.ai API
    def _cfg(self, key: str, default: str) -> str:
        return str(self.settings.get(key) or default).rstrip("/")

    def model(self, key: str) -> str:
        """``image_model`` | ``image_edit_model`` | ``video_model``: Connector-Einstellung, sonst Konfiguration."""
        return str(self.settings.get(key) or getattr(creative_cfg(self.state), key)).strip()

    def _client(self, timeout: float, **kw: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self.transport, **kw)

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
        return data if isinstance(data, dict) else {}

    async def health(self) -> Health:
        try:
            async with self._client(15) as c:
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
        async with self._client(60) as c:
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

    async def _run_task(
        self,
        model: str,
        inputs: dict[str, Any],
        *,
        timeout_s: float = POLL_TIMEOUT_S,
        interval_s: float | None = None,
        what: str = "Bild",
    ) -> list[str]:
        base = self._cfg("api_base", DEFAULT_API)
        interval_s = POLL_INTERVAL_S if interval_s is None else interval_s
        async with self._client(30) as c:
            r = await c.post(
                f"{base}/api/v1/jobs/createTask", headers=self._headers(), json={"model": model, "input": inputs}
            )
            task_id = (self._check(r).get("data") or {}).get("taskId")
            if not task_id:
                raise ToolUserError("KIE.ai: Kein taskId erhalten.")
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                await asyncio.sleep(interval_s)
                try:
                    r = await c.get(
                        f"{base}/api/v1/jobs/recordInfo", headers=self._headers(), params={"taskId": task_id}
                    )
                except httpx.TransportError:  # kurzer Netzaussetzer: die Aufgabe läuft bei KIE weiter
                    logger.debug("KIE recordInfo for %s failed, retrying", task_id, exc_info=True)
                    continue
                info = self._check(r).get("data") or {}
                state = info.get("state")
                if state == "success":
                    try:
                        urls = json.loads(info.get("resultJson") or "{}").get("resultUrls") or []
                    except ValueError:
                        urls = []
                    if not urls:
                        raise ToolUserError(f"KIE.ai: Aufgabe erfolgreich, aber ohne Ergebnis ({what}).")
                    return [str(u) for u in urls]
                if state == "fail":
                    raise ToolUserError(f"KIE.ai: Erzeugung fehlgeschlagen: {str(info.get('failMsg', ''))[:200]}")
        raise ToolUserError(
            f"KIE.ai: Zeitüberschreitung bei der Erzeugung ({what}, Aufgabe {task_id} läuft bei KIE evtl. weiter)."
        )

    async def _download(self, url: str, kind: str = "images") -> Path:
        out_dir = media_dir(self.state, kind)
        out_dir.mkdir(parents=True, exist_ok=True)
        async with self._client(60 if kind == "images" else 180, follow_redirects=True) as c:
            r = await c.get(url)  # Ergebnis-URL ist öffentlich; bewusst ohne Authorization-Header
        if r.status_code >= 400 or not r.content:
            raise ToolUserError(f"Ergebnis konnte nicht geladen werden (HTTP {r.status_code}).")
        if len(r.content) > MAX_VIDEO_BYTES:
            raise ToolUserError("Ergebnisdatei ist unerwartet groß und wurde verworfen.")
        ctype = r.headers.get("content-type", "").split(";")[0].strip()
        if kind == "videos":
            suffix = {"video/webm": ".webm", "video/quicktime": ".mov"}.get(ctype, ".mp4")
        else:
            suffix = {"image/jpeg": ".jpg", "image/webp": ".webp"}.get(ctype, ".png")
        path = out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}{suffix}"
        path.write_bytes(r.content)
        path.chmod(0o600)
        return path

    # ------------------------------------------------------------------ tools
    async def generate_image(self, a: dict[str, Any]) -> dict[str, Any]:
        cfg = creative_cfg(self.state)
        prompt = str(a["prompt"])
        aspect = str(a.get("aspect_ratio") or "1:1")
        ref_raw = a.get("reference_image_path")
        refs: list[str] = []
        if ref_raw:
            refs = [await self._upload_reference(resolve_image_path(self.state, ref_raw))]
            model = self.model("image_edit_model")
        else:
            model = self.model("image_model")
        urls = await self._run_task(model, image_inputs(model, prompt, aspect, cfg.image_resolution, refs))
        path = await self._download(urls[0], "images")
        artifact_id = artifacts.put(
            "image",
            {"media": "images", "file": path.name, "path": str(path), "prompt": prompt[:300], "aspect_ratio": aspect},
        )
        return {
            "image_path": str(path),
            "model": model,
            "artifact_id": artifact_id,
            "note": _SHOWN_NOTE.format(what="Bild"),
            "hint": "Zum Zusenden an Sir telegram_send_photo mit image_path verwenden (braucht Freigabe).",
        }

    async def generate_video(self, a: dict[str, Any]) -> dict[str, Any]:
        cfg = creative_cfg(self.state)
        prompt = str(a["prompt"])
        aspect = str(a.get("aspect_ratio") or "adaptive")
        duration = int(a.get("duration_s") or 5)
        if not VIDEO_MIN_S <= duration <= VIDEO_MAX_S:
            raise ToolUserError(f"Die Dauer muss zwischen {VIDEO_MIN_S} und {VIDEO_MAX_S} Sekunden liegen.")
        resolution = str(a.get("resolution") or cfg.video_resolution)
        first_frame = None
        if a.get("reference_image_path"):
            first_frame = await self._upload_reference(resolve_image_path(self.state, a["reference_image_path"]))
        model = self.model("video_model")
        inputs = video_inputs(prompt, aspect, duration, resolution, bool(a.get("audio", True)), first_frame)
        urls = await self._run_task(
            model, inputs, timeout_s=cfg.video_timeout_s, interval_s=VIDEO_POLL_INTERVAL_S, what="Video"
        )
        path = await self._download(urls[0], "videos")
        artifact_id = artifacts.put(
            "video",
            {
                "media": "videos",
                "file": path.name,
                "path": str(path),
                "prompt": prompt[:300],
                "aspect_ratio": aspect,
                "duration_s": duration,
            },
        )
        return {
            "video_path": str(path),
            "model": model,
            "duration_s": duration,
            "artifact_id": artifact_id,
            "note": _SHOWN_NOTE.format(what="Video"),
        }

    def tools(self) -> list[ToolSpec]:
        async def image_generate(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await self.generate_image(a)

        async def video_generate(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await self.generate_video(a)

        ref_desc = "Optionaler Pfad eines Referenzbilds (generierte Bilder, Workspaces, Projekte)"
        return [
            ToolSpec(
                "image_generate",
                "Erzeugt ein Bild über KIE.ai aus einem Prompt (optional mit Referenzbild) und speichert es lokal. "
                "Sir sieht es direkt im Chat. Gibt den Dateipfad zurück; versendet nichts.",
                _obj(
                    {
                        "prompt": {"type": "string", "minLength": 3, "maxLength": 4000},
                        "aspect_ratio": {"type": "string", "enum": ASPECT_RATIOS, "default": "1:1"},
                        "reference_image_path": {"type": "string", "description": ref_desc},
                    },
                    ["prompt"],
                ),
                image_generate,
                "medium",  # verbraucht kostenpflichtiges KIE-Guthaben, aber keine Außenwirkung auf Dritte
                "kie_creative",
                writes=True,
                timeout_s=POLL_TIMEOUT_S + 60,
                summarize=lambda a: f"Bild erzeugen ({a.get('aspect_ratio', '1:1')}): {str(a.get('prompt', ''))[:120]}",
            ),
            ToolSpec(
                "video_generate",
                "Erzeugt ein kurzes Video über KIE.ai (Wan 3.0) aus einem Prompt, optional mit Referenzbild als "
                "erstem Bild, und speichert es lokal. Dauert oft mehrere Minuten. Sir sieht es direkt im Chat. "
                "Gibt den Dateipfad zurück; versendet nichts.",
                _obj(
                    {
                        "prompt": {
                            "type": "string",
                            "minLength": 3,
                            "maxLength": 4000,
                            "description": "Beschreibt Bewegung, Kamera und Ablauf, nicht nur ein Standbild",
                        },
                        "aspect_ratio": {"type": "string", "enum": VIDEO_ASPECT_RATIOS, "default": "adaptive"},
                        "duration_s": {
                            "type": "integer",
                            "minimum": VIDEO_MIN_S,
                            "maximum": VIDEO_MAX_S,
                            "default": 5,
                            "description": "Länge in Sekunden",
                        },
                        "resolution": {"type": "string", "enum": VIDEO_RESOLUTIONS},
                        "audio": {"type": "boolean", "default": True, "description": "Tonspur erzeugen"},
                        "reference_image_path": {"type": "string", "description": ref_desc + " – wird erstes Bild"},
                    },
                    ["prompt"],
                ),
                video_generate,
                "medium",  # wie image_generate: kostet Guthaben (Video deutlich mehr), keine Außenwirkung
                "kie_creative",
                writes=True,
                timeout_s=creative_cfg(self.state).video_timeout_s + 180,  # + Upload und Download
                summarize=lambda a: (
                    f"Video erzeugen ({a.get('duration_s', 5)} s, {a.get('aspect_ratio', 'adaptive')}): "
                    f"{str(a.get('prompt', ''))[:120]}"
                ),
            ),
        ]
