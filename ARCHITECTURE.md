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

Verteilt (Windows, Linux, weitere Rechner): drei logisch getrennte Komponenten – **Jarvis Core Server** (`core/`),
**Jarvis Client** (Desktop-App/Browser/Handy, auch gegen einen entfernten Core) und **Jarvis Device Agent**
(`device_agent/`, steuert einen Rechner im Auftrag des Core), verbunden über Tailscale. Details:
[docs/DISTRIBUTED.md](docs/DISTRIBUTED.md).

## Komponenten

| Komponente | Ort | Aufgabe |
|---|---|---|
| **Jarvis Core** | `core/src/jarvis` | Zentrale Orchestrierung, REST/WS-API, Policy-Engine, Tools, Gedächtnis, Aufgaben, Telefonie, Coding-Runner |
| **PlatformAdapter** | `core/src/jarvis/platform/` | Alles Betriebssystem-Spezifische des Core (Mac/Windows/Linux, headless) mit Capability-Flags + Grund; `MacAdapter` beschreibt die bisherigen Mac-Wege unverändert; Werkzeuge mit `requires` verschwinden, wo eine Fähigkeit fehlt |
| **Device-Hub** | `core/src/jarvis/devices/` | Kopplung, WebSocket und Remote-Command-Routing zu den Device Agents; `devices`-Connector mit den `device_*`-Werkzeugen |
| **Jarvis Device Agent** | `device_agent/` | Leichtgewichtiger Agent je Rechner (Windows Win32, Linux X11/Wayland, macOS über die Bridge): Programme, Fenster, Maus, Tastatur, Screenshots, Dateien, Terminal, Claude Code, Browser, Benachrichtigungen, Audio |
| **Installer** | `installer/`, `scripts/windows/`, `scripts/linux/` | Windows/Linux: Abhängigkeiten prüfen und melden (z. B. Obsidian), Modelle laden und prüfen (Piper, Whisper, Wake-Word), Ersteinrichtung von Client, Device Agent, Core |
| **Agent (Claude Code)** | `core/src/jarvis/agent/claude_agent.py` | Agent-Loop über die offizielle Claude Code CLI mit Sirs Subscription; einziges Werkzeug-Set = Jarvis-MCP-Server |
| **Persönlichkeiten** | `core/src/jarvis/agent/personas.py`, `config/personalities/` | Auswahl Jarvis / René Redo (`/api/persona`, Event `persona.changed`): Systemprompt, Begrüßungen, Stimme; Wechsel ohne Neustart, Werkzeuge/Gedächtnis/Rechte unverändert |
| **Modellrouter** | `core/src/jarvis/agent/model_router.py` | Sonnet 5.5 Standard, einfache Anfragen → Haiku 5.5, Opus 5.5 nur auf Befehl/manuell |
| **Hermes Agent** (Altoption, ungenutzt) | `~/.hermes` | früherer Modus `agent.provider: hermes` mit lokalem Ollama-Modell; wird nicht gestartet |
| **JarvisBridge** | `bridge/` (Swift) | Native macOS-Steuerung, eigene Token-/Kill-Switch-/Policy-Prüfung (Defense in Depth) |
| **Voice-Daemon** | `core/src/jarvis/voice/daemon.py` | Wake Word, Endpointing mit spekulativem STT, Streaming-Turns über `/api/voice/speak`, Barge-in, Gesprächsmodus, Push-to-Talk, Latenzmessung |
| **Sprachausgabe** | `core/src/jarvis/voice/pipeline.py`, `fish_stream.py` | Text-Chunker, Commitment-Guard, Hybrid-TTS (warmes HTTP + Fish-Live-WebSocket), Fallback macOS; Stimme je Antwort fest (Persona-Wechsel ab der nächsten Antwort) |
| **Audiohelfer** | `core/native/JarvisAudio` (Swift) | Mikrofon + lückenlose Streaming-Wiedergabe (AVAudioEngine), optional Echounterdrückung |
| **Live-Graph** | `core/src/jarvis/graph.py`, `GET /api/graph` | Wissensgraph aus echten Daten (Gedächtnis, Agenten, Werkzeuge, Skills, Aufgaben, Jobs, Gespräche) |
| **Mobile-Gateway** | `core/src/jarvis/mobile/gateway.py` | Zweiter TLS-Listener `:8767` im Core-Prozess, nur Mobile-Pfade (Allowlist), Zertifikats-Pinning per QR |
| **Kopplung/Tokens** | `mobile/pairing.py`, `mobile/tokens.py` | Keystore-gebundene Geräte, 10-min-Zugriffstoken, rotierende Refresh-Tokens, signierte Requests |
| **Benachrichtigungs- & Anruf-Engine** | `mobile/alerts.py` | Kategorie-Regeln, Ruhezeit, Dedupe, Limits, Snooze, Terminerinnerungen; entscheidet nichts/still/Push/Anruf |
| **Push** | `mobile/push.py` | FCM HTTP v1, Ende-zu-Ende verschlüsselte Daten-Nachrichten |
| **Anrufe** | `mobile/calls.py`, `mobile/voice_agent.py`, `mobile/livekit_server.py`, `mobile/rtc_proxy.py` | LiveKit-Server (selbst gehostet, vom Core überwacht), Jarvis als Raumteilnehmer mit Whisper → Orchestrator → Fish-Streaming, Rückruf-Vormerkungen |
| **Android-App** | `apps/jarvis_mobile` (Flutter + Kotlin) | UI (Riverpod, GoRouter, Fragment-Shader), Core-Telecom, CallStyle, natives LiveKit, FCM, Keystore, Android-Auto-CarAppService – siehe docs/MOBILE.md |
| **Desktop-App** | `desktop/` (Tauri 2, React, TS strict) | 3D-Gedächtnis als Hauptansicht (`src/holo`, React Three Fiber), Chat-Dock, Systempanel mit allen Ansichten, Menüleisten-Icon, ⌘⇧J |
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
Beendet Jarvis selbst einen Prozess (Stopp/Barge-in, Leerlauf, Reload, Shutdown), wird er zuerst aus dem Pool genommen
und mit Grund markiert: kein Turn bekommt einen sterbenden Prozess, ein Stopp endet als „Abgebrochen“ statt als Fehler.
Stirbt die CLI von selbst, nennt Jarvis Exit-Code und stderr-Auszug und wiederholt den Turn einmal (per `--resume`, frisch,
falls schon das Fortsetzen scheiterte; liefen bereits Werkzeuge, wird der zweite Versuch angewiesen, nichts doppelt zu tun).
Ein Barge-in merkt sich einen Abbruch nur für einen Turn dieser Sitzung, der gerade läuft oder auf seinen Platz wartet.
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

**Events.** Jedes Bus-Event trägt `seq, ts, type, source, session_id, task_id, nodes` (Graph-Knoten-IDs wie
`memory:<uuid>`, `tool:<name>`, `connector:<id>`, `agent:jarvis|coding|scheduler|voice|telephony`, `task:<uuid>`,
`coding:<uuid>`, `session:<uuid>`). Sitzung/Aufgabe/Werkzeug fließen per `contextvars` (`events.activity`) in
verschachtelte Aufrufe. Die Desktop-App faltet die Events in eine Live-Schicht (`desktop/src/holo/graph/live.ts`) –
sichtbar wird nur, was tatsächlich passiert ist.

**Sprachturn.** Mikrofon → Wake Word → Endpointer (spekulatives Whisper ab 280 ms Pause, Vorabruf des Gedächtnis-
Embeddings, TTS-Vorwärmung) → `/api/voice/speak` `turn` → Orchestrator (Recall parallel zu Speicherung/Modellwahl) →
Claude-Deltas → TextChunker → Commitment-Guard → Hybrid-TTS → getaggte PCM-Frames → Audiohelfer. Abbruch (`cancel`)
stoppt Wiedergabe, Synthese und nur den Agent-Lauf dieser Sitzung.

**Unterbrechen (Barge-in) und Stopp.** Gilt in allen Sprachkanälen; Sir kann Jarvis jederzeit ins Wort fallen, solange er
spricht *oder* noch an der Antwort arbeitet:
- *Erkennung:* anhaltende Sprache (`voice.barge_in_ms`, 320 ms) deutlich über dem Eigenecho (Mac: `BargeInDetector`, ohne
  Echounterdrückung relativ zu den Pegelspitzen der eigenen Stimme; Anruf: Echounterdrückung des Handys; Telefonleitung:
  `BARGE_IN_RMS` über dieselbe Mindestdauer; Handy-Overlay: AEC-Quelle + Eigenecho-Pegel). Ein Klick oder Husten genügt nicht.
  „Hey Jarvis“ unterbricht am Mac immer. Handy-Overlay: `BargeInDetector` (Kotlin, rein rechnerisch, JVM-getestet) – Eigenecho
  wird in den ersten 300 ms gemessen, Sir muss ≥ 2,2× darüber liegen (3,2× ohne AEC des Geräts) und 400 ms durchhalten;
  die letzten 800 ms laufen als Pre-Roll-Puffer mit.
- *Einstellung:* Handy › Einstellungen › „Jarvis ins Wort fallen“ (Standard an, gilt für Overlay und Anruf; die App meldet sie
  beim Start/Annehmen eines Anrufs als `barge_in` an `POST /api/mobile/calls[/{id}/answer]`). Aus: nur der Stopp-Button
  unterbricht. Der Stopp-Button bleibt in beiden Fällen als Rückfall.
- *Wirkung:* Wiedergabepuffer verwerfen (Mac `flush`, Anruf `clear_queue`, Twilio `clear`), Synthese abbrechen
  (`SpeechTurn.cancel`), nur den Agent-Lauf **dieser** Sitzung abbrechen (`Orchestrator.interrupt(session_id)`); die Wörter des
  Unterbrechers (Puffer von ~0,6 s) sind der Anfang der nächsten Äußerung und werden ganz normal verarbeitet.
  Mac ohne Echounterdrückung, Barge-in per Stimme während einer Agent-Antwort: das Mikrofon hört auch Jarvis selbst (z. B.
  „Einen Moment, Sir.“ beim Werkzeugstart). Die Wiedergabe stoppt sofort, Synthese und Agent-Lauf aber erst, wenn die
  Transkription zeigt, dass Sir gesprochen hat; war es Jarvis' Echo (oder nichts), läuft die Antwort weiter und wird vorgelesen.
- *Echo:* Erkennt die Transkription Jarvis' eigene Worte wieder (`voice.commands.is_echo`: Mac kurz nach/während der
  Wiedergabe, Anruf, Overlay), wird sie nie beantwortet.
- *Stopp-Befehl:* `voice.commands.is_stop_command` – „Stopp“, „Jarvis, stopp“, „Hör auf“, „Sei still“, „Das reicht“ … nur als
  ganze Äußerung („stopp den Timer in zehn Minuten“ ist eine normale Anfrage). Jarvis schweigt, der Befehl geht **nicht** an den
  Agenten, das Gespräch bleibt offen (anders als „Gespräch beenden“/„Tschüss“). `POST /api/voice/stt` liefert dafür
  `command: "stop"`, damit das Handy-Overlay dieselbe Definition nutzt.
- *Buttons:* Desktop (Voice-Ansicht „Stopp“; Chat „Unterbrechen“ auch während des Vorlesens – `POST /api/chat/interrupt`
  schickt zusätzlich `voice.command interrupt` an den Daemon), Handy-Anruf („STOPP“ → `POST /api/mobile/calls/{id}/interrupt`),
  Handy-Overlay („STOPP“; Tippen auf den Puls unterbricht und hört zu).

**Gedächtnis.** Notizen im Vault (eine Erinnerung = eine Markdown-Datei, Ordner je Art). Index in `memory_items`
(Embedding 1024-dim, HNSW cosine; tsvector für Volltext; Konfidenz, Aliase, Nutzung) und `memory_links` (Wissensgraph aus
`[[Links]]`, Entitäten und typisierten Beziehungen). Suche hybrid (Volltext + Vektor, RRF, Graph-Erweiterung, Re-Ranking).
Schreiben über Entitätsauflösung + satzweise Dedupe; die Extraktion sieht Verlauf und vorhandene Notizen und wählt
create/update/supersede/skip; episodische Notizen fassen abgeschlossene Gespräche zusammen. Duplikate: gleicher Titel+Art
oder Kosinus ≥ 0,93 → Ergänzung statt neuer Notiz. Korrektur → alte Notiz bleibt als `superseded` erhalten. Sensible Inhalte
(IBAN, Passwörter, Gesundheit …) landen erst nach Bestätigung auf der Platte. Ein Dateiwatcher übernimmt Änderungen aus Obsidian.

**Coding.** `CodingRunner` → eigener Git-Worktree je Job → `claude -p --restricted --strict-mcp-config --permission-mode acceptEdits
--permission-prompts none --allowedTools … --settings {sandbox}` → Stream-JSON-Log → Testbefehl → bei Fehlschlag automatische
Fehleranalyse (zweiter Lauf mit Testausgabe, `--resume`) → Abschluss (`CodingRunner._wrap_up`, nach jedem Job-Ende, auch
nach Ausnahme oder Abbruch): offene Änderungen committen (WIP-Commit, wenn nicht grün), `result.commit` = Spitze des
Job-Branches inkl. Commits des Agenten → Auslieferung (`CodingRunner._ship_flow`, `coding.auto_ship`): bei grünem Ergebnis
Branch pushen → PR (kein Draft) → PR mergen (405 „noch nicht mergebar“ wird ~10 min wiederholt) → Supabase deployen
(`db push`, `functions deploy`, falls `supabase/` im Projekt; sonst übersprungen und vermerkt; Datenbankpasswort für
`db push` per `SUPABASE_DB_PASSWORD` aus dem Schlüsselbund: Projektpasswort `supabase-db-password-<project-ref>`, sonst
Standardpasswort `supabase-db-password`; lehnt die Datenbank es ab, wird nichts weiter probiert – `result.ship.state =
"waiting_input"`, Benachrichtigung an Sir, Antwort über `coding_supabase_password`, das Passwort pro Projekt speichert und
den Deploy fortsetzt) → Vercel deployen
(`CodingRunner._ship_vercel` → `VercelConnector.deploy_after_merge`, nur bei `vercel.json` bzw. `.vercel/project.json` und
aktivem Connector; ist das Vercel-Projekt schon mit dem Repo verbunden, übernimmt die Git-Integration; ein Fehler steht in
`result.ship.vercel` und macht den Merge nicht rückgängig) → Job-Branch auf origin und
lokal sowie Worktree löschen (nur wenn nichts Ungemergtes darin ist). Rot, Fehler oder Abbruch: Job-Branch wird gepusht
(Sicherung), aber nichts auf `main`, kein Merge, kein Deploy, der Worktree bleibt für „Wiederholen“. Lief der Job direkt
auf `main` (Kundenseite ohne eigenen Worktree): grün → `main` ohne PR nach origin pushen (nur Fast-Forward), rot → gar
nichts pushen; Commits werden gegen `origin/main` gezählt. Ist origin leer (frisch angelegtes GitHub-Repo), wird `main`
zuerst initial gepusht (ohne lokalen `main` wird der Job-Branch zu `main`), vermerkt in `result.ship.empty_repo`.
Ergebnis in `result.ship`, Probleme in `result.delivery_error`. Erneuter Versuch: `coding_ship_job`; Draft-PR nur noch auf Wunsch über
`coding_prepare_pr`. Parallelität: `CodingRunner._slot` vergibt bis zu `coding.max_parallel_jobs` Plätze (Semaphore), Jobs
für dieselbe Kundenseite (ohne eigenen Worktree) zusätzlich nacheinander; eine Sperre je Repository (`_repo_lock`)
serialisiert Worktree anlegen/entfernen und die ganze Auslieferung, sodass parallele Jobs nacheinander nach `main` gehen.

**Agent-Monitor.** Das Coding-Log (`coding-logs/<job>.jsonl`) hält je Werkzeugaufruf Argument-Kurzfassung, Ergebnis-Kurzfassung und
Token-Nutzung (`coding/monitor.py`); `GET /api/coding/jobs/{id}/monitor` macht daraus den Schnappschuss (aktueller Schritt, Schritte,
Laufzeit ohne Pausen, Tokens, Kosten – Kosten meldet die CLI erst am Ende eines Laufs). Live per `coding.progress`-Events, Polling als
Reserve. Steuerung: `pause`/`resume` (SIGSTOP/SIGCONT auf die Prozessgruppe, das Zeitlimit wird um die Pause verlängert), `cancel`,
`message` (Folge-Turn derselben Claude-Session nach dem aktuellen Lauf, mit `immediate` sofort durch Beenden des Laufs). Aufgaben:
`/api/tasks/{id}/pause|resume|message` (weiches Pausieren zwischen Schritten; Nachrichten gehen an den laufenden Coding-Job der Aufgabe).
Desktop (`AgentsView`) und Handy (Aktivität → Agenten) nutzen dieselben Endpunkte; Pausieren und Abbrechen fragen im UI vorher nach.

**Chat-Zeitleiste.** Jeder Turn wird von `agent/timeline.py` (`TurnRecorder`) als geordnete Liste aufgezeichnet:
`message` (Nutzer, Jarvis-Textsegmente, Systemmeldungen), `tool` (je Aufruf: `tool_use_id`, Status, Start/Ende in ms, Eingabe,
Ergebnis, optional `display` wie eine Route), `process` (Gedankenprozess: Quelle `model` = echtes Reasoning des Modells, nur
wenn die API es liefert; `protocol` = protokollierte Aktion wie Modellwahl, Gedächtnisabruf, Werkzeugwahl, Plan, Freigabe;
`summary` = nachträglich aus den Protokolldaten gebildet) und `turn`. Jedes Element hat ID, `ts` (ms), eine fortlaufende `seq`
je Gespräch und die `turn_id`. Ein Werkzeugaufruf schließt das laufende Textsegment; so stehen Text und Tools in
Ereignisreihenfolge. Persistenz in `chat_events` (Upsert, ein Schreiber je Turn), live als `chat.event`; Text-Streaming als
`chat.delta`/`chat.thinking` mit der Element-ID. `GET /api/sessions/{id}/events` liefert die Zeitleiste seitenweise nach Turns
(ältere Nachrichten ohne Ereignisse werden davor einsortiert), `POST …/events/{eid}/check` speichert Checklisten-Haken in der
Nachricht. Desktop (`desktop/src/lib/timeline.ts`) und Handy (`apps/jarvis_mobile/lib/state/timeline.dart`) falten dieselben
Ereignisse nach denselben Regeln.

**Routendienst.** Connector `routing` (`connectors/routing.py`, Provider in `routing/providers.py`): Geocoding über Nominatim
(User-Agent, max. 1 Anfrage/s, Cache), Routing über OSRM (Standard: FOSSGIS-Server für Auto/Fahrrad/Fuß) oder Valhalla,
austauschbar über die Schnittstellen `Geocoder`/`RoutingProvider`. Das Modell bekommt nur Kennzahlen; die Geometrie geht als
Anzeige-Artefakt (`tools/artifacts.py`) an das Tool-Element und wird im Chat als Karte gezeigt (Desktop: Leaflet + OSM-Kacheln,
Handy: OSM-Kacheln mit gezeichneter Route).

**Slash-Befehle.** `GET /api/commands` (`jarvis/commands.py`) ist die einzige Quelle der Befehlsliste für Desktop und Handy; der Server
beschreibt (`action`: prompt, model, new, stop, navigate, killswitch), die Apps führen aus.

**Telefonie.** Twilio Programmable Voice + bidirektionale Media Streams (µ-law 8 kHz über WebSocket). Webhooks mit
`X-Twilio-Signature`-Prüfung, Einmal-Stream-Token je Anruf, PIN-Pflicht (DTMF oder gesprochen), Barge-in (`clear`),
Transkript in `calls`. SIP-Endpunkte werden über eine Twilio SIP Domain / Elastic SIP Trunk auf denselben Webhook geleitet.
Der lokale Testmodus (`jarvisctl phone-test`) spricht exakt dasselbe Protokoll – ausdrücklich kein echtes Telefonat.

**Smartphone-Anruf.** Jarvis → Sir: Ereignis/Auftrag → `AlertEngine.decide` → `CallService.ring_owner` (Prüfung:
Agent, LiveKit, Whisper, Push, Limits, ein Anruf je Ereignis) → Raum + Sprachagent tritt bei und synthetisiert die
Begrüßung vor → verschlüsselter FCM-Push `call` → Telefon: Core-Telecom `addCall` (Klingeln, Sperrbildschirm, Bluetooth,
Auto) → Annehmen → `/answer` liefert das Raum-Token → natives LiveKit über `wss://gateway/rtc` → Gespräch. Kein
Annehmen in 45 s → `missed` + Benachrichtigung. Sir → Jarvis: `/api/mobile/calls` nur wenn `availability` es erlaubt.

## Erweiterbarkeit

- **Weitere LLM-Provider:** `agent.provider` (Claude Code oder Hermes); Adapter in `agent/claude_agent.py` bzw. `agent/llm.py`.
- **Weitere Connectoren:** Klasse mit `Manifest` + `tools()` + `health()` in `connectors/`, in `app_state.py` registrieren.
- **Externe MCP-Server:** `config/mcp_servers.yaml` (z. B. offizieller GitHub-MCP-Server) – gleiche Policy-Pipeline.
- **Weitere Telefonie-/TTS-Anbieter:** `TelephonyProvider` bzw. `TTSProvider`-Interface.
- **Mehrere Geräte/Benutzer:** Geräte mit Rollen (`devices`), Tokens nur als SHA-256 gespeichert; Sitzungen pro Akteur.
- **Andere Betriebssysteme / VPS:** Alles OS-Spezifische des Core liegt hinter `PlatformAdapter` (Mac, Windows, Linux,
  headless); `config/platform/<os>.yaml` liefert Pfade und Vorgaben, `GET /api/system/capabilities` zeigt, was fehlt und warum.
  Auf einem VPS: Core + Postgres, TLS-Reverse-Proxy (z. B. Caddy) vor `:8765`, `server.public_base_url` setzen, Secrets per
  `JARVIS_SECRET_<NAME>`-Umgebungsvariablen (systemd credentials) statt Schlüsselbund. Rechner, die Jarvis steuern soll,
  verbinden sich als Device Agents (`device_agent/`), Clients über `clients.remote_api` – siehe docs/DISTRIBUTED.md.
- **Android-App:** `apps/jarvis_mobile`, Rolle `mobile_owner`, dieselbe REST/WS-API über das Mobile-Gateway.
- **iPhone-App (später):** gleiche Gateway-/Token-Mechanik; CallKit statt Telecom, APNs-VoIP statt FCM.

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
