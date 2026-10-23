"""Platform abstraction of the Jarvis Core Server.

``get_adapter()`` returns the adapter for this host (cached). Detection follows ``sys.platform``; it can be
forced with ``JARVIS_PLATFORM=mac|windows|linux`` and ``JARVIS_HEADLESS=1`` (e.g. a Linux server without a
display, or tests). On the Mac the result is always ``MacAdapter`` – the existing code paths, unchanged.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jarvis.platform.base import PlatformAdapter, PlatformId
from jarvis.platform.capabilities import CAPABILITIES, CapabilityReport, CapabilityState
from jarvis.platform.linux import LinuxAdapter
from jarvis.platform.mac import MacAdapter
from jarvis.platform.windows import WindowsAdapter

__all__ = [
    "CAPABILITIES",
    "CapabilityReport",
    "CapabilityState",
    "LinuxAdapter",
    "MacAdapter",
    "PlatformAdapter",
    "PlatformId",
    "WindowsAdapter",
    "create_adapter",
    "detect_headless",
    "detect_platform_id",
    "get_adapter",
    "set_adapter",
]

_ALIASES: dict[str, PlatformId] = {
    "mac": "mac",
    "macos": "mac",
    "darwin": "mac",
    "osx": "mac",
    "windows": "windows",
    "win": "windows",
    "win32": "windows",
    "linux": "linux",
}
_ADAPTERS: dict[PlatformId, type[PlatformAdapter]] = {
    "mac": MacAdapter,
    "windows": WindowsAdapter,
    "linux": LinuxAdapter,
}


def detect_platform_id(environ: Mapping[str, str] | None = None, sys_platform: str | None = None) -> PlatformId:
    env = os.environ if environ is None else environ
    forced = env.get("JARVIS_PLATFORM", "").strip().lower()
    if forced:
        if forced not in _ALIASES:
            raise ValueError(f"JARVIS_PLATFORM={forced!r} unbekannt (mac, windows, linux)")
        return _ALIASES[forced]
    plat = sys.platform if sys_platform is None else sys_platform
    if plat == "darwin":
        return "mac"
    if plat.startswith(("win32", "cygwin", "msys")):
        return "windows"
    return "linux"  # Linux and other POSIX systems (BSD) share the freedesktop tooling


def detect_headless(pid: PlatformId, environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    flag = env.get("JARVIS_HEADLESS", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    if pid == "linux":
        return not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))
    return False  # a Mac or Windows PC is headless only when explicitly configured (server/service mode)


def create_adapter(
    environ: Mapping[str, str] | None = None,
    sys_platform: str | None = None,
    data_dir: Path | None = None,
    **kwargs: Any,
) -> PlatformAdapter:
    pid = detect_platform_id(environ, sys_platform)
    return _ADAPTERS[pid](environ=environ, headless=detect_headless(pid, environ), data_dir=data_dir, **kwargs)


_current: PlatformAdapter | None = None


def get_adapter() -> PlatformAdapter:
    global _current
    if _current is None:
        _current = create_adapter()
    return _current


def set_adapter(adapter: PlatformAdapter | None) -> None:
    """Replace the process-wide adapter (AppState sets one with its data dir; tests inject fakes)."""
    global _current
    _current = adapter
