"""Capability flags: what this host can do, and – when it cannot – why.

Every feature that depends on the operating system is described by a capability id. A ``PlatformAdapter``
reports one ``CapabilityState`` per id; tools declare the ids they need (``ToolSpec.requires``). A missing
capability never crashes anything: the tool disappears from the agent's list and a direct call answers
"auf diesem System nicht verfügbar – <Grund>".
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Literal

CapabilityStatus = Literal["available", "degraded", "unavailable"]

# id → German description (shown in the desktop app and in /api/system/capabilities)
CAPABILITIES: dict[str, str] = {
    "host.macos_bridge": "macOS-Steuerung über die JarvisBridge (mac_*-Werkzeuge)",
    "desktop.reveal": "Datei im Dateimanager zeigen",
    "desktop.open": "URLs und Dateien mit dem Standardprogramm öffnen",
    "voice.capture": "Mikrofon für den Sprachdienst",
    "voice.playback": "Lautsprecher-Wiedergabe für den Sprachdienst",
    "voice.native_helper": "Audiohelfer mit Echounterdrückung (Swift)",
    "voice.wakeup_routine": "Weckroutine („Jarvis, wach auf“: Klang und Programme)",
    "voice.tts.system": "Systemstimme als letzte Rückfallstufe der Sprachausgabe",
    "voice.tts.local_mlx": "Lokale Jarvis-Stimme über MLX (Apple Silicon)",
    "image.scale": "Große Bilder für das Modell verkleinern",
    "secrets.os_store": "Zugangsdaten im Schlüsselspeicher des Systems",
    "service.autostart": "Dienste beim Anmelden starten",
    "process.groups": "Prozessgruppen beenden/anhalten (POSIX)",
    "coding.runner": "Coding-Jobs über den Coding-Runner",
    "shell.run": "Freigegebene Programme ausführen (Shell-Connector)",
    "browser.edge": "Microsoft Edge für die Browser-Automatisierung",
    "devices.remote": "Entfernte Geräte über den Jarvis Device Agent steuern",
}


@dataclass(frozen=True, slots=True)
class CapabilityState:
    status: CapabilityStatus
    reason: str = ""

    @property
    def usable(self) -> bool:
        return self.status != "unavailable"

    def to_dict(self) -> dict[str, str]:
        return {"status": self.status, "reason": self.reason}


AVAILABLE = CapabilityState("available")


def available(reason: str = "") -> CapabilityState:
    return CapabilityState("available", reason)


def degraded(reason: str) -> CapabilityState:
    return CapabilityState("degraded", reason)


def unavailable(reason: str) -> CapabilityState:
    return CapabilityState("unavailable", reason)


class CapabilityReport(Mapping[str, CapabilityState]):
    """Immutable mapping capability id → state. Unknown ids count as unavailable (fail closed)."""

    def __init__(self, states: Mapping[str, CapabilityState]) -> None:
        self._states = dict(states)

    def __getitem__(self, cap: str) -> CapabilityState:
        return self._states[cap]

    def __iter__(self) -> Iterator[str]:
        return iter(self._states)

    def __len__(self) -> int:
        return len(self._states)

    def state(self, cap: str) -> CapabilityState:
        return self._states.get(cap) or unavailable(f"Unbekannte Fähigkeit „{cap}“")

    def usable(self, cap: str) -> bool:
        return self.state(cap).usable

    def reason(self, cap: str) -> str:
        return self.state(cap).reason

    def missing(self, caps: tuple[str, ...] | list[str]) -> list[str]:
        """Capability ids out of ``caps`` that this host does not have."""
        return [c for c in caps if not self.usable(c)]

    def explain(self, caps: tuple[str, ...] | list[str]) -> str:
        """One German sentence for the first missing capability (empty when all are usable)."""
        for c in self.missing(caps):
            st = self.state(c)
            label = CAPABILITIES.get(c, c)
            return f"{label}: {st.reason}" if st.reason else label
        return ""

    def to_dict(self) -> dict[str, dict[str, str]]:
        return {cap: {**st.to_dict(), "label": CAPABILITIES.get(cap, cap)} for cap, st in sorted(self._states.items())}
