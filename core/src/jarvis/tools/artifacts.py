"""Anzeige-Artefakte von Werkzeugen (z. B. Routengeometrie für die Karte im Chat).

Ein Werkzeug legt große, nur für die Oberfläche bestimmte Daten hier ab und gibt dem Modell nur die
kurze ``artifact_id`` zurück – das spart Tokens und hält Geometrie aus dem Kontext. Der Orchestrator
findet die ID im Werkzeugergebnis, entnimmt das Artefakt und speichert es am Tool-Ereignis der
Zeitleiste (``display``); damit überlebt es auch ein Neuladen des Chats.

Erzeugte Bilder und Videos hängen nicht allein an diesem flüchtigen Speicher: ``media_display`` leitet die
Anzeige aus dem Werkzeugergebnis selbst ab (``image_path``/``video_path`` → nackter Dateiname). Das greift,
wenn das Artefakt schon entnommen wurde (Ergebnis aus dem Idempotenz-Ledger, erneuter Aufruf nach einer
Freigabe), abgelaufen ist oder der Core zwischendurch neu gestartet wurde.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections import OrderedDict
from pathlib import PurePath
from typing import Any

_ID_RE = re.compile(r"art_[0-9a-f]{20}")
# gleiche Regel wie kie_creative.resolve_media_file: nur ein nackter Dateiname
_MEDIA_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}\.[a-z0-9]{2,5}")
_MEDIA_KEYS = {"image_path": ("image", "images"), "video_path": ("video", "videos")}
_HIDDEN = ("path",)  # Anzeige-Artefakte tragen keine absoluten Pfade in die Zeitleiste


def payload_data(raw: str | dict[str, Any] | None) -> Any:
    """Die ``data`` eines Jarvis-Werkzeugergebnisses ``{"status", "tool", "data"}`` (roh oder schon geparst)."""
    payload: Any = raw
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except ValueError:
            return None
    if isinstance(payload, dict) and {"status", "tool"} <= payload.keys():
        return payload.get("data")
    return payload


def media_display(data: Any, args: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Anzeige eines erzeugten Bilds/Videos aus dem Ergebnis selbst; ``None`` wenn es keins enthält."""
    if not isinstance(data, dict):
        return None
    for key, (kind, media) in _MEDIA_KEYS.items():
        raw = data.get(key)
        if not isinstance(raw, str) or not raw:
            continue
        name = PurePath(raw).name
        if not _MEDIA_FILE.fullmatch(name):
            return None
        out: dict[str, Any] = {"kind": kind, "media": media, "file": name}
        a = args or {}
        if isinstance(a.get("prompt"), str):
            out["prompt"] = a["prompt"][:300]
        if isinstance(a.get("aspect_ratio"), str):
            out["aspect_ratio"] = a["aspect_ratio"]
        duration = data.get("duration_s", a.get("duration_s"))
        if kind == "video" and isinstance(duration, int):
            out["duration_s"] = duration
        return out
    return None


def public_display(art: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in art.items() if k not in _HIDDEN}


class ArtifactStore:
    def __init__(self, max_items: int = 64, ttl_s: float = 1800) -> None:
        self.max_items = max_items
        self.ttl_s = ttl_s
        self._items: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()

    def put(self, kind: str, payload: dict[str, Any]) -> str:
        aid = f"art_{uuid.uuid4().hex[:20]}"
        self._items[aid] = (time.monotonic(), {"kind": kind, **payload})
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)
        return aid

    def take(self, artifact_id: str) -> dict[str, Any] | None:
        hit = self._items.pop(artifact_id, None)
        if hit is None or time.monotonic() - hit[0] > self.ttl_s:
            return None
        return hit[1]

    def take_from_text(self, text: str) -> list[dict[str, Any]]:
        """Alle Artefakte, deren IDs in einem Werkzeugergebnis vorkommen (in Reihenfolge)."""
        out = []
        for aid in dict.fromkeys(_ID_RE.findall(text or "")):
            art = self.take(aid)
            if art is not None:
                out.append(art)
        return out

    def display_for(
        self, raw: str | dict[str, Any] | None, args: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Was die Oberfläche zu einem erfolgreichen Werkzeugergebnis zeigt: das Artefakt, sonst ein Bild/Video."""
        text = raw if isinstance(raw, str) else json.dumps(raw or {}, ensure_ascii=False, default=str)
        shown = self.take_from_text(text)
        if shown:
            return public_display(shown[0])
        return media_display(payload_data(raw), args)


artifacts = ArtifactStore()
