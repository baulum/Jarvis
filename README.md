# J.A.R.V.I.S.

Persönlicher KI-Assistent für macOS, der lokal auf dem eigenen Mac läuft. Jarvis spricht und hört zu („Hey Jarvis“), bedient
den Mac, recherchiert im Web, führt mehrstufige Geschäftsabläufe aus (Lexware, Google, Telegram, GitHub, Vercel), merkt sich Wissen
in einem Obsidian-Vault, telefoniert (Twilio, Anrufe über die eigene Android-App) und gibt Programmieraufträge an Claude Code
weiter. Jede Aktion läuft durch ein Freigabesystem mit Audit-Log und Not-Aus.

Als Modell dient die Claude Code CLI mit der Claude-Subscription: Sonnet 5.5 als Standard, Haiku 5.5 für einfache Anfragen,
Opus 5.5 nur auf Wunsch. Lokale LLMs werden nicht verwendet.

## Funktionen

- **Sprache:** Wake Word, Endpointing mit spekulativem STT (whisper.cpp, large-v3-turbo), Streaming-TTS (lokal per
  Qwen3-TTS, Fish Audio oder macOS `say`), Gesprächsmodus, Unterbrechen (Barge-in) und Stopp-Befehle, Weckroutine
  („Hey Jarvis, wach auf“)
- **Mac-Steuerung:** Apps, Fenster, Bedienungshilfen-Baum, Tastatur/Maus, Bildschirmfotos, Zwischenablage, Dateien,
  Kurzbefehle über die native JarvisBridge (AppleScript standardmäßig deaktiviert)
- **Recherche:** Browser über Playwright (Microsoft Edge mit eigenem Profil oder verstecktes Chromium)
- **Connectoren:** System, Gedächtnis (Obsidian), Mac, Browser, Shell (Allowlist), Coding, Lexware Office, Google (Gmail,
  Kalender, Drive), Telegram, GitHub, Vercel (Deploys), HTTP-API, Webhooks, Fish Audio, KIE.ai (Bild- und
  Videogenerierung), Telefonie, Routen (Nominatim + OSRM/Valhalla, Kartenanzeige im Chat)
- **Veröffentlichen auf Vercel:** `vercel_deploy` bringt ein lokales Projekt oder ein GitHub-Repo live (Projekt anlegen,
  mit GitHub verbinden, Umgebungsvariablen, Deploy, URL zurück; Risikostufe `high`, also mit Freigabe),
  `vercel_list_projects` und `vercel_deployment_status` lesen Projekte und Build-Status – siehe [Vercel](#vercel)
- **Externe MCP-Server:** über `config/mcp_servers.yaml`, mit derselben Freigabe-Pipeline wie eigene Werkzeuge
- **Gedächtnis:** eine Erinnerung = eine Markdown-Datei im Obsidian-Vault; Index in PostgreSQL mit pgvector
  (Qwen3-Embedding-0.6B als ONNX im Core), hybride Suche (Volltext + Vektor + Wissensgraph), automatische
  Wissensextraktion nach Gesprächen, Dedupe, Korrekturen als `superseded`, sensible Inhalte erst nach Bestätigung
- **Mehrstufige Aufgaben:** deterministische Playbooks (`new_customer_project`, `tool_sequence`) mit persistierten
  Schritten, Idempotenzschlüsseln und Wiederaufnahme nach Neustart; Scheduler für zeitgesteuerte Aufträge
- **Coding-Jobs:** Claude Code in einem eigenen Git-Worktree je Job, Testlauf mit automatischer Fehleranalyse,
  Agent-Monitor (Schritte, Tokens, Kosten; Pausieren, Fortsetzen, Abbrechen, Nachrichten) und automatische Auslieferung
- **Freigabesystem:** Rollen mit Risikoobergrenzen, Freigabe-Modi, Taint nach externen Inhalten, Rate-Limits,
  Audit-Log mit Hash-Kette, Not-Aus
- **Telefonie:** Twilio Programmable Voice mit Media Streams, Signaturprüfung und PIN-Pflicht; lokaler Testmodus
- **Android-App:** Chat, Aktivität, Freigaben, Push-Benachrichtigungen, Anrufe mit Jarvis über LiveKit, Assistent-Overlay
  mit Wake Word, Android Auto
- **Backups:** nächtlicher Dump von Datenbank, Vault und Konfiguration, AES-256-GCM-verschlüsselt, mit Aufbewahrungsregeln

## Architektur

| Komponente | Ort | Technik |
|---|---|---|
| Core | `core/` | Python 3.13, FastAPI (`127.0.0.1:8765`), SQLAlchemy/asyncpg, Alembic; Orchestrator, REST/WebSocket-API, MCP-Server (`/mcp`), ToolExecutor, Gedächtnis, Task-Engine, Scheduler, Telefonie, Coding-Runner |
| Agent | `core/src/jarvis/agent/` | Claude Code CLI, ein Prozess je Gespräch; einziges Werkzeug-Set ist der Jarvis-MCP-Server; Modellrouter |
| Voice-Daemon | `core/src/jarvis/voice/` | Wake Word (openWakeWord), VAD, whisper.cpp (`:8178`), TTS; Audiohelfer in Swift (`core/native/JarvisAudio`) |
| JarvisBridge | `bridge/` | Swift 6, ohne Fremdabhängigkeiten, `127.0.0.1:8766`; eigene Token-, Not-Aus-, Policy- und Rate-Limit-Prüfung |
| Desktop-App | `desktop/` | Tauri 2, React 19, TypeScript, Three.js/React Three Fiber (3D-Gedächtnis als Hauptansicht), Zustand, Tailwind; Menüleisten-Icon, ⌘⇧J |
| Mobile-Gateway | `core/src/jarvis/mobile/` | zweiter TLS-Listener `:8767` nur für Mobile-Pfade, Zertifikats-Pinning per QR-Code, LiveKit-Server, FCM-Push |
| Android-App | `apps/jarvis_mobile/` | Flutter + Kotlin (Telecom, LiveKit, FCM, Keystore, CarAppService) |
| Datenbank | Homebrew | PostgreSQL 17 + pgvector |
| Gedächtnis | `~/Documents/Jarvis Vault` | Obsidian-Vault als Quelle der Wahrheit, Dateiwatcher übernimmt Änderungen |

Jede Eingabe (Desktop, Stimme, Telegram, Telefon, App, Webhook, Scheduler) läuft durch den Orchestrator: Sitzung →
Modellwahl → Gedächtnisabruf → Claude-Code-Turn → Werkzeugaufrufe über `POST /mcp` → `ToolExecutor`.

**Werkzeugaufruf und Freigaben.** Der `ToolExecutor` prüft der Reihe nach: Werkzeug aktiv, JSON-Schema der Argumente,
Not-Aus, Rollen-Obergrenze und Rate-Limits, Freigabepflicht (Risiko über `auto_approve` der Rolle oder Sitzung durch
externe Inhalte „tainted“), Idempotenz-Ledger, dann Ausführung mit Timeout und Audit-Eintrag. Risikostufen:
`read` < `low` < `medium` < `high` < `critical`. Freigaben erteilt der Besitzer am Desktop, per Telegram-Button, in der App
(signiert, biometrisch bestätigt) oder am Telefon nach PIN; kritische Aktionen nur am gekoppelten Desktop. Per Stimme
gibt es keine Freigaben. Freigabe-Modi in der Desktop-App: YOLO, „Gefährliches blockieren“ (Standard), „Alles blockieren“,
„Eigene Regeln“.

**Coding-Jobs.** Der `CodingRunner` legt je Job einen Git-Worktree auf einem `jarvis/*`-Branch an und startet Claude Code mit
`--restricted`, `--strict-mcp-config` und einer Werkzeug-Allowlist (`coding.allowed_tools`). Danach laufen die Tests; schlagen
sie fehl, folgt ein zweiter Lauf mit der Testausgabe. Am Ende werden offene Änderungen committet. Bei grünem Ergebnis und
`coding.auto_ship: true` wird ausgeliefert: Standardmäßig (`coding.ship_via_pr: false`) wird der Job-Branch auf
`origin/main` rebased und direkt nach `main` gepusht; bei Konflikt oder geschütztem Branch geht es über einen PR, der
anschließend gemergt wird. Danach Supabase-Deploy (falls `supabase/` im Projekt liegt), Vercel-Deploy in Produktion (falls
das Projekt eine `vercel.json` oder einen Vercel-Link `.vercel/project.json` hat und der Vercel-Connector aktiv ist; ist das
Vercel-Projekt bereits mit dem Repo verbunden, baut Vercel selbst über die Git-Integration) und Aufräumen von Branch und Worktree.
Bei rotem Ergebnis, Fehler oder Abbruch wird nur der Job-Branch gesichert, nichts gemergt. Bis zu `coding.max_parallel_jobs`
(Standard 3) Jobs laufen gleichzeitig, jeder in seinem Worktree; weitere warten als „queued“. Das Ausliefern läuft pro
Repository nacheinander (der nächste Job rebased auf das frisch gemergte `main`), Jobs für dieselbe Kundenseite ebenso.

**Coding-Jobs an Jarvis selbst.** Ist das Projekt eines Jobs das Repository, aus dem der laufende Core stammt, und hat der
Job Dateien geändert, die Core oder Desktop-App laden (`core/src`, Abhängigkeiten und Migrationen in `core/`, `config/`,
`desktop/`; nicht Doku, Tests, Android-App, Device Agent), startet Jarvis sich nach der Auslieferung selbst neu
(`core/src/jarvis/coding/self_restart.py`, abschaltbar mit `coding.self_restart: false`). Voraussetzung: Der Merge nach
`main` ist erfolgt und das lokale Projekt wurde auf den neuen Stand gebracht; sonst wird nur der Grund vermerkt. Nicht
committete Änderungen im lokalen Projekt verhindern das nur, wenn sie Dateien betreffen, die die Auslieferung ändert;
andere bleiben beim Fast-Forward unangetastet erhalten. Jobs an
anderen Projekten (z. B. Lead-Tool) lösen nie einen Neustart aus. Reihenfolge:

1. Auslieferung abschließen, Job-Ergebnis schreiben und „fertig“ melden.
2. Warten, bis kein anderer Coding-Job mehr läuft oder wartet (im Ergebnis steht dann `waiting` mit den Job-IDs) – laufende
   Jobs werden nie abgebrochen.
3. Ein eigenständiger Helfer-Prozess (überlebt das Ende des Cores) übernimmt: Läuft die Desktop-App, wird sie zuerst per SIGTERM
   beendet (kein AppleScript-Beenden: das löst ggf. einen Automation-Dialog aus und stoppt je nach Einstellung „Dienste
   beim Beenden stoppen“ alle Dienste; SIGKILL nur, wenn sie nach 10 s noch läuft). Dann wird der Core neu
   gestartet – als launchd-Dienst `com.jarvis.core` per `launchctl kickstart -k`, sonst Stopp über die PID-Datei und
   `scripts/start.sh --no-app` (startet auch den Voice-Daemon, falls keiner läuft) – und per `/api/health` geprüft.
   Zuletzt wird die Desktop-App wieder geöffnet, aber nur, wenn sie vorher lief.
4. Der neue Core trägt das Ergebnis in den Job ein: `result.ship.self_restart` (`state`: `done`, `failed` oder `skipped`,
   dazu Core und Desktop-App im Einzelnen). Fehler stehen zusätzlich unter `result.warnings` und kommen als Benachrichtigung
   `coding.self_restart`; die Auslieferung selbst bleibt davon unberührt. Protokoll: `~/Library/Logs/Jarvis/self-restart.log`.

Der Neustart der Desktop-App lädt nur das installierte Bundle neu; geänderte Oberflächen-Funktionen erscheinen erst nach
`scripts/install-app.sh` (die App weist selbst darauf hin, wenn ihr Build älter ist als der Code).

Details: [ARCHITECTURE.md](ARCHITECTURE.md), Sicherheitsmodell: [SECURITY.md](SECURITY.md).

## Installation

**Windows und Linux** (Client, Device Agent und/oder Core): `scripts\windows\install.ps1` bzw. `scripts/linux/install.sh` –
prüft Abhängigkeiten, lädt Modelle, führt durch die Einrichtung. Details: [docs/DISTRIBUTED.md](docs/DISTRIBUTED.md).

**macOS** – Voraussetzungen: macOS auf Apple Silicon, Homebrew, Xcode Command Line Tools, Claude Code CLI (angemeldet über
`claude` → `/login`), ca. 6 GB freier Speicher.

```bash
scripts/setup.sh   # einmalig, idempotent
```

Das Skript installiert die Homebrew-Pakete (u. a. postgresql@17, pgvector, whisper-cpp, ffmpeg, uv, node, pnpm, rustup,
cloudflared, livekit), legt die Datenbank an, richtet die Python-Umgebung ein, lädt die Modelle, koppelt Desktop und Voice
als Geräte, baut JarvisBridge und die Desktop-App, installiert beide nach `/Applications` und schließt mit `jarvisctl doctor` ab.

Danach J.A.R.V.I.S. aus „Programme“ öffnen; die App startet whisper-server, JarvisBridge, Core und Voice-Daemon selbst.
Nach Code-Änderungen aktualisiert `scripts/install-app.sh` die installierte App.

Einmalig nötig sind die macOS-Berechtigungen (Bedienungshilfen und Bildschirmaufnahme für JarvisBridge, Mikrofon für
J.A.R.V.I.S.). Connectoren werden in der Desktop-App unter **Connectoren** oder per `jarvisctl secret set <name>` eingerichtet.

| Dokument | Inhalt |
|---|---|
| [SETUP.md](SETUP.md) | Installation, Start, Berechtigungen, Connectoren, Stimme, Modelle, Skills, Tests, Fehlerbehebung |
| [docs/ANLEITUNG-ZUGANGSDATEN.md](docs/ANLEITUNG-ZUGANGSDATEN.md) | Zugangsdaten für alle Connectoren Schritt für Schritt |
| [docs/ANLEITUNG-TWILIO.md](docs/ANLEITUNG-TWILIO.md) | Telefonie mit Twilio |
| [docs/MOBILE.md](docs/MOBILE.md) | Android-App: Installation, Kopplung, Firebase, Anrufe, Android Auto |
| [PROGRESS.md](PROGRESS.md) | Entwicklungsstand, Testnachweise, offene Punkte |

## Entwicklung und Tests

Dienste ohne installierte App starten und stoppen:

```bash
scripts/start.sh [--no-voice] [--no-app]
scripts/status.sh
scripts/stop.sh
```

`jarvisctl` (in `core/`, Aufruf mit `uv run jarvisctl …`) bietet u. a. `serve`, `migrate`, `voice`, `secret`, `pair`,
`mobile-pair`, `mobile-status`, `backup`, `status`, `doctor`, `killswitch`, `chat` und `phone-test`.

Tests und Prüfungen:

```bash
# Core
cd core
uv run ruff check src tests
uv run mypy src
uv run pytest tests/unit                        # ohne Infrastruktur
uv run pytest -m integration tests/integration  # Test-DB, Claude-CLI, Embedding-Modell
uv run pytest -m e2e tests/e2e                  # laufender Stack

# Desktop
cd desktop
pnpm typecheck && pnpm lint && pnpm test && pnpm build

# Android-App
cd apps/jarvis_mobile
flutter analyze && flutter test

# Bridge
cd bridge
swift test
```

Die CI (`.github/workflows/ci.yml`) führt Ruff, Mypy und die Unit-Tests des Core, Typprüfung, ESLint und Vitest der
Desktop-App, `flutter analyze`/`flutter test`, `swift test` sowie einen Secret-Scan mit gitleaks aus. Integrations- und
E2E-Tests laufen nur lokal.

## Konfiguration

| Datei | Inhalt |
|---|---|
| `config/jarvis.yaml` | Server, Pfade, Datenbank, Agent und Modelle, Stimme, Telefonie, Benachrichtigungen, Mobile, Backup, Coding, KIE.ai-Modelle, Browser, Scheduler |
| `config/permissions.yaml` | Rollen, Risikostufen, Werkzeug-Overrides, Shell-Allowlist, Rate-Limits |
| `config/personality.yaml` | Anrede, Charakterzüge, Humor, Begrüßungen |
| `config/mcp_servers.yaml` | externe MCP-Server |

- Jeder Wert in `config/jarvis.yaml` lässt sich per Umgebungsvariable `JARVIS__SECTION__KEY` überschreiben
  (z. B. `JARVIS__SERVER__PORT=9000`); Beispiele in [.env.example](.env.example).
- Zugangsdaten liegen nie in Dateien, sondern im macOS-Schlüsselbund (Dienst `jarvis`); auf Servern alternativ als
  `JARVIS_SECRET_<NAME>`-Umgebungsvariablen.
- Laufzeitdaten: `~/Library/Application Support/Jarvis` (Tokens, Not-Aus-Flag `KILL`, Modelle, Freigabe-Modus),
  Logs unter `~/Library/Logs/Jarvis/`, Coding-Workspaces unter `~/Developer/Jarvis-Workspaces`.

### Vercel

**Wo der Token hingehört:** Token unter <https://vercel.com/account/settings/tokens> erstellen (Scope: eigener Account bzw.
das Team) und in Jarvis eintragen – entweder in der Desktop-App unter **Connectoren › Vercel › Einrichten** (Feld
„Vercel Access Token“) oder im Terminal:

```bash
cd core
uv run jarvisctl secret set vercel-token      # fragt den Token verdeckt ab → macOS-Schlüsselbund (Dienst "jarvis")
uv run jarvisctl secret set vercel-team-id    # nur für Team-Projekte (team_…)
```

Ohne Schlüsselbund (Server) gehen auch `JARVIS_SECRET_VERCEL_TOKEN` / `JARVIS_SECRET_VERCEL_TEAM_ID` oder `VERCEL_TOKEN` /
`VERCEL_TEAM_ID` in der Umgebung des Core. Danach den Connector **Aktiv** schalten und **Testen**. Der Token erscheint nie in
Logs, Fehlermeldungen oder Ergebnissen und wird nicht an Coding-Jobs weitergegeben.

| Werkzeug | Risiko | Zweck |
|---|---|---|
| `vercel_deploy(project_path, production=true, project_name?, env?, env_targets?, source?, ref?)` | high | Projekt bei Bedarf anlegen, mit dem GitHub-Repo verbinden, Umgebungsvariablen setzen, deployen, URL zurückgeben |
| `vercel_list_projects(search?, limit?)` | read | Projekte mit verbundenem Repo, Produktions-URL und letztem Deploy |
| `vercel_deployment_status(deployment? \| project?)` | read | Zustand, URL und Fehlermeldung eines Deploys bzw. die letzten Deploys eines Projekts |

- `project_path` ist ein absoluter Pfad (unter `paths.projects_dir`, den Coding-Workspaces oder den in den
  Connector-Einstellungen freigegebenen `allowed_roots`) oder ein GitHub-Repo `owner/name`.
- Quelle (`source: auto`): Ist der lokale Stand sauber und gepusht, baut Vercel direkt aus GitHub; sonst lädt Jarvis die
  Dateien hoch (`.gitignore` wird beachtet, `.env*`, `.git`, `node_modules` und `.vercel` nie).
- Umgebungsvariablen: `env: {"NAME": "wert"}`; geheime Werte besser als `"secret:<schlüsselbund-name>"`. Freigabe-Text und
  Audit-Log zeigen nur die Namen.
- Ein abgeleiteter Projektname greift nie auf ein Vercel-Projekt zu, das mit einem anderen Repo verbunden ist.
