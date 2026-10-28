"""Selectable personalities (Einstellungen → Persönlichkeit): Jarvis and further personas such as René Redo.

A persona is one YAML file – ``config/personality.yaml`` (Jarvis) or ``config/personalities/<id>.yaml`` – with the
complete persona part of the system prompt, its local voice and a Hörprobe. Tools, memory, permissions and the
operating rules are shared: a switch changes who speaks, never what Jarvis can do.

The choice is stored in ``<data_dir>/persona.json`` and applied without a restart (``AppState.set_persona``): the
system prompt from the next turn on (idle Claude processes are restarted, busy ones after their turn) and the voice
from the next reply on – a reply that is being spoken keeps its voice. Desktop and phone follow the bus event
``persona.changed``. A new persona only needs its YAML file and, for its own voice, a reference clip in
``scripts/local-tts/voices/<voice>.wav`` + ``.txt`` (see scripts/local-tts/design_voice.py).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from jarvis.config import Personality

logger = logging.getLogger(__name__)


class PersonaStore:
    def __init__(self, path: Path, personas: list[Personality]) -> None:
        if not personas:
            raise ValueError("at least one persona is required")
        self.path = path
        self._personas = {p.id: p for p in personas}
        self.default_id = personas[0].id
        self.active_id = self._load()

    def _load(self) -> str:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            pid = str(data.get("id") or "") if isinstance(data, dict) else ""
        except FileNotFoundError:
            return self.default_id
        except (OSError, ValueError) as e:
            logger.warning("persona.json unlesbar (%s) – %s bleibt aktiv", e, self.default_id)
            return self.default_id
        if pid not in self._personas:
            if pid:
                logger.warning("Gespeicherte Persönlichkeit %r gibt es nicht mehr – %s ist aktiv", pid, self.default_id)
            return self.default_id
        return pid

    @property
    def active(self) -> Personality:
        return self._personas[self.active_id]

    def all(self) -> list[Personality]:
        return list(self._personas.values())

    def get(self, pid: str) -> Personality | None:
        return self._personas.get(pid)

    def set(self, pid: str) -> Personality:
        """Persist the choice; raises ``KeyError`` for an unknown persona."""
        if pid not in self._personas:
            raise KeyError(pid)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"id": pid}), encoding="utf-8")
        os.replace(tmp, self.path)
        self.active_id = pid
        return self._personas[pid]

    @staticmethod
    def summary(p: Personality) -> dict[str, Any]:
        return {
            "id": p.id,
            "label": p.display_name,
            "description": p.description,
            "voice": p.voice,
            "sample": p.sample,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"active": self.active_id, "personas": [self.summary(p) for p in self._personas.values()]}
