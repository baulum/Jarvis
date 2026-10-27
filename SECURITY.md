# J.A.R.V.I.S. – Sicherheitsmodell

## Grundsätze
1. **Lokal zuerst:** Alle Dienste binden an `127.0.0.1`. Keine Daten verlassen den Mac außer an explizit eingerichtete Dienste
   (Lexware, Google, Telegram, GitHub, Vercel, Fish Audio, Twilio).
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
| mobile_owner | gekoppeltes Smartphone (Keystore-Schlüssel) | high | low | ja (nicht kritisch, signiert + biometrisch) |
| phone_owner | Anruf **nach PIN** | high | low | ja (nicht kritisch) |
| phone_unverified | Anruf ohne PIN | read | read | nein |
| operator / viewer | weitere Geräte | high / read | low / read | nein |
| system | Scheduler, Webhooks | high | low | nein |

Risikostufen: `read` < `low` < `medium` (externe Änderung, noch nicht für Dritte sichtbar) < `high` (sichtbar für Dritte:
E-Mail-Versand, Rechnungsversand, PR, Anrufe, UI-Eingaben, Shell) < `critical` (Geld, endgültige Rechnungen, Löschungen).
Overrides in `permissions.yaml` können Risiken nur **erhöhen**.

## Freigabe-Modi (Desktop-App › Freigaben)

| Modus | Läuft sofort | Braucht Freigabe |
|---|---|---|
| **YOLO** | alles | nur kritische Aktionen in Sitzungen mit externen Inhalten |
| **Gefährliches blockieren** (Standard) | read, low, medium | high, critical |
| **Alles blockieren** | nur read | jede Aktion mit Auswirkung |
| **Eigene Regeln** | pro Risikostufe „Sofort“/„Fragen“, pro Werkzeug „Sofort“/„Fragen“/„Sperren“ (Glob-Muster möglich) | ebenso |

- Gespeichert in `~/Library/Application Support/Jarvis/approval-mode.json`; ändern darf nur die Rolle **owner**
  (`PUT /api/approvals/mode`), weder der Agent noch Stimme, Telegram oder Telefon. Jeder Wechsel steht im Audit-Log.
- Unabhängig vom Modus: Not-Aus, `max_risk` jeder Rolle (z. B. Anrufer ohne PIN nur lesend), Rate-Limits, Audit.
- Taint: In „Gefährliches/Alles blockieren“ braucht nach externen Inhalten jede Aktion mit Auswirkung eine Freigabe;
  in „Eigene Regeln“ abschaltbar; in YOLO bleiben nur kritische Aktionen geschützt.
- Hinweis zu YOLO: Per „Hey Jarvis“ kann jeder im Raum Aktionen auslösen, die dann ohne Rückfrage laufen.

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
- Vercel: Token (`vercel-token`) nur im Authorization-Header gegen `api.vercel.com`; Fehlermeldungen enthalten nie Request
  oder Header, `VERCEL_TOKEN`/`JARVIS_SECRET_*` werden aus der Umgebung von Coding-Jobs und git-Aufrufen entfernt.
  `vercel_deploy` ist `high`; Werte von Umgebungsvariablen erscheinen weder im Freigabe-Text noch im Audit-Log
  (nur Namen), `secret:<name>` liest sie direkt aus dem Schlüsselbund. Lokale Deploys nur aus Projekten, Workspaces und
  freigegebenen `allowed_roots`; `.env*`-Dateien werden nie hochgeladen.
- Gerätetokens: nur SHA-256-Hash in der DB; Klartext einmalig bei der Kopplung bzw. als 0600-Datei für lokale Komponenten.
- Agent: Claude Code läuft mit `--restricted` (keine Benutzer-/Projekt-Settings, Hooks, Plugins), `--tools ""` (kein Datei-/
  Shell-Zugriff), `--strict-mcp-config` mit ausschließlich dem Jarvis-MCP-Server und `--allowedTools mcp__jarvis`.
  Das MCP-Token (Rolle `agent`, nur `/mcp`) liegt in einer 0600-Datei, nicht in der Prozess-Kommandozeile.
- Nutzung der Subscription: unveränderte CLI, eigener Login, rein persönliche Automatisierung auf dem eigenen Mac.
  Jeder Turn verbraucht Kontingent; bei Erschöpfung meldet Jarvis das ehrlich.
- Claude Code: offizielle Subscription-Anmeldung. Der Runner entfernt `ANTHROPIC_API_KEY` & Co. aus der Umgebung und prüft
  `claude auth status`. Es werden keine OAuth-Tokens gelesen oder weitergegeben.

## Isolierte Coding-Jobs
- Eigener Git-Worktree je Job, nur auf `jarvis/*`-Branches. Die Auslieferung nach einem **grünen** Job (Push des Job-Branches,
  PR, Merge nach `main`, Supabase-Deploy, Vercel-Deploy bei `vercel.json`/Vercel-Link und aktivem Connector – abschaltbar
  über `deploy_after_merge`) läuft als Standard automatisch im Runner (`coding.auto_ship`, abschaltbar) –
  ohne Einzelfreigabe, aber nie bei rotem Ergebnis oder Fehler, nie per Direkt-Push auf `main`, und jeder Lauf wird als
  `coding.ship` auditiert. Rote, fehlgeschlagene oder abgebrochene Jobs pushen nur ihren `jarvis/*`-Branch (Sicherung).
  Nach dem Merge löscht der Runner nur den eigenen `jarvis/*`-Branch und den Worktree im Jarvis-Workspace, und nur wenn
  der Worktree sauber ist und der Branch noch auf dem gemergten Commit steht. Manuelles Nachholen (`coding_ship_job`) und Draft-PR (`coding_prepare_pr`) bleiben `high`.
- Supabase-Datenbankpasswörter liegen nur im Schlüsselbund (`supabase-db-password`, je Projekt
  `supabase-db-password-<project-ref>`) und gehen nur als `SUPABASE_DB_PASSWORD` an `supabase db push`; in der CLI-Ausgabe
  werden sie geschwärzt. Pro Deploy genau ein Anmeldeversuch, danach wartet die Auslieferung auf Sir.
  `coding_supabase_password` (`high`) legt das genannte Passwort schon vor der Freigabe ab und entfernt es aus den
  Argumenten – Freigabe, Audit, Idempotenz-Ledger und die Werkzeuganzeige der Chat-Timeline sehen es nicht (Sirs eigene
  Chatnachricht mit dem Passwort bleibt im Verlauf).
- `claude --restricted` ignoriert Projekt-/Benutzer-Settings und Hooks fremder Repos, Datei-Tools auf den Worktree beschränkt.
- `--strict-mcp-config` (keine MCP-Server aus dem Repo), Tool-Allowlist, `--permission-prompts none`.
- macOS-Seatbelt-Sandbox für Bash mit `failIfUnavailable`, Netzwerk-Allowlist (npm, PyPI, GitHub, Fonts-CDNs).
- Geklonte Repos werden nicht ausgeführt; Testbefehle nur aus einer Allowlist (`npm`, `pnpm`, `node`, `python3`, `uv`, …).

## Telefonie
- Twilio-Webhooks werden per `X-Twilio-Signature` (HMAC-SHA1) geprüft; ohne gültige Signatur → 403.
- Media-Stream nur mit Einmal-Token (120 s gültig, an den Anruf gebunden).
- **Die Rufnummer authentifiziert nie.** PIN-Pflicht (DTMF oder gesprochen, Konstantzeit-Vergleich), 3 Fehlversuche → Auflegen.
- Rate-Limits pro Nummer; Fremde teilen sich ein eigenes Stundenbudget und können Sir nicht aussperren;
  `telephony.reject_unknown_callers: true` nimmt nur Nummern aus `owner_numbers` an.
- Anrufprotokolle mit Transkript – **erst nach erfolgreicher PIN**: eine gesprochene PIN wird weder gespeichert noch
  über den Event-Bus verteilt.
- Ausgehende Anrufe melden sich nur mit „Hier ist Jarvis“; der Anrufgrund wird erst nach der PIN gesprochen.

## Smartphone (J.A.R.V.I.S.-App)
- **Kopplung:** Einmalcode (144 Bit, 10 min) nur vom Owner-Gerät (`jarvisctl mobile-pair`); das Telefon beweist mit
  einer Signatur den Besitz seines neuen **Android-Keystore-Schlüssels** (EC P-256, StrongBox wenn vorhanden,
  nicht exportierbar). Rate-Limit 10 Versuche/Stunde/IP.
- **Tokens:** Zugriffstoken `jat_…` (HMAC-SHA256, 10 min, Gerät/Rolle/Familie; Schlüssel `mobile-token-key` im
  Schlüsselbund). Refresh-Token `jrt_…` nur als SHA-256 gespeichert, Einmalverwendung mit Rotation; ein bereits
  benutztes Token widerruft die gesamte Familie (Diebstahlerkennung; ein Wiederholungs-Request innerhalb von 30 s
  nach einem verlorenen Response wird toleriert). Jede Erneuerung ist mit dem Geräteschlüssel
  signiert (`refresh|gerät|ts|nonce|sha256(body)`, ±120 s, Nonce-Cache gegen Replay).
- **Freigaben vom Telefon:** nur über `POST /api/mobile/approvals/<id>` mit frischer Gerätesignatur
  (`approval:<id>`); die App verlangt vorher Fingerabdruck/Geräte-PIN. Kritische Aktionen bleiben am Desktop.
- **Transport:** eigener Listener `:8767` (TLS, selbstsigniert, Fingerabdruck im QR-Code gepinnt) liefert nur eine
  Allowlist von Mobile-Pfaden; alles andere – Audit, Connectoren, Geräte, MCP, Approval-Modus, Export – antwortet 404.
  Requests mit Cloudflare-Headern werden dort immer abgewiesen. Core `:8765` bleibt auf 127.0.0.1.
  LiveKit-Signalisierung nur über diesen TLS-Kanal (`/rtc`-Proxy, nur mit einem vom Core signierten Raum-Token,
  Nachrichten ≤ 1 MiB, max. 8 Verbindungen), Medien DTLS-SRTP; Raum-Token je Anruf (1 h). Bei `tls: external`
  bindet das Gateway nur an 127.0.0.1.
- **Gedächtnis auf dem Handy:** als „sensibel“ markierte Notizen sind für Nicht-Owner-Geräte unsichtbar (Liste,
  Suche, Graph, Cluster).
- **Push:** FCM-Daten-Nachrichten Ende-zu-Ende AES-256-GCM-verschlüsselt (Schlüssel je Gerät, auf dem Telefon im
  Keystore, AAD = Geräte-ID), mit ID + Ablauf; das Telefon verwirft Duplikate/Abgelaufenes. FCM-Dienstkonto nur im
  Schlüsselbund (`fcm-service-account`).
- **App:** kein Klartext-HTTP, keine Cloud-Backups/Geräteübertragung der Zugangsdaten, Refresh-Token AES-GCM mit
  Keystore-Schlüssel verschlüsselt, keine Business-API-Schlüssel. Logs enthalten keine Tokens.
- **Anrufe:** Jarvis ruft nur auf ausdrücklichen Auftrag oder per vom Owner gesetzter Kategorie an; ein Anruf je
  Ereignis, Limits je Stunde, Ruhezeit. Agent-Werkzeuge `mobile_*` haben Risiko `low` (erreichen nur Sir), in
  „tainted“ Sitzungen gilt die normale Freigabepflicht.
- **Widerruf:** App › Abmelden, Einstellungen › Gekoppelte Telefone oder `DELETE /api/mobile/devices/<id>` – alle
  Refresh-Tokens ungültig, Push-Token gelöscht, Zugriffstoken spätestens nach 5 s Cache abgelehnt.
- **Test-Hooks:** `POST /api/mobile/test/ring-local` (Owner, nur Loopback, nicht im Gateway) und der
  `DevPushReceiver` (nur System/adb, `android.permission.DUMP`) – ein simulierter Anruf ist ohne klingelnde
  Sitzung im Core wertlos.

## Device Agents und entfernte Clients

- **Kopplung:** Einmalcode (10 min, nur vom Besitzer erzeugbar), der Agent prüft vorher den SHA-256-Fingerabdruck des
  Gateway-Zertifikats; erst danach gehen Code bzw. Token über die Leitung (Token im ersten WebSocket-Frame, nicht im Header).
- **Token-Reichweite:** Rolle `device_agent` steht nicht in `permissions.yaml` → das Token öffnet nur die Agent-WebSocket,
  keine Chats, kein Gedächtnis, keinen Event-Stream. Widerruf in der App oder mit `jarvisctl devices revoke <id>` trennt sofort.
- **Befehle:** `device_*`-Werkzeuge laufen durch dieselbe Pipeline wie alle Werkzeuge (Risiko, Freigaben, Audit, Rate-Limits);
  Tippen, Klicken, Terminal und Claude Code sind `high`. Eingaben mit Zielprogramm prüfen den Vordergrund vorher.
- **Am Gerät:** nur freigegebene Ordner (Standard Benutzerordner; `.ssh`, `.gnupg`, die Agent-Konfiguration gesperrt, Symlinks
  zählen nicht als innen), Terminal abschaltbar, lokaler Not-Aus. Der Not-Aus des Core wird an alle Agents weitergegeben –
  dann nur noch lesende Aktionen. Windows: Fenster mit Administratorrechten nehmen keine Eingaben an (UIPI).
- **Entfernte Clients** (`clients.remote_api`): das Gateway liefert dann die Client-API, jede Anfrage braucht ein Geräte-Token;
  `/mcp`, Webhooks, Telefonie-Endpunkte und der OAuth-Callback bleiben dort gesperrt. Empfohlen nur über Tailscale.
- **Windows-Secrets:** Anmeldeinformationsverwaltung; Einträge über 1200 Zeichen werden in Teilen gespeichert.

## Not-Aus
- Desktop (immer sichtbar), Menüleiste, Telegram `/stop`, `jarvisctl killswitch on` oder `touch ~/Library/Application Support/Jarvis/KILL`.
- Wirkung: alle nicht-lesenden Tools gesperrt, Bridge verweigert Aktionen, laufende Coding-Jobs werden beendet, Agent-Lauf
  abgebrochen, Task-Engine und Scheduler pausieren. Aufheben nur durch den Besitzer am Desktop/CLI.

## Backups
- Täglich 03:30 (und beim Start nachgeholt): `pg_dump` + Vault + Konfiguration, AES-256-GCM (blockweise, Abschneiden wird
  erkannt), Schlüssel im Schlüsselbund (`backup-key`; `jarvisctl backup key` zum Ablegen im Passwortmanager). Zugangsdaten
  und Token-Dateien sind nie enthalten. Aufbewahrung: 14 neueste + 1 je Woche der letzten 8 Wochen. Wiederherstellung:
  `jarvisctl backup restore <Datei>` (entpackt nur in einen Ordner, überschreibt nichts Laufendes).

## Audit
- Append-only-Tabelle `audit_log` mit SHA-256-Hash-Kette (`GET /api/audit/verify` erkennt Manipulationen – getestet).
- Aktionen ab Risiko `high` schreiben **vorher** einen Eintrag (`outcome=started`); ist das Protokoll nicht beschreibbar,
  läuft die Aktion nicht. Getippter/eingefügter Text (`mac_type_text`, Zwischenablage) wird nur als Länge protokolliert.
- Protokolliert: Tool-Aufrufe (Argumente geschwärzt), Freigaben/Ablehnungen, Konfigurationsänderungen, Gerätekopplungen,
  Telefon-Authentifizierung, Gedächtnisänderungen, Not-Aus.

## Netzwerk & Webhooks
- REST-API: Bearer-Token, Rate-Limits pro IP, Login-Fehlversuchsbremse, Sicherheits-Header, CORS nur für die Desktop-App.
- Eingehende generische Webhooks: HMAC-SHA256 (`X-Jarvis-Signature: sha256=…`), Payload wird als nicht vertrauenswürdig behandelt.
- Für öffentliche Erreichbarkeit (Twilio) nur über einen TLS-Tunnel/Reverse-Proxy (z. B. Cloudflare Tunnel) – die Signaturprüfung
  nutzt `server.public_base_url`.

## Bekannte Grenzen
- Die Bestätigung von Freigaben am Handy (Fingerabdruck/PIN) ist ein App-seitiges Gate; der Geräteschlüssel selbst
  ist nicht biometrisch gebunden. Eine Härtung (separater Freigabe-Schlüssel mit `setUserAuthenticationRequired`)
  erfordert eine neue Kopplung und ist noch offen.
- Die Hash-Kette des Audits erkennt Änderungen einzelner Zeilen, aber nicht das komplette Neuberechnen durch jemanden
  mit Schreibzugriff auf die Datenbank.
- Der Coding-Runner läuft bewusst ohne Sandbox (`coding.sandbox: false`), weil Builds sonst scheitern – er arbeitet
  dann mit den Rechten von Sir.
- Ein 4B-Modell kann Tools falsch wählen; die Policy-Engine fängt Seiteneffekte ab (siehe PROGRESS.md, Beobachtungen).
- Die Bridge ist ad-hoc signiert: nach einem Neubau müssen macOS-Rechte ggf. neu erteilt werden.
- Bildschirmanalyse und Zwischenablage können fremde Inhalte enthalten → als untrusted markiert.
