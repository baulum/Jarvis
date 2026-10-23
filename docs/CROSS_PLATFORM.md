# J.A.R.V.I.S. auf Windows, Linux und Raspberry Pi – Audit und Konzept

Stand: 2026-10-23 · Branch `jarvis/cross-platform-audit-jarvis-au-5db89405` · **Nur Audit, es wurde kein Produktivcode geändert.**

Ziel: Jarvis soll zusätzlich zu macOS auf Windows, Linux und später Raspberry Pi (ARM, evtl. headless) als Host laufen.
**Das Verhalten auf macOS darf sich nicht ändern.**

## Inhalt

0. [Kurzfassung](#0-kurzfassung)
1. [Vorgehen und Grenzen](#1-vorgehen-und-grenzen)
2. [Bestandsaufnahme macOS-spezifischer Stellen](#2-bestandsaufnahme-macos-spezifischer-stellen) (2.1 – 2.20, mit Entsprechungen)
3. [Vorschlag: PlatformAdapter](#3-vorschlag-plattform-abstraktion-platformadapter)
4. [Phasen, Aufwand, Mac-Regressionsrisiko](#4-phasen-aufwand-und-risiko-für-mac-regressionen)
5. [Teststrategie und CI-Matrix](#5-teststrategie-und-ci-matrix)
6. [Offene Fragen an Sir](#6-offene-fragen-an-sir)

---

## 0. Kurzfassung

1. **Der Kern ist weiter als gedacht, aber nicht „plattformneutral“.** `ARCHITECTURE.md:204` nennt als macOS-spezifisch nur Bridge,
   Voice-Daemon und `say`. Das Audit findet zusätzlich: `open -R` (Finder), `sips` (Bildverkleinerung), `open`/`afplay` im
   Wake-up, Homebrew-Pfade im PATH, `:` als PATH-Trenner, `os.uname()`, `signal.SIGUSR1`, Unix-Dateirechte als Schutzmechanismus,
   persönliche Pfade (`/Users/paul/…`), `~/Library/…` als Konfigurations-Default, mac-lastige Persona-Texte und Tool-Namen `mac_*`.
2. **Die Bridge-HTTP-API ist bereits die Plattformgrenze.** Alle 21 `mac_*`-Tools laufen über `BridgeClient`
   (`core/src/jarvis/connectors/mac.py:32-79`) gegen 29 Routen (`bridge/Sources/JarvisBridgeCore/Routing/Routes.swift:9-49`).
   Eine Windows-/Linux-Bridge, die dieselben Routen spricht, macht den Python-Teil weitgehend wiederverwendbar.
3. **Zugangsdaten laufen über `keyring`, nicht über die `security`-CLI.** Der „Jarvis-Schlüsselbund“ ist `keyring` mit Dienst
   `jarvis` (`core/src/jarvis/secrets.py:19,43,58`), plus Env-Variablen `JARVIS_SECRET_*` mit Vorrang (`secrets.py:34-41`).
   Die `security`-CLI kommt nur beim Signieren vor (`scripts/install-app.sh:13`). Windows (Credential Manager) und Linux
   (Secret Service) sind über dasselbe `keyring`-API erreichbar; `uv.lock` enthält bereits `pywin32-ctypes` und `secretstorage`/`jeepney`.
   Auf einem **headless Pi gibt es keinen Secret Service** – dafür fehlt heute ein Fallback (`secrets.py:58` fängt Fehler beim Schreiben nicht ab).
4. **Es gibt schon Muster für „Fähigkeit nicht vorhanden“**, auf denen das Konzept aufsetzt: `wakeup.supported()`
   (`voice/wakeup.py:35-37`), `isMac()` im Desktop (`desktop/src/lib/native.ts:71-74`), Connector-Gate und `Health`
   (`connectors/base.py:75-131`), `ToolRegistry.is_enabled` (`tools/registry.py:110-115`), `AudioIO`-ABC mit Fallback-Fabrik
   (`voice/audio_io.py:37-66,307-320`), `TTSProvider`.
5. **Konkrete Absturzstellen außerhalb von macOS** (nicht „nicht verfügbar“, sondern Ausnahme): `TTSService.provider()` gibt als
   letzte Stufe immer `say` zurück, auch wenn es fehlt (`voice/speech.py:444` vs. `:178-179`); `jarvisctl init` nutzt `os.uname()`
   (`cli.py:99`, unter Windows nicht vorhanden); der Voice-Daemon registriert `SIGUSR1` (`voice/daemon.py:681`, unter Windows nicht vorhanden);
   `Path.chmod`-basierte Schutzmaßnahmen sind unter Windows wirkungslos.
6. **Empfohlene Reihenfolge:** zuerst *Core headless auf Linux/Pi* (billig, hoher Nutzen: Server + Handy-App), dann Linux-Desktop,
   dann Windows. Der Mac-Pfad bleibt als `MacAdapter` byte-identisch; neue Dinge sind additiv (neue Dateien, neue optionale Felder
   mit Default „wie heute“). Aufwand grob 63–98 Personentage insgesamt, der Minimalpfad „Server auf Linux/Pi + Handy“ etwa 15–23.
7. **Basis für „Mac-Tests bleiben grün“:** `uv run pytest tests/unit` im unveränderten Worktree am 2026-10-23: **599 bestanden, 0 Fehler**
   (macOS, Python 3.13.9). Die Core-Unit-Tests laufen laut `ci.yml:16-32` bereits auf `ubuntu-latest`.
8. **Der Coding-Runner (`coding/runner.py`, `coding:`-Block der Config) bleibt unangetastet** (frühere Vorgabe von Sir). Er ist unten nur
   inventarisiert, weil er POSIX-/macOS-Annahmen enthält (Process Groups, SIGSTOP, Seatbelt-Doku, Homebrew-Pfade). Auf Windows
   ist er damit nicht ohne Eingriff nutzbar – das ist eine Entscheidung für Sir (Frage 11).

---

## 1. Vorgehen und Grenzen

- Quellen: ausschließlich Dateien in diesem Repo (Python-Core, Swift-Bridge, Swift-Audiohelfer, Tauri/Rust, React, Skripte,
  Konfiguration, CI, Tests, `uv.lock`). Zitiert wird als `Datei:Zeile` (Zeilen gelesen am 2026-10-23).
- **Spalten „Windows / Linux / Pi“ in den Tabellen sind Vorschläge aus allgemeinem Plattformwissen.** Sie sind im Repo nicht belegt
  und auf diesen Systemen **nicht getestet**. Wo etwas ungewiss ist, steht „prüfen“. Es wurde nichts auf Windows/Linux/Pi ausgeführt.
- Bewertung der Entsprechung: **ja** = gleichwertig machbar · **teils** = machbar mit Einschränkung (steht dabei) · **nein** = keine
  sinnvolle Entsprechung.
- „Pi“ meint Raspberry Pi mit 64-Bit-Linux (aarch64). „headless“ meint: keine Desktop-Sitzung (kein Display, kein Fenstersystem),
  evtl. ohne Audio-Hardware. Headless ist eine Eigenschaft, kein Betriebssystem: ein Linux-Server oder Pi kann beides sein.
- Android-App (`apps/jarvis_mobile`) ist ein **Client**, kein Host. Sie ist hier nur relevant, weil sie bei einem headless Host der
  Hauptzugang wäre (Gateway `:8767`, `mobile/gateway.py`).
- Electron gibt es im Repo nicht; die Desktop-App ist **Tauri 2** (`desktop/src-tauri`).

---

## 2. Bestandsaufnahme macOS-spezifischer Stellen

Schreibweise: `core/src/jarvis/` ist als `core/` abgekürzt, `bridge/Sources/JarvisBridgeCore/` als `bridge/`.

### 2.1 osascript / AppleScript

| ID | Fundstelle | macOS heute | Windows | Linux | Pi / headless |
|---|---|---|---|---|---|
| A1 | `core/connectors/mac.py:240-241` (Handler), `:423-431` (`mac_applescript`, Risiko `high`) | `POST /applescript` an die Bridge | nein (PowerShell/COM ist kein Gegenstück; Empfehlung: nicht portieren) | nein (D-Bus ist kein Gegenstück) | nein |
| A2 | `bridge/Handlers/AutomationHandlers.swift:8-36` | `NSAppleScript` auf dem Main-Thread, Fehlernummer wird zurückgegeben | – | – | – |
| A3 | `bridge/Security/Policy.swift:30,43,57,86`; `config/permissions.yaml:81-82` | doppelt abgeschaltet: Policy `allowAppleScript: false` und `mac.applescript: enabled: false` | – | – | – |
| A4 | `bridge/Support/JarvisBridge.entitlements:5-7`, `bridge/Support/Info.plist:31-32`, `desktop/src-tauri/Entitlements.plist:7-9`, `desktop/src-tauri/Info.plist:8-9` | Apple-Events-Entitlement und Nutzungstext | entfällt | entfällt | entfällt |
| A5 | `scripts/stop.sh:15` (Kommentar) | Bridge wird per SIGTERM beendet, um keinen Automation-Dialog auszulösen | – | – | – |

Hinweis: `osascript` selbst wird im Repo **nicht** aufgerufen; AppleScript läuft ausschließlich in-process über `NSAppleScript`.

### 2.2 Accessibility / UI-Tree

| ID | Fundstelle | macOS heute | Windows | Linux | Pi / headless |
|---|---|---|---|---|---|
| B1 | `core/connectors/mac.py:144-150,268-289,435-444` (`mac_ui_tree`, `mac_ui_press`, `_compact_tree`) | Baum mit `id` (Pfad), `role`, `title`, `value`, `enabled`; `maxDepth ≤ 8`, 250 Knoten | ja: UI Automation (ControlType, Invoke/Toggle/ExpandCollapse-Patterns) | teils: AT-SPI2 über D-Bus (Qualität je Toolkit; Electron-Apps brauchen aktivierte Barrierefreiheit – prüfen) | nein |
| B2 | `bridge/Handlers/AXSupport.swift:7-17,20-52,131-152,190,220,242`; `bridge/Handlers/UIHandlers.swift:33,60,79-82` | `AXUIElement*`, Pfad-IDs, BFS-Suche nach Rolle/Titel, `AXPress`, Wert setzen | wie B1 | wie B1 | nein |
| B3 | `bridge/Routing/Routes.swift:24-26` | `/ui/tree`, `/ui/press`, `/ui/set-value` – **`/ui/set-value` hat kein Python-Tool** | – | – | – |
| B4 | `bridge/Handlers/SystemHandlers.swift:7-9,21-32`; `core/api/routes.py:989-1024`; `desktop/src/views/PermissionsView.tsx:7-8,23-35`; `desktop/src/lib/api.ts:264` | TCC-Recht „Bedienungshilfen“ prüfen/anfordern, UI zeigt „macOS-Berechtigungen“ | Rechtekonzept anders (kein Dialog; Einschränkung: erhöhte Fenster, UIPI) | kein zentrales Recht; a11y-Bus muss laufen | – |
| B5 | `bridge/Sources/JarvisBridge/main.swift:30-48` | Bridge startet sich nach erteilter Berechtigung selbst neu (`/bin/sh` + `/usr/bin/open -g`), damit Eingabe-Events wirken | entfällt | entfällt | – |
| B6 | `bridge/Security/Policy.swift:59` | `blockedApps` = Bundle-IDs `com.apple.keychainaccess`, `com.apple.systempreferences` | eigene Liste (z. B. Anmeldeinformationsverwaltung, Einstellungen) | eigene Liste | – |

### 2.3 mdfind / Spotlight

| ID | Fundstelle | macOS heute | Windows | Linux | Pi / headless |
|---|---|---|---|---|---|
| C1 | `core/connectors/mac.py:214-220,363-378` (`mac_files_search`) | Parameter `query`, `only_in`, `names_only`, `limit ≤ 50` | – | – | – |
| C2 | `bridge/Handlers/FileHandlers.swift:9-55` (`/usr/bin/mdfind` in `:41`, `-onlyin`, `-name`; Schutz gegen führendes `-` in `:14`) | Spotlight-Abfrage; „any“ durchsucht auch **Inhalte** | teils: Windows-Search-Index (`SYSTEMINDEX`), Fallback Verzeichnis-Walk; Inhalte nur soweit indiziert | teils: `plocate`/`locate` (nur Namen), Fallback `find`/Walk | teils: `find`/Walk nur Namen (braucht keine Desktop-Sitzung) |
| C3 | `bridge/Handlers/FileHandlers.swift:57-82` | `localizedTypeDescription`, `isPackage` | Typbeschreibung aus Endung/MIME | wie Windows | – |
| C4 | `config/permissions.yaml:118` | `mdfind` steht in der Shell-Allowlist | entfällt | entfällt | entfällt |

Semantik-Lücke: „Inhaltssuche“ gibt es nur mit Index. Auf anderen Plattformen sollte `files.search.content` als **teils/nein**
gemeldet werden und `names_only` der Normalfall sein.

### 2.4 Finder reveal, URL öffnen

| ID | Fundstelle | macOS heute | Windows | Linux | Pi / headless |
|---|---|---|---|---|---|
| D1 | `core/connectors/mac.py:228-229,397-404` (`mac_files_reveal`) | `POST /files/reveal` | ja: `explorer.exe /select,"<Pfad>"` | teils: D-Bus `org.freedesktop.FileManager1.ShowItems`, sonst `xdg-open <Ordner>` | nein |
| D2 | `bridge/Handlers/FileHandlers.swift:151-159` | `NSWorkspace.activateFileViewerSelecting` | – | – | – |
| D3 | `core/api/routes.py:307-324` | Chat-Anhänge „im Finder zeigen“ per `open -R`; **Plattformprüfung existiert schon:** `sys.platform != "darwin"` → HTTP 501 (`:312-313`) | wie D1 | wie D1 | 501 mit Grund |
| D4 | `desktop/src/hooks/useChatFile.ts:58-65`, `components/chat/Lightbox.tsx:31`, `GeneratedMedia.tsx:63`, `Attachments.tsx:163`, `lib/api.ts:196-197` | Beschriftung „Im Finder zeigen“ | „Im Explorer zeigen“ | „Im Dateimanager zeigen“ | Button ausblenden |
| D5 | `core/connectors/mac.py:231-232,405-412`; `bridge/Handlers/AutomationHandlers.swift:112-126` | `NSWorkspace.open`, nur `http`/`https`/`mailto` | ja: `Start-Process`/`os.startfile` | ja: `xdg-open` | nein (Link nur zurückgeben) |
| D6 | `desktop/src-tauri/capabilities/default.json:19-31` | Tauri-Opener für `https://*`, `http://*`, `mailto:*` | plattformneutral | plattformneutral | – |

Test: `core/tests/unit/test_attachments.py:330-350` setzt `routes.sys.platform = "darwin"` – dieses Muster ist die Vorlage für Adapter-Tests.

### 2.5 Kurzbefehle / shortcuts

| ID | Fundstelle | macOS heute | Windows | Linux | Pi / headless |
|---|---|---|---|---|---|
| E1 | `core/connectors/mac.py:234-238,413-422` (`mac_shortcuts_list`, `mac_shortcut_run`) | `GET /shortcuts`, `POST /shortcuts/run` | nein (kein Systemgegenstück) | nein | nein |
| E2 | `bridge/Handlers/AutomationHandlers.swift:40-108` | `/usr/bin/shortcuts list` bzw. `run`, Ein-/Ausgabe über Temp-Dateien (0700/0600), Timeout 20/60 s | – | – | – |
| E3 | `bridge/Routing/Routes.swift:47-48` | – | – | – | – |

Option für später (nicht Teil der Phasen 1–5): ein Ordner mit freigegebenen Skripten als Ersatz. Entscheidung bei Sir.

### 2.6 Schlüsselbund („Jarvis-Schlüsselbund“) / `security`-CLI

| ID | Fundstelle | macOS heute | Windows | Linux | Pi / headless |
|---|---|---|---|---|---|
| F1 | `core/secrets.py:3-7,16-19,34-41,43-46,54-58,60-66` | `keyring` (macOS-Keychain), Dienst `jarvis`; Env `JARVIS_SECRET_<NAME>` hat Vorrang; `get` fängt `KeyringError`, `set`/`delete` nicht | ja: Credential Manager über `keyring` (`pywin32-ctypes` in `uv.lock:2576`); **Größenlimit je Eintrag prüfen** (`fcm-service-account` ist ein JSON-Dokument, `config.py:295`) | teils: Secret Service (`secretstorage`/`jeepney` in `uv.lock:2755,1182`) – braucht Sitzung, D-Bus und einen Keyring-Daemon | **nein** → `get` liefert `None` (alle Connectoren „nicht konfiguriert“), `set`/`ensure_random` werfen; Fallback nötig: Env/`systemd-creds` oder verschlüsselte Datei |
| F2 | `scripts/install-app.sh:13` | **einzige** `security`-CLI-Nutzung: `security find-identity -v -p codesigning` (Signatur-Identität, keine Secrets) | entfällt | entfällt | entfällt |
| F3 | `bridge/Security/Auth.swift:2,35,45,52,58` | `Security`-Framework nur für `SecRandomCopyBytes`; Bridge-Token liegt als Datei `0600` in `~/Library/Application Support/Jarvis/bridge.token` (`BridgePaths.swift:10`) | Zufall: `secrets`/`os.urandom`; Datei-ACL statt `0600` | wie macOS | wie Linux |
| F4 | Verwendete Konten: `config.py:63,89,106,167-168,176,288-289,295,327`; `app_state.py:75`; `mobile/tokens.py:36`; `agent/hermes_setup.py:30`; `connectors/vercel.py:43-44`; `connectors/browser.py:166`; `connectors/base.py:36` (je Connector `SecretField`) | alle über `secret_store` | – | – | – |
| F5 | Texte, die „Schlüsselbund/Keychain“ nennen: `desktop/src/views/ConnectorsView.tsx:58`, `cli.py:129,307`, `telephony/service.py:131-139`, `backup/service.py:10-12`, `config/jarvis.yaml:2`, `.env.example:3-4` | – | „Anmeldeinformationsverwaltung“ | „Schlüsselbund/Secret Service“ | „Secret-Datei“ |
| F6 | `scripts/setup.sh:24` | DB-Passwort wird per `keyring.set_password` gesetzt | – | – | – |
| F7 | `core/backup/service.py:10-12`; `config.py:322-327` | Backup-Schlüssel nur im Keychain (kein Backup der Secrets) | – | – | Bei einem Pi ohne Keyring muss der Backup-Schlüssel anders verwahrt werden |

### 2.7 launchctl / LaunchAgents / Dienststart

| ID | Fundstelle | macOS heute | Windows | Linux | Pi / headless |
|---|---|---|---|---|---|
| G1 | `bridge/scripts/install-launchagent.sh:7-12,20-23,26-57,61-65`; `uninstall-launchagent.sh:9-19` | LaunchAgent `com.jarvis.bridge`, `RunAtLoad`, `KeepAlive`, `LimitLoadToSessionType: Aqua`, `launchctl bootstrap/bootout/enable` | Task Scheduler „bei Anmeldung“ (kein Dienst: UI-Automation und Audio brauchen die interaktive Sitzung) | systemd `--user`-Unit | systemd `--user` + `loginctl enable-linger` |
| G2 | `scripts/launchd/install.sh:6-45`, `uninstall.sh:3-8` | je ein LaunchAgent für whisper, tts, core, voice; fester Pfad `/opt/homebrew/bin/whisper-server` (`:37`) | Task-Scheduler-Aufgaben | systemd-Units (`Restart=on-failure`) | wie Linux |
| G3 | `scripts/start.sh:6-49`, `scripts/stop.sh:4-16` | bash, `nohup` + PID-Dateien in `~/Library/Application Support/Jarvis/run`, `brew services` (`:27`), `open -g` (Bridge, `:37`), `open` (App, `:48`), `pkill -f …` | PowerShell-/Python-Supervisor (`jarvisctl up/down`, Vorschlag) | `jarvisctl up/down` oder systemd | systemd |
| G4 | `desktop/src-tauri/src/lib.rs:144-147`; `desktop/src/lib/native.ts:93-103` | `tauri-plugin-autostart` mit `MacosLauncher::LaunchAgent` (Parameter wirkt nur auf macOS) | Plugin schreibt Autostart-Eintrag (prüfen) | Plugin schreibt `.desktop`-Autostart (prüfen) | entfällt |
| G5 | `desktop/src-tauri/src/launcher.rs:34-103` | startet/stoppt Dienste über `/bin/bash scripts/start.sh` bzw. `stop.sh` mit PATH `/usr/bin:/bin:/usr/sbin:/sbin` (`:45-49`) | eigener Start ohne bash | wie macOS möglich | entfällt |
| G6 | `scripts/setup.sh:19` | `brew services start postgresql@17` | – | `systemctl enable --now postgresql` | wie Linux |

### 2.8 `open -a` / App starten / Wake-up-Routine

| ID | Fundstelle | macOS heute | Windows | Linux | Pi / headless |
|---|---|---|---|---|---|
| H1 | `core/connectors/mac.py:121-132,152-162`; `bridge/Handlers/AppHandlers.swift:168-214` | läuft die App: `/apps/activate`, sonst `/apps/launch` (Bundle-ID über LaunchServices, Name über `AppLocator`); `focus()` prüft Vordergrund-App per Bundle-ID/Name | ja: `Start-Process`, Startmenü/App-Paths; Kennung = AUMID/Exe-Pfad statt Bundle-ID | teils: `gtk-launch`/`gio launch` mit `.desktop`-ID | nein |
| H2 | `bridge/Handlers/AppHandlers.swift:107-160` | `AppLocator` durchsucht `/Applications`, `/System/Applications`, `~/Applications` (`:111-116`) | Startmenü-Verknüpfungen | `.desktop`-Dateien (`/usr/share/applications`, `~/.local/share/applications`) | – |
| H3 | `bridge/Handlers/AppHandlers.swift:216-236`, `:238-252` | `activate` mit macOS-14-Workaround (`:227`), `quit` = regulärer `terminate()` | ja: `SetForegroundWindow` (Einschränkungen), UIA `WindowPattern.Close` | X11 ja, Wayland teils | nein |
| H4 | `core/voice/wakeup.py:30-31,217-261`; `core/api/routes.py:1163-1190` | Wake-up öffnet Programme mit `/usr/bin/open` (mit `-a`, `-b` oder einer URL); wertet **englische macOS-Fehlermeldungen** aus (`_open_failure`, `:255-261`) | später, andere Kommandos | später | nein |
| H5 | `core/config.py:128-139`; `config/jarvis.yaml:103-112` | `apps`: Namen, `.app`-Pfade, Bundle-IDs, URLs | Namen/`.exe`/AUMID | Namen/`.desktop`-IDs | – |
| H6 | `desktop/src/components/WakeupCard.tsx:1-14`; `desktop/src/lib/native.ts:76-91` | Karte nur auf dem Mac (`isMac()`); Dateiauswahl behandelt `.app` als Datei | Auswahl `.exe`/`.lnk` | Auswahl `.desktop` | – |
| H7 | `config/permissions.yaml:119` | `open` in der Shell-Allowlist | entfällt (`start` ist kein Programm) | `xdg-open` | – |

### 2.9 `say` / `afplay` / Sprachausgabe

| ID | Fundstelle | macOS heute | Windows | Linux | Pi / headless |
|---|---|---|---|---|---|
| I1 | `core/voice/speech.py:171-219` (`MacSayTTS`) | `say -v Anna -r 185 -o out.aiff` → `ffmpeg` → PCM; `available()` prüft `say` und `ffmpeg` (`:178-179`) | SAPI (`System.Speech`) per PowerShell → WAV → PCM | `espeak-ng`/Piper (prüfen) → PCM | wie Linux oder gar keine Stimme |
| I2 | `core/voice/speech.py:404-444,479-484`; `core/config.py:119-125` | `provider()` fällt zuletzt immer auf `say` zurück, **ohne `available()` zu prüfen** (`:444`); Default-Provider `macos` (`config.py:120`) | – | – | **Absturz** (`FileNotFoundError` beim Aufruf von `say`, `:184`) |
| I3 | `core/cli.py:183-184` | `phone-test --say` nutzt `say -v Anna` und schreibt nach `/tmp/jarvis-phone-N.aiff` | nur Testwerkzeug; `tempfile` statt `/tmp` | – | – |
| I4 | `core/voice/audio_io.py:202-266` (`FallbackAudio`, `afplay` in `:261`); `core/voice/wakeup.py:165-198` (`afplay -v`) | Wiedergabe über `afplay` je Satzblock | `sounddevice.OutputStream` (Bibliothek ist schon abhängig) | `sounddevice` oder `paplay`/`aplay` | wie Linux, nur mit Audio-Hardware |
| I5 | `scripts/local-tts/requirements.txt:1` (`mlx-audio==0.5.9`); `scripts/local-tts/server.py:24`; `scripts/setup.sh:54-57`; `scripts/start.sh:30-33`; `core/config.py:113-116` | Lokale Jarvis-Stimme per MLX (Apple Silicon); Core spricht sie nur über `LocalTtsCfg.server_url` | anderer lokaler TTS-Server hinter derselben URL **oder** Fish Audio | wie Windows | Fish Audio (API) oder entfernter TTS-Server |
| I6 | `config/permissions.yaml:120` | `say` in der Shell-Allowlist | entfällt | entfällt | entfällt |

Positiv: Fish Audio (`FishAudioTTS`) und `LocalTTS` sind reine HTTP-Clients und laufen plattformunabhängig.

### 2.10 Hartkodierte Pfade

| ID | Fundstelle | Inhalt | Windows | Linux | Pi / headless |
|---|---|---|---|---|---|
| J1 | `config/jarvis.yaml:17-22`; `core/config.py:45-55` (`PathsCfg`: Pflichtfelder, kein Default), `:29-30` (`_expand`) | `~/Library/Application Support/Jarvis`, `~/Library/Logs/Jarvis`, `~/Documents/Jarvis Vault`, `~/Developer/…` | `%APPDATA%\Jarvis` bzw. `%LOCALAPPDATA%`, Logs darin | `$XDG_DATA_HOME/jarvis`, `$XDG_STATE_HOME/jarvis/logs` | wie Linux; Datenträger/SD-Karte beachten |
| J2 | `core/config.py:35` | `CUSTOMER_SITES_DIR = /Users/paul/Documents/Kunden-Seiten` (importiert in `coding/runner.py:38`) | existiert dort nicht → `is_customer_site()` ist `False`, harmlos | wie Windows | wie Windows |
| J3 | `core/connectors/google.py:50-54` | `ATTACHMENT_ROOTS` = `/Users/paul/Developer/Projects`, `…/Jarvis-Workspaces` | Gmail-Anhänge sind auf fremden Hosts gesperrt (fail-closed) | wie Windows | wie Windows |
| J4 | `core/agent/persona.py:53` | Pfade von Lead-Tool und Jarvis im System-Prompt | Prompt zeigt falsche Pfade | wie Windows | wie Windows |
| J5 | `config/jarvis.yaml:203,223,224` (Block `coding:`) | `extra_path`, `extra_read_dirs`, `extra_write_dirs` mit `/Users/paul/…` | **nicht anfassen** (Runner-Konfiguration) | – | – |
| J6 | `desktop/src-tauri/src/launcher.rs:15-17,19-32,36,77-79`; `core_client.rs:13-20` | `HOME` (Fallback `/tmp`), `Library/Application Support/Jarvis/{root,keep-running,desktop.token}`, `Library/Logs/Jarvis/launcher.log`; **Env-Override** `JARVIS_DESKTOP_TOKEN_FILE` (`core_client.rs:15-17`) | `USERPROFILE`/`%APPDATA%` | `dirs`/Tauri-Pfad-API | entfällt |
| J7 | `bridge/Security/BridgePaths.swift:4-28`; `bridge/Security/Policy.swift:58` | Support/Log-Verzeichnis; erlaubte Datei-Roots `~/Documents`, `~/Desktop`, `~/Downloads`, `~/Developer` | Bridge-Pendant liest dieselben Dateien (siehe 3.6) | wie Windows | – |
| J8 | `scripts/setup.sh:6`, `start.sh:6-8`, `stop.sh:4`, `install-app.sh:8`, `build-audio-helper.sh:7`, `launchd/install.sh:7-8`, `bridge/scripts/install-launchagent.sh:10-11` | `~/Library/…` in jedem Skript | eigene Skripte/`jarvisctl` | wie macOS-Skripte, andere Pfade | – |
| J9 | Homebrew/`/usr/local`: `core/agent/claude_agent.py:69`, `core/coding/runner.py:51,289,314-316`, `core/connectors/generic.py:50`, `core/mobile/livekit_server.py:94`, `core/telephony/tunnel.py:20`, `core/cli.py:268`, `core/mobile/gateway.py:100` (`/Applications/Tailscale.app/…`), `scripts/setup.sh:8,21`, `scripts/start.sh:10`, `scripts/launchd/install.sh:11,37` | zusätzliche Suchpfade bzw. feste Binärpfade | harmlos (Pfad existiert nicht), aber **kein Ersatz**: Programme müssen im PATH liegen | `/usr/bin` ist meist im PATH; Pfade harmlos | wie Linux |
| J10 | PATH-Trenner `:`: `core/agent/claude_agent.py:69`, `core/coding/runner.py:289`, `core/connectors/generic.py:51`, `core/agent/hermes_setup.py:166` | `":".join(...)` | muss `os.pathsep` (`;`) sein | ok | ok |
| J11 | Weitere: `core/connectors/browser.py:301` (Setup-Text), `core/backup/service.py:124` / `config.py:322` (`~/Backups/Jarvis`), `core/agent/skills.py:61` (`~/.claude`), `core/agent/hermes_setup.py:34,39` (`~/.hermes`, Legacy, ungenutzt), `ARCHITECTURE.md:213-222` | – | `~/.claude` gilt dort ebenfalls (`%USERPROFILE%`) | ok | ok |

Wichtig für die Umsetzung: `load_settings` unterstützt bereits Env-Overrides `JARVIS__SECTION__KEY` (`config.py:424-444`). Pfade lassen
sich damit auf anderen Hosts **ohne Codeänderung** setzen; der Mac-Default in `jarvis.yaml` bleibt dabei unverändert.

### 2.11 Tauri (Electron entfällt)

| ID | Fundstelle | macOS heute | Windows | Linux | Pi / headless |
|---|---|---|---|---|---|
| K1 | `desktop/src-tauri/tauri.conf.json:19-26`; `capabilities/default.json:10` | `titleBarStyle: Overlay`, `hiddenTitle` (macOS-Optik, Ziehen über `allow-start-dragging`) | wirkt vermutlich nicht → eigene Fensterleiste prüfen (Plattform-Overlay-Datei) | wie Windows | entfällt |
| K2 | `tauri.conf.json:40-43` | `bundle.targets: ["app"]` (nur macOS-`.app`); Kategorie im macOS-Stil | `nsis`/`msi` | `deb`/`appimage`/`rpm` | entfällt (Core als Paket/systemd) |
| K3 | `tauri.conf.json:45-51`; `desktop/src-tauri/icons/` | Icon-Liste mit `icon.icns`; **`icon.ico` liegt im Repo, ist aber nicht eingetragen** | Eintrag nötig | PNG vorhanden | – |
| K4 | `tauri.conf.json:52-55`; `Entitlements.plist:5-9`; `Info.plist:5-9` | macOS-Block (`minimumSystemVersion 13.0`), Mikrofon- und Apple-Events-Rechte | entfällt | entfällt | – |
| K5 | `tauri.conf.json:30-31` | CSP `connect-src` erlaubt nur `127.0.0.1:8765` | bleibt, solange der Core lokal läuft | wie Windows | **Remote-Core** (Pi/VPS) erfordert Änderung der CSP **und** von `core_client.rs:11` |
| K6 | `desktop/src-tauri/src/lib.rs:62,70,137,161-163` | `CmdOrCtrl+Q`; Tray-Icon als Template (`icon_as_template(true)`, nur macOS wirksam); Hotkey `SUPER+SHIFT+J` (= ⌘⇧J, auf Windows/Linux Win-/Super-Taste); Meldung nennt „Cmd+Shift+J“ | Hotkey mit `CmdOrCtrl` prüfen | wie Windows | – |
| K7 | `lib.rs:167-175,186-192` | Schließen = Verstecken (Tray); `RunEvent::Reopen` ist bereits `cfg(target_os = "macos")` | ok | ok | – |
| K8 | `desktop/src-tauri/src/main.rs:2` | `windows_subsystem = "windows"` (Release) ist bereits gesetzt | ok | – | – |
| K9 | `config/jarvis.yaml:12-14` | CORS erlaubt `tauri://localhost` **und** `http://tauri.localhost` (Windows-Origin) | schon vorbereitet | ok | – |
| K10 | `desktop/src/lib/native.ts:13-23` | Token wird nur in der Tauri-App gelesen (`get_core_token`); im Browser nur im Dev-Modus mit `?devtoken=` | – | – | **kein Web-Client ohne Änderung** (Frage 5) |
| K11 | `desktop/src/lib/native.ts:71-74`; `components/Shell.tsx:53`; `views/PermissionsView.tsx:23`; `components/CommandBar.tsx:28` | `isMac()` per User-Agent; Texte „macOS-Rechte/-Berechtigungen“; `⌘K` (der Handler prüft schon `metaKey` oder `ctrlKey`) | Texte plattformabhängig machen | wie Windows | – |
| K12 | `.github/workflows/ci.yml:34-54`; `desktop/src-tauri/src/core_client.rs:62-71` | CI prüft nur `tsc`, `eslint`, `vitest`, `pnpm audit`; **kein Tauri-Build, kein `cargo test`** (der Rust-Test existiert, läuft aber nirgends) | – | – | – |

### 2.12 Build / Installer / Signierung

| ID | Fundstelle | macOS heute | Windows | Linux | Pi / headless |
|---|---|---|---|---|---|
| L1 | `bridge/scripts/build-app.sh:4-7,17-29,32-36`; `bridge/Support/Info.plist:23-26` | `swift build`, `.app` zusammensetzen, `plutil`, `codesign` (Hardened Runtime, standardmäßig ad-hoc `-`), `LSUIElement`, Mindestversion 14.0 | Bridge-Build (je nach Sprache) + Signierung | Binary/Paket | – |
| L2 | `scripts/install-app.sh:12-16,19-21,24,28-29,33-38` | Signatur-Identität per `security find-identity`; ohne Zertifikat ad-hoc und Warnung „macOS-Rechte gehen bei Updates verloren“; `pnpm tauri build --bundles app` mit `APPLE_SIGNING_IDENTITY`; `ditto` nach `/Applications`; `open` | Installer (NSIS/MSI), Code-Signing-Zertifikat (SmartScreen) | `deb`/AppImage | – |
| L3 | `scripts/build-audio-helper.sh:7-11`; `bridge/Package.swift:6,12-23` | `swiftc` für `jarvis-audio`; `.macOS(.v14)`; AppKit, ApplicationServices, Carbon, Network, ScreenCaptureKit, Security | entfällt | entfällt | – |
| L4 | `scripts/setup.sh:2-16,87-89`; `README.md:81`, `SETUP.md:3` | „macOS auf Apple Silicon, Homebrew, Xcode CLT“ als Voraussetzung | eigene Anleitung | eigene Anleitung (apt) | eigene Anleitung |
| L5 | Notarisierung | im Repo nicht vorhanden (lokal gebaute, selbst signierte Apps) | – | – | – |

### 2.13 Mikrofon / Wake-Word

| ID | Fundstelle | macOS heute | Windows | Linux | Pi / headless |
|---|---|---|---|---|---|
| M1 | `core/voice/audio_io.py:69-199` (`NativeAudio`), `:303-320`; `core/native/JarvisAudio/main.swift:1-60`; `scripts/build-audio-helper.sh` | Swift-Helfer mit `AVAudioEngine` und Apple-Voice-Processing (Echounterdrückung); Protokoll über stdin/stdout; bei Fehler Rückfall auf `FallbackAudio` | `sounddevice` für Ein- **und** Ausgabe (neue `AudioIO`-Klasse); Echounterdrückung: nein (Standard-Config ist ohnehin `echo_cancellation: false`, `config.py:161`) | wie Windows; `libportaudio2` nötig (`uv.lock:2794`: für Linux gibt es nur das Pure-Python-Rad ohne PortAudio, für Windows und macOS liegen Räder mit Bibliothek vor) | nur mit Mikrofon-/Lautsprecher-Hardware (USB, HAT); sonst Sprachkanal „nicht verfügbar“ |
| M2 | `core/voice/audio_io.py:214-233` | Mikrofon über `sounddevice` (PortAudio) – bereits plattformneutral; `mute()` schließt den Stream (Kommentar nennt macOS-Anzeige) | ja | ja | ja |
| M3 | `core/voice/wakeword.py:17,34-38` | `onnxruntime` mit `CPUExecutionProvider`, Modelle aus `<data_dir>/models` (`scripts/setup.sh:50-53`, `cli.py:279-280`) | ja | ja | ja: `uv.lock:1968` enthält `onnxruntime` für `manylinux_2_28_aarch64` |
| M4 | `core/voice/daemon.py:137-139,165,262,610-612,681` | Mikrofonrecht wird nur **vermutet** (`"granted" if audio…`); ein fehlendes Mikrofon wird bereits abgefangen (`:610-614`: Zustand `error`, Daemon bleibt im Leerlauf); `signal.SIGUSR1` (`:681`) | `SIGUSR1` fehlt → Startfehler; Recht über Windows-Datenschutzeinstellungen | `SIGUSR1` ok | wie Linux |
| M5 | `core/config.py:142-163`; `ARCHITECTURE.md:108-121` | Barge-in funktioniert **ohne** Echounterdrückung (`BargeInDetector`, `barge_in: auto`) | ja | ja | ja |
| M6 | `scripts/start.sh:29`; `core/config.py:98-101`; `scripts/setup.sh:12,48-49` | `whisper-server` (Homebrew `whisper-cpp`, Modell `large-v3-turbo-q5_0`); Core spricht ihn nur über `server_url` | Binary/Build prüfen | Build/Paket prüfen | Modell vermutlich zu schwer → kleineres Modell **oder** `server_url` auf den Mac/VPS (messen) |
| M7 | `core/voice/wakeup.py:35-37` | Wake-up-Routine ist über `supported()` auf `darwin` begrenzt | Capability `voice.wakeup_routine` | wie Windows | nein |
| M8 | `desktop/src-tauri/src/lib.rs:51,76-91` | Push-to-Talk aus dem Tray ruft `/api/voice/command` | ok | ok | entfällt |

### 2.14 Zwischenablage

| ID | Fundstelle | macOS heute | Windows | Linux | Pi / headless |
|---|---|---|---|---|---|
| N1 | `core/connectors/mac.py:207-212,346-362` | `GET/POST /clipboard`, Ergebnis als „untrusted“ umhüllt (6000 Zeichen) | ja: `Get-Clipboard`/`Set-Clipboard` oder Win32 | teils: X11 `xclip`/`xsel`, Wayland `wl-copy`/`wl-paste` (Fokus-Einschränkungen) | nein |
| N2 | `bridge/Handlers/ClipboardHandlers.swift:5-25` | `NSPasteboard.general`, nur Text, Typenliste (max. 20) | – | – | – |
| N3 | `desktop/src/hooks/useAttachmentDraft.ts:105` | Einfügen von Screenshots/Dateien im Webview | plattformneutral | plattformneutral | – |

### 2.15 Screenshot

| ID | Fundstelle | macOS heute | Windows | Linux | Pi / headless |
|---|---|---|---|---|---|
| O1 | `core/connectors/mac.py:176-205,336-345` | Bridge liefert PNG (Base64), Core speichert nach `<data_dir>/screenshots`, Claude liest die Datei (Fallback Ollama-Vision) | ja: GDI/DXGI/Windows.Graphics.Capture | teils: X11 ja; Wayland nur über Portal (Zustimmungsdialog) | nein (außer virtuelles Display) |
| O2 | `bridge/Handlers/ScreenshotHandler.swift:8-85`; `bridge/Handlers/SystemHandlers.swift:27`; `bridge/Package.swift:19` | `ScreenCaptureKit` (`SCScreenshotManager`), TCC-Recht „Bildschirmaufnahme“, Fenster- oder Display-Auswahl, Größenanpassung | – | – | – |

### 2.16 App-Liste / Fensterliste

| ID | Fundstelle | macOS heute | Windows | Linux | Pi / headless |
|---|---|---|---|---|---|
| P1 | `core/connectors/mac.py:117-119,137-142,245,264-267`; `bridge/Handlers/AppHandlers.swift:55-71` | `NSWorkspace.runningApplications` (nur `regular`), Felder `name`, `bundleId`, `pid`, `active`, `hidden`, `path` | ja: Top-Level-Fenster + `psutil`; `bundleId` → AUMID/Exe | X11 ja (EWMH), Wayland **teils/nein** (kein generisches API) | nein |
| P2 | `bridge/Handlers/WindowHandlers.swift:32-98` | `CGWindowListCopyWindowInfo` (Bounds, Layer, Titel nur mit Screen-Recording-Recht), Vordergrund-App plus AX-Fokus | ja | X11 ja, Wayland teils | nein |
| P3 | `core/connectors/mac.py:29` | Selektor `name`/`bundleId`/`pid` | `name`/`exe`/`pid` | `name`/`desktop_id`/`pid` | – |

### 2.17 Tastatur- und Maus-Automation

| ID | Fundstelle | macOS heute | Windows | Linux | Pi / headless |
|---|---|---|---|---|---|
| Q1 | `core/connectors/mac.py:152-174,290-335` | vor jeder Eingabe wird die Ziel-App aktiviert und geprüft (`focus`), dann `/input/type`, `/input/key` bzw. `/input/click`; Modifier-Enum `cmd, shift, alt, ctrl, fn` (`:308`), Beispieltext „`modifiers=['cmd']`“ (`:301`) | `SendInput`; erhöhte Fenster nicht erreichbar (UIPI); `cmd` braucht eine Zuordnung (Ctrl oder Win) | X11: XTest; Wayland: nur `ydotool`/libei mit Rechten → meist **nein** | nein |
| Q2 | `bridge/Handlers/InputHandlers.swift:7-143` | `CGEvent` über HID-Tap, Text zeichenweise (`:67`, 8 ms Pause `:95`), Not-Aus-Prüfung je Zeichen (`:73-77`) | – | – | – |
| Q3 | `bridge/Handlers/InputHandlers.swift:147-202` | Klick in globalen Koordinaten (Display-Prüfung `:158`, Blockliste für Zielfenster `:175-180`) | – | – | – |
| Q4 | `bridge/Handlers/InputHandlers.swift:205-260` | `KeyMap` mit `kVK_*` und layoutabhängiger Zuordnung | Virtual-Key-Codes + Layout (`VkKeyScan`) | Keysyms | – |

### 2.18 Shell-Allowlist

| ID | Fundstelle | macOS heute | Windows | Linux | Pi / headless |
|---|---|---|---|---|---|
| R1 | `config/permissions.yaml:88-122`; `core/connectors/generic.py:30-112` | Allowlist nach **Programmnamen**; nur macOS: `sw_vers` (`:117`), `mdfind` (`:118`), `open` (`:119`), `say` (`:120`), `pmset` (`:121`), `networksetup` (`:122`) | `ls cat grep head tail wc df du uptime pwd` gibt es nicht; **`find` löst auf `find.exe` auf, ein anderes Programm** – Namenskollision in einer Allowlist, die nur nach Namen prüft (`generic.py:59`) | funktioniert weitgehend; mac-Einträge sind harmlos („nicht installiert“, `generic.py:41-44,65-66`) | wie Linux |
| R2 | `core/connectors/generic.py:46-51,55,59,67-70` | `:`-PATH plus `/opt/homebrew/bin`, `~/Library/Android/sdk/platform-tools`; `shlex.split` (POSIX-Syntax); `"/" in exe`; Arbeitsverzeichnis muss unter `Path.home()` liegen | `os.pathsep`, Windows-Quoting, `\`-Trenner; `.cmd`/`.bat`-Shims (`npm`, `pnpm`) werden über `cmd.exe` gestartet → Argument-Injection-Klasse (BatBadBut); `_FORBIDDEN_ARG_CHARS` (`:23`) enthält `%`, `^`, `"` nicht | ok | ok |
| R3 | `core/connectors/generic.py:41-44` | `Health` meldet `not_installed` – **kein Absturz**, bereits sauber | – | – | – |

### 2.19 Prozess-/POSIX-Annahmen (querschnittlich)

| ID | Fundstelle | Annahme | Windows | Linux | Pi |
|---|---|---|---|---|---|
| S1 | `core/agent/claude_agent.py:250-254,289-293`; `:99-111` | `start_new_session=True`, `os.killpg(…, SIGTERM)`; Signalnamen aus negativem Exit-Code | `killpg` fehlt; Prozessbaum per Job-Objekt/`taskkill /T` oder `psutil` | ok | ok |
| S2 | `core/coding/runner.py:534-545,611-644,686-690,1175,1252,1283,1292-1301,1391` (**Runner bleibt unangetastet**) | `start_new_session`, `killpg` mit `SIGTERM`/`SIGKILL`; Pause/Fortsetzen über `SIGSTOP`/`SIGCONT` | `SIGSTOP`/`SIGCONT`/`killpg` existieren nicht → Runner dort nicht lauffähig | ok | ok |
| S3 | `core/cli.py:99` | `os.uname().nodename` | **fehlt unter Windows** → `jarvisctl init` bricht ab (portabel wäre `socket.gethostname()` wie in `jobs/engine.py:44`) | ok | ok |
| S4 | `core/voice/daemon.py:681` | `signal.SIGUSR1` | **fehlt unter Windows** → Voice-Daemon startet nicht | ok | ok |
| S5 | Dateirechte als Schutz: `core/app_state.py:94` (`data_dir` 0700), `core/cli.py:32-34`, `core/agent/attachments.py:190,195`, `core/mobile/gateway.py:138,162`, `core/mobile/livekit_server.py:90`, `core/backup/service.py:215,241`, `core/connectors/kie_creative.py:330`, `core/agent/hermes_setup.py:56`; Prüfung `core/cli.py:284-289` (`mode & 0o077`) | Unix-Modi als Zugriffsschutz für Token, Schlüssel, Zertifikate | **wirkungslos** (nur Schreibschutz-Flag); Schutz über ACLs nötig, `doctor`-Prüfung ist dort bedeutungslos | ok | ok |
| S6 | `core/agent/skills.py:161` | `symlink_to` für Skill-Verzeichnisse | braucht Privileg/Entwicklermodus → Fallback Kopie/Junction | ok | ok |
| S7 | `core/cli.py:183` | `/tmp` fest | `tempfile.gettempdir()` | ok | ok |
| S8 | `core/security/guards.py:18-23`; `bridge/Security/BridgePaths.swift:11`; `bridge/Security/KillSwitch.swift:4-20` | Not-Aus = Datei `KILL` im Datenordner, **gemeinsam** von Core und Bridge | jede Bridge muss dieselbe Datei beobachten (Vertrag) | wie Windows | wie Windows |
| S9 | Signal-Handler | im Python-Code wurde **kein** `add_signal_handler` gefunden; `uvicorn` übernimmt Signale | ok | ok | ok |

### 2.20 Netzwerk und Fremdprogramme

| ID | Fundstelle | macOS heute | Windows | Linux | Pi |
|---|---|---|---|---|---|
| T1 | `scripts/setup.sh:12,19-27`; `core/cli.py:268`; `core/backup/service.py:204-210`; `core/config.py:58-63` | PostgreSQL 17 + pgvector über Homebrew; `pg_dump` für Backups; Host/Port konfigurierbar | Installer + pgvector aufwendig; Container oder entfernte DB (`database.host`) | Paketquellen (pgvector-Paket prüfen) | wie Linux; **ARM-Verfügbarkeit von pgvector prüfen**, oder DB auf anderem Host |
| T2 | `core/mobile/livekit_server.py:92-106`; `core/config.py:277-289` | `livekit-server` aus Homebrew (feste Pfade), Meldung `brew install livekit` | Binary prüfen | Binary prüfen | `linux/arm64`-Binary prüfen |
| T3 | `core/telephony/tunnel.py:20,70` | `cloudflared` (feste Pfade, Meldung `brew install`) | Binary vorhanden (prüfen) | ja | ARM-Binary prüfen |
| T4 | `core/mobile/gateway.py:70-105` | LAN-/Tailscale-Adressen per `psutil` (plattformneutral); Filter für Schnittstellennamen `bridge`, `vmnet`, `docker` (macOS-Namen, `:84`); Tailscale-CLI-Fallback `/Applications/Tailscale.app/…` (`:100`) | Namen wie `vEthernet` ergänzen | Namen wie `br-`, `veth`, `virbr` ergänzen | wie Linux |
| T5 | `core/agent/claude_agent.py:183,211,609,663,694`; `core/config.py:77` | Claude-Code-CLI per `shutil.which("claude")`; Subscription-Login | **Verfügbarkeit der CLI je Plattform ist aus dem Repo nicht ableitbar → prüfen** | prüfen | prüfen (ARM) |
| T6 | `core/connectors/browser.py:1-8,115-149,178-195,44`; `core/config.py:246-256`; `scripts/setup.sh:30` | Playwright mit Edge (`channel="msedge"`), Rückfall Chromium; User-Agent behauptet „Macintosh“ (`:44`) | Edge vorhanden | Edge nicht Standard → Chromium; UA anpassen | Playwright-Chromium für ARM prüfen |
| T7 | `core/agent/attachments.py:305-336` | große Bilder werden über `sips` verkleinert; ohne `sips` liefert `image_for_model` `None` (Bild wird nicht an das Modell gegeben) – **kein Absturz, aber Funktionsverlust** | Pillow (liegt transitiv in `uv.lock:2023`, ist keine direkte Abhängigkeit) | wie Windows | wie Windows |
| T8 | `core/memory/embeddings.py` (in-process ONNX), `scripts/setup.sh:33-37` | Embedding-Modell ca. 1,1 GB im Core-Prozess (Kommentar im Skript) | ok | ok | **RAM-Bedarf prüfen** |
| T9 | `core/mobile/phone_control.py`, `core/mobile/phone_tools.py` | Android-Handy als gesteuertes Gerät – unabhängig vom Host | gleich | gleich | gleich |

---

## 3. Vorschlag: Plattform-Abstraktion (`PlatformAdapter`)

### 3.1 Leitprinzipien (Schutz des Mac-Verhaltens)

1. **Der Mac-Pfad ist heutiger Code, nur anders verdrahtet.** `MacAdapter` delegiert an die bestehenden Klassen
   (`BridgeClient`/`MacConnector`, `MacSayTTS`, `NativeAudio`/`FallbackAudio`, `wakeup`-Funktionen, `keyring`, `scripts/*.sh`,
   Swift-Bridge). Es werden **keine** Mac-Dateien verschoben oder umbenannt, solange nicht eine Golden-Test-Absicherung steht (Abschnitt 5).
2. **Additiv statt ersetzend.** Neue Felder haben Defaults, die dem heutigen Verhalten entsprechen (z. B. `ToolSpec.requires = ()`).
   Neue Dateien: `core/src/jarvis/platform/…`, `config/platform/*.yaml`, `desktop/src-tauri/tauri.<os>.conf.json`.
3. **Erkennung explizit überschreibbar:** `JARVIS_PLATFORM=mac|win|linux|headless`. Standard: `sys.platform`/`platform.machine()` plus
   Sitzungsmerkmale (`DISPLAY`, `WAYLAND_DISPLAY`, `XDG_SESSION_TYPE`). Ohne Hinweise auf eine Desktop-Sitzung → `headless`.
4. **Nicht unterstützt heißt „nicht verfügbar mit Grund“, nie Ausnahme** (3.5).
5. **Sicherheitsmodell bleibt gleich:** Policy, Risikostufen, Freigaben, Not-Aus, Audit gelten unverändert; keine neue Rechteausweitung
   (insbesondere kein generisches „PowerShell ausführen“ als Ersatz für AppleScript).

### 3.2 Schichten

```
 Orchestrator / ToolExecutor / MCP-Server (unverändert)
                  │
         ToolRegistry + Capability-Gate  ◄── CapabilityReport (3.4)
                  │
   ┌──────────────┴───────────────────────────────────────────────┐
   │ PlatformAdapter  (mac | win | linux | headless)               │
   │   paths · secrets · services · audio · procs · desktop        │
   └───────┬──────────────────────────────────────────────┬───────┘
           │ in-process                                   │ HTTP 127.0.0.1:8766 (Bridge-Protokoll v1)
   Python-Implementierungen                     ┌──────────┴───────────┐
   (keyring, sounddevice, psutil, …)            │ Swift-Bridge (Mac)    │ unverändert
                                                │ Bridge Win/Linux      │ neu, gleiche Routen
                                                └──────────────────────┘
```

Der **Bridge-Vertrag** ist die vorhandene Routentabelle (`Routes.swift:9-49`, 29 Routen) plus Token-Datei, Not-Aus-Datei, Policy-Datei,
Loopback-only, Audit-Log. Er wird als „Protokoll v1“ dokumentiert und per Contract-Test fixiert (Abschnitt 5).

### 3.3 Schnittstelle (Skizze, kein Produktivcode)

```python
# core/src/jarvis/platform/adapter.py  – Entwurf
class PlatformAdapter(Protocol):
    id: Literal["mac", "win", "linux", "headless"]

    def info(self) -> PlatformInfo: ...            # os, arch, session_type, display, audio_devices
    def probe(self) -> CapabilityReport: ...       # capability-id -> (status, reason)

    paths:    PathsProvider            # data_dir, log_dir, defaults für vault/workspaces/projects
    secrets:  SecretsBackend           # get/set/delete/exists (keyring | env | verschlüsselte Datei)
    services: ServiceManager           # install/uninstall autostart, up/down
    procs:    ProcessControl           # spawn_group, terminate_tree, suspend/resume
    audio:    AudioBackend | None      # open_audio(), system_tts(), play_file(), aec
    desktop:  DesktopControl | None    # apps, windows, ui, input, screenshot, clipboard,
                                       # files.search/reveal, open_url, scripting, shortcuts
```

- `MacAdapter.desktop` ist der bestehende `BridgeClient` (HTTP). `WinAdapter.desktop`/`LinuxAdapter.desktop` sind derselbe Client,
  der auf eine Bridge dieser Plattform zeigt (3.6) – der Python-Connector bleibt gleich.
- `HeadlessAdapter` **umhüllt** einen OS-Adapter und maskiert `desktop` (und ohne Geräte auch `audio`); `paths`, `secrets`,
  `services`, `procs` stammen vom Basis-OS. So bleibt „Pi = Linux + headless“ ohne Doppelcode.
- Die Teilschnittstellen folgen dem im Repo üblichen Idiom: ABC/Protocol + Fabrik + Rückfall (wie `AudioIO`/`open_audio`,
  `TTSProvider`/`TTSService`, `TelephonyProvider`), Spawn-Funktionen injizierbar (wie `wakeup.play_sound(spawn=…)`) – damit lassen sich
  Kommandozeilen je Plattform **ohne** das Betriebssystem testen.

### 3.4 Capability-Flags

Jede Fähigkeit meldet `available | degraded | unavailable` plus einen deutschen Grund (für UI und Agent). Anfangswerte
(Zielzustand nach den Phasen; **mac = heutiges Verhalten, keine Änderung**):

| Capability | mac | win | linux-Desktop (X11 / Wayland) | headless / Pi |
|---|---|---|---|---|
| `paths.default` | ja | ja | ja | ja |
| `secrets.env` (`JARVIS_SECRET_*`) | ja | ja | ja | ja |
| `secrets.os_store` | ja | ja (Größenlimit prüfen) | teils (Secret Service nötig) | nein |
| `secrets.file` (neuer Fallback) | – | optional | optional | ja |
| `service.autostart` | ja (LaunchAgent) | ja (Task Scheduler) | ja (systemd --user) | ja (systemd + linger) |
| `desktop.apps` (list/launch/quit) | ja | ja | ja / teils | nein |
| `desktop.windows` (list/frontmost) | ja | ja | ja / teils | nein |
| `desktop.ui_tree`, `desktop.ui_press` | ja | ja (UIA) | teils (AT-SPI) | nein |
| `desktop.input` (type/key/click) | ja | ja (nicht in erhöhte Fenster) | ja / nein | nein |
| `desktop.screenshot` | ja | ja | ja / teils (Portal) | nein |
| `desktop.clipboard` | ja | ja | ja / teils | nein |
| `desktop.reveal` | ja | ja | teils | nein |
| `desktop.open_url` | ja | ja | ja | nein |
| `desktop.scripting` (AppleScript) | ja (per Policy aus) | nein | nein | nein |
| `desktop.shortcuts` | ja | nein | nein | nein |
| `files.search.names` | ja | ja | ja | ja |
| `files.search.content` | ja (Spotlight) | teils (Index) | teils/nein | nein |
| `files.manage` (move/mkdir) | ja | ja | ja | ja |
| `voice.capture` | ja | ja | ja | nur mit Hardware |
| `voice.playback` | ja | ja | ja | nur mit Hardware |
| `voice.aec` | teils (Helfer, Standard aus) | nein | nein | nein |
| `voice.wakeword` | ja | ja | ja | nur mit Mikrofon |
| `voice.tts.system` | ja (`say`) | ja (SAPI) | teils (espeak-ng/Piper) | teils |
| `voice.tts.local` (MLX-Server) | ja | nein (anderer Server nötig) | nein (anderer Server nötig) | nein |
| `voice.wakeup_routine` | ja | nein (später) | nein (später) | nein |
| `image.scale` | ja (`sips`) | ja (Pillow) | ja (Pillow) | ja (Pillow) |
| `shell.run` | ja | ja (eigene Allowlist) | ja | ja |
| `browser.edge` | ja | ja | nein → Chromium | nein → Chromium |
| `coding.runner` | ja (unverändert) | außerhalb dieses Konzepts | außerhalb dieses Konzepts | außerhalb dieses Konzepts |
| `ui.tray`, `ui.global_hotkey` | ja | ja | teils | nein (keine App) |

### 3.5 „Nicht verfügbar“ statt Absturz – Mechanik

1. **Erkennung:** `adapter.probe()` beim Start und in der bestehenden Health-Schleife (`connectors/base.py:260-267`, alle 300 s).
2. **Tool-Kopplung:** neues optionales Feld `ToolSpec.requires: tuple[str, ...] = ()` (nach `summarize`, `tools/registry.py:40-52`).
   Alle heutigen Tools bleiben unverändert (leeres Tupel). `mac_*` bekommen ihre Capability-IDs; auf dem Mac sind sie immer `available`,
   die Laufzeitprüfung der Bridge (`MacConnector.health`) bleibt wie heute.
3. **Registry:** `ToolRegistry.is_enabled` (`registry.py:110-115`) prüft zusätzlich die Capability. Nicht verfügbare Tools verschwinden aus
   `all()` (damit aus der MCP-Liste für den Agenten), bleiben aber in `all(include_disabled=True)` mit `unavailable_reason`.
4. **Executor:** Ruft ein veralteter Tool-Cache dennoch ein solches Tool auf, antwortet der Executor **vor** der Freigabeprüfung mit
   `ToolUserError("… auf diesem System nicht verfügbar – <Grund>")` und Audit-Eintrag. Keine Freigabe für Unmögliches
   (Ablauf: `ARCHITECTURE.md:73-80`).
5. **API/UI:** neuer Endpunkt `GET /api/system/capabilities`; `GET /api/permissions` (`routes.py:989-1016`) bekommt `unavailable_reason`
   je Tool; `Health` bekommt den Status `unsupported` (Literal in `base.py:77`) oder nutzt `disabled` mit Grund. Die Ansichten „Berechtigungen“,
   „Connectoren“ und die Weckroutinen-Karte zeigen ausgegraut „nicht verfügbar: …“ (heute nur `supported` in `wakeup.py:134`).
6. **Routen mit Plattformprüfung:** `_require_mac` (`routes.py:1140-1142`) und die `darwin`-Prüfung in `media_reveal` (`:312-313`)
   werden zu `_require(capability)`. Die heutigen Meldungen bleiben wörtlich erhalten.
7. **Sprache:** `open_audio` ohne Gerät führt zu `voice.status` mit `state="unavailable"` und `detail` (Feld existiert, `routes.py:1193-1199`)
   statt des heutigen `state="error"` in `daemon.py:610-614` (der Fehlerfall wird dort schon abgefangen; neu ist die Unterscheidung „Fehler“ und „gibt es hier nicht“). `TTSService.provider()` liefert ohne `say` eine `NullTTS`; Kanäle antworten dann nur als Text.
8. **Persona:** Eine kurze Zeile „Auf diesem Host gibt es keine Desktop-Steuerung …“ wird **nur** ergänzt, wenn etwas fehlt. Auf dem Mac bleibt
   der System-Prompt wortgleich (per Golden-Test abgesichert). Die mac-lastigen Texte (`persona.py:19,34,87`, `voice_agent.py:59`,
   `mcp/server.py:69`, `mobile/phone_tools.py:20`) werden erst in Phase 6 angefasst.

### 3.6 Bridge für Windows/Linux

Optionen für das Pendant zur Swift-Bridge (Entscheidung bei Sir, Frage 2):

| Option | Vorteil | Nachteil |
|---|---|---|
| **A. Python-Sidecar** (`pywinauto`/`uiautomation`, `pyatspi`, `mss`, `pynput`, `pyperclip`; eigener Prozess, gleiche HTTP-Routen) | ein Codebestand für Win und Linux; Contract-Tests in pytest; Startpfad wie Core | Sicherheitsschicht (Token, Not-Aus, Policy, `PathGuard`, Rate-Limit, Audit – heute `bridge/Security/*.swift`) muss in Python nachgebaut werden; Abhängigkeitsgewicht |
| **B. Rust-Binary** (im Tauri-Workspace) | eine Binärdatei, gleiche Toolchain wie die App | getrennte Implementierung je OS; mehr Aufwand |
| **C. In-process im Core** (kein zweiter Prozess) | kein zweiter Hop | verliert die Trennung/Defense-in-depth; Core müsste in der Benutzersitzung laufen |

Empfehlung: **A**, weil sie den geringsten Aufwand hat und den Vertrag sichtbar macht. Der Mac läuft weiter mit der Swift-Bridge.
Zusätzlich: `GET /health` der neuen Bridge meldet `capabilities`; `MacConnector.health` ignoriert unbekannte Schlüssel
(`mac.py:105-111`), die Swift-Bridge muss dafür nicht angefasst werden.

### 3.7 Weitere Bausteine

- **Pfade:** `PathsProvider` liefert plattformtypische Defaults (Python: `platformdirs`; Rust: `dirs` bzw. Tauri-Pfad-API). Auf dem Mac liefert er
  **exakt** die heutigen Literale. Konfiguration über `config/platform/<os>.yaml` als Overlay unter `jarvis.yaml` (nur geladen, wenn die Datei
  existiert; auf dem Mac gibt es keine). Zwischenlösung ohne Code: Env-Overrides (`config.py:424-444`).
- **Secrets:** `SecretStore` behält `keyring` (Mac-Keychain, Dienst `jarvis`, **keine Migration**) und die Env-Quelle. Neu: Backend-Auswahl
  Env → OS-Speicher → (headless) verschlüsselte Datei mit Schlüssel aus `systemd-creds`/`JARVIS_MASTER_KEY`; `NoKeyringError` bei `set`/`delete`
  abfangen; Größenlimit unter Windows prüfen (`fcm-service-account`).
- **Dienste:** `ServiceManager`. Mac: heutige Skripte (`start.sh`, LaunchAgents) unverändert. Linux: systemd-User-Units aus derselben Dienstliste
  (whisper, tts, core, voice, bridge). Windows: Task Scheduler. Neu: `jarvisctl up|down` als plattformneutraler Supervisor für Windows/Linux; `launcher.rs`
  wählt per `cfg!(target_os)` zwischen `start.sh` (Mac, unverändert) und `jarvisctl up`.
- **Audio/TTS:** neue `AudioIO`-Klasse `SounddeviceAudio` (Ein- und Ausgabe über PortAudio, mit korrektem `flush()`/`drained()`); `open_audio` wählt per Adapter,
  Mac-Reihenfolge `NativeAudio` → `FallbackAudio` bleibt. `SystemTTS` als Schnittstelle neben `MacSayTTS`; Kette bleibt `local` → `fish` → System.
- **Prozesse:** `ProcessControl` kapselt `start_new_session`/`killpg` für `claude_agent.py` und andere Nicht-Runner-Stellen; Windows über `psutil`
  (`suspend`/`resume`/Prozessbaum). **Runner bleibt außen vor.**
- **Shell-Allowlist:** `shell_allowlist` bleibt die Mac-Liste. Neu optional `shell_allowlist_by_platform` (Mac-Eintrag = heutige Liste, wörtlich).
  Auf Windows eigene Liste ohne `find` und Quoting-Härtung für `.cmd`.
- **Desktop-App:** Plattform-Overlays `tauri.windows.conf.json`/`tauri.linux.conf.json` (Bundle-Ziele, Icons, Fensteroptik); `tauri.conf.json` bleibt für den
  Mac unverändert. `isMac()` wird durch eine Abfrage von `/api/system/capabilities` ergänzt, Texte nach Plattform („Finder“/„Explorer“/„Dateimanager“).

### 3.8 Tool-Namen

`mac_*` ist LLM-sichtbar und steht in Persona (`persona.py:34`), Tool-Label (`voice_agent.py:59`), Freigabe-/Verlaufsdaten und
`permissions.yaml:81`. Empfehlung: **`mac_*` und `MacConnector` bleiben auf dem Mac unverändert.** Auf Windows/Linux registriert ein neuer
`DesktopConnector` (id `desktop`, Tools `desktop_*`) dieselben Funktionen; die Tool-Bauer werden dafür gemeinsam genutzt, ein Golden-Test fixiert
Namen, Beschreibung, Schema und Risiko der `mac_*`-Tools. Eine spätere Vereinheitlichung (Phase 6) ist optional und ändert den Mac-Prompt – nur mit Freigabe.

---

## 4. Phasen, Aufwand und Risiko für Mac-Regressionen

Aufwand in Personentagen (PT), eine mit dem Repo vertraute Person. **Grobe Schätzung aus dem Lesen des Codes, nicht gemessen.**
Größte Unsicherheiten: Wayland, Windows-UI-Automation, Pi-Leistung (STT/Embedding), Verfügbarkeit externer Binaries auf ARM/Windows.

| Phase | Inhalt | Aufwand | Mac-Regressionsrisiko | Voraussetzung |
|---|---|---|---|---|
| 0 | Absicherung: Golden-Snapshots auf dem Mac, Plattform-Erkennung (`JARVIS_PLATFORM`), CI-Matrix-Gerüst, Lockfile-/Wheel-Check je Plattform | 2–3 | sehr niedrig (nur Tests und neue Dateien) | – |
| 1 | **Core headless auf Linux/Pi:** Capability-Register und -Gate, Pfad-Overlay, Secrets-Fallback, Korrekturen an Leckstellen (`os.uname`, `SIGUSR1`, `TTSService`-Rückfall, `os.pathsep`, `sips`-Ersatz), systemd-Units, Betriebsdoku (PostgreSQL, pgvector, entferntes STT/TTS) | 8–12 | niedrig (additiv; `registry.is_enabled` und `TTSService.provider` sind die berührten Stellen) | 0 |
| 2 | **Linux-Desktop:** Bridge-Sidecar (X11 zuerst), `DesktopConnector`, Tauri-Linux-Bundle, Tray/Hotkey | 15–25 | niedrig (neue Dateien, Overlay-Konfig) | 1 |
| 3 | **Windows-Host:** Bridge (UIA, `SendInput`, Screenshot), `launcher.rs` ohne bash, Task Scheduler, ACL-Schutz für Token, `psutil`-Prozesssteuerung, NSIS/MSI, Signierung, Windows-Shell-Allowlist | 20–30 | niedrig–mittel (berührt `launcher.rs`, `native.ts`, gemeinsame UI-Texte) | 1, Bridge-Teile aus 2 |
| 4 | **Sprache auf Win/Linux/Pi:** `SounddeviceAudio`, `SystemTTS` (SAPI/espeak-ng/Piper), `open_audio` per Adapter, Wake-Word auf ARM prüfen | 10–15 | niedrig–mittel (Signatur von `open_audio`) | 1 |
| 5 | **Pi-Betrieb:** ARM-Wheels und Binaries verifizieren, Ressourcenlimits, Update-Weg, Backup-Schlüssel ohne Keyring | 5–8 | niedrig | 1, 4 (falls Sprache) |
| 6 | (optional) Namen/Persona/UI-Texte vereinheitlichen | 3–5 | **mittel–hoch** (ändert Prompt und Tool-Namen auf dem Mac) | 0–5, Freigabe |
| **Summe** | | **63–98** | | |
| **Minimalpfad** (Server auf Linux/Pi + Handy-App) | Phasen 0, 1, 5 | **15–23** | niedrig | |

Regeln für jede Phase (Gates gegen Mac-Regressionen):

1. Bestehende Tests werden **nicht geändert**; neue Tests liegen in neuen Dateien.
2. Vor dem ersten Eingriff in eine geteilte Stelle (`registry.py`, `speech.py`, `audio_io.py`, `secrets.py`, `config.py`, `lib.rs`, `launcher.rs`) steht der
   passende Golden-Test (5.2).
3. Vor jedem Merge: Mac-Rauchtest (Abschnitt 5.5).
4. Phasen 2–4 dürfen den Mac-Codepfad nicht „bei Gelegenheit“ umbauen.

---

## 5. Teststrategie und CI-Matrix

### 5.1 Ausgangslage

- `core`: `ruff`, `mypy`, `pytest tests/unit` laufen bereits auf **`ubuntu-latest`** (`.github/workflows/ci.yml:16-32`). Integration/E2E bleiben lokal (Marker).
- `desktop`: `tsc`, `eslint`, `vitest`, `pnpm audit` auf `ubuntu-latest` (`ci.yml:34-54`); kein Tauri-Build, kein `cargo test`.
- `bridge`: `swift test` auf `macos-latest` (`ci.yml:72-80`). `mobile`: Flutter auf `ubuntu-latest`.
- **Basis am 2026-10-23 (macOS, Python 3.13.9, unveränderter Worktree):** `uv run --directory core --frozen pytest -q tests/unit` → **599 bestanden, 0 Fehler**.

### 5.2 Mac muss unverändert grün bleiben

- **Harte Regel:** Der Satz der vorhandenen Tests läuft ohne Änderung durch (Gate G1). Ein CI-Schritt kann Änderungen an bestehenden Testdateien im PR als Warnung ausweisen.
- **Golden-/Snapshot-Tests (vor dem Umbau auf dem Mac erzeugt, danach wortgleich):**
  1. `ToolRegistry`-Dump (Name, Beschreibung, Parameter-Schema, Risiko, Connector, `enabled`) für alle Tools.
  2. System-Prompt (`persona.py`) auf dem `MacAdapter` byte-identisch.
  3. `load_settings()`-Ergebnis und die aufgelösten Pfade gleich den heutigen Literalen.
  4. `MacConnector`-Aufrufe an die Bridge (aufgezeichnete Requests per `httpx`-Mock) unverändert.
  5. `TTSService`-Providerreihenfolge auf dem Mac (`local` → `fish` → `say`).
- **Swift:** `swift test` unverändert (Gate G3). **Desktop:** `pnpm typecheck|lint|test` (G4). **Rust:** `cargo test` zusätzlich auf einem macOS-Runner (G5).

### 5.3 Neue Tests für die anderen Plattformen

- **Adapter-Vertragstests** (pytest, parametrisiert über `mac|win|linux|headless`) mit injizierten Spawn-Funktionen: prüfen die Kommandozeilen
  je Plattform, ohne das Betriebssystem zu brauchen (Vorlage: `wakeup.play_sound(spawn=…)`, `test_attachments.py:330-350`).
- **Capability-Tests:** jede nicht unterstützte Fähigkeit → Tool nicht in `registry.all()`, direkter Aufruf liefert `ToolUserError` mit Grund, `/api/system/capabilities`
  nennt den Grund, **keine Ausnahme**. Genau diese Tests hätten die Fälle `TTSService`/`say`, `os.uname`, `SIGUSR1` gefangen.
- **Bridge-Contract-Tests** (pytest + `httpx` gegen eine laufende Bridge): dieselbe Testdatei läuft gegen die Swift-Bridge (macOS-Runner) und gegen die Win/Linux-Bridge.
  Geprüft werden Routen, Fehlercodes (`permission_denied`, `kill_switch_engaged`, …), Token, Loopback, Not-Aus-Datei, Pfad-Policy.
- **Plattformfallen als Unit-Tests:** `os.pathsep`, Pfade mit Leerzeichen, `Path.home()`-Prüfung der Shell, Dateirechte-Prüfung nach Plattform, `find.exe`-Kollision.
- **Pi:** Smoke-Test „Core startet headless, `/api/health` ok, Sprachkanal `unavailable`, Handy-Gateway erreichbar“; Ressourcenmessung (RAM, Startzeit) als Bericht.

### 5.4 CI-Matrix (Entwurf)

```yaml
core-unit:
  strategy:
    fail-fast: false
    matrix:
      os: [ubuntu-latest, macos-latest, windows-latest]   # Windows anfangs nicht blockierend
  runs-on: ${{ matrix.os }}
  steps:
    - uses: actions/checkout@v4
    - uses: astral-sh/setup-uv@v5
    - run: uv sync --frozen
      working-directory: core
    - run: uv run pytest -q tests/unit
      working-directory: core
core-quality:            # ruff + mypy nur einmal, auf ubuntu-latest (wie heute)
core-integration:        # ubuntu-latest mit Postgres+pgvector-Servicecontainer, später (heute nur lokal)
core-arm:                # ubuntu-24.04-arm (Verfügbarkeit für dieses Repo prüfen) oder selbst gehosteter Pi-Runner
desktop:                 # vitest/tsc/eslint auf den drei Systemen (billig)
tauri-build:             # tauri build (Debug/Bundle) auf macos/windows/ubuntu – Linux mit apt-Bibliotheken
bridge-swift:            # unverändert: macos-latest, swift test
bridge-contract:         # macos: gegen Swift-Bridge; ubuntu/windows: gegen Python-Bridge
```

Kostenhinweis: macOS- und Windows-Minuten sind bei privaten Repos teurer als Linux (laut GitHub-Preismodell, vor Aktivierung prüfen).
Vorschlag: Windows- und Arm-Jobs zuerst als „nicht blockierend“, erst nach einigen grünen Läufen verpflichtend.

### 5.5 Mac-Rauchtest vor jedem Merge (manuell, kurz)

`scripts/start.sh` → `jarvisctl doctor` → ein `mac_*`-Tool (z. B. App-Liste und Screenshot) über die Bridge → „Hey Jarvis“ mit Antwort per Stimme →
Desktop-App: Tray, ⌘⇧J, „Im Finder zeigen“ → `scripts/stop.sh`.

---

## 6. Offene Fragen an Sir

Jede Frage mit Empfehlung; Antworten steuern Umfang und Reihenfolge.

1. **Windows: Desktop-App oder nur Server?** Soll auf Windows die Tauri-App laufen **und** der PC steuerbar sein (Phase 3 komplett), oder reicht Core + Handy/Web-Client?
   *Empfehlung:* zuerst nur Core (Phasen 0/1), die App danach – der Bridge-Aufwand ist der größte Block.
2. **Soll ein Windows-/Linux-Rechner gesteuert werden** (UI-Automation, Tippen, Screenshots) oder nur als Host für Chat, Gedächtnis, Aufgaben, Telefonie dienen?
   Davon hängt die Bridge (3.6, Optionen A–C) ab. *Empfehlung:* Option A, aber erst, wenn Bedarf besteht.
3. **Raspberry Pi: headless Server oder Sprachstation** (Mikrofon/Lautsprecher, „Hey Jarvis“ im Raum)? Welches Modell und wie viel RAM? Das Embedding-Modell (~1,1 GB, `setup.sh:33`)
   und `whisper-server` sind die Hauptlasten. *Empfehlung:* headless Server; STT/TTS bei Bedarf auf dem Mac lassen (`voice.stt.server_url`, `tts.local.server_url` sind schon URLs).
4. **Wo laufen PostgreSQL, Whisper und TTS bei einem Pi?** Lokal, oder verteilt (z. B. DB und STT auf dem Mac/VPS)? `database.host` und `server_url` erlauben das schon. *Empfehlung:* Datenbank
   lokal auf dem Pi nur, wenn pgvector für ARM bestätigt ist; sonst verteilt.
5. **Web-Client gewünscht?** Heute liest nur die Tauri-App das Token (`native.ts:13-23`), CSP und `core_client.rs` sind auf `127.0.0.1:8765` festgelegt (`tauri.conf.json:30-31`).
   Ein Browser-Zugang bräuchte eine eigene Anmeldung und eine Freigabe von Pfaden am Gateway. *Empfehlung:* zunächst die Android-App als Fernzugang nutzen (Gateway `:8767` existiert).
6. **Linux-Desktop: X11 oder Wayland, welche Distribution?** Wayland schränkt Eingabe, Screenshots und Fensterlisten stark ein (Abschnitte 2.15–2.17). *Empfehlung:* X11-Sitzung als Ziel, Wayland als „teils“.
7. **Windows-Version, Adminrechte, Signierung?** Code-Signing-Zertifikat (Kosten, SmartScreen) oder unsigniert für den Eigengebrauch? Linux-Paketformat (AppImage/deb)? *Empfehlung:* unsigniert für den Eigengebrauch beginnen.
8. **Tool-Namen:** `mac_*` behalten und `desktop_*` für andere Systeme ergänzen (Empfehlung), oder langfristig vereinheitlichen? Letzteres ändert den Mac-Prompt (Phase 6).
9. **Shell-Allowlist je Plattform** einführen (Windows ohne `find`, mit Quoting-Härtung)? *Empfehlung:* ja, mit Mac-Liste unverändert als Standard.
10. **Secrets ohne Keyring (headless):** verschlüsselte Datei, `systemd-creds` oder nur Env-Variablen? *Empfehlung:* `systemd-creds` bzw. Env zuerst (kein neuer Code), Datei später.
11. **Coding-Runner auf Windows/Linux?** Der Runner bleibt laut früherer Vorgabe unberührt. Er nutzt `killpg`, `SIGSTOP`/`SIGCONT` und `start_new_session`; unter Windows ist er so nicht lauffähig.
    Auf Linux/Pi sollte er laufen. *Empfehlung:* Coding-Jobs bleiben auf dem Mac; Windows bekommt die Capability `coding.runner = nicht verfügbar`. Eine Anpassung nur mit ausdrücklicher Freigabe.
12. **Mehrere Hosts gleichzeitig** (Mac **und** Windows-PC steuerbar)? Das bräuchte eine Geräteauswahl in den Tools (Host-Parameter, Geräte-Registry) – deutlich größerer Umbau.
    *Empfehlung:* ein aktiver Desktop-Host je Core; Mehrhost später.
13. **Persönliche Pfade** (`CUSTOMER_SITES_DIR`, `ATTACHMENT_ROOTS`, Persona-Pfade; Abschnitt 2.10) in die Konfiguration verlagern? Betrifft `config.py:35` (vom Runner importiert) und die Persona.
    *Empfehlung:* `ATTACHMENT_ROOTS` und Persona-Pfade in die Config, `CUSTOMER_SITES_DIR` nur mit Freigabe (Runner).
14. **Priorität:** erst Linux/Pi (Minimalpfad ≈ 15–23 PT) oder erst Windows? *Empfehlung:* Linux/Pi zuerst.
15. **CI-Budget:** Dürfen Windows-/macOS-/Arm-Runner dauerhaft laufen, oder nur nachts/manuell?
16. **Verfügbarkeit externer Programme prüfen** (vor Phase 1): Claude-Code-CLI, `livekit-server`, `cloudflared`, `whisper-server`, PostgreSQL+pgvector jeweils für Windows und Linux/ARM – aus dem Repo nicht ableitbar.

---

## Anhang: Hinweise zum Dokument

- Dieses Dokument ist das einzige Ergebnis des Auftrags; es wurden weder Produktivcode noch bestehende Tests verändert.
- `ARCHITECTURE.md:204` („Core ist plattformneutral …“) sollte bei der Umsetzung von Phase 1 korrigiert werden (nicht Teil dieses Audits).
- Zeilenangaben beziehen sich auf den Stand des Worktrees am 2026-10-23 und verschieben sich bei Änderungen.
