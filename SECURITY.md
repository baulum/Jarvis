# J.A.R.V.I.S. – Sicherheitsmodell

## Grundsätze
1. **Lokal zuerst:** Alle Dienste binden an `127.0.0.1`. Keine Daten verlassen den Mac außer an explizit eingerichtete Dienste
   (Lexware, Google, Telegram, GitHub, Fish Audio, Twilio).
2. **Jede Aktion läuft durch eine Stelle:** `ToolExecutor` – unabhängig davon, ob Hermes, ein Playbook, Telegram, das Telefon
   oder der Scheduler sie auslöst.
3. **Defense in Depth:** Die Swift-Bridge prüft Token, Not-Aus, eine eigene Policy-Datei und Rate-Limits zusätzlich.
4. **Ehrlichkeit vor Bequemlichkeit:** Nicht eingerichtete Funktionen melden „nicht eingerichtet“, nie simulierte Erfolge.

## Rollen und Freigaben (`config/permissions.yaml`)
| Rolle | Kanal | Max. Risiko | Automatisch bis | Darf freigeben |
|---|---|---|---|---|
| owner | gepaarte Desktop-App/CLI | critical | low | ja (auch kritisch) |
| voice_owner | „Hey Jarvis“ am Mac | high | low | **nein** (jeder im Raum kann sprechen) |
| telegram_owner | gekoppelter Telegram-Chat | high | low | ja (nicht kritisch) |
| phone_owner | Anruf **nach PIN** | high | low | ja (nicht kritisch) |
| phone_unverified | Anruf ohne PIN | read | read | nein |
| operator / viewer | weitere Geräte | high / read | low / read | nein |
| system | Scheduler, Webhooks | high | low | nein |

Risikostufen: `read` < `low` < `medium` (externe Änderung, noch nicht für Dritte sichtbar) < `high` (sichtbar für Dritte:
E-Mail-Versand, Rechnungsversand, PR, Anrufe, UI-Eingaben, Shell) < `critical` (Geld, endgültige Rechnungen, Löschungen).
Overrides in `permissions.yaml` können Risiken nur **erhöhen**.

## Prompt-Injection
- Alle externen Inhalte (Webseiten, E-Mails, Drive, GitHub-Issues, Zwischenablage, Bildschirminhalt, Webhooks, externe MCP-Server)
  werden mit `wrap_untrusted` eingezäunt; Fence-Fälschungen im Inhalt werden neutralisiert, Injektionsmuster gemeldet.
- Tools mit `untrusted_output` markieren die Sitzung als **tainted** → danach braucht **jede** nicht-lesende Aktion eine Freigabe
  (getestet: nach einer Websuche wurde `mac_open_url` zur Freigabe vorgelegt).
- Der Persona-Prompt verbietet, Anweisungen aus eingezäunten Inhalten zu befolgen; die Policy-Engine verlässt sich nicht darauf.
- Browser: frischer, isolierter Kontext je Aufruf (keine Cookies/Logins), nur http(s), SSRF-Schutz gegen interne Adressen.

## Zugangsdaten
- macOS-Schlüsselbund (Dienst `jarvis`) über `keyring`; auf Servern alternativ `JARVIS_SECRET_<NAME>`-Umgebungsvariablen.
- API liefert nur „gesetzt/fehlt“, nie Werte. Logs und Audit schwärzen token-artige Zeichenketten und sensible Schlüssel.
- Gerätetokens: nur SHA-256-Hash in der DB; Klartext einmalig bei der Kopplung bzw. als 0600-Datei für lokale Komponenten.
- Agent: Claude Code läuft mit `--restricted` (keine Benutzer-/Projekt-Settings, Hooks, Plugins), `--tools ""` (kein Datei-/
  Shell-Zugriff), `--strict-mcp-config` mit ausschließlich dem Jarvis-MCP-Server und `--allowedTools mcp__jarvis`.
  Das MCP-Token (Rolle `agent`, nur `/mcp`) liegt in einer 0600-Datei, nicht in der Prozess-Kommandozeile.
- Nutzung der Subscription: unveränderte CLI, eigener Login, rein persönliche Automatisierung auf dem eigenen Mac.
  Jeder Turn verbraucht Kontingent; bei Erschöpfung meldet Jarvis das ehrlich.
- Claude Code: offizielle Subscription-Anmeldung. Der Runner entfernt `ANTHROPIC_API_KEY` & Co. aus der Umgebung und prüft
  `claude auth status`. Es werden keine OAuth-Tokens gelesen oder weitergegeben.

## Isolierte Coding-Jobs
- Eigener Git-Worktree je Job, nur auf `jarvis/*`-Branches; Push/PR nur nach Freigabe (`high`).
- `claude --restricted` ignoriert Projekt-/Benutzer-Settings und Hooks fremder Repos, Datei-Tools auf den Worktree beschränkt.
- `--strict-mcp-config` (keine MCP-Server aus dem Repo), Tool-Allowlist, `--permission-prompts none`.
- macOS-Seatbelt-Sandbox für Bash mit `failIfUnavailable`, Netzwerk-Allowlist (npm, PyPI, GitHub, Fonts-CDNs).
- Geklonte Repos werden nicht ausgeführt; Testbefehle nur aus einer Allowlist (`npm`, `pnpm`, `node`, `python3`, `uv`, …).

## Telefonie
- Twilio-Webhooks werden per `X-Twilio-Signature` (HMAC-SHA1) geprüft; ohne gültige Signatur → 403.
- Media-Stream nur mit Einmal-Token (120 s gültig, an den Anruf gebunden).
- **Die Rufnummer authentifiziert nie.** PIN-Pflicht (DTMF oder gesprochen, Konstantzeit-Vergleich), 3 Fehlversuche → Auflegen.
- Rate-Limits pro Nummer und global; vollständige Anrufprotokolle mit Transkript.
- Ausgehende Anrufe sagen nur den Benachrichtigungstitel an; alles Weitere erst nach PIN.

## Not-Aus
- Desktop (immer sichtbar), Menüleiste, Telegram `/stop`, `jarvisctl killswitch on` oder `touch ~/Library/Application Support/Jarvis/KILL`.
- Wirkung: alle nicht-lesenden Tools gesperrt, Bridge verweigert Aktionen, laufende Coding-Jobs werden beendet, Agent-Lauf
  abgebrochen, Task-Engine und Scheduler pausieren. Aufheben nur durch den Besitzer am Desktop/CLI.

## Audit
- Append-only-Tabelle `audit_log` mit SHA-256-Hash-Kette (`GET /api/audit/verify` erkennt Manipulationen – getestet).
- Protokolliert: Tool-Aufrufe (Argumente geschwärzt), Freigaben/Ablehnungen, Konfigurationsänderungen, Gerätekopplungen,
  Telefon-Authentifizierung, Gedächtnisänderungen, Not-Aus.

## Netzwerk & Webhooks
- REST-API: Bearer-Token, Rate-Limits pro IP, Login-Fehlversuchsbremse, Sicherheits-Header, CORS nur für die Desktop-App.
- Eingehende generische Webhooks: HMAC-SHA256 (`X-Jarvis-Signature: sha256=…`), Payload wird als nicht vertrauenswürdig behandelt.
- Für öffentliche Erreichbarkeit (Twilio) nur über einen TLS-Tunnel/Reverse-Proxy (z. B. Cloudflare Tunnel) – die Signaturprüfung
  nutzt `server.public_base_url`.

## Bekannte Grenzen
- Ein 4B-Modell kann Tools falsch wählen; die Policy-Engine fängt Seiteneffekte ab (siehe PROGRESS.md, Beobachtungen).
- Die Bridge ist ad-hoc signiert: nach einem Neubau müssen macOS-Rechte ggf. neu erteilt werden.
- Bildschirmanalyse und Zwischenablage können fremde Inhalte enthalten → als untrusted markiert.
