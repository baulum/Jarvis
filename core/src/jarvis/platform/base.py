"""``PlatformAdapter``: everything the Core Server needs from the operating system, behind one interface.

The adapters only *describe* commands (argv lists) and probe what is installed; spawning stays with the caller.
That keeps them testable on any host: ``which``, ``environ`` and the import check are injectable, so a test on
the Mac can ask the Windows adapter which command reveals a file or why the wake-up routine is missing.
"""

from __future__ import annotations

import abc
import importlib
import importlib.util
import os
import platform
import shutil
import socket
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, ClassVar, Literal

from jarvis.platform.capabilities import CapabilityReport, CapabilityState, unavailable

PlatformId = Literal["mac", "windows", "linux"]
Which = Callable[[str], str | None]


def can_import(module: str) -> bool:
    """True when ``module`` imports cleanly (sounddevice raises OSError when PortAudio is missing)."""
    if importlib.util.find_spec(module) is None:
        return False
    try:
        importlib.import_module(module)
    except Exception:
        return False
    return True


def keyring_state() -> CapabilityState:
    """Is there a real OS secret store behind ``keyring`` (Keychain, Credential Manager, Secret Service)?"""
    try:
        import keyring

        kr = keyring.get_keyring()
    except Exception as e:
        return unavailable(f"keyring nicht nutzbar ({type(e).__name__}) – Zugangsdaten über JARVIS_SECRET_<NAME>")
    module = type(kr).__module__
    if module.startswith(("keyring.backends.fail", "keyring.backends.null")) or getattr(kr, "backends", None) == []:
        return unavailable(
            "Kein Schlüsselspeicher gefunden (z. B. Secret Service ohne Desktop-Sitzung) – "
            "Zugangsdaten über Umgebungsvariablen JARVIS_SECRET_<NAME> setzen"
        )
    return CapabilityState("available", type(kr).__name__)


def ps_quote(value: str) -> str:
    """A PowerShell single-quoted string literal. PowerShell also treats the typographic quotes ‘ ’ ‚ ‛ as
    single quotes, so every one of them is doubled – otherwise a file name could end the literal."""
    out = []
    for ch in value:
        out.append(ch * 2 if ch in "'‘’‚‛" else ch)
    return "'" + "".join(out) + "'"


class PlatformAdapter(abc.ABC):
    id: ClassVar[PlatformId]
    label: ClassVar[str]  # "macOS", "Windows", "Linux"
    file_manager: ClassVar[str]  # "Finder", "Explorer", "Dateimanager"

    def __init__(
        self,
        *,
        which: Which = shutil.which,
        environ: Mapping[str, str] | None = None,
        importable: Callable[[str], bool] = can_import,
        headless: bool = False,
        data_dir: Path | None = None,
    ) -> None:
        self.which = which
        self.environ: Mapping[str, str] = os.environ if environ is None else environ
        self.importable = importable
        self.headless = headless
        self.data_dir = data_dir
        self._report: CapabilityReport | None = None

    # ------------------------------------------------------------------ description
    def info(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "headless": self.headless,
            "session": self.session_type(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "hostname": socket.gethostname(),
            "python": sys.version.split()[0],
            "file_manager": self.file_manager,
        }

    def session_type(self) -> str:
        return "headless" if self.headless else "desktop"

    # ------------------------------------------------------------------ capabilities
    @abc.abstractmethod
    def probe(self) -> dict[str, CapabilityState]:
        """Fresh capability states for this host (cheap: ``which`` and import checks only)."""

    def capabilities(self, refresh: bool = False) -> CapabilityReport:
        if self._report is None or refresh:
            self._report = CapabilityReport(self.probe())
        return self._report

    # ------------------------------------------------------------------ commands (argv, never a shell string)
    @abc.abstractmethod
    def reveal_command(self, path: Path) -> list[str] | None:
        """Shows ``path`` selected in the file manager; None when this host has no file manager."""

    @abc.abstractmethod
    def open_command(self, target: str) -> list[str] | None:
        """Opens a URL or file with the default program; None when not possible."""

    @abc.abstractmethod
    def play_command(self, path: Path, volume: float = 1.0) -> list[str] | None:
        """Plays an audio file (MP3/WAV) to the end; None when no player is installed."""

    def config_overlay(self, config_dir: Path) -> Path | None:
        """``config/platform/<id>.yaml`` – loaded under ``jarvis.yaml`` on this platform (the Mac has none)."""
        path = config_dir / "platform" / f"{self.id}.yaml"
        return path if path.is_file() else None

    # ------------------------------------------------------------------ helpers
    def _have(self, *programs: str) -> str | None:
        for p in programs:
            found = self.which(p)
            if found:
                return found
        return None
