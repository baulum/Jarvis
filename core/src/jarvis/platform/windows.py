"""``WindowsAdapter``: the Core Server on Windows 10/11.

Desktop control (programs, windows, mouse, keyboard, screenshots) is not done in-process: the Jarvis Device
Agent on the PC connects to the Core and executes it (``device_*`` tools). The Core itself only needs the
Explorer, PowerShell (SAPI voice, media playback) and Task Scheduler.
"""

from __future__ import annotations

from pathlib import Path

from jarvis.platform.base import PlatformAdapter, keyring_state, ps_quote
from jarvis.platform.capabilities import AVAILABLE, CapabilityState, available, degraded, unavailable

EDGE_PATHS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


class WindowsAdapter(PlatformAdapter):
    id = "windows"
    label = "Windows"
    file_manager = "Explorer"

    def session_type(self) -> str:
        return "headless" if self.headless else "windows"

    def powershell(self) -> str | None:
        return self._have("powershell", "powershell.exe", "pwsh", "pwsh.exe")

    def probe(self) -> dict[str, CapabilityState]:
        explorer = self._have("explorer", "explorer.exe")
        ps = self.powershell()
        audio = self.importable("sounddevice")
        no_desktop = unavailable("Keine interaktive Windows-Sitzung (Dienst ohne Desktop)")
        edge = self._have("msedge", "msedge.exe") or next((p for p in EDGE_PATHS if Path(p).exists()), None)
        return {
            "host.macos_bridge": unavailable(
                "Nur auf macOS – auf Windows steuert der Jarvis Device Agent den Rechner (device_*-Werkzeuge)"
            ),
            "desktop.reveal": no_desktop
            if self.headless
            else (AVAILABLE if explorer else unavailable("explorer.exe nicht gefunden")),
            "desktop.open": no_desktop
            if self.headless
            else (AVAILABLE if explorer else unavailable("explorer.exe nicht gefunden")),
            "voice.capture": AVAILABLE if audio else unavailable("sounddevice fehlt (uv sync --extra voice)"),
            "voice.playback": AVAILABLE if audio else unavailable("sounddevice fehlt (uv sync --extra voice)"),
            "voice.native_helper": unavailable(
                "Der Swift-Audiohelfer gibt es nur auf macOS – Windows nutzt sounddevice ohne Echounterdrückung"
            ),
            "voice.wakeup_routine": unavailable("Die Weckroutine ist bisher nur auf macOS umgesetzt"),
            "voice.tts.system": AVAILABLE if ps else unavailable("PowerShell nicht gefunden (SAPI-Stimme)"),
            "voice.tts.local_mlx": unavailable(
                "MLX läuft nur auf Apple Silicon – lokale Stimme auf Windows: Piper-Server (scripts/local-tts)"
            ),
            "image.scale": AVAILABLE if self.importable("PIL") else unavailable("Pillow fehlt"),
            "secrets.os_store": _windows_secrets(keyring_state()),
            "service.autostart": AVAILABLE
            if self._have("schtasks", "schtasks.exe")
            else unavailable("schtasks.exe nicht gefunden"),
            "process.groups": degraded("Keine POSIX-Prozessgruppen – Prozessbäume werden über psutil beendet"),
            "coding.runner": unavailable(
                "Der Coding-Runner nutzt POSIX-Signale (SIGSTOP/SIGCONT, killpg) und bleibt auf dem Mac"
            ),
            "shell.run": AVAILABLE,
            "browser.edge": AVAILABLE if edge else degraded("Microsoft Edge nicht gefunden – Chromium wird verwendet"),
            "devices.remote": available("Gateway :8767, Tailscale empfohlen"),
        }

    def reveal_command(self, path: Path) -> list[str] | None:
        exe = self._have("explorer", "explorer.exe")
        # explorer.exe parses "/select,<path>" itself; the path is one argv element, never a shell string
        return [exe, f"/select,{path}"] if exe and not self.headless else None

    def open_command(self, target: str) -> list[str] | None:
        exe = self._have("explorer", "explorer.exe")
        return [exe, target] if exe and not self.headless else None

    def play_command(self, path: Path, volume: float = 1.0) -> list[str] | None:
        ps = self.powershell()
        if ps is None:
            return None
        vol = max(0.0, min(1.0, volume))
        script = "; ".join(
            [
                "Add-Type -AssemblyName PresentationCore",
                "$p = New-Object System.Windows.Media.MediaPlayer",
                f"$p.Open([Uri]::new({ps_quote(str(path))}))",
                f"$p.Volume = {vol:.2f}",
                "$n = 0",
                "while (-not $p.NaturalDuration.HasTimeSpan -and $n -lt 100) { Start-Sleep -Milliseconds 50; $n++ }",
                "$p.Play()",
                "if ($p.NaturalDuration.HasTimeSpan) "
                "{ Start-Sleep -Milliseconds ([int]$p.NaturalDuration.TimeSpan.TotalMilliseconds + 150) } "
                "else { Start-Sleep -Seconds 3 }",
                "$p.Close()",
            ]
        )
        return [ps, "-NoProfile", "-NonInteractive", "-Command", script]


def _windows_secrets(state: CapabilityState) -> CapabilityState:
    if not state.usable:
        return state
    return degraded(
        "Windows-Anmeldeinformationsverwaltung – Einträge über 1200 Zeichen (z. B. fcm-service-account) "
        "werden in Teile zerlegt gespeichert"
    )
