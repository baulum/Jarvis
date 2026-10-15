# J.A.R.V.I.S. – Einrichtung

Voraussetzungen: macOS auf Apple Silicon, Homebrew, Xcode (Command Line Tools), Ollama.app, Claude Code CLI (angemeldet mit
der Claude-Subscription über `claude` → `/login`). Freier Speicher: ca. 10 GB.

## 1. Installation (einmalig, idempotent)
```bash
scripts/setup.sh
```
Das Skript installiert/prüft Homebrew-Pakete (postgresql@17, pgvector, whisper-cpp, ffmpeg, uv, node, pnpm, rustup), legt die
Datenbank `jarvis` an (Passwort im Schlüsselbund), richtet die Python-Umgebung ein, führt Migrationen aus, lädt die Modelle
(`qwen3.5:4b` → Alias `jarvis-brain` mit 64k Kontext, `qwen3-embedding:0.6b`, Whisper large-v3-turbo, openWakeWord),
installiert Hermes über den offiziellen Installer, erzeugt das Hermes-Profil `jarvis`, koppelt Desktop/Voice/Hermes als Geräte,
baut die Swift-Bridge und die Desktop-App und schließt mit `jarvisctl doctor` ab.

## 2. Starten / Stoppen
```bash
scripts/start.sh            # whisper-server, JarvisBridge, Core (+ Hermes-Gateway), Voice-Daemon, Desktop-App
scripts/start.sh --no-voice # ohne Mikrofon
scripts/stop.sh
scripts/status.sh           # JSON-Systemstatus
```
Optional beim Login: `scripts/launchd/install.sh [--with-voice]` (entfernen: `scripts/launchd/uninstall.sh`).
Die Desktop-App hat in den Einstellungen einen eigenen Schalter „Beim Anmelden starten“.

## 3. macOS-Berechtigungen (manuell, einmalig)
Systemeinstellungen › Datenschutz & Sicherheit:
- **Bedienungshilfen** und **Bildschirmaufnahme** → `JarvisBridge` (bzw. in der App unter Berechtigungen „Anfordern“)
- **Mikrofon** → das Programm, das den Voice-Daemon startet (Terminal bzw. beim LaunchAgent `uv`/Python)
- **Automation** → wird bei der ersten AppleScript-Aktion abgefragt (AppleScript ist standardmäßig deaktiviert)

## 4. Connectoren einrichten
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

## 5. Persönlichkeit & Verhalten anpassen
- `config/personality.yaml` – Anrede, Charakterzüge, Humor, Begrüßungen (wird beim Core-Start als Hermes-SOUL.md übernommen)
- `config/permissions.yaml` – Rollen, Risikostufen, Shell-Allowlist, Rate-Limits
- `config/jarvis.yaml` – Modelle, Stimme, Benachrichtigungsregeln, Ruhezeiten, Pfade
- `config/mcp_servers.yaml` – externe MCP-Server

## 6. Tests
```bash
cd core
uv run pytest tests/unit                       # ohne Infrastruktur
uv run pytest -m integration tests/integration # Test-DB jarvis_test + Ollama
uv run pytest -m e2e tests/e2e                 # laufender Stack (Sprachkette Ende-zu-Ende)
cd ../desktop && pnpm test && pnpm lint && pnpm build
cd ../bridge && swift test
```

## Fehlerbehebung
- `jarvisctl doctor` prüft Binärdateien, Secrets, Tokens und Modelle.
- Logs: `~/Library/Logs/Jarvis/` (`core.jsonl`, `hermes-gateway.log`, `whisper.log`, `voice.log`, `bridge.log`).
- Hermes nutzt **ein** Host-Gateway für alle Profile; Jarvis startet es als Kindprozess (`hermes gateway run --replace`),
  sobald der MCP-Endpunkt erreichbar ist. Der API-Listener wird über das Default-Profil (`~/.hermes/.env`) aktiviert.
- Wenig Arbeitsspeicher (16 GB): Agent-Modell ≈ 5,5 GB, Whisper ≈ 1 GB, Embeddings ≈ 0,7 GB. Speicherintensive Apps
  (Xcode, Simulator, Rust-Builds) können Ollama-Fehler „Compute error“ auslösen; der Core wiederholt solche Anfragen.
