# Anleitung: Zugangsdaten für J.A.R.V.I.S. einrichten

Schritt für Schritt für alle Dienste. Reihenfolge ist frei; jeder Dienst funktioniert unabhängig.

**So trägst du Zugangsdaten ein (für alle Dienste gleich):**
- **Desktop-App:** *Connectoren* › Karte des Dienstes › **Einrichten** › Felder ausfüllen › **Speichern** › **Testen**.
- **Oder Terminal:** `cd ~/Documents/Programmieren/Jarvis/core && uv run jarvisctl secret set <name>`
  (fragt den Wert verdeckt ab).
- Alles landet ausschließlich im **macOS-Schlüsselbund** (Dienst `jarvis`), nie in Dateien oder Logs.
- Nach dem Speichern: Connector mit dem Schalter **Aktiv** einschalten. Neue Werkzeuge stehen Jarvis sofort zur Verfügung.

> Sag mir Bescheid, sobald ein Dienst eingetragen ist – ich teste ihn dann live.

---

## 1. Lexware Office (Kunden, Rechnungsentwürfe)

**Voraussetzung:** Lexware-Office-Tarif mit Zugriff auf die Public API.

1. In Lexware Office anmelden: <https://app.lexware.de>
2. API-Schlüssel erzeugen: <https://app.lexware.de/addons/public-api>
   (Menü: *Einstellungen › Erweiterungen › Public API*). Datenschutzhinweis bestätigen, **Schlüssel erzeugen**, kopieren.
   Der Schlüssel wird nur einmal angezeigt.
3. In Jarvis eintragen: Connector **Lexware Office** › *Einrichten* › Feld **Lexware Public API Key**
   – oder `uv run jarvisctl secret set lexware-api-key`.
4. Optional in den Einstellungen des Connectors: `default_tax_rate` (19/7/0), `tax_type` (`net`, `gross`, `vatfree` – Kleinunternehmer:
   `vatfree`), `payment_term_days`.
5. **Aktiv** einschalten › **Testen** → zeigt „Verbunden: \<Firmenname\>“.

Hinweise: Die API erstellt Rechnungen als **Entwurf**; finalisieren geht nur in Lexware (Link wird mitgeliefert) oder als
„finale Rechnung“ (kritische Aktion, nur am Desktop freigebbar). E-Mail-Versand läuft über Gmail (Abschnitt 2).
Doku: <https://developers.lexware.io/docs/>

---

## 2. Google: Gmail, Kalender, Drive

Du legst einmalig ein eigenes OAuth-Projekt an (kostenlos).

1. Google Cloud Console öffnen: <https://console.cloud.google.com/> → oben **Projekt auswählen › Neues Projekt** (z. B. „Jarvis“).
2. Drei APIs aktivieren (jeweils **Aktivieren** klicken):
   - Gmail API: <https://console.cloud.google.com/apis/library/gmail.googleapis.com>
   - Google Calendar API: <https://console.cloud.google.com/apis/library/calendar-json.googleapis.com>
   - Google Drive API: <https://console.cloud.google.com/apis/library/drive.googleapis.com>
3. OAuth-Zustimmungsbildschirm einrichten: <https://console.cloud.google.com/auth/overview>
   - **Erste Schritte** › App-Name „Jarvis“, deine E-Mail als Support- und Kontaktadresse, Zielgruppe **Extern**.
   - Unter **Zielgruppe** deine eigene Gmail-Adresse als **Testnutzer** hinzufügen.
   - **Wichtig:** Im Status „Testing“ laufen Google-Autorisierungen nach **7 Tagen** ab. Für Dauerbetrieb unter *Zielgruppe*
     **App veröffentlichen** („In Produktion“). Für deine private Nutzung ist keine Google-Prüfung nötig; beim Verbinden erscheint
     dann einmalig der Hinweis „Google hat diese App nicht überprüft“ → *Erweitert › Weiter zu Jarvis*.
4. OAuth-Client erstellen: <https://console.cloud.google.com/auth/clients> › **Client erstellen**
   - Anwendungstyp **Desktop-App**, Name „Jarvis“ › **Erstellen** › **JSON herunterladen**.
5. In Jarvis eintragen: Connector **Google** › *Einrichten* › Feld **OAuth-Client (JSON)** → kompletten Inhalt der JSON-Datei einfügen
   – oder `uv run jarvisctl secret set google-oauth-client --from-file ~/Downloads/client_secret_….json`.
6. In der App **Mit Google verbinden** klicken → Browser öffnet sich → Google-Konto wählen → alle Berechtigungen bestätigen →
   „Google ist verbunden“.
7. **Aktiv** einschalten › **Testen** → „Verbunden als \<deine Adresse\>“.

Falls Google „redirect_uri_mismatch“ meldet: In Schritt 4 stattdessen Typ **Webanwendung** wählen und als autorisierte
Weiterleitungs-URI exakt `http://127.0.0.1:8765/api/connectors/google/oauth/callback` eintragen, JSON erneut herunterladen
und Schritt 5–6 wiederholen.

Genutzte Berechtigungen (minimal): Gmail lesen, Entwürfe, Senden (nur nach Freigabe), Kalendertermine, Drive lesen.

---

## 3. Telegram (Chat, Sprachnachrichten, Freigabe-Buttons)

1. In Telegram den offiziellen **@BotFather** öffnen: <https://t.me/BotFather>
2. `/newbot` senden → Anzeigename (z. B. „Jarvis“) → Benutzername, der auf `bot` endet (z. B. `paul_jarvis_bot`).
3. BotFather schickt den **Token** (Format `123456789:AA…`). Kopieren.
4. In Jarvis eintragen: Connector **Telegram** › *Einrichten* › **Bot-Token von @BotFather**
   – oder `uv run jarvisctl secret set telegram-bot-token`.
5. In der App beim Telegram-Connector **Koppeln** klicken → es erscheint ein 6-stelliger Code (10 Minuten gültig).
6. Deinem neuen Bot in Telegram schreiben: `/pair <Code>` → Antwort „Kopplung erfolgreich“.
   Nur dieser Chat wird danach akzeptiert; andere Absender werden ignoriert und protokolliert.
7. Testen: dem Bot „Wie spät ist es?“ schreiben. Befehle: `/status`, `/tasks`, `/new`, `/stop` (Not-Aus).

---

## 4. GitHub (Repos, Branches, Commits, Pull Requests, Issues)

1. Neuen Fine-grained Token anlegen: <https://github.com/settings/personal-access-tokens/new>
2. Name „Jarvis“, Ablaufdatum wählen (z. B. 90 Tage).
3. **Repository access:** „All repositories“ oder gezielt ausgewählte Repos.
4. **Permissions › Repository permissions:**
   - Contents: **Read and write**
   - Pull requests: **Read and write**
   - Issues: **Read and write**
   - Metadata: Read-only (automatisch)
5. **Generate token** → kopieren (beginnt mit `github_pat_`).
6. In Jarvis eintragen: Connector **GitHub** › *Einrichten* – oder `uv run jarvisctl secret set github-token`.
7. **Aktiv** einschalten › **Testen** → „Angemeldet als \<dein Login\>“.

Für `git push` aus Coding-Jobs nutzt git deine normalen Zugangsdaten (z. B. `gh auth login`: <https://cli.github.com/>).

---

## 4a. Vercel (Websites und Apps veröffentlichen)

1. Token anlegen: <https://vercel.com/account/settings/tokens> › **Create Token**.
   Name „Jarvis“, **Scope** = dein Account (oder das Team, in dem die Projekte liegen), Ablaufdatum wählen › kopieren.
2. In Jarvis eintragen: Connector **Vercel** › *Einrichten* › Feld **Vercel Access Token**
   – oder `uv run jarvisctl secret set vercel-token`.
   Auf einem Server ohne Schlüsselbund alternativ `JARVIS_SECRET_VERCEL_TOKEN` oder `VERCEL_TOKEN` in der Umgebung des Core.
3. Nur für Team-Projekte: **Team-ID** (vercel.com › Team › *Settings › General › Team ID*, beginnt mit `team_`) im Feld
   **Team-ID (optional)** – oder `uv run jarvisctl secret set vercel-team-id` bzw. `VERCEL_TEAM_ID`.
4. Für Deploys direkt aus GitHub der Vercel-GitHub-App Zugriff auf die Repos geben: <https://github.com/apps/vercel>
   (ohne sie lädt Jarvis lokale Projekte per Upload hoch; reine `owner/name`-Deploys brauchen die App).
5. **Aktiv** einschalten › **Testen** → „Angemeldet als \<dein Vercel-Name\>“.

Werkzeuge: `vercel_deploy` (Risikostufe *high*, also mit Freigabe), `vercel_list_projects`, `vercel_deployment_status`.
Geheime Umgebungsvariablen für Projekte am besten als `secret:<name>` übergeben – der Wert kommt dann aus dem Schlüsselbund
und läuft nicht durch den Chat. Coding-Jobs deployen nach dem Merge automatisch, wenn das Projekt eine `vercel.json` oder
einen Vercel-Link (`.vercel/project.json`) hat; abschaltbar in den Connector-Einstellungen (`deploy_after_merge`).

---

## 5. Fish Audio (Stimme von J.A.R.V.I.S.)

1. Konto anlegen/anmelden: <https://fish.audio>
2. API-Schlüssel erzeugen: <https://fish.audio/app/api-keys> › **Create New Key** › kopieren.
3. Stimme auswählen: in der Bibliothek <https://fish.audio/discovery> eine Stimme öffnen (oder eine eigene klonen).
   Die **Voice-ID** ist die Zeichenkette nach `/m/` in der Adresse, z. B. `https://fish.audio/m/ca3007f96ae7499ab87d27ea3599956a`
   → `ca3007f96ae7499ab87d27ea3599956a`.
4. In Jarvis eintragen: Connector **Fish Audio (Stimme)** › *Einrichten* › **Fish Audio API-Key** und Feld
   **reference_id** (= Voice-ID) ausfüllen, optional `model` (Standard `s1`) › **Speichern**.
5. **Aktiv** einschalten › **Testen** → „Stimme: \<Name der Stimme\>“. Ab sofort spricht Jarvis (Mac, Telefon) mit dieser Stimme;
   Systemstatus › *Sprachausgabe* zeigt „TTS aktiv: fish_audio“. Ist der Connector aus oder Fish Audio nicht erreichbar,
   spricht Jarvis automatisch mit der macOS-Stimme.

Preise/Kontingent: <https://fish.audio/plan/>

---

## 6. Telefonie mit Twilio (Anrufe an und von Jarvis)

Eigene, ausführliche Anleitung: **[ANLEITUNG-TWILIO.md](ANLEITUNG-TWILIO.md)**. Alles wird dort in der App eingetragen, ohne
Terminal und ohne eigene Domain; die Anleitung vergleicht auch kostenlose Alternativen (FRITZ!Box).

---

## 7. Optional

| Dienst | Wozu | Eintragen |
|---|---|---|
| Webhooks (eingehend) | Externe Dienste lösen Jarvis-Anweisungen aus | `uv run jarvisctl secret set webhook-signing-secret`, Routen im Connector *Webhooks* |
| HTTP API | Beliebige REST-APIs (Allowlist) | Connector *HTTP API* › `allowed_hosts`, optional `http-api-token` |
| Offizieller GitHub-MCP-Server | ~50 zusätzliche GitHub-Tools | `config/mcp_servers.yaml` › `github_official.enabled: true` (nutzt `github-token`) |

---

## Überblick: Schlüsselbund-Einträge

| Name | Dienst |
|---|---|
| `lexware-api-key` | Lexware Office |
| `google-oauth-client`, `google-oauth-token` (automatisch) | Google |
| `telegram-bot-token` | Telegram |
| `github-token` | GitHub |
| `vercel-token`, `vercel-team-id` (optional) | Vercel |
| `fish-audio-api-key` | Fish Audio |
| `twilio-account-sid`, `twilio-auth-token`, `phone-pin` | Telefonie |
| `webhook-signing-secret`, `http-api-token` | optional |

Status prüfen: `uv run jarvisctl secret status <name>` · löschen: `uv run jarvisctl secret delete <name>`
