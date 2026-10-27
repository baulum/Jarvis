"""Wire protocol Device Agent ↔ Jarvis Core Server (version 1).

Same tables as ``core/src/jarvis/devices/protocol.py`` – a contract test in the core checks that both match.
The agent opens one WebSocket to ``<core>/api/devices/agent/ws``. Its first frame is ``hello``; it carries the
device token only *after* the TLS certificate was checked against the pinned fingerprint.
"""

from __future__ import annotations

PROTOCOL_VERSION = 1

# action → capability it needs on the device
ACTIONS: dict[str, str] = {
    "system.info": "system.info",
    "apps.list": "apps.list",
    "apps.open": "apps.launch",
    "apps.quit": "apps.quit",
    "windows.list": "windows.list",
    "windows.focus": "windows.focus",
    "windows.frontmost": "windows.frontmost",
    "input.type": "input.keyboard",
    "input.key": "input.keyboard",
    "input.click": "input.mouse",
    "input.move": "input.mouse",
    "input.scroll": "input.mouse",
    "screen.capture": "screen.capture",
    "clipboard.get": "clipboard",
    "clipboard.set": "clipboard",
    "files.list": "files.read",
    "files.read": "files.read",
    "files.search": "files.read",
    "files.stat": "files.read",
    "files.read_chunk": "files.read",
    "files.write": "files.write",
    "files.write_chunk": "files.write",
    "files.move": "files.write",
    "files.mkdir": "files.write",
    "files.reveal": "files.reveal",
    "shell.run": "terminal",
    "claude.run": "claude_code",
    "browser.open": "browser.open",
    "browser.automate": "browser.automation",
    "notify": "notifications",
    "audio.volume": "audio.volume",
    "audio.speak": "audio.speak",
    "audio.play": "audio.play",
    "ui.tree": "ui.elements",
    "ui.press": "ui.elements",
    "automation.list": "automation",
    "automation.run": "automation",
    "automation.applescript": "automation.applescript",
}

# capability → German label
CAPABILITY_LABELS: dict[str, str] = {
    "system.info": "Systeminformationen",
    "apps.list": "Laufende Programme auflisten",
    "apps.launch": "Programme öffnen",
    "apps.quit": "Programme beenden",
    "windows.list": "Fenster auflisten",
    "windows.focus": "Fenster in den Vordergrund holen",
    "windows.frontmost": "Aktives Programm und Fenster erkennen",
    "input.keyboard": "Tastatur (tippen, Tastenkombinationen)",
    "input.mouse": "Maus (klicken, bewegen, scrollen)",
    "screen.capture": "Screenshots",
    "clipboard": "Zwischenablage",
    "files.read": "Dateien lesen und suchen",
    "files.write": "Dateien schreiben, verschieben, Ordner anlegen",
    "files.reveal": "Datei im Dateimanager zeigen",
    "terminal": "Terminal-Befehle",
    "claude_code": "Claude Code",
    "browser.open": "URL im Standardbrowser öffnen",
    "browser.automation": "Browser-Automatisierung (Playwright)",
    "notifications": "Benachrichtigungen",
    "audio.volume": "Lautstärke",
    "audio.speak": "Sprachausgabe auf dem Gerät",
    "audio.play": "Audiodateien abspielen",
    "ui.elements": "Bedienelemente lesen und drücken (Accessibility / UI Automation / AT-SPI)",
    "automation": "Automationen (Apple-Kurzbefehle bzw. freigegebene Skripte)",
    "automation.applescript": "AppleScript (nur macOS)",
}

ERROR_CODES = ("unavailable", "invalid", "denied", "kill_switch", "failed", "timeout", "unknown_action", "offline")

# actions that only read (allowed while the kill switch is engaged)
READ_ONLY_ACTIONS = frozenset(
    {
        "system.info",
        "apps.list",
        "windows.list",
        "windows.frontmost",
        "ui.tree",
        "automation.list",
        "screen.capture",
        "clipboard.get",
        "files.list",
        "files.read",
        "files.search",
        "files.stat",
        "files.read_chunk",
    }
)

MAX_FRAME_BYTES = 24 * 1024 * 1024
WS_PATH = "/api/devices/agent/ws"
REGISTER_PATH = "/api/devices/agents/register"


def capabilities() -> list[str]:
    return sorted(set(ACTIONS.values()))
