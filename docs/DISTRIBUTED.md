# J.A.R.V.I.S. verteilt: Core Server, Clients und Device Agents (Windows, Linux, Mac)

Stand: 2026-10-27 (Erweiterung um Bedienelemente, Automationen, Inhaltssuche und Vordergrundfenster). Umsetzung der Entscheidungen aus [CROSS_PLATFORM.md](CROSS_PLATFORM.md) (Audit) – Raspberry Pi bleibt vorerst
außen vor, der Coding-Runner ist unverändert.

## 1. Drei Komponenten

```
                      Tailscale (WireGuard, privat)
 ┌───────────────────────── Mac ─────────────────────────┐        ┌──────────── Windows-PC / Linux-PC ────────────┐
 │ Jarvis Core Server  (core/, FastAPI)                  │        │ Jarvis Client                                 │
 │  Agenten · LLM (Claude Code) · Gedächtnis/Graph ·     │◄──────►│  Desktop-App (Tauri) oder Browser (/app)      │
 │  Tools/MCP · Aufgaben · Gespräche · Automationen ·    │ HTTPS  │  client.json: Core-Adresse + Geräte-Token     │
 │  API/WebSocket · Benutzer/Geräte · Command-Routing    │        │                                               │
 │  ├─ 127.0.0.1:8765  lokale API (Mac-App, Voice)       │  WSS   │ Jarvis Device Agent (device_agent/)           │
 │  ├─ :8767 TLS-Gateway (Handy, Device Agents,          │◄──────►│  Programme · Fenster · Maus · Tastatur ·      │
 │  │        mit clients.remote_api auch Clients)        │        │  Screenshots · Dateien · Terminal ·           │
 │  ├─ DeviceHub: Befehle an verbundene Agents           │        │  Claude Code · Browser · Benachrichtigungen · │
 │  └─ PlatformAdapter (MacAdapter = bisheriger Code)    │        │  Audio · Systeminfos                          │
 │ JarvisBridge (Swift) – steuert den Mac wie bisher     │        └───────────────────────────────────────────────┘
 └───────────────────────────────────────────────────────┘
```

| Komponente | Ort | Aufgabe | Plattformen |
|---|---|---|---|
| **Jarvis Core Server** | `core/` | Agenten, LLM, Gedächtnis/Wissensgraph, Tool-/MCP-Orchestrierung, Aufgaben, Gespräche, Automationen, API/WebSocket, Benutzer- und Geräteverwaltung, Remote-Command-Routing (`devices/hub.py`). Keine harte Abhängigkeit von macOS: alles OS-Spezifische hinter `PlatformAdapter` | macOS (unverändert), Windows, Linux |
| **Jarvis Client** | `desktop/` (Tauri, React), Browser-Client = derselbe Build unter `/app` | Oberfläche; nicht vom lokalen Backend abhängig: verbindet sich mit dem lokalen oder einem entfernten Core | Mac, Windows, Linux, Browser, Android (`apps/jarvis_mobile`) |
| **Jarvis Device Agent** | `device_agent/` (`jarvis-device`) | leichtgewichtig, authentifiziert per Geräte-Token, eine ausgehende WebSocket-Verbindung zum Core, führt Befehle aus | Windows (Win32/ctypes), Linux (X11/Wayland, freedesktop), macOS (über die JarvisBridge) |

## 2. PlatformAdapter und Capability-Flags (Core)

`core/src/jarvis/platform/`: `MacAdapter`, `WindowsAdapter`, `LinuxAdapter` (headless als Eigenschaft). Jeder Adapter meldet
für jede Fähigkeit `available | degraded | unavailable` **mit deutschem Grund** (`capabilities.py`), z. B.:

| Fähigkeit | Mac | Windows | Linux (Desktop / headless) |
|---|---|---|---|
| `host.macos_bridge` (mac_*-Werkzeuge) | ja | nein – Device Agent übernimmt | nein – Device Agent übernimmt |
| `desktop.reveal` („Im Finder/Explorer zeigen“) | `open -R` | `explorer /select,` | FileManager1 über D-Bus, sonst Ordner / headless: nein |
| `voice.tts.system` (letzte Stimme) | `say` | SAPI (PowerShell) | espeak-ng, sonst stumm (nur Text) |
| `voice.tts.local_mlx` | ja (Apple Silicon) | nein → Piper-Server | nein → Piper-Server |
| `voice.capture/playback` | Audiohelfer/afplay | sounddevice | sounddevice (PortAudio) |
| `voice.wakeup_routine` | ja | nein (Grund) | nein (Grund) |
| `image.scale` | `sips` | Pillow | Pillow |
| `coding.runner` | ja | nein (SIGSTOP/killpg) | eingeschränkt (ungetestet) |
| `secrets.os_store` | Schlüsselbund | Anmeldeinformationsverwaltung (große Einträge in Teilen) | Secret Service / sonst `JARVIS_SECRET_*` |

Mechanik: `ToolSpec.requires` (z. B. alle `mac_*` → `host.macos_bridge`). Fehlt eine Fähigkeit, verschwindet das Werkzeug aus der
Liste des Agenten, ein direkter Aufruf antwortet *„… ist auf diesem System nicht verfügbar – <Grund>“* (vor jeder Freigabe).
`GET /api/system/capabilities` liefert Plattform, Fähigkeiten, nicht verfügbare Werkzeuge und die Device Agents; die Desktop-App
zeigt das unter **Geräte & Plattform**. Auf dem Mac sind alle bisherigen Werkzeuge unverändert aktiv (Golden-Test
`tests/unit/test_capability_gate.py`); der Mac lädt keine Konfigurations-Overlays.

Konfiguration je Betriebssystem: `config/platform/windows.yaml` bzw. `linux.yaml` (Pfade, Stimme, Browser) über `config/jarvis.yaml`;
`JARVIS__…`-Umgebungsvariablen gelten weiterhin zuletzt. Erkennung erzwingbar mit `JARVIS_PLATFORM=mac|windows|linux` und
`JARVIS_HEADLESS=1`. Shell-Allowlist je Plattform: `shell_allowlist_by_platform` in `config/permissions.yaml` (Windows ohne `find`,
`.cmd/.bat` ohne cmd.exe-Sonderzeichen).

## 3. Device Agent

### Protokoll (v1)
`core/src/jarvis/devices/protocol.py` = `device_agent/src/jarvis_device/protocol.py` (Contract-Test hält beide gleich).

1. **Kopplung:** `jarvisctl devices pair` (oder Desktop-App › Geräte & Plattform › *Gerät koppeln*) erzeugt einen Einmalcode
   (10 min) und zeigt Adresse + Fingerabdruck des Gateway-Zertifikats. Der Agent prüft den Fingerabdruck **bevor** er den Code
   sendet (`POST /api/devices/agents/register`) und erhält ein Geräte-Token (Rolle `device_agent`).
2. **Verbindung:** eine WebSocket `wss://<core>:8767/api/devices/agent/ws`; das Token steht im ersten Frame (`hello`), nach der
   Zertifikatsprüfung. Danach: `command` → `result`, `killswitch`, `capabilities` (neu geprüft alle 5 min), `ping/pong`.
3. **Routing:** `DeviceHub.call(gerät, aktion, args)` wählt das Gerät (Name, Hostname, ID – oder das einzige verbundene), prüft
   dessen Fähigkeit (*„Maus ist auf Linux-PC nicht verfügbar: Wayland erlaubt …“*), wartet mit Zeitlimit auf das Ergebnis.

Das Token öffnet **nur** diese WebSocket: `device_agent` ist keine Rolle der API, Chats/Gedächtnis/Event-Stream bleiben zu.

### Werkzeuge (`devices`-Connector, nur sichtbar, wenn mindestens ein Agent gekoppelt ist)
`device_list`, `device_system_info`, `device_apps_list`, `device_app_open`, `device_app_quit`, `device_windows_list`,
`device_window_focus`, `device_frontmost`, `device_screenshot` (Analyse wie `mac_screenshot`), `device_ui_tree`,
`device_ui_press`, `device_click`, `device_mouse_move`, `device_scroll`, `device_type_text`, `device_key`,
`device_clipboard_get/set`, `device_files_list/search/mkdir/move/reveal` (`device_files_search` mit `mode: content` nutzt
den Suchindex des Systems), `device_file_read/write`, `device_shell_run`, `device_claude_code`, `device_open_url`,
`device_browser_automate`, `device_notify`, `device_audio`, `device_automations_list`, `device_automation_run`,
`device_applescript` (nur gekoppelte Macs).
Risiken wie bei `mac_*`: Tippen, Klicken, Bedienelement drücken, Terminal, Claude Code, AppleScript = `high` (Freigabe),
Automation ausführen = `medium`. Eingaben mit `app` aktivieren das Programm und
prüfen den Vordergrund, sonst wird abgebrochen. Klick-Koordinaten sind Pixel des letzten Screenshots (der Agent rechnet zurück).

### Umsetzung je Betriebssystem
| | Windows | Linux X11 | Linux Wayland | macOS |
|---|---|---|---|---|
| Programme öffnen | Startmenü (`Get-StartApps` → `shell:AppsFolder`), PATH | `.desktop` (gtk-launch/gio), PATH | wie X11 | JarvisBridge |
| Fenster | EnumWindows, SetForegroundWindow | wmctrl/xdotool | nicht verfügbar (Grund) | JarvisBridge |
| Maus/Tastatur | SendInput (Unicode), nicht in Admin-Fenster (UIPI) | xdotool | Tastatur über wtype, Maus nicht verfügbar | JarvisBridge (Klick, Tasten; kein Bewegen) |
| Screenshots | mss (DPI-bewusst) | mss | grim / gnome-screenshot | JarvisBridge (ScreenCaptureKit) |
| Zwischenablage | Win32 | xclip/xsel | wl-clipboard | JarvisBridge |
| Vordergrundfenster | GetForegroundWindow | xdotool (sonst AT-SPI) | AT-SPI (Programm + Titel) | JarvisBridge |
| Bedienelemente (`ui.tree`/`ui.press`) | UI Automation (PowerShell, .NET) | AT-SPI (`python3-gi`) | AT-SPI | JarvisBridge (Accessibility) |
| Automationen | Skripte im Automationen-Ordner (`.ps1/.cmd/.bat/.py/.exe`) | Skripte (`.sh/.py`, ausführbar) | wie X11 | Apple-Kurzbefehle + Skripte; AppleScript, wenn die Bridge-Richtlinie es erlaubt |
| Inhaltssuche (`files.search` `mode: content`) | Windows-Suche (SYSTEMINDEX) | Tracker/LocalSearch, Namen: plocate | wie X11 | Spotlight (`mdfind`) |
| Benachrichtigung | Toast (WinRT) | notify-send | notify-send | osascript |
| Lautstärke/Sprache/Abspielen | Core Audio, SAPI, MediaPlayer | pactl/wpctl/amixer, espeak-ng, ffplay | wie X11 | osascript, say, afplay |
| Dateien, Terminal, Claude Code, Systeminfo, Browser (Playwright) | plattformneutral, nur unterhalb der freigegebenen Ordner | | | |

Ohne Suchindex durchsucht der Agent die freigegebenen Ordner selbst (Textdateien bis 2 MB, Zeitlimit 25 s); Treffer aus
einem Index werden auf die freigegebenen Ordner beschränkt. Protokoll v1 wächst nur additiv: ein älterer Agent meldet
die neuen Fähigkeiten nicht, der Core antwortet dann „… ist auf <Gerät> nicht verfügbar“ statt den Befehl zu senden.

Lokale Schutzschicht: freigegebene Ordner (`jarvis-device roots add …`, Standard: Benutzerordner; `.ssh`, `.gnupg`, die eigene
Konfiguration sind gesperrt), Terminal abschaltbar (`jarvis-device config terminal off`), lokaler Not-Aus
(`jarvis-device killswitch on`) zusätzlich zum Not-Aus des Core (beide erlauben nur noch lesende Aktionen).
Automationen: nur Skripte, die der Benutzer selbst in den Automationen-Ordner legt (`jarvis-device automations` zeigt ihn;
er liegt in der geschützten Konfiguration, der Core kann dort nichts schreiben), abschaltbar mit
`jarvis-device config automations off`.

## 4. Netzwerk: Tailscale

- **Device Agents und Handy** verbinden sich mit dem TLS-Gateway `:8767` (selbstsigniertes Zertifikat, Fingerabdruck gepinnt) über
  die Tailscale-Adresse des Mac (`100.x.y.z`, steht in `jarvisctl devices pair` zuerst).
- **Desktop-App auf Windows/Linux und Browser** brauchen ein Zertifikat, dem der Webview vertraut: auf dem Mac
  `tailscale serve --bg --https=443 https+insecure://localhost:8767` und in `config/jarvis.yaml`
  ```yaml
  clients:
    remote_api: true     # Gateway liefert die volle Client-API (jede Anfrage braucht weiterhin ein Geräte-Token)
    web_client: true     # optional: Browser-Client unter https://<mac>.<tailnet>.ts.net/app/ (desktop/dist bauen)
  ```
  Nie über das Gateway erreichbar: `/mcp`, `/api/webhooks/*`, `/api/telephony/*`, der OAuth-Callback.
- Client-Token: `jarvisctl pair "Windows-PC" --role owner` (eigene Rechner) bzw. `--role operator`.

## 5. Installation auf Windows

Voraussetzung: Windows 10 (1809+) oder 11, Tailscale angemeldet im selben Tailnet wie der Mac.

```powershell
git clone https://github.com/baulum/Jarvis.git; cd Jarvis
powershell -ExecutionPolicy Bypass -File scripts\windows\install.ps1
```

Der Installer (`installer/jarvis_installer`, nur Python-Standardbibliothek; `install.ps1` installiert bei Bedarf uv):
1. fragt die Rollen (Standard `client,device`; `core` nur, wenn der Core auf diesem PC laufen soll),
2. **prüft und meldet fehlende Abhängigkeiten** mit Installationshinweis – u. a. Obsidian, Tailscale, Claude Code, Git, PostgreSQL
   + pgvector, FFmpeg, whisper.cpp, PowerShell – und bietet `winget` für die installierbaren an,
3. **lädt die Modelle** (Rolle core): Piper-Stimme de_DE-thorsten-high, Whisper (groß 574 MB / klein 190 MB / keins), Wake-Word –
   mit Prüfsumme, fortsetzbar, nach Platzprüfung,
4. installiert den Device Agent (`uv tool install`), koppelt ihn (Adresse, Code, Fingerabdruck aus `jarvisctl devices pair`) und
   richtet den Autostart bei der Anmeldung ein (Aufgabenplanung, `pythonw` ohne Konsole),
5. speichert die Verbindung der Desktop-App (`%APPDATA%\Jarvis\client.json`) und installiert die gebaute App, falls vorhanden
   (`pnpm tauri build` erzeugt NSIS/MSI),
6. schreibt `%APPDATA%\Jarvis\install-report.json` mit Erledigtem und Offenem.

Nur prüfen: `install.ps1 --check` (mit `--json` maschinenlesbar). Unbeaufsichtigt, z. B.:
`install.ps1 --roles device --core-url https://100.101.102.103:8767 --code <Code> --fingerprint <SHA-256> --yes`.

Core auf Windows (optional): `scripts\windows\start.ps1` startet whisper-server, Piper-Stimme (`scripts/local-tts/piper_server.py`,
selbe HTTP-Schnittstelle wie die Mac-Stimme auf :8179), Core und Voice-Daemon; `stop.ps1` beendet sie samt Kindprozessen. Die
Desktop-App ruft diese Skripte selbst auf – außer sie ist nur Client.

## 6. Installation auf Linux

```bash
scripts/linux/install.sh            # gleicher Installer; Hinweise mit apt-Paketnamen
scripts/linux/start.sh / stop.sh    # Core-Dienste (headless: ohne Voice und App)
```
Für den Device Agent unter X11: `sudo apt install xdotool wmctrl xclip libnotify-bin` (Wayland: `wtype wl-clipboard grim`),
für Bedienelemente und das aktive Fenster unter Wayland zusätzlich `python3-gi gir1.2-atspi-2.0` (prüft der Installer).
Autostart: XDG-Autostart-Eintrag `~/.config/autostart/jarvis-device.desktop` (läuft in der Desktop-Sitzung).

## 7. Tests

| Bereich | Befehl | Inhalt |
|---|---|---|
| Core | `cd core && uv run pytest -q tests/unit` | u. a. `test_platform_adapters.py` (Adapter-Vertrag auf jedem Host), `test_capability_gate.py` (Mac-Golden-Test), `test_cross_platform_core.py` (frühere Absturzstellen), `test_devices.py` (Hub, API, Werkzeuge, Gateway, Protokoll-Contract), `test_installer.py` |
| Device Agent | `cd device_agent && uv run pytest -q` | Dispatcher, Sicherheitsregeln, Backends ohne Betriebssystem (Strukturen, Kommandozeilen), echte WebSocket-Sitzung gegen einen Test-Core, Autostart, `test_automation_ui.py` (Automationen, Bedienelemente, Inhaltssuche je Plattform) |
| Desktop | `cd desktop && pnpm test` | u. a. `connection.test.ts` |
| CI | `.github/workflows/ci.yml` | Core zusätzlich auf Windows/macOS (nicht blockierend), Device Agent auf Linux/Windows/macOS, PowerShell-Syntax |

## 8. Offen / bewusst nicht enthalten

- Raspberry Pi (ARM, headless Sprachstation) – vorerst weggelassen.
- Coding-Runner auf Windows (POSIX-Signale) – unverändert; Coding-Jobs laufen auf dem Mac, `device_claude_code` deckt einfache
  Claude-Code-Aufträge auf dem PC ab.
- Echte Ende-zu-Ende-Läufe auf Windows/Linux-Hardware stehen aus (die Backends sind ohne Betriebssystem getestet, die CI-Jobs
  laufen auf den GitHub-Runnern).
- Code-Signing der Windows-App (SmartScreen-Hinweis bei unsignierten Builds).
