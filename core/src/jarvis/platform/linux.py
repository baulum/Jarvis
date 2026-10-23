"""``LinuxAdapter``: the Core Server on a Linux desktop (X11/Wayland) or headless (server, no display).

Headless is a property, not another OS: without ``DISPLAY``/``WAYLAND_DISPLAY`` everything that needs a desktop
session reports "nicht verfügbar" with the reason, the rest (API, memory, tasks, devices) runs unchanged.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from jarvis.platform.base import PlatformAdapter, keyring_state
from jarvis.platform.capabilities import AVAILABLE, CapabilityState, available, degraded, unavailable


class LinuxAdapter(PlatformAdapter):
    id = "linux"
    label = "Linux"
    file_manager = "Dateimanager"

    def session_type(self) -> str:
        if self.headless:
            return "headless"
        if self.environ.get("WAYLAND_DISPLAY") or self.environ.get("XDG_SESSION_TYPE") == "wayland":
            return "wayland"
        return "x11"

    def probe(self) -> dict[str, CapabilityState]:
        no_desktop = unavailable("Keine Desktop-Sitzung (headless: DISPLAY/WAYLAND_DISPLAY fehlen)")
        xdg = self._have("xdg-open")
        audio = self.importable("sounddevice")
        player = self._player()
        tts = self._have("espeak-ng", "espeak")
        edge = self._have("microsoft-edge", "microsoft-edge-stable")
        reveal: CapabilityState
        if self.headless:
            reveal = no_desktop
        elif self._have("dbus-send"):
            reveal = AVAILABLE
        elif xdg:
            reveal = degraded("Nur der Ordner wird geöffnet (dbus-send fehlt, FileManager1 nicht nutzbar)")
        else:
            reveal = unavailable("weder dbus-send noch xdg-open installiert")
        return {
            "host.macos_bridge": unavailable(
                "Nur auf macOS – auf Linux steuert der Jarvis Device Agent den Rechner (device_*-Werkzeuge)"
            ),
            "desktop.reveal": reveal,
            "desktop.open": no_desktop
            if self.headless
            else (AVAILABLE if xdg else unavailable("xdg-open fehlt (Paket xdg-utils)")),
            "voice.capture": AVAILABLE
            if audio
            else unavailable("sounddevice/PortAudio fehlt (apt install libportaudio2; uv sync --extra voice)"),
            "voice.playback": AVAILABLE
            if audio
            else unavailable("sounddevice/PortAudio fehlt (apt install libportaudio2; uv sync --extra voice)"),
            "voice.native_helper": unavailable(
                "Der Swift-Audiohelfer gibt es nur auf macOS – Linux nutzt sounddevice ohne Echounterdrückung"
            ),
            "voice.wakeup_routine": unavailable("Die Weckroutine ist bisher nur auf macOS umgesetzt")
            if player
            else unavailable("Weckroutine nur auf macOS; außerdem fehlt ein Audioplayer (ffplay/mpg123/paplay)"),
            "voice.tts.system": AVAILABLE if tts else unavailable("espeak-ng fehlt (apt install espeak-ng)"),
            "voice.tts.local_mlx": unavailable(
                "MLX läuft nur auf Apple Silicon – lokale Stimme auf Linux: Piper-Server (scripts/local-tts)"
            ),
            "image.scale": AVAILABLE if self.importable("PIL") else unavailable("Pillow fehlt"),
            "secrets.os_store": keyring_state(),
            "service.autostart": AVAILABLE if self._have("systemctl") else unavailable("systemd (systemctl) fehlt"),
            "process.groups": AVAILABLE,
            "coding.runner": degraded("Auf Linux nicht getestet – Coding-Jobs laufen regulär auf dem Mac"),
            "shell.run": AVAILABLE,
            "browser.edge": AVAILABLE
            if edge
            else degraded("Microsoft Edge nicht installiert – Chromium wird verwendet"),
            "devices.remote": available("Gateway :8767, Tailscale empfohlen"),
        }

    def _player(self) -> str | None:
        return self._have("ffplay", "mpg123", "mpv", "paplay", "aplay")

    def reveal_command(self, path: Path) -> list[str] | None:
        if self.headless:
            return None
        dbus = self._have("dbus-send")
        if dbus:
            uri = "file://" + quote(str(path))
            return [
                dbus,
                "--session",
                "--print-reply",
                "--dest=org.freedesktop.FileManager1",
                "--type=method_call",
                "/org/freedesktop/FileManager1",
                "org.freedesktop.FileManager1.ShowItems",
                f"array:string:{uri}",
                "string:",
            ]
        xdg = self._have("xdg-open")
        return [xdg, str(path.parent)] if xdg else None

    def open_command(self, target: str) -> list[str] | None:
        xdg = self._have("xdg-open")
        return [xdg, target] if xdg and not self.headless else None

    def play_command(self, path: Path, volume: float = 1.0) -> list[str] | None:
        vol = max(0.0, min(1.0, volume))
        if exe := self._have("ffplay"):
            return [exe, "-nodisp", "-autoexit", "-loglevel", "error", "-volume", str(round(vol * 100)), str(path)]
        if exe := self._have("mpg123"):
            return [exe, "-q", "-f", str(round(vol * 32768)), str(path)]
        if exe := self._have("mpv"):
            return [exe, "--no-video", "--really-quiet", f"--volume={round(vol * 100)}", str(path)]
        if path.suffix.lower() == ".wav":
            if exe := self._have("paplay"):
                return [exe, f"--volume={round(vol * 65536)}", str(path)]
            if exe := self._have("aplay"):
                return [exe, "-q", str(path)]
        return None
