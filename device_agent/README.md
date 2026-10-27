# Jarvis Device Agent (`jarvis-device`)

Lässt den Jarvis Core Server diesen Rechner steuern: Programme, Fenster, Maus, Tastatur, Screenshots, Zwischenablage,
Bedienelemente (Accessibility / UI Automation / AT-SPI), Dateien samt Inhaltssuche, Terminal, Claude Code, Browser
(Playwright), Automationen, Benachrichtigungen, Audio und Systeminformationen.
Windows (Win32), Linux (X11, Wayland soweit erlaubt) und macOS (über die JarvisBridge). Architektur, Protokoll und
Sicherheitsregeln: [docs/DISTRIBUTED.md](../docs/DISTRIBUTED.md).

## Installation

Am einfachsten über den Installer des Repos (Rolle `device`): `scripts\windows\install.ps1` bzw. `scripts/linux/install.sh`.
Von Hand:

```bash
uv tool install ./device_agent            # mit Browser-Automatisierung: "./device_agent[browser]"
jarvisctl devices pair                    # auf dem Core-Rechner: Adresse, Code, Fingerabdruck
jarvis-device pair --core https://100.101.102.103:8767 --code <Code> --fingerprint <SHA-256>
jarvis-device autostart enable            # bei jeder Anmeldung starten (Aufgabenplanung / XDG / LaunchAgent)
jarvis-device run                         # oder im Vordergrund starten
```

## Befehle

| Befehl | Wirkung |
|---|---|
| `jarvis-device capabilities` | prüft, was auf diesem Rechner geht – mit Grund für alles, was fehlt |
| `jarvis-device status` | Konfiguration (ohne Token) und lokaler Not-Aus |
| `jarvis-device roots list\|add\|remove <Ordner>` | Ordner, auf die der Core zugreifen darf (Standard: Benutzerordner) |
| `jarvis-device config terminal on\|off` | Terminal-Befehle erlauben/sperren |
| `jarvis-device config browser on\|off` | Browser-Automatisierung erlauben/sperren |
| `jarvis-device config automations on\|off` | Automationen (Skripte, auf dem Mac auch Apple-Kurzbefehle) erlauben/sperren |
| `jarvis-device config claude <Programm>` | Claude-Code-Programm (Standard `claude`) |
| `jarvis-device automations` | legt den Automationen-Ordner an und listet die Skripte darin |
| `jarvis-device killswitch on\|off` | lokaler Not-Aus: nur noch lesende Aktionen |
| `jarvis-device unpair` | Token löschen (im Core zusätzlich widerrufen) |

Konfiguration und Log: `%APPDATA%\JarvisDevice` (Windows), `~/.config/jarvis-device` (Linux),
`~/Library/Application Support/JarvisDevice` (macOS).

**Automationen:** Skripte im Unterordner `automations` der Konfiguration startet Jarvis per Namen
(`device_automation_run`) – Windows: `.ps1`, `.cmd`, `.bat`, `.py`, `.exe`; Linux/macOS: ausführbare Dateien, `.sh`,
`.py`. Die Eingabe kommt über stdin und `JARVIS_INPUT`. Der Ordner liegt in der geschützten Konfiguration: Jarvis kann
dort nichts ablegen, nur ausführen, was Sie selbst hineinlegen.

**Linux, Bedienelemente:** `sudo apt install python3-gi gir1.2-atspi-2.0` (der Agent ruft dafür das System-`python3`
auf); unter Wayland erkennt er darüber auch das aktive Fenster. Chromium/Electron-Programme zeigen ihre Elemente erst
mit `--force-renderer-accessibility`.

## Entwicklung

```bash
cd device_agent
uv sync
uv run pytest -q          # ohne Betriebssystemzugriff: Backends werden mit simulierten Hilfsprogrammen geprüft
uv run ruff check src tests && uv run mypy src
```
