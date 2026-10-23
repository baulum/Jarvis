"""``MacAdapter``: the existing macOS code paths, described – not changed.

Everything here mirrors what the Core did on the Mac before the adapters existed: ``open -R`` for the Finder,
``open`` for URLs, ``afplay`` for sounds, ``say`` as the last TTS stage, the Swift JarvisBridge for desktop
control and the Swift audio helper for the microphone. The Mac never loads a platform config overlay.
"""

from __future__ import annotations

import platform
from pathlib import Path

from jarvis.platform.base import PlatformAdapter, keyring_state
from jarvis.platform.capabilities import AVAILABLE, CapabilityState, available, degraded, unavailable


class MacAdapter(PlatformAdapter):
    id = "mac"
    label = "macOS"
    file_manager = "Finder"

    def session_type(self) -> str:
        return "aqua"

    def probe(self) -> dict[str, CapabilityState]:
        have_open = self._have("open")
        helper = self.data_dir / "bin" / "jarvis-audio" if self.data_dir else None
        mic = self.importable("sounddevice")
        return {
            # the bridge's runtime state (running, TCC permissions, kill switch) stays in MacConnector.health
            "host.macos_bridge": AVAILABLE,
            "desktop.reveal": AVAILABLE if have_open else unavailable("/usr/bin/open fehlt"),
            "desktop.open": AVAILABLE if have_open else unavailable("/usr/bin/open fehlt"),
            "voice.capture": AVAILABLE
            if mic or (helper is not None and helper.exists())
            else unavailable("sounddevice fehlt (uv sync --extra voice) und der Audiohelfer ist nicht gebaut"),
            "voice.playback": AVAILABLE if self._have("afplay") else unavailable("afplay fehlt"),
            "voice.native_helper": AVAILABLE
            if helper is not None and helper.exists()
            else unavailable("Audiohelfer nicht gebaut (scripts/build-audio-helper.sh)"),
            "voice.wakeup_routine": AVAILABLE,
            "voice.tts.system": AVAILABLE
            if self._have("say") and self._have("ffmpeg")
            else unavailable("say oder ffmpeg fehlt (brew install ffmpeg)"),
            "voice.tts.local_mlx": AVAILABLE
            if platform.machine() == "arm64"
            else unavailable("MLX braucht Apple Silicon"),
            "image.scale": AVAILABLE
            if self._have("sips") or self.importable("PIL")
            else unavailable("weder sips noch Pillow vorhanden"),
            "secrets.os_store": keyring_state(),
            "service.autostart": AVAILABLE if self._have("launchctl") else unavailable("launchctl fehlt"),
            "process.groups": AVAILABLE,
            "coding.runner": AVAILABLE,
            "shell.run": AVAILABLE,
            "browser.edge": AVAILABLE
            if Path("/Applications/Microsoft Edge.app").exists()
            else degraded("Microsoft Edge ist nicht installiert – der Browser-Connector nutzt Chromium"),
            "devices.remote": available("Gateway :8767, Tailscale empfohlen"),
        }

    def reveal_command(self, path: Path) -> list[str] | None:
        exe = self._have("open")
        return [exe, "-R", str(path)] if exe else None

    def open_command(self, target: str) -> list[str] | None:
        exe = self._have("open")
        return [exe, target] if exe else None

    def play_command(self, path: Path, volume: float = 1.0) -> list[str] | None:
        exe = self._have("afplay")
        return [exe, "-v", f"{max(0.0, min(1.0, volume)):.2f}", str(path)] if exe else None

    def config_overlay(self, config_dir: Path) -> Path | None:
        return None  # the Mac runs on config/jarvis.yaml alone – exactly as before the adapters existed
