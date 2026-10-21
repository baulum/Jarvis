# J.A.R.V.I.S. – Entwicklungsstand

> Wiedereinstiegspunkt für neue Sessions. Nach jeder größeren Änderung aktualisieren.
> Doku: ARCHITECTURE.md (Aufbau), SETUP.md (Installation/Betrieb), SECURITY.md (Sicherheitsmodell).

## Stand: 2026-10-21 – Chat-Zeitleiste, Gedankenprozess, Routendienst

- **Routendienst** (Connector `routing`, Werkzeuge `route_plan`, `route_geocode`): Auto/Fahrrad/Fußweg, Entfernung, Fahrzeit,
  bis zu zwei Alternativen; OpenStreetMap ohne Schlüssel; Fehler (Zeitüberschreitung, keine Route, nicht erreichbar) klar benannt
- **Chat-Zeitleiste** (`chat_events`, Migration `f2a3b4c5d6e7`): Text, Tool-Aufrufe und Prozessschritte in Ereignisreihenfolge,
  persistent, nach Neuladen identisch; parallele Tools mit eigener ID und Start/Ende
- **Desktop**: Tool-Zeilen (aufklappbar, gemeinsame Zeitachse), Gedankenprozess je Antwort mit Quellenangabe, klickbare
  Checklisten, Routenkarte (Leaflet), gedrosseltes Streaming, Auto-Scroll nur am Ende, Laden älterer Turns
- **Handy**: dieselbe Zeitleiste und Darstellung, Karte ohne neues Paket (OSM-Kacheln + gezeichnete Route)

Offen: Migration auf die Test-DB anwenden (`jarvis_test`; die Produktions-DB migriert beim Core-Start selbst), danach
`pytest -m integration` (Postgres-Variante von `test_chat_timeline.py`); Flutter-Code mit `flutter analyze` und `flutter test`
prüfen (in der Entstehungssitzung nicht ausführbar).

## Stand: 2026-10-21 – Zuverlässigkeit (Jobs, Benachrichtigungen, Anruf-Anrede)

- **Hänger behoben:** abgebrochene Coding-Jobs melden `coding.finished` (wartende Playbook-Schritte und Rückruf-
  Vormerkungen hingen sonst für immer); Task-Schritte, die nach einem Absturz auf einen noch „laufenden“ Ledger-Eintrag
  treffen, werden alle 30 s neu versucht (vorher: ewig `waiting_external`); Pause hält das Job-Zeitbudget an; Timeouts
  eskalieren SIGTERM → SIGKILL; nach Neustart werden auch pausierte Jobs als unterbrochen markiert und Sir bekommt je
  Job eine Benachrichtigung (`coding.interrupted`); Core-Stopp beendet laufende Agent-Prozesse
- **Benachrichtigungen:** wiederholte Jobs/Aufgaben wurden 24 h als Duplikat verschluckt (Schlüssel je Benachrichtigung);
  `task.input_required` geht jetzt auch ans Handy; fehlgeschlagene Ausführung nach Freigabe meldet `task.failed`;
  Desktop-Toasts und -Liste öffnen Job/Aufgabe/Freigabe direkt; Android öffnet alte Links nicht erneut (Drehen/Recents)
- **Anruf:** „Hallo Sir, der Build ist fertig.“ verlor die Nachricht (Regex bis Satzende); „Sir, …“ nach der Begrüßung
  wird nicht doppelt gesagt; eine erneute Begrüßung am Anfang der ersten Antwort wird beim Streamen entfernt
- Tests: Core 299 Unit (+18), Task-Engine-Integration 5 (+2); Desktop 54 (+3); ruff/mypy/tsc/eslint 0 Fehler.
  Nicht ausgeführt: Flutter/Kotlin (kein SDK in der Coding-Umgebung). `test_graph_sync` braucht `alembic upgrade head`
  auf `jarvis_test` (Spalte `coding_jobs.model` fehlt dort)

## Stand: 2026-10-17 – J.A.R.V.I.S. Mobile (Android)

Neue Android-App `apps/jarvis_mobile` (Flutter 3.41 + Kotlin) und Mobile-Schicht im Core (`core/src/jarvis/mobile/`).
Anleitung, Android-Auto-Analyse und vollständige Testmatrix: **docs/MOBILE.md**.

### Core
- **Mobile-Gateway** `:8767` (TLS, selbstsigniert, Fingerabdruck per QR gepinnt; nur Mobile-Pfade, Rest 404) als
  zweiter Listener im Core; Core-API bleibt auf 127.0.0.1
- **Kopplung/Tokens:** `jarvisctl mobile-pair` (QR im Terminal), Keystore-gebundene Geräte (Rolle `mobile_owner`),
  10-min-Zugriffstoken, rotierende Refresh-Tokens mit Familienwiderruf, signierte Refresh-/Freigabe-Requests
- **Benachrichtigungs- & Anruf-Engine** (`alerts.py`): Kategorien aus/still/Push/Anruf, Ruhezeit, Dedupe, Limits,
  ein Anruf je Ereignis, ehrlicher Fallback auf Push, verpasst → Benachrichtigung, Snooze, Terminerinnerungen
  (Google-Kalender), „wieder online“; Notifier-Kanal `mobile`
- **Push:** FCM HTTP v1 mit Ende-zu-Ende-verschlüsselten Daten-Nachrichten (AES-GCM je Gerät)
- **Anrufe über LiveKit** (selbst gehostet, vom Core überwacht): Jarvis tritt dem Raum bei, Whisper → Orchestrator
  (gleicher Agent, gleiches Gedächtnis) → Fish-Audio-Streaming (gleiche Stimme), Barge-in, echte Werkzeug-/
  Gedächtnis-Ereignisse über den Datenkanal, Transkripte; vorab synthetisierte Begrüßung
- **Agent-Werkzeuge:** `mobile_notify`, `mobile_call_owner`, `mobile_call_when` („ruf mich an, sobald …“),
  `mobile_get_location` (letzter Standort des Handys, nur mit Freigabe in der App – docs/MOBILE.md › Standortfreigabe)
- Whisper-Wiederholungsschleifen („…?\n …?\n …?“) werden jetzt für alle Sprachkanäle zusammengefasst

| Messung (E2E, simuliertes Telefon über LiveKit) | Wert |
|---|---|
| Gesprächsbeginn → Begrüßung hörbar | 4,2 s → **1,3–1,6 s** (Vorsynthese während des Verbindens) |
| Frageende → erster Antwortton | **3,6 s** (eine Messung 8,9 s; Claude-Latenz schwankt) |

### App
- Eigenes Design (tiefschwarz, Eisblau/Cyan, Saira + JetBrains Mono, Haarlinien statt Karten, keine Tab-Leiste):
  Energiekern als Fragment-Shader (Impeller) – reagiert auf echten Pegel und abgeleiteten Zustand (Bereit, Hört zu,
  Denkt nach, Gedächtnis, Werkzeuge, Spricht, Im Gespräch, Offline), adaptive Qualität, Reduced Motion
- Startbildschirm mit schwebender Eingabe (Text, Diktat, Anrufknopf), Gespräch (Streaming, geräteübergreifender
  Verlauf), Aktivität (Freigaben mit Biometrie + Signatur, Aufgaben, Benachrichtigungen, Anrufe, Rückrufe),
  mobiles Gedächtnis (2D-Konstellation, Live-Hervorhebung), Anrufbildschirm, Einstellungen mit ehrlichem Status
- Nativ: Keystore, gepinntes OkHttp, Core-Telecom-Anrufe (CallStyle, Sperrbildschirm, Remote-Surfaces, Halten),
  LiveKit-Android mit `NoAudioHandler`, FCM-Dienst mit Entschlüsselung/Replay-Schutz, Benachrichtigungs-Aktionen,
  Android-Auto-`CarAppService` (Kategorie CALLING)

### Auf dem Emulator geprüft (Android 16, kein physisches Telefon angeschlossen)
Kopplung über das echte TLS-Gateway · Jarvis anrufen (Begrüßung + Transkript) · eingehender Anruf bei offener App,
auf dem Sperrbildschirm und bei beendeter App (Test-Hook statt FCM) · Annehmen aus der CallStyle-Benachrichtigung ·
Ablehnen · Verpasst + Folgebenachrichtigung · Auflegen während der Begrüßung · Snooze/Antworten aus der
Benachrichtigung · Chat · Aktivität · Gedächtnis · Einstellungen.
Dabei gefunden und behoben: Refresh-Familienwiderruf wurde zurückgerollt; LiveKit-Token im Header wurde vom Proxy
nicht weitergereicht; Telecom akzeptiert nur 4 DisconnectCause-Codes; `addCall` endet nicht von selbst (Folgeanruf
wurde ignoriert); Anrufbildschirm als Startroute ließ sich nicht schließen; Begrüßung blockierte das Auflegen.

### Tests (2026-10-17)
Core: 108 Unit + 46 Integration + E2E (Mobile-Anruf mit Sprache, bestehende Sprach-E2E); Flutter 10; Kotlin 5;
mypy strict, ruff, flutter analyze: 0 Befunde.

### Offen / benötigt von Sir
- **Firebase-Projekt** (kostenlos) für Benachrichtigungen und eingehende Anrufe – Schritte in docs/MOBILE.md §3
- **Physisches Android-Telefon** per USB für den Gerätetest (Mikrofon, Bluetooth, Netzwechsel, Biometrie)
- **Tailscale** (Konto nötig) für Erreichbarkeit unterwegs; ohne: nur im Heimnetz
- **Android Auto**: Beta bei Google, Play nur interne/geschlossene Tests; ungetestet (kein Fahrzeug/DHU-Setup)
- Release-Signierschlüssel (APKs derzeit mit Debug-Schlüssel signiert)
- Test-Nebeneffekt: eine Antwort aus der Benachrichtigung hat einen echten Kalendereintrag „Erinnerung“ heute 16:00 angelegt

## Stand: 2026-10-16 (Abend) – J.A.R.V.I.S. 2.0

### Neue Oberfläche: das 3D-Gedächtnis ist die Hauptansicht
- `desktop/src/holo/`: Three.js/React Three Fiber, ein instanzierter Draw für alle Knoten (Glyphen-Atlas mit 14
  Wissensarten, LOD: fern Lichtpunkt, nah Linien-Icon), ein Draw für alle Kanten (Signalfluss entlang aktiver Pfade),
  J.A.R.V.I.S.-Kern reagiert auf Mikrofon-/Ausgabepegel und Zustand, dezenter Staub als Tiefenhinweis
- Daten ausschließlich aus `/api/graph` (Agenten, Connectoren, 75 Werkzeuge, Skills, Erinnerungen, Entitäten, Aufgaben,
  Coding-Jobs, Zeitpläne, Gespräche) mit echten Relationen; Layout (d3-force-3d) im Web Worker, Cluster je Wissensart,
  stabile Positionen, neue Knoten wachsen aus ihrem Nachbarn, sanfte Reorganisation
- Kamera (CameraControls): drehen, zoomen, verschieben; Klick fliegt zum Knoten, Detail-Overlay rechts (Inhalt, Kategorie,
  Erstellt/Aktualisiert, Quelle, Aliase, Konfidenz, verknüpfte Personen/Projekte, Bearbeiten, Löschen mit Bestätigung);
  Cluster-Fokus über Filter, Gesamtansicht (Doppelklick ins Leere / Esc), Idle-Drehung, sanfte Ausrichtung auf abgerufene
  Erinnerungen; Suche `/` bzw. ⌘K (Labels + Hybrid-Gedächtnissuche + alle bisherigen Ansichten)
- Live-Schicht aus echten Events (Werkzeugaufrufe, Gedächtnisabrufe, Coding-Jobs, Aufgaben, Freigaben) – keine erfundenen
  Denkprozesse; ein abgeleiteter Zustand: Bereit · Hört zu · Denkt nach · Durchsucht das Gedächtnis · Arbeitet mit
  Werkzeugen · Programmiert · Spricht · Wartet auf Freigabe · Fehler
- Schwebendes Chat-Dock (Text, Push-to-Talk, Wellenform aus echten Pegeln), Freigaben und Not-Aus immer sichtbar, alle
  bisherigen Ansichten (Aufgaben, Connectoren, Skills, Logs, Einstellungen …) im Systempanel (Raster-Symbol oder Suche)
- Leistung (gemessen in Chrome auf dem M1, 3191 Knoten / 4209 Kanten synthetisch im Test-Tab): Hauptthread 120 Hz,
  gerendert wird on demand mit max. 60 fps, 30 fps während Whisper transkribiert, 0 bei verdecktem Fenster;
  adaptive Auflösung und Partikelzahl (PerformanceMonitor)

### Einheitlicher Event-Stream
- Jedes Event: `id, seq, ts, type, source, session_id, task_id, nodes, data` (`core/src/jarvis/events.py`);
  Sitzung/Aufgabe/Werkzeug werden per `contextvars` in verschachtelte Aufrufe getragen (ein Gedächtnisabruf innerhalb
  eines Werkzeugaufrufs einer Aufgabe kennt Aufgabe, Sitzung und Werkzeug); neues Event `memory.recalled`

### Gedächtnis v2 (`core/src/jarvis/memory/`)
- Analyse – warum Wissen verloren ging bzw. verwässerte: (1) gleiche Entität unter anderem Titel („Kunde Müller …“ vs.
  „Müller …“) → zweite Notiz; (2) Paraphrasen als „Ergänzung“ angehängt; (3) Extraktion sah nur den einzelnen Satz, keine
  Vorgeschichte und keine vorhandenen Notizen; (4) Extraktions-Warteschlange nur im RAM – Neustart/Claude-Ausfall = verloren;
  (5) Recall nur Vektor-Top-4 mit Schwelle, keine Volltextsuche; (6) keine Gesprächs-Episoden
- Jetzt: Entitätsauflösung (kanonische Namen, Aliase, Kind-Gruppen), satzweise Dedupe, LLM-Extraktion (Haiku über die
  Subscription) mit Verlauf + ähnlichsten vorhandenen Notizen → create / update (konsolidierte Fassung) / supersede
  (Widerspruch, alte Notiz bleibt „veraltet“) / skip, Konfidenz, typisierte Beziehungen; persistente Extraktion mit
  Wiederholung; episodisches Gedächtnis (Zusammenfassung je Gespräch nach 20 min Ruhe); neue Arten Unternehmen, Aufgaben/
  Verpflichtungen, Termine, E-Mails, Dokumente; Schutz gegen Informationsverlust bei Umformulierungen (Zahlen/Namen)
- Hybrid-Suche: Volltext (tsvector) + pgvector parallel, RRF, Graph-Erweiterung, Re-Ranking (Ähnlichkeit, Schlagwort,
  Aktualität, Nutzung, Konfidenz), komprimierter Kontextblock; Sprache: 250-ms-Budget + Vorabruf aus dem spekulativen
  Transkript → Recall 54–66 ms (vorher ~290 ms)
- Vault bereinigt: 3 Duplikat-Gruppen zusammengeführt (alte Notizen als „superseded“ erhalten), Backup:
  `~/Library/Application Support/Jarvis/backups/vault-20261016-1225.tgz`

### Sprache in Echtzeit
Gemessene Ausgangslage (2026-10-16, Haiku, Fish Audio): Endpointing fix 1,6 s · Whisper 1,05–1,4 s · Recall 0,24–0,43 s ·
Claude erstes Token ~3,2 s (neue Sitzung, inkl. unnötigem Modellwechsel) · TTS: Daemon wartete auf den ganzen Satz +
afplay je Satz → geschätzt ~7 s bis zum ersten Ton.
- Endpointing mit spekulativem STT: Whisper läuft ab 280 ms Pause; klingt der Satz fertig, endet der Turn nach 650 ms,
  sonst nach bis zu 1,6 s (z. B. „… und“, „ähm“)
- whisper-server `-ac 768 -nf`: 1,05 → 0,55 s (gleiche Transkripte); Äußerungen > 14 s werden an leisen Stellen geteilt
- Ein WebSocket `/api/voice/speak`: Turn, Audio-Streaming (Tag je Antwort → veraltete Chunks werden verworfen), Abbruch,
  Pegel, Metriken. Text wird schon während des Schreibens gesprochen (erster Teilsatz früh)
- Fish Audio: gemessen HTTP auf warmer Verbindung ~260 ms TTFB vs. WebSocket ~440 ms nach Text (+~550 ms Aufbau) →
  Hybrid: erstes Stück per warmem HTTP, Rest über den vorab geöffneten Live-Socket (durchgehende Prosodie); gleiche
  Stimme (reference_id, Modell s1); Fallback HTTP → macOS-Stimme
- Swift-Audiohelfer `jarvis-audio` (`core/native/JarvisAudio`): lückenlose Streaming-Wiedergabe, sofortiges Stoppen
- Ehrlichkeit: Sätze wie „Ich kümmere mich darum“ werden erst gesprochen, wenn ein Werkzeug den Auftrag angenommen hat
  (sonst verworfen); startet ein Turn mit Werkzeugen, sagt Jarvis sofort „Einen Moment, Sir.“ (keine Behauptung)
- Gesprächsmodus: nach einer Antwort 30 s weiter zuhören ohne „Hey Jarvis“ (`voice.conversation_window_s`)
- Claude-Prozesse starten mit dem Standardmodell des Routers (kein Modellwechsel beim ersten Turn)

| Messung (Sprachende → …) | vorher | jetzt (warm) |
|---|---|---|
| Turn-Ende erkannt + Transkript fertig | 1,6 s + 1,05–1,4 s | 0,95–1,05 s (Transkript wiederverwendet) |
| Gedächtnisabruf | 0,24–0,43 s | 0,05–0,07 s |
| Claude erstes Token | ~3,2 s | 1,1–2,1 s (Rest nicht beeinflussbar) |
| TTS erster Ton nach Text | Satz komplett + afplay | 0,26–0,46 s |
| **Erster hörbarer Ton** | **~7 s (geschätzt)** | **2,9–3,5 s** (E2E-Test), 3,3–4,8 s in Sirs echten Gesprächen |

Werkzeug-Aufgaben (Kalender, Recherche) dauern weiter so lange wie die Werkzeuge; Jarvis bestätigt jetzt sofort.
Messwerte jedes Turns: `~/Library/Logs/Jarvis/voice-metrics.jsonl`, `GET /api/voice/metrics`, `scripts/bench/voice_latency.py`.

### Recherche im echten Edge
- Websuche nutzt Sirs Microsoft Edge (sichtbares Fenster, eigenes Jarvis-Profil `…/Jarvis/edge-profile`, Cookies bleiben)
  statt eines frischen Headless-Browsers; Bing, DuckDuckGo und Google laufen parallel, der erste brauchbare Treffer zählt
  (gemessen 0,7–2,3 s statt sequenzieller Timeouts; live: „Bennis Playland“ mit Adresse/Öffnungszeiten gefunden).
  Bewusst keine Fingerprint-Tarnung/„undetectable“-Plugins.

### Bekannte Grenzen / offen
- Echounterdrückung (macOS Voice Processing) ist **aus**: im Test dämpfte sie auch Sirs Stimme im Raum (Pegel 30 vs.
  Stille 20) und das Wake-Word-Modell erkannte auf dem bereinigten Signal nichts. Folge: Unterbrechen während Jarvis
  spricht nur per „Hey Jarvis“. Barge-in per Stimme ist implementiert und getestet (E2E), aktiv mit
  `voice.echo_cancellation: true` – braucht noch einen Test mit echter Stimme bzw. Headset
- Claude Code (Subscription) bestimmt mit 1–2 s die Restlatenz; Ollama/Hermes werden nicht mehr genutzt (Altoption)
- Fish-Audio-Modell `s1` ist laut Fish ab 31.12.2026 abgekündigt (wird dann von `s2.1-pro` bedient) – Stimme prüfen
- Graph-API liefert die 1500 neuesten Erinnerungen; weitere je Art per „weitere laden“ (Filter unten links)
- Echte Anrufe (Twilio) weiterhin ungetestet (Konto fehlt)

### Tests (2026-10-16)
Core: 80 Unit + 38 Integration (Gedächtnis-Schichten, Graph-Synchronisierung, Event-Envelope, Werkzeug-Events, echte
Haiku-Extraktion) + 3 E2E-Sprache (voller Turn mit Latenzmessung, Barge-in per Stimme, per Wake Word); Desktop: 25
(Graph-Modell, Live-Events, Zustand, Kamera, Layout, Detail-Overlay inkl. Bearbeiten/Löschen); mypy strict, ruff, eslint 0 Fehler.

## Stand: 2026-10-15

### Installiert / eingerichtet
- PostgreSQL 17 (Homebrew-Service) + pgvector 0.8.7; DBs `jarvis` und `jarvis_test`; Passwort im Schlüsselbund (`jarvis/postgres`)
- Ollama 0.40.1: `jarvis-brain` (= qwen3.5:4b, num_ctx 65536), `qwen3-embedding:0.6b`, `qwen3:4b` (nur Vergleich)
- Hermes Agent v0.22.1 (offizieller Installer, `--skip-browser --skip-computer-use`), Profil `jarvis`;
  Host-Gateway (Multiplexing) mit API-Listener 127.0.0.1:8642, Profil unter `/p/jarvis`
- whisper.cpp 1.9.5 + `ggml-large-v3-turbo-q5_0.bin`; openWakeWord-ONNX-Modelle (hey_jarvis_v0.1)
- Playwright Chromium (headless shell)
- Aufgeräumt (mit Freigabe): npm-Cache, Ollama-Modell qwen3.6

### Architekturwechsel (2026-10-15, auf Wunsch von Sir)
- Agent-Gehirn ist jetzt die **Claude Code CLI mit Sirs Subscription** (nicht mehr Hermes + lokales Modell).
  Ein Prozess je Gespräch (stream-json), Bereitschaftsprozess, Idle-Stop + `--resume`.
- **Modellrouter:** Sonnet 5.5 Standard, einfache Anfragen → Haiku 5.5, Opus 5.5 nur auf Befehl/manuell;
  Wechsel ohne Neustart per Control-Request (gemessen 1–2 s). Getestet: Haiku → „Nimm Opus“ → Opus → auto → Sonnet.
- Hilfsaufgaben (Wissensextraktion, Web-Extraktion, Screenshot-Analyse) über Claude Haiku.
- Lokal bleiben nur Embeddings (qwen3-embedding, Gedächtnissuche) und Whisper (STT). Hermes bleibt als Option (`agent.provider: hermes`).
- Gemessen: „Welche Programme laufen?“ 4,0 s inkl. Tool; Neukunden-Satz → Playbook korrekt gestartet in 5,5 s.

### Globale Claude-Skills (2026-10-15)
- 67 Skills erkannt (eigene + 7 aktive Plugins); Schalter in der App; Agent lädt Skills (getestet: high-end-visual-design),
  deaktivierter Skill ist sofort nicht mehr verfügbar (getestet). Coding-Runner nutzt dieselben Skills.

### Mac-Steuerung – Sicherheitsverbesserung aus dem Live-Test
- Tippen/Tasten/Klicks verlangen eine Ziel-App, die direkt vor der Ausführung aktiviert und geprüft wird
  (eine spät erteilte Freigabe kann nicht in ein falsches Fenster tippen).
- Bridge tippt ein Zeichen pro Event (Rechner hatte nur das erste Zeichen übernommen).
- Bridge mit Apple-Development-Zertifikat signiert → macOS-Rechte bleiben über Updates erhalten.

### 2026-10-16: App-Start, ohne Ollama, Telefonie in der App
- J.A.R.V.I.S. liegt in /Applications (signiert, Mikrofon-Entitlement); die App startet whisper, Bridge, Core und Voice selbst,
  „Beenden“ stoppt alles; live getestet (Start, Wake Word + Frage, Beenden, Neustart)
- Ollama wird nicht mehr genutzt oder gestartet: Embeddings laufen im Core (Qwen3-Embedding-0.6B ONNX); 15 Notizen neu
  eingebettet, semantische Suche live geprüft
- Connector „Telefon (Twilio)“: Zugangsdaten und PIN in der App, automatischer Cloudflare-Schnelltunnel und Webhook-Eintrag,
  Knopf „Testanruf an mich“; Tunnel live geprüft: von außen nur Telefonie-Webhook erreichbar (Rest 404)
- Offen: echter Twilio-Anruf (wartet auf Konto, Nummer und Bundle); FRITZ!Box-SIP als kostenlose Alternative angeboten

### Live mit echten Zugangsdaten getestet (2026-10-15)
- Google: Gmail (neueste Mails), Kalender (Termine 7 Tage), Drive (Suche) – über Jarvis im Chat
- Lexware: Verbindung „PJE Systems“, offene Rechnungen + Kundensuche (nur lesend; Schreibtest wartet auf Sirs Ja)
- Fish Audio: Stimme „Jarvis (MCU)“, Synthese 0,87 s, Ausgabe über Voice-Daemon; 402 (kein API-Guthaben) wird klar gemeldet
- macOS: Bildschirmaufnahme erteilt → Screenshot-Analyse (Claude) live korrekt; Bedienungshilfen noch offen
- Voice: echtes Mikrofon, Wake Word, Whisper, Antwort, Folgefrage; Barge-in live (zurück ins Zuhören)
- macOS-Steuerung: Bedienungshilfen erteilt → Jarvis tippt im Rechner 12*12= und liest 144 aus dem UI-Baum (Bridge startet sich nach Rechte-Erteilung selbst neu)
- Freigabe-Modi YOLO / Gefährliches blockieren / Alles blockieren / Eigene Regeln (nur Owner, auditiert)
- GitHub: angemeldet als baulum, Repos (30, inkl. PJE-Systems) über Jarvis gelistet

### Funktioniert – real getestet
| Bereich | Nachweis |
|---|---|
| Core-Start, Migrationen, Gerätetokens, Status | `scripts/start.sh`, `scripts/status.sh` |
| Hermes ↔ MCP ↔ Policy ↔ Bridge ↔ macOS | Chat „Welche Programme laufen?“ → echte App-Liste; „Öffne die Rechner-App“ → App gestartet |
| Freigabesystem | Modell-Umweg (`mac_shortcut_run`) abgefangen; Approve/Reject über API |
| Prompt-Injection-Taint | Websuche → Sitzung tainted → nächste Aktion (`mac_open_url`) braucht Freigabe |
| Sprachkette | E2E-Test: synth. „Hey Jarvis“ → Wake Word → VAD → Whisper → Hermes → TTS („56.“), 13 s |
| Wake Word | 4/4 positiv (≥0,99), 4/4 negativ (≤0,01), CPU 3 % Echtzeit |
| Telefonie (lokaler Testmodus) | PIN gesprochen erkannt, Frage → Antwort „3 plus 4 ist 7“ als µ-law-Audio |
| Gedächtnis | Integrationstests: Anlegen, Duplikat, Ergänzung, Korrektur, Löschen, Graph, sensible Bestätigung, Vault-Fremdänderung, LLM-Extraktion |
| Executor/Task-Engine | Integrationstests: exactly-once nach Freigabe, Idempotenz, Rollen, Not-Aus, Taint, Audit-Manipulation erkannt, Wiederaufnahme nach Crash, Pause für Eingaben |
| macOS Bridge | 47 Swift-Tests + Live-Checks |
| Desktop-App | `pnpm build`, `pnpm lint` (0 Fehler), 11 Vitest-Tests, Release-.app verbindet sich (WS als owner) |
| Coding-Runner | Echte Jobs: Python-Modul (3 Tests grün) und Müller-Website (Vite/TS, `npm run build` grün, Commit) |
| Neukunden-Playbook | Satz aus dem Master-Prompt → Playbook; Lexware-Schritte ohne Key ehrlich übersprungen; Website gebaut |

Testsummen: Core 25 Unit + 17 Integration + 1 E2E; Desktop 11; Bridge 47.

### Modellwahl (scripts/bench/tool_calling_bench.py)
| Modell | Tool-Calling | Median | RAM | 64k Kontext |
|---|---|---|---|---|
| **qwen3.5:4b** | 27/30 | ~3 s | 5,5 GB | stabil → gewählt |
| qwen3:4b | 30/30 (32k) | 12,3 s | 7,6 GB | Ollama-Absturz bei 64k |
Hermes-Latenz: Kaltstart ~20 s (Modell laden), danach ~2–7 s je Turn (Prompt-Cache, ~6,7k Tokens Systemprompt inkl. Tools).

### Beobachtungen / bekannte Grenzen
- 4B-Modell wählt gelegentlich unpassende Tools (Umwege) → Persona-Regeln verschärft; Policy fängt Seiteneffekte ab.
- Hermes `/v1/skills` wirft upstream `TypeError` → Jarvis liest SKILL.md-Dateien direkt (Fallback).
- Speicher (16 GB): parallel Xcode/Simulator/Rust-Build → Ollama „Compute error“ → Retry-Logik im Core.
- Eine VS-Code-Erweiterung committet automatisch in dieses Repo (Commits „Implement …“ unter Pauls Namen) – nicht von Claude.

### Offen / nächste Schritte
- [x] Desktop-App visuell geprüft (Chat, Systemstatus, Aufgaben inkl. Schrittansicht, Connectoren) – über Vite-Dev + Viewer-Token
- [x] Playbook: fehlgeschlagene Tests erscheinen als Warnung im Abschluss
- [x] mypy strict: 0 Befunde (60 Dateien); dabei echter Fehler gefunden (Notifier.list überschattete `list`)
- [x] Voice mit echtem Mikrofon: „Hey Jarvis“ erkannt, Whisper, Antwort (Haiku) gesprochen, Folgefrage ohne Wake Word.
      Dabei gefunden + behoben: falscher Barge-in direkt nach dem Wake Word, Fragmente wie „K.“ als Anfrage.
- [ ] Bedienungshilfen + Bildschirmaufnahme für JarvisBridge (Dialoge ausgelöst, noch nicht erteilt)
- [ ] Connectoren mit echten Zugangsdaten verifizieren (Lexware, Google, Telegram, GitHub, Fish Audio, Twilio)

### Benötigt von Sir
- Schritt-für-Schritt-Anleitung für alle Zugangsdaten: docs/ANLEITUNG-ZUGANGSDATEN.md
- macOS: Bedienungshilfen + Bildschirmaufnahme für JarvisBridge.app, Mikrofon für den Voice-Daemon
- Zugangsdaten: Lexware-API-Key, Google-OAuth-Client, Telegram-Bot-Token, GitHub-Token, Fish-Audio-Key + Voice-ID,
  Twilio (SID/Token/Nummer) + öffentliche HTTPS-URL, Telefon-PIN
