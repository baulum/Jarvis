# J.A.R.V.I.S.

Persönlicher, lokal laufender KI-Assistent für macOS: spricht (Wake Word „Hey Jarvis“), bedient den Mac, recherchiert im Web,
führt mehrstufige Geschäftsabläufe aus (Lexware, Google, Telegram, GitHub), merkt sich Wissen in einem Obsidian-Vault,
telefoniert (Twilio) und delegiert Programmieraufträge an Claude Code – mit Freigabesystem, Audit-Log und Not-Aus.

- **Gehirn:** Claude Code CLI mit der Claude-Subscription (Sonnet 5.5 Standard, Haiku 5.5 für Einfaches, Opus 5.5 auf Wunsch); keine lokalen LLMs
- **Core:** Python/FastAPI (`core/`), **macOS-Bridge:** Swift (`bridge/`), **Desktop-App:** Tauri/React (`desktop/`)

```bash
scripts/setup.sh   # einmalig: installiert alles und legt J.A.R.V.I.S. in „Programme“
# danach: J.A.R.V.I.S. aus „Programme“ öffnen – startet alle Dienste selbst
```

| Dokument | Inhalt |
|---|---|
| [SETUP.md](SETUP.md) | Installation, Start, Berechtigungen, Connectoren, Tests |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Komponenten, Abläufe, Erweiterbarkeit, VPS |
| [SECURITY.md](SECURITY.md) | Rollen, Freigaben, Prompt-Injection, Secrets, Sandbox, Telefonie |
| [PROGRESS.md](PROGRESS.md) | Entwicklungsstand, Testnachweise, offene Punkte |
