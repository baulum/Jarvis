# J.A.R.V.I.S.

Persönlicher, lokal laufender KI-Assistent für macOS: spricht (Wake Word „Hey Jarvis“), bedient den Mac, recherchiert im Web,
führt mehrstufige Geschäftsabläufe aus (Lexware, Google, Telegram, GitHub), merkt sich Wissen in einem Obsidian-Vault,
telefoniert (Twilio) und delegiert Programmieraufträge an Claude Code – mit Freigabesystem, Audit-Log und Not-Aus.

- **Gehirn:** Hermes Agent (Nous Research) mit lokalem Modell über Ollama (`qwen3.5:4b`, 64k Kontext)
- **Core:** Python/FastAPI (`core/`), **macOS-Bridge:** Swift (`bridge/`), **Desktop-App:** Tauri/React (`desktop/`)

```bash
scripts/setup.sh   # einmalig
scripts/start.sh   # starten
```

| Dokument | Inhalt |
|---|---|
| [SETUP.md](SETUP.md) | Installation, Start, Berechtigungen, Connectoren, Tests |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Komponenten, Abläufe, Erweiterbarkeit, VPS |
| [SECURITY.md](SECURITY.md) | Rollen, Freigaben, Prompt-Injection, Secrets, Sandbox, Telefonie |
| [PROGRESS.md](PROGRESS.md) | Entwicklungsstand, Testnachweise, offene Punkte |
