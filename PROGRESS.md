# J.A.R.V.I.S. – Entwicklungsstand

> Wiedereinstiegspunkt für neue Sessions. Nach jeder größeren Änderung aktualisieren.
> Doku: ARCHITECTURE.md (Aufbau), SETUP.md (Installation/Betrieb), SECURITY.md (Sicherheitsmodell).

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
