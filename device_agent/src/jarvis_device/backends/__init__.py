"""OS backends of the device agent; ``create_backend()`` picks the one for this computer."""

from __future__ import annotations

import sys

from jarvis_device.backends.base import Capture, DesktopBackend, Unavailable


def create_backend(platform: str | None = None) -> DesktopBackend:
    plat = sys.platform if platform is None else platform
    if plat.startswith("win"):
        from jarvis_device.backends.windows import WindowsBackend

        return WindowsBackend()
    if plat == "darwin":
        from jarvis_device.backends.macos import MacBackend

        return MacBackend()
    from jarvis_device.backends.linux import LinuxBackend

    return LinuxBackend()


__all__ = ["Capture", "DesktopBackend", "Unavailable", "create_backend"]
