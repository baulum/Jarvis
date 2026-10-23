"""Agent configuration: where the Core is, how to trust it, and what the Core may touch on this computer.

Stored as JSON in the per-user config directory (Windows ``%APPDATA%\\JarvisDevice``, macOS
``~/Library/Application Support/JarvisDevice``, Linux ``$XDG_CONFIG_HOME/jarvis-device``). The file holds the
device token, so it is written with mode 0600 (POSIX); on Windows the user profile is private to the user.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

TerminalMode = Literal["on", "off"]


def config_dir(environ: dict[str, str] | None = None, platform: str | None = None) -> Path:
    env = dict(os.environ) if environ is None else environ
    plat = sys.platform if platform is None else platform
    if override := env.get("JARVIS_DEVICE_HOME"):
        return Path(override).expanduser()
    if plat.startswith("win"):
        base = env.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "JarvisDevice"
    if plat == "darwin":
        return Path.home() / "Library" / "Application Support" / "JarvisDevice"
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "jarvis-device"


@dataclass(slots=True)
class AgentConfig:
    core_url: str = ""  # https://<host>:8767 – the Core's TLS gateway (Tailscale address preferred)
    fingerprint: str = ""  # SHA-256 of the gateway certificate (self-signed); empty = normal CA verification
    name: str = field(default_factory=socket.gethostname)
    device_id: str = ""
    token: str = ""
    # Folders the Core may read/write (files.*, shell cwd, Claude Code cwd). Default: the user's home.
    allowed_roots: list[str] = field(default_factory=lambda: [str(Path.home())])
    terminal: TerminalMode = "on"
    claude_binary: str = "claude"
    claude_args: list[str] = field(default_factory=lambda: ["--permission-mode", "acceptEdits"])
    browser_automation: bool = True

    @property
    def paired(self) -> bool:
        return bool(self.core_url and self.token)

    def roots(self) -> list[Path]:
        out: list[Path] = []
        for r in self.allowed_roots:
            try:
                out.append(Path(r).expanduser().resolve())
            except OSError:
                continue
        return out

    def public(self) -> dict[str, Any]:
        """Everything except the token (for ``jarvis-device status``)."""
        data = asdict(self)
        data["token"] = "gesetzt" if self.token else ""
        return data

    # ------------------------------------------------------------------ persistence
    @classmethod
    def load(cls, path: Path) -> AgentConfig:
        if not path.is_file():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        cfg = cls(**known)
        if cfg.terminal not in ("on", "off"):
            cfg.terminal = "on"
        return cfg

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        if os.name == "posix":
            tmp.chmod(0o600)
        tmp.replace(path)


def config_path(environ: dict[str, str] | None = None) -> Path:
    return config_dir(environ) / "config.json"
