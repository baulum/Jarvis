# J.A.R.V.I.S. – Entwicklungsstand

> Diese Datei ist der Wiedereinstiegspunkt für neue Sessions. Immer aktuell halten.

## Stand: 2026-10-15

### Installiert / eingerichtet
- PostgreSQL 17 (Homebrew-Service) + pgvector 0.8.7, DB `jarvis`, Rolle `jarvis` (Passwort im Schlüsselbund, Konto `postgres`)
- Ollama 0.40.1: `jarvis-brain` (= qwen3.5:4b, num_ctx 65536), `qwen3-embedding:0.6b`, `qwen3:4b` (Vergleich)
- Hermes Agent v0.22.1 (offizieller Installer, ohne eigene Browser-/Computer-Use-Tools), Profil `jarvis`
- Rust 1.91 (rustup), whisper.cpp 1.9.5 (+ Modell large-v3-turbo q5_0), openWakeWord-ONNX-Modelle
- Modelle liegen in `~/Library/Application Support/Jarvis/models`

### Funktioniert (real getestet)
- Jarvis Core (FastAPI, 127.0.0.1:8765): Start, Migrationen, Gerätetokens, Audit-Hashkette
- Hermes-Integration: Host-Gateway als Kindprozess, Profil unter `/p/jarvis`, Jarvis-Tools via MCP (`/mcp`)
- Chat → Hermes → MCP → Policy/Executor → Swift-Bridge → macOS (App-Liste, App öffnen)
- Freigabesystem (Approval erzeugt, über API abgelehnt), Kill-Switch-Datei geteilt mit Bridge
- macOS Bridge (Swift): 47 Tests grün, Live-Checks (siehe bridge/README.md)

### Modellwahl (Benchmark scripts/bench/tool_calling_bench.py)
| Modell | Tool-Calling | Median | RAM | 64k Kontext |
|---|---|---|---|---|
| qwen3.5:4b | 27/30 | ~3 s | 5,5 GB | stabil |
| qwen3:4b | 30/30 (32k) | 12,3 s | 7,6 GB | Ollama-Absturz bei 64k |

### Offen / nächste Schritte
- [ ] Desktop-App (Tauri) – in Arbeit
- [ ] Voice-Daemon (Wake Word, VAD, STT, TTS) – Implementierung + Test
- [ ] Unit-/Integrationstests für Core
- [ ] Start-/Stop-Skripte, LaunchAgents
- [ ] Doku: ARCHITECTURE.md, SETUP.md, SECURITY.md, .env.example
- [ ] Telefonie lokaler Testmodus testen
- [ ] Playbook new_customer_project E2E (ohne Lexware-Zugang: Setup-Zustand)

### Benötigt von Sir (Zugangsdaten/Berechtigungen)
- macOS: Bedienungshilfen + Bildschirmaufnahme für JarvisBridge.app, Mikrofon für Voice-Daemon
- Lexware API-Key, Google OAuth-Client, Telegram-Bot-Token, GitHub-Token, Fish-Audio-Key, Twilio (SID/Token/Nummer + öffentliche URL), Telefon-PIN
