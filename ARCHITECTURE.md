# J.A.R.V.I.S. – Architektur

```
                    ┌────────────────────── Mac (lokal) ─────────────────────────────┐
  Desktop-App ──────┤  Tauri/React  ── REST + WebSocket ──┐                          │
  (Menüleiste)      │                                     ▼                          │
  Voice-Daemon ─────┤  Mikrofon/Lautsprecher ──────► Jarvis Core (FastAPI :8765)     │
  (Hey Jarvis)      │  openWakeWord · VAD               │  Orchestrator · Sessions   │
                    │                                   │  ToolExecutor (Policy,     │
  Telegram ─────────┤  Bot-API Long-Polling ───────────►│   Freigaben, Idempotenz)   │
  Telefon (Twilio) ─┤  Webhook + Media-Stream (WS) ────►│  Task-Engine · Scheduler   │
                    │                                   │  Memory (Vault+pgvector)   │
                    │                                   │  Connectors · MCP-Server   │
                    │      ┌────────── /mcp ◄───────────┤  Audit (Hash-Kette)        │
                    │      ▼                            └──┬───────┬────────┬────────┘
                    │  Claude Code CLI (Subscription),   │       │        │
                    │  1 Prozess je Gespräch, Modell-    │       │        │
                    │  router Haiku/Sonnet/Opus 5.5      │       │        │
                    │                                    ▼       ▼        ▼
                    │       JarvisBridge (Swift :8766)  PostgreSQL  whisper.cpp  Claude Code CLI
                    │       AX · ScreenCaptureKit ·     +pgvector   (:8178)      (Worktrees,
                    │       CGEvent · NSWorkspace                                 Sandbox)
                    └───────────────────────────────────────────────────────────────┘
```

## Komponenten

| Komponente | Ort | Aufgabe |
|---|---|---|
| **Jarvis Core** | `core/src/jarvis` | Zentrale Orchestrierung, REST/WS-API, Policy-Engine, Tools, Gedächtnis, Aufgaben, Telefonie, Coding-Runner |
| **Agent (Claude Code)** | `core/src/jarvis/agent/claude_agent.py` | Agent-Loop über die offizielle Claude Code CLI mit Sirs Subscription; einziges Werkzeug-Set = Jarvis-MCP-Server |
| **Modellrouter** | `core/src/jarvis/agent/model_router.py` | Sonnet 5.5 Standard, einfache Anfragen → Haiku 5.5, Opus 5.5 nur auf Befehl/manuell |
| **Hermes Agent** (Altoption, ungenutzt) | `~/.hermes` | früherer Modus `agent.provider: hermes` mit lokalem Ollama-Modell; wird nicht gestartet |
| **JarvisBridge** | `bridge/` (Swift) | Native macOS-Steuerung, eigene Token-/Kill-Switch-/Policy-Prüfung (Defense in Depth) |
| **Voice-Daemon** | `core/src/jarvis/voice/daemon.py` | Wake Word, VAD, STT-Upload, Satz-für-Satz-TTS, Barge-in, Push-to-Talk |
| **Desktop-App** | `desktop/` (Tauri 2, React, TS strict) | Kommandozentrale, Menüleisten-Icon, globaler Kurzbefehl ⌘⇧J |
| **Embedding-Modell** | im Core (`memory/embeddings.py`) | Qwen3-Embedding-0.6B als ONNX (fastembed), 1024 Dim., für die semantische Gedächtnissuche – kein Ollama |
| **Cloudflare Quick Tunnel** | Kindprozess des Core | öffentliche HTTPS-Adresse nur für die Twilio-Webhooks; `api/exposure.py` lässt darüber nur `/api/telephony/*` durch |
| **whisper.cpp** | Homebrew | `whisper-server` mit large-v3-turbo (q5_0), Deutsch |
| **PostgreSQL 17 + pgvector** | Homebrew | Sessions, Aufgaben, Freigaben, Ledger, Audit, Memory-Index, Anrufe, Jobs |
| **Obsidian-Vault** | `~/Documents/Jarvis Vault` | Quelle der Wahrheit fürs Gedächtnis (Markdown + YAML-Frontmatter) |

## Kernabläufe

**Gespräch.** Jede Eingabe (Desktop, Stimme, Telegram, Telefon, Webhook, Scheduler) geht durch den `Orchestrator`:
Sitzung (mit Idle-Timeout) → Modellwahl (Befehl/Router) → Gedächtnisabruf (pgvector) → Turn-Kontext (Zeit, Kanal,
Taint-Warnung, Erinnerungen) → Claude-Code-Prozess der Sitzung (`--input-format stream-json`, Modellwechsel per
Control-Request ohne Neustart) → Events auf den Bus → Transkript speichern → asynchrone Wissensextraktion (Haiku).
Ein vorgewärmter Bereitschaftsprozess macht auch den ersten Turn schnell (gemessen: ~4 s inkl. Tool-Aufruf; Folge-Turns
erstes Token ~1 s). Untätige Prozesse werden nach 15 min beendet und per `--resume` fortgesetzt.
Agent-Läufe sind serialisiert, damit der MCP-Server jeden Tool-Aufruf dem richtigen Akteur und der richtigen Sitzung zuordnet.
Ist Claude nicht verfügbar (z. B. Kontingent erschöpft), antwortet Jarvis deterministisch und ehrlich – ohne LLM und ohne
Erledigt-Behauptungen.

**Modellwahl.** Standard Sonnet 5.5. Kurze, einfache Anfragen ohne Geschäfts-, Planungs-, Code- oder Recherchebezug
→ Haiku 5.5. Opus 5.5 nur auf Befehl („nimm Opus“, „ab jetzt Opus“, „nur diesmal Opus …“), per App-Umschalter, API
(`POST /api/agent/model`) oder Agent-Tool `agent_set_model`. „Automatische Modellwahl“ stellt zurück.

**Tool-Aufruf.** Claude Code → `POST /mcp` (Bearer-Token der Rolle `agent`) → `ToolExecutor.execute`:
1. Tool existiert und ist aktiv (Connector eingerichtet, kein Override `enabled: false`)
2. JSON-Schema-Validierung der Argumente
3. Not-Aus (alles außer `read` gesperrt)
4. Rollen-Obergrenze, Rate-Limits
5. Freigabepflicht: Risiko > `auto_approve` der Rolle **oder** Sitzung ist durch externe Inhalte „tainted“
6. Idempotenz-Ledger für schreibende Tools (gleicher Schlüssel → Ergebnis aus dem Ledger statt erneuter Ausführung)
7. Ausführung mit Timeout, Audit-Eintrag, Taint bei `untrusted_output`

**Freigaben.** Eine Freigabe speichert Tool + Argumente + Idempotenzschlüssel. Nach „Freigeben“ (Desktop, Telegram-Button)
führt der Executor die Aktion genau einmal aus und meldet das Ergebnis (`approval.decided`). Kritische Aktionen nur am
gepaarten Desktop-Gerät. Wartende Task-Schritte werden dadurch automatisch fortgesetzt.

**Mehrstufige Aufgaben.** Playbooks (`core/src/jarvis/jobs/playbooks.py`) sind deterministische Schrittfolgen. Das kleine
Modell muss nur die Parameter extrahieren (ein Tool-Call `task_start_playbook`). Die Engine:
- persistiert Aufgabe und Schritte, Worker claimen mit Lease (`FOR UPDATE SKIP LOCKED`) → Wiederaufnahme nach Absturz/Neustart
- erzeugt deterministische Idempotenzschlüssel pro Schritt und Aufrufposition → keine doppelten Lexware-Kontakte/Rechnungen
- pausiert für Freigaben (`waiting_approval`), fehlende Angaben (`waiting_input`) und externe Ereignisse (`waiting_external`, z. B. Coding-Job fertig)
- überspringt nicht eingerichtete Connectoren nachvollziehbar (Schritt `skipped` + Hinweis)

Beispiel `new_customer_project` („Müller Gebäudereinigung, Website, 499 €“): Kunde prüfen → Pflichtangaben → Kunde anlegen (Freigabe)
→ Rechnungsentwurf (Freigabe) → Projektordner + Git → BRIEF.md → Claude-Code-Job (Freigabe) → Tests abwarten →
Gedächtnis aktualisieren → Benachrichtigung (Desktop/Telegram).

**Gedächtnis.** Notizen im Vault (eine Erinnerung = eine Markdown-Datei, Ordner je Art). Index in `memory_items`
(Embedding 1024-dim, HNSW cosine) und `memory_links` (Wissensgraph aus `[[Links]]` und Entitäten). Duplikate: gleicher Titel+Art
oder Kosinus ≥ 0,93 → Ergänzung statt neuer Notiz. Korrektur → alte Notiz bleibt als `superseded` erhalten. Sensible Inhalte
(IBAN, Passwörter, Gesundheit …) landen erst nach Bestätigung auf der Platte. Ein Dateiwatcher übernimmt Änderungen aus Obsidian.

**Coding.** `CodingRunner` → eigener Git-Worktree je Job → `claude -p --restricted --strict-mcp-config --permission-mode acceptEdits
--permission-prompts none --allowedTools … --settings {sandbox}` → Stream-JSON-Log → Testbefehl → bei Fehlschlag automatische
Fehleranalyse (zweiter Lauf mit Testausgabe, `--resume`) → Commit auf `jarvis/*`-Branch → optional Draft-PR.

**Telefonie.** Twilio Programmable Voice + bidirektionale Media Streams (µ-law 8 kHz über WebSocket). Webhooks mit
`X-Twilio-Signature`-Prüfung, Einmal-Stream-Token je Anruf, PIN-Pflicht (DTMF oder gesprochen), Barge-in (`clear`),
Transkript in `calls`. SIP-Endpunkte werden über eine Twilio SIP Domain / Elastic SIP Trunk auf denselben Webhook geleitet.
Der lokale Testmodus (`jarvisctl phone-test`) spricht exakt dasselbe Protokoll – ausdrücklich kein echtes Telefonat.

## Erweiterbarkeit

- **Weitere LLM-Provider:** `agent.provider` (Claude Code oder Hermes); Adapter in `agent/claude_agent.py` bzw. `agent/llm.py`.
- **Weitere Connectoren:** Klasse mit `Manifest` + `tools()` + `health()` in `connectors/`, in `app_state.py` registrieren.
- **Externe MCP-Server:** `config/mcp_servers.yaml` (z. B. offizieller GitHub-MCP-Server) – gleiche Policy-Pipeline.
- **Weitere Telefonie-/TTS-Anbieter:** `TelephonyProvider` bzw. `TTSProvider`-Interface.
- **Mehrere Geräte/Benutzer:** Geräte mit Rollen (`devices`), Tokens nur als SHA-256 gespeichert; Sitzungen pro Akteur.
- **VPS:** Core ist plattformneutral (macOS-spezifisch sind nur Bridge, Voice-Daemon, `say`). Auf einem VPS: Core + Postgres +
  Hermes + Ollama/anderer Provider, TLS-Reverse-Proxy (z. B. Caddy) vor `:8765`, `server.public_base_url` setzen,
  Secrets per `JARVIS_SECRET_<NAME>`-Umgebungsvariablen (systemd credentials) statt Schlüsselbund. Bridge/Voice bleiben am Mac
  und verbinden sich als Geräte.
- **iPhone-App:** nutzt dieselbe REST/WS-API mit eigenem Gerätetoken (Rolle `operator`).

## Datenhaltung

| Daten | Ort |
|---|---|
| Konfiguration | `config/*.yaml` (validiert, `JARVIS__SECTION__KEY`-Overrides) |
| Zugangsdaten | macOS-Schlüsselbund, Dienst `jarvis` |
| Gerätetokens (Desktop/Voice/Bridge) | `~/Library/Application Support/Jarvis/*.token` (Ordner 0700, Dateien 0600) |
| Not-Aus-Flag | `~/Library/Application Support/Jarvis/KILL` (gemeinsam mit der Bridge) |
| Modelle | `~/Library/Application Support/Jarvis/models` |
| Logs | `~/Library/Logs/Jarvis/` (JSON-Lines, rotiert) |
| Gedächtnis | Obsidian-Vault + Postgres-Index |
| Coding-Workspaces | `~/Developer/Jarvis-Workspaces`, Kundenprojekte `~/Developer/Projects` |
