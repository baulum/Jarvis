"""The machine the installer runs on: OS, paths, program lookup – injectable for tests."""

from __future__ import annotations

import glob
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

OS_IDS = ("windows", "mac", "linux")


def detect_os(sys_platform: str | None = None) -> str:
    plat = sys.platform if sys_platform is None else sys_platform
    if plat.startswith("win"):
        return "windows"
    if plat == "darwin":
        return "mac"
    return "linux"


@dataclass
class Host:
    os: str
    home: Path
    environ: Mapping[str, str]
    arch: str = field(default_factory=platform.machine)
    which: Callable[[str], str | None] = shutil.which
    exists: Callable[[Path], bool] = lambda p: p.exists()
    globber: Callable[[str], list[str]] = glob.glob

    @classmethod
    def current(cls) -> Host:
        return cls(os=detect_os(), home=Path.home(), environ=os.environ)

    def expand(self, raw: str) -> Path:
        """``~`` and ``%VAR%`` / ``$VAR`` with this host's environment (also on a test host of another OS)."""
        out = raw
        for key, value in self.environ.items():
            out = out.replace(f"%{key}%", value).replace(f"${{{key}}}", value)
        if out.startswith("~"):
            out = str(self.home) + out[1:]
        return Path(out)

    # ------------------------------------------------------------------ Jarvis locations (match config/platform/*.yaml)
    @property
    def data_dir(self) -> Path:
        if self.os == "windows":
            return self.home / "AppData" / "Roaming" / "Jarvis"
        if self.os == "mac":
            return self.home / "Library" / "Application Support" / "Jarvis"
        return self.home / ".local" / "share" / "jarvis"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def vault_dir(self) -> Path:
        return self.home / "Documents" / "Jarvis Vault"


def run(argv: Sequence[str], timeout: float = 600, cwd: Path | None = None) -> tuple[int, str]:
    """Runs a program (argv list), returns (exit code, combined output). Never raises for a failing program."""
    try:
        res = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=timeout, cwd=cwd, check=False
        )
    except FileNotFoundError:
        return 127, f"{argv[0]} nicht gefunden"
    except subprocess.TimeoutExpired:
        return 124, f"{argv[0]}: Zeitüberschreitung"
    return res.returncode, (res.stdout + res.stderr).strip()


Runner = Callable[[Sequence[str]], tuple[int, str]]
