"""Anzeige-Artefakte von Werkzeugen (z. B. Routengeometrie für die Karte im Chat).

Ein Werkzeug legt große, nur für die Oberfläche bestimmte Daten hier ab und gibt dem Modell nur die
kurze ``artifact_id`` zurück – das spart Tokens und hält Geometrie aus dem Kontext. Der Orchestrator
findet die ID im Werkzeugergebnis, entnimmt das Artefakt und speichert es am Tool-Ereignis der
Zeitleiste (``display``); damit überlebt es auch ein Neuladen des Chats.
"""

from __future__ import annotations

import re
import time
import uuid
from collections import OrderedDict
from typing import Any

_ID_RE = re.compile(r"art_[0-9a-f]{20}")


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


artifacts = ArtifactStore()
