# UpTik – Evaluation und Integration als Connector `uptik`

Stand: 2026-10-29. Ziel: Videos auf TikTok, Instagram Reels und optional YouTube Shorts veröffentlichen – sofort oder
geplant, mehrere Konten gleichzeitig – **ohne eigene Developer-Apps** und über die bestehende Connector-Verwaltung.

> **Risiko vorweg:** Browser-Automatisierung auf den Weboberflächen verstößt gegen die Nutzungsbedingungen von
> TikTok, Instagram/Meta und YouTube/Google und kann Konten einschränken oder sperren. Plattformänderungen an der
> Oberfläche können die Adapter jederzeit brechen. Bis heute wurde **nichts live veröffentlicht** (siehe unten).

## 1. Bestehende Jarvis-Architektur (Grundlage)

| Thema | Wo / wie | Genutzt für `uptik` |
|---|---|---|
| Connector-Registrierung | `connectors/base.py`: `Manifest` + `tools()` + `health()` + `start/stop`; `ConnectorManager` speichert `enabled` und Einstellungen in `connector_configs`, gated die Werkzeuge über `registry.set_connector_gate`; Registrierung in `app_state.py` | `UptikConnector` (`uptik/tools.py`), aus per Standard |
| Werkzeuge | `tools/registry.py` (`ToolSpec`, Risiko, `writes`, `prepare`, `summarize`, `private_args`) | 9 Werkzeuge `uptik_*` |
| Freigaben | `tools/executor.py` + `tools/approval_mode.py`: Risiko > Modus → `Approval`; `decide()` führt genau einmal aus; Idempotenz-Ledger, Audit, Not-Aus, Rate-Limits | Veröffentlichen/Planen/Konto verbinden = Risiko *high* |
| Secrets | `secrets.py` (`secret_store`: macOS-Schlüsselbund, Windows Credential Manager, Linux Secret Service, `JARVIS_SECRET_*`) | Schlüssel für die Sitzungsverschlüsselung |
| Events/Benachrichtigungen | `events.bus`, `jobs/notify.py` (Desktop, Handy, Telegram) | `uptik.updated`, `uptik.circuit`, Benachrichtigungen `uptik.*` |
| Core ↔ Apps | REST (`/api/...`) + WebSocket (`/ws`, Bus-Events); Handy über das Mobile-Gateway mit Allowlist (`mobile/gateway.py`) | Router `/api/uptik`, Allowlist-Einträge |
| Geräte | Device Agent (`device_agent/`), Aktionen in `protocol.py` (Core und Agent identisch, Vertragstest) | neue Aktion `browser.session` |
| UI | Desktop: `components/ui.tsx` (Button, Section, Tabs, Toggle, Field, Dialog …), Tokens in `index.css`; Handy: `ui/widgets/hud.dart`, `core/theme.dart` (`J.*`) | Ansicht `UptikView`, Screen `UptikScreen` – nur vorhandene Komponenten |
| Videoproduktion | `social/` (tägliches PJE-Video, Event `social.updated` mit `status=ready`) | Übergabe über `SocialBridge` (abonniert nur das Event) |

Der frühere Job „Social Media: tägliche Videoproduktion für PJE Systems“ (5ff0236c) ist bereits in `main`
(Commits aa3c9b6, 11b68a9); sein Branch existiert nicht mehr. Sein Autopost-Teil (`social/platforms.py`) nutzt die
**offiziellen APIs mit Developer-Apps** – genau das schließt Sir hier aus. Wiederverwendet wurde nur das Muster
(Connector je Plattform, `social_publish_video` mit Risiko *high*) und die Videobibliothek als Quelle der Übergabe;
`uptik` hängt nicht davon ab.

## 2. UpTik geprüft (nur gelesen, nichts ausgeführt)

Quelle: https://github.com/tymon5368/uptik (gelesen am 2026-10-29: README, LICENSE, `go.mod`, Ports, Queue, Pipeline,
Circuit-Breaker, TikTok-/YouTube-Uploader, Launcher, Installer-Skript). Das Installationsskript (`curl | bash`) und
keine Binärdatei wurden ausgeführt oder heruntergeladen.

* **Was es ist:** Go-Desktop-App (Wails v2, Svelte-5-Oberfläche, System-Tray, Autostart, Selbst-Updater) mit
  SQLite-Queue (modernc), die über das Chrome DevTools Protocol (go-rod) TikTok Studio, YouTube Studio und die Meta
  Business Suite (Facebook Reels) bedient. 21 Sterne, ein Autor, erster Commit 2026-09-11, letzter Push 2026-10-01,
  viele Meldungen auf Vietnamesisch.
* **Lizenz:** MIT (© 2026 Tymon) – Übernahme von Code oder Konzepten mit Hinweis erlaubt.
* **Plattformen:** TikTok, YouTube, Facebook. **Kein Instagram.**
* **Architektur:** hexagonal (`domain`, `ports`, `usecases`, `adapters`); `PlatformUploader`
  (`ID`, `LoginURL`, `CheckLogin`, `UploadVideo`); Outbox-Queue mit Lease, Wiederherstellung hängender Jobs auf
  `pending`; „Golden Hours“ 11:30/18:30/21:30; Selektor-Fallback-Listen; Circuit-Breaker als Stichwortliste.
* **Schnittstelle nach außen:** keine – keine API, kein CLI, kein IPC; nur die GUI-Bindings von Wails. Eine
  Einbindung als Bibliothek in den Python-Core wäre ein Go-Subprozess mit eigenem Protokoll.
* **Bedenkliche Teile (nicht übernommen):**
  * TikTok-Uploader **entfernt Captcha-/Verify-iframes aus der Seite** (`iframe[src*="verify"], .captcha-verify-container` → `remove()`) – Captcha-Umgehung.
  * Chrome-Start mit `--disable-blink-features=AutomationControlled` (versteckt das Automatisierungsmerkmal), `--no-sandbox`.
  * Akzeptiert JavaScript-Dialoge automatisch; Datei- und Zeitwahl über eingeschleustes JavaScript.
  * Der Circuit-Breaker ist nur ein Fehlertext: keine Kontosperre, keine Benachrichtigung, die übrigen Kanäle laufen weiter; keine Wiederholungen mit Backoff.
  * Installer entfernt das macOS-Quarantäne-Flag (`xattr -cr`); Selbst-Updater tauscht die eigene Binärdatei.
  * Marketing-Aussagen zum Vermeiden von Anti-Bot-Merkmalen werden nicht übernommen.

## 3. Entscheidung

**UpTik dient nur als Konzeptvorlage; Jarvis hat einen eigenen, wartbaren Adapter in Python.** Gründe: keine
nutzbare Schnittstelle (GUI-App ohne API), andere Sprache/Laufzeit, kein Instagram, sicherheitsrelevante Muster, die
gegen die harten Grenzen verstoßen, und sehr junges Ein-Personen-Projekt. Übernommene Konzepte (mit MIT-Hinweis in
`core/src/jarvis/uptik/THIRD_PARTY_NOTICE.md`): Uploader-Port, Outbox-Queue mit Wiederherstellung,
Selektor-Fallback-Listen, Circuit-Breaker-Idee (hier verschärft). Playwright ist bereits Abhängigkeit von Core und
Device Agent, also keine neue Laufzeit.

## 4. Was gebaut wurde

```
core/src/jarvis/uptik/
  model.py        Plattformen, Zustände, Fehlerklassen (wiederholbar / nicht wiederholbar / manuelle Prüfung)
  validation.py   Regeln je Plattform (Textlänge, Hashtags, Titel, Format, Größe, Dauer, Hochformat), ffprobe
  timing.py       Zeitpunkt in Europe/Berlin (Sommer-/Winterzeit), exponentielles Backoff mit Obergrenze
  circuit.py      Erkennung: Captcha, Checkpoint, 2FA/Telefon, Ratenlimit, HTTP 429, ungewöhnliche Aktivität
  sessions.py     Sitzungen AES-256-GCM verschlüsselt, Schlüssel im Schlüsselbund (HKDF je Konto)
  store.py        eigene SQLite-DB: Konten, Queue/Historie, Ereignisse, Eingang
  runners.py      Browser lokal (Playwright) oder auf Sirs Rechner (Device Agent, browser.session)
  adapters/       tiktok.py, instagram.py, youtube.py (BrowserSession-Schnittstelle, nur deklarative Schritte)
  service.py      Konten, Anmeldung, Vorbereitung, Fan-out, Worker, Retry, Circuit-Breaker, Übergabe
  handoff.py      VideoClip + SocialBridge (Übergabe aus der Videoproduktion)
  tools.py        Connector + Agent-Werkzeuge
  api.py          REST /api/uptik (Desktop, Browser-Client, Handy)
```

### Ablauf einer Veröffentlichung

1. **Vorbereiten** (`prepare`, vor der Freigabe): Video auflösen (nur Uploads, Eingang, Videobibliothek und
   `video_dirs`), Konten auflösen (verbunden, nicht angehalten), je Plattform validieren, Zeitpunkt prüfen.
2. **Freigabe**: `uptik_publish_now` / `uptik_schedule_post` haben Risiko *high*. Im Standardmodus fragt Jarvis
   immer; die Freigabe zeigt Video, Konten, Zeitpunkt und Text. Auch Autopost aus der Videoproduktion
   (Principal `system`) landet in der Freigabe.
3. **Einreihen**: eine Zeile je Konto (Fan-out), `run_at` = jetzt oder Zeitpunkt; dieselbe Freigabe/Übergabe reiht
   nie doppelt ein.
4. **Worker** (alle 10 s, nicht bei Not-Aus oder deaktiviertem Connector): je Konto ein Beitrag zur Zeit, bis zu
   `max_parallel` Konten gleichzeitig; Browser öffnen mit entschlüsselter Sitzung → Adapter → Sitzung wieder
   verschlüsselt speichern.
5. **Ergebnis**: veröffentlicht | wiederholbarer Fehler → `retry_wait` mit Backoff (60/120/240 s … bis 1 h, ±10 %,
   höchstens `max_attempts`) | nicht wiederholbar → fehlgeschlagen | **Hindernis → Circuit-Breaker** |
   kein Erfolgsnachweis nach dem letzten Klick → `needs_review` (nie automatisch wiederholt, Doppelpost-Gefahr).

### Circuit-Breaker (Pflicht)

Erkennt der Adapter vor oder nach jedem Schritt ein Hindernis (sichtbarer Text, Adresse, HTTP 429), klickt er
nichts mehr an, das Konto wird **angehalten** (`circuit_state = tripped`), alle wartenden Beiträge des Kontos werden
`blocked`, Sir bekommt eine Benachrichtigung (Stufe *error*), und es gibt **keinen Timer und keinen automatischen
Wiederholungsversuch**. Andere Konten laufen weiter. Freigeben kann nur Sir in der App (Desktop: „Konto freigeben“,
Handy: dasselbe), wahlweise mit „angehaltene Beiträge wieder einreihen“; es gibt dafür bewusst kein Agent-Werkzeug.
Eine abgelaufene Anmeldung stoppt das Konto ebenfalls; eine erfolgreiche Neuanmeldung hebt nur diesen Grund auf.

### Anmeldung und Sitzungen

`uptik_connect_account` (Freigabe) öffnet ein **sichtbares** Browserfenster mit der Anmeldeseite. Sir meldet sich
selbst an – Passwort, 2FA, Captcha erledigt er selbst; Jarvis sieht und speichert kein Passwort. Danach wird nur der
Storage-State (Cookies + Local Storage) verschlüsselt gespeichert (`<data_dir>/uptik/sessions/<konto>.session`, 0600;
Schlüssel `uptik-session-key` im Schlüsselbund). Jeder Lauf ist ein neuer, nicht persistenter Browser-Kontext: es
bleibt kein Profilordner mit Klartext-Cookies zurück. Das vorhandene Chrome-Profil des Besitzers wird bewusst **nicht**
ausgelesen (dafür müsste Chromes eigene Cookie-Verschlüsselung umgangen werden; außerdem ist das Profil gesperrt,
solange Chrome läuft).

### Keine Anti-Erkennung

Echtes installiertes Google Chrome (sonst Edge unter Windows bzw. Playwrights Chromium) mit Standardeinstellungen:
kein Stealth-Plugin, kein geänderter User-Agent oder Fingerprint, keine Proxys, kein Verstecken des
Automatisierungsmerkmals, keine simulierten „menschlichen“ Tippverzögerungen, keine Skript-Ausführung in der Seite.
Captchas und Prüfungen werden nie gelöst, entfernt oder weggeklickt; auch TikToks Inhaltsprüfung („Trotzdem posten?“)
wird nicht bestätigt (→ `needs_review`).

### Videoübergabe

Schmale Schnittstelle: `UptikService.on_video_ready(VideoClip(...))` – wirft nie, standardmäßig aus
(`handoff_enabled`). `SocialBridge` hängt sich an das bestehende Event `social.updated` (`status=ready`) und arbeitet
im Hintergrund; die Videoproduktion wurde nicht verändert und wird nie blockiert. Der Clip landet im **Eingang**; nur
für Konten mit eingeschaltetem **Autopost** (je Konto, Standard aus, Einschalten mit Rückfrage) wird eine
Veröffentlichung zur Freigabe vorgelegt – sofort oder zur Uhrzeit `handoff_publish_time` (Berlin). Captions je
Plattform kommen aus der Produktion (TikTok/Instagram-Text, YouTube-Titel/Beschreibung).

### Plattformen

| Ziel | Weg | Einstellungen | Grenzen (konservativ) |
|---|---|---|---|
| TikTok | TikTok Studio `tiktokstudio/upload` | Sichtbarkeit (alle/Freunde/nur ich), Kommentare, Duett, Stitch, KI-Kennzeichnung | Text+Hashtags ≤ 2200, ≤ 30 Hashtags, MP4/MOV/WebM, ≤ 2 GB, 3 s–10 min |
| Instagram Reels | instagram.com → Erstellen → Datei → Weiter → Weiter → Teilen | Kommentare aus, Like-Zahl verbergen | ≤ 2200, ≤ 30 Hashtags, MP4/MOV, ≤ 1 GB, 3 s–3 min |
| YouTube Shorts (optional, `youtube_shorts_enabled`) | YouTube Studio Upload-Dialog | Sichtbarkeit (öffentlich/nicht gelistet/privat), „speziell für Kinder“ | Titel 1–100 Zeichen ohne `<>`, Beschreibung ≤ 5000, ≤ 3 min, Hochformat/quadratisch |

YouTube Shorts ist ohne Developer-App über YouTube Studio im Browser machbar und deshalb eingebaut, aber
**standardmäßig aus** und als experimentell markiert (Google-Konten reagieren besonders empfindlich auf Automatisierung).
Einstellungen, die Sir wählt, werden geprüft gesetzt: Lässt sich eine nicht setzen, wird **nicht** veröffentlicht.

## 5. Wo der Browser läuft (macOS, Windows, Linux)

Jedes Konto hat einen Runner: `local` (Browser auf dem Rechner mit dem Core) oder `device` (Browser auf Sirs
Rechner über den Device Agent – nötig, wenn der Core auf einem Server ohne Bildschirm läuft).

| | Core lokal (`runner: local`) | Über den Device Agent (`runner: device`) |
|---|---|---|
| **macOS** | Chrome unter `/Applications/Google Chrome.app` (oder `~/Applications`), sonst Playwright-Chromium (`uv run playwright install chromium`) | `pip install 'jarvis-device[browser]'`, `playwright install chromium`; Chrome wird bevorzugt |
| **Windows** | `%ProgramFiles%\Google\Chrome\Application\chrome.exe` (auch `(x86)`, `%LocalAppData%`), sonst Microsoft Edge | wie links; Edge ist immer vorhanden |
| **Linux** | `google-chrome`, `google-chrome-stable`, `chromium` im `PATH`; braucht eine grafische Sitzung (X11/Wayland) für die Anmeldung | wie links; der Server-Core selbst braucht dann keinen Browser |

Ablauf mit Device Agent: Core prüft das Gerät (`browser.automation` verfügbar) → überträgt das Video über die
bestehende, TLS-gesicherte Agent-Verbindung mit SHA-256-Prüfung nach `device_staging_dir`
(Standard `~/Downloads/Jarvis-UpTik`, muss in den freigegebenen Ordnern des Agents liegen) → `browser.session open`
mit dem entschlüsselten Storage-State (nur im Speicher, nur über TLS) → dieselben deklarativen Schritte → Storage-State
zurück, im Core wieder verschlüsselt. Der Agent schreibt keine Sitzung auf die Platte und schließt untätige Fenster
nach 15 Minuten. Bei geplanten Beiträgen muss der Rechner mit dem Browser zum Zeitpunkt laufen; sonst wartet der
Beitrag mit Backoff.

Pfade und Schlüsselbund sind gekapselt: Datenordner über `config/platform/<os>.yaml`, Chrome-Suche in
`uptik/runners.py` (`chrome_candidates`) bzw. `device_agent/.../browser_session.py`, Secrets über `secret_store`.

## 6. Bedienung

* **Connectoren › UpTik Autopost**: aktivieren/deaktivieren, Einstellungen, „Konten und Warteschlange“.
* **Desktop › UpTik Autopost**: Konten (anlegen, anmelden, prüfen, abmelden, Autopost, angehaltene Konten
  freigeben), Veröffentlichen (Video aus dem Eingang oder hochladen, Text, Hashtags, Titel, Konten, Einstellungen,
  Zeitpunkt, „Prüfen“), Warteschlange (abbrechen), Historie (Link öffnen, unklare Beiträge abschließen),
  Einstellungen.
* **Handy › Social Media › Connector › „UpTik Autopost (Browser) öffnen“** (oder Deep Link `jarvis://uptik`): dieselben
  Funktionen über dieselben Endpunkte; Videos kommen aus dem Eingang (Hochladen vom Handy ist nicht eingebaut).
* **Agent**: `uptik_status`, `uptik_list_accounts`, `uptik_connect_account`, `uptik_publish_now`,
  `uptik_schedule_post`, `uptik_validate_post`, `uptik_list_queue`, `uptik_cancel_post`, `uptik_history`.

## 7. Tests

Alle automatisierten Tests laufen gegen einen Fake-Browser (`core/tests/unit/uptik/conftest.py`: `FakeWeb`,
`FakeRunner`, `FakeSession`); die echten Plattform-Adapter laufen darauf, keine Verbindung zu einer Plattform.
`core/tests/unit/uptik/`: Queue (Reihenfolge, Lease, Neustart), Retry, Zeitplanung (Sommer-/Winterzeit),
Validierung, Circuit-Breaker (Erkennung + Zustände), Sitzungsverschlüsselung, simulierte Anmeldung, Übergabe,
Fan-out sofort und geplant, Fehler mit Wiederholung, Captcha/429/Checkpoint, deaktivierter Connector, Not-Aus, REST-API,
Gateway-Allowlist. Dazu `desktop/src/lib/uptik.test.ts`, `apps/jarvis_mobile/test/uptik_test.dart`,
`device_agent/tests/test_browser_session.py`.

## 8. Nicht verifiziert / offen

* **Keine Live-Veröffentlichung** auf TikTok, Instagram oder YouTube, keine echte Anmeldung, kein echter Browserlauf
  der Adapter. Die Selektoren beruhen auf den aktuellen Weboberflächen und UpTiks Vorlage und sind ungeprüft.
* Für einen Live-Test braucht es Sirs Freigabe: ein **Testkonto** je Plattform (kein Geschäftskonto), Sichtbarkeit
  „nur ich“/„privat“, ein Testvideo, ein beobachteter Lauf am Desktop.
* Device-Agent-Weg (`browser.session`) nie auf echtem Windows/Linux gelaufen.
* Offene Entscheidungen für Sir: YouTube Shorts einschalten oder nicht; Testkonten für Live-Tests; ob Übergabe aus
  der Videoproduktion aktiv sein soll; Uhrzeit für übernommene Videos.
