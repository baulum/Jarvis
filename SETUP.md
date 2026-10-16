# J.A.R.V.I.S. – Einrichtung

Voraussetzungen: macOS auf Apple Silicon, Homebrew, Xcode (Command Line Tools), Claude Code CLI (angemeldet mit
der Claude-Subscription über `claude` → `/login`). Freier Speicher: ca. 6 GB. **Kein Ollama nötig.**

## 1. Installation (einmalig, idempotent)
```bash
scripts/setup.sh
```
Das Skript installiert/prüft Homebrew-Pakete (postgresql@17, pgvector, whisper-cpp, ffmpeg, uv, node, pnpm, rustup,
cloudflared), legt die Datenbank `jarvis` an (Passwort im Schlüsselbund), richtet die Python-Umgebung ein, führt Migrationen aus,
lädt die Modelle (Embedding-Modell Qwen3-Embedding-0.6B als ONNX für das Gedächtnis, Whisper large-v3-turbo, openWakeWord),
koppelt Desktop und Voice als Geräte, baut JarvisBridge und die Desktop-App, signiert beide mit deinem Apple-Development-Zertifikat,
installiert sie nach **/Applications** (`scripts/install-app.sh`) und schließt mit `jarvisctl doctor` ab.

## 2. Starten / Stoppen
**J.A.R.V.I.S. aus „Programme“ öffnen.** Das reicht: Die App startet whisper-server, JarvisBridge, Core und Voice-Daemon
selbst (Log: `~/Library/Logs/Jarvis/launcher.log`).
- Fenster schließen → Jarvis läuft in der Menüleiste weiter (inkl. „Hey Jarvis“).
- Menüleiste › **Beenden** (⌘Q) → stoppt alle Dienste. Menüleiste › **Dienste neu starten** bei Problemen.
- Einstellungen › „Beim Anmelden starten“ → Jarvis startet mit dem Mac.
- Updates nach Code-Änderungen: `scripts/install-app.sh` (baut, signiert, installiert, startet neu).

Für Entwicklung weiterhin: `scripts/start.sh [--no-voice] [--no-app]`, `scripts/stop.sh`, `scripts/status.sh`.

## 3. macOS-Berechtigungen (manuell, einmalig)
Systemeinstellungen › Datenschutz & Sicherheit:
- **Bedienungshilfen** und **Bildschirmaufnahme** → `JarvisBridge` (bzw. in der App unter Berechtigungen „Anfordern“)
- **Mikrofon** → **J.A.R.V.I.S.** (die App startet den Voice-Daemon; macOS fragt beim ersten Start)
- **Automation** → wird bei der ersten AppleScript-Aktion abgefragt (AppleScript ist standardmäßig deaktiviert)

## 4. Connectoren einrichten

**Schritt-für-Schritt-Anleitung mit allen Links:** [docs/ANLEITUNG-ZUGANGSDATEN.md](docs/ANLEITUNG-ZUGANGSDATEN.md)
In der Desktop-App unter **Connectoren** oder per CLI (`cd core && uv run jarvisctl secret set <name>`):

| Connector | Was wird benötigt | Wo |
|---|---|---|
| Lexware Office | Public-API-Schlüssel (`lexware-api-key`) | Lexware › Einstellungen › Erweiterungen › Public API |
| Google | OAuth-Client JSON (Desktop-App, `google-oauth-client`), dann „Mit Google verbinden“ | Google Cloud Console; Gmail-, Calendar-, Drive-API aktivieren |
| Telegram | Bot-Token (`telegram-bot-token`), dann „Koppeln“ und `/pair <Code>` an den Bot | @BotFather |
| GitHub | Fine-grained PAT (`github-token`) | github.com › Settings › Developer settings |
| Fish Audio | API-Key (`fish-audio-api-key`) + `voice.tts.fish_audio.reference_id`, `voice.tts.provider: fish_audio` | fish.audio |
| Telefonie | `twilio-account-sid`, `twilio-auth-token`, `phone-pin`; in `config/jarvis.yaml`: `telephony.twilio.from_number`, `telephony.owner_numbers`, `server.public_base_url` | Twilio Console; öffentliche HTTPS-URL z. B. per Cloudflare Tunnel |

Twilio: Voice-Webhook der Rufnummer → `POST https://<public_base_url>/api/telephony/twilio/voice`.
Lokaler Test ohne Twilio: `uv run jarvisctl phone-test --say "eins zwei drei vier" --say "Wie spät ist es?"` (benötigt eine PIN).

## 5. Sprachmodell (Claude 5.5)
- Standard: **Sonnet 5.5**; einfache Anfragen automatisch **Haiku 5.5**; **Opus 5.5** nur auf Wunsch.
- Per Sprache/Chat: „Nimm Opus“ (dieses Gespräch), „Ab jetzt Haiku“ (Standard), „Nur diesmal Opus: …“ (eine Antwort),
  „Zurück zur automatischen Modellwahl“.
- In der Desktop-App: Umschalter über dem Eingabefeld (Auto / Haiku / Sonnet / Opus).
- Konfiguration: `agent.model`, `agent.auto_downgrade`, `agent.utility_model` in `config/jarvis.yaml`.
- Lokale Modelle (Ollama/Hermes) werden nicht verwendet; der frühere Hermes-Modus ist nur noch als Altoption im Code.

## 6. Globale Claude-Skills
- Jarvis nutzt deine Skills aus `~/.claude/skills` sowie die Skills aktiver Claude-Code-Plugins.
- Desktop-App › **Skills › Claude-Skills**: jeden Skill einzeln an-/ausschalten (eigene Skills standardmäßig an, Plugin-Skills aus).
- Im Gespräch lädt Jarvis passende Skills selbst; Aufgaben mit Dateien/Code (z. B. Websites) gibt er an Claude Code weiter,
  das dieselben Skills im isolierten Worktree nutzt.
- Technisch: aktivierte Skills werden als Plugin-Verzeichnis `~/Library/Application Support/Jarvis/skills-plugin` (Symlinks)
  per `--plugin-dir` geladen – deine übrigen Claude-Einstellungen, Hooks und MCP-Server bleiben außen vor.

## 7. Persönlichkeit & Verhalten anpassen
- `config/personality.yaml` – Anrede, Charakterzüge, Humor, Begrüßungen (wird beim Core-Start als Hermes-SOUL.md übernommen)
- `config/permissions.yaml` – Rollen, Risikostufen, Shell-Allowlist, Rate-Limits
- `config/jarvis.yaml` – Modelle, Stimme, Benachrichtigungsregeln, Ruhezeiten, Pfade
- `config/mcp_servers.yaml` – externe MCP-Server

## 8. Tests
```bash
cd core
uv run pytest tests/unit                       # ohne Infrastruktur
uv run pytest -m integration tests/integration # Test-DB jarvis_test, Claude-CLI, Embedding-Modell
uv run pytest -m e2e tests/e2e                 # laufender Stack (Sprachkette Ende-zu-Ende)
cd ../desktop && pnpm test && pnpm lint && pnpm build
cd ../bridge && swift test
```

## Fehlerbehebung
- `jarvisctl doctor` prüft Binärdateien, Secrets, Tokens und Modelle.
- Logs: `~/Library/Logs/Jarvis/` (`core.jsonl`, `hermes-gateway.log`, `whisper.log`, `voice.log`, `bridge.log`).
- Nur bei `agent.provider: hermes`: Hermes nutzt **ein** Host-Gateway für alle Profile; Jarvis startet es als Kindprozess (`hermes gateway run --replace`),
  sobald der MCP-Endpunkt erreichbar ist. Der API-Listener wird über das Default-Profil (`~/.hermes/.env`) aktiviert.
- Arbeitsspeicher: Whisper ≈ 1 GB, Embedding-Modell im Core ≈ 1,2 GB, je Claude-Code-Prozess ≈ 0,3 GB.
- Telefonie: siehe [docs/ANLEITUNG-TWILIO.md](docs/ANLEITUNG-TWILIO.md) (inkl. Alternativen).

## Neu in 2.0 (2026-10-16)
- **Audiohelfer** `jarvis-audio` (Swift): wird von `scripts/setup.sh` bzw. beim Start automatisch gebaut
  (`scripts/build-audio-helper.sh`, braucht `swiftc` aus den Xcode Command Line Tools). Fehlt er, nutzt der
  Voice-Daemon den bisherigen Weg (sounddevice + afplay).
- **Recherche** öffnet Microsoft Edge mit einem eigenen Profil (`~/Library/Application Support/Jarvis/edge-profile`).
  Dort kann man sich bei Bedarf einmal anmelden (z. B. Google), Sirs normales Edge-Profil bleibt unberührt.
  `browser.engine: chromium` in `config/jarvis.yaml` schaltet auf den bisherigen versteckten Browser zurück.
- **Sprache**: Einstellungen unter `voice` in `config/jarvis.yaml` – `conversation_window_s` (weiterhören ohne Wake Word),
  `min_end_of_turn_silence_ms` / `end_of_turn_silence_ms` (Endpointing), `echo_cancellation` (experimentell, aus).
- **Datenbank-Migrationen** laufen beim Core-Start automatisch.
- **Latenz messen**: `cd core && uv run python ../scripts/bench/voice_latency.py --wav <aufnahme.wav>`;
  jeder echte Sprachturn landet in `~/Library/Logs/Jarvis/voice-metrics.jsonl`.
- **Bedienung**: Ziehen = drehen, Scrollen = zoomen, Rechtsklick-Ziehen = verschieben, Klick = Knoten öffnen,
  Doppelklick ins Leere / Esc = Gesamtansicht, `/` oder ⌘K = Suche, Enter = Chat, Raster-Symbol = alle Systemansichten.
