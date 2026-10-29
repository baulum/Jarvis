"""Wire protocol between the Jarvis Core Server and the Jarvis Device Agent (version 1).

The device agent opens one WebSocket to ``/api/devices/agent/ws`` over Tailscale or the LAN to the TLS gateway
(:8767, certificate pinned). Its token travels in the first frame, after the certificate check. Frames are JSON:

device → core   {"type": "hello", "protocol": 1, "token": "jdv_…", "device": {...}, "capabilities": {...}}
core → device   {"type": "welcome", "device_id": "...", "kill_switch": false}
core → device   {"type": "command", "id": "...", "action": "screen.capture", "args": {...}, "timeout_s": 60}
device → core   {"type": "result", "id": "...", "ok": true, "data": ...}
device → core   {"type": "result", "id": "...", "ok": false, "error": {"code": "...", "message": "..."}}
core → device   {"type": "killswitch", "engaged": true}
device → core   {"type": "capabilities", "capabilities": {...}}      (re-probed, e.g. after a permission change)
both            {"type": "ping"} / {"type": "pong"}

This file is the contract: ``device_agent/src/jarvis_device/protocol.py`` carries the same tables and a test
checks that both stay identical. Version 1 only grows additively (new actions, capabilities, optional args): an
older agent does not report a new capability, so the hub answers "vom Gerät nicht gemeldet" instead of sending it.
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
    "browser.session": "browser.automation",
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

# capability → German label (desktop app, tool errors)
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

# result error codes
ERROR_CODES = ("unavailable", "invalid", "denied", "kill_switch", "failed", "timeout", "unknown_action", "offline")

# actions that only read (allowed while the kill switch is engaged, like read tools in the executor)
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

DEVICE_ROLE = "device_agent"
MAX_FRAME_BYTES = 24 * 1024 * 1024  # screenshots arrive base64-encoded


def capabilities() -> list[str]:
    return sorted(set(ACTIONS.values()))
