# J.A.R.V.I.S. Mobile – Android-App

Begleit-App für den bestehenden Jarvis-Core: Jarvis anrufen, von Jarvis angerufen werden (echte Android-Systemanrufe),
Chat mit demselben Agenten, Benachrichtigungen mit Aktionen, Aufgaben/Freigaben, mobiles Gedächtnis.
Kein zweiter Agent, keine eigene Datenbank: Die App spricht ausschließlich mit dem Core (`/api/*`, `/ws`, LiveKit).

```
 Smartphone (Flutter UI + Kotlin)                          Mac (Jarvis Core)
 ┌────────────────────────────────┐   TLS, Zertifikat     ┌──────────────────────────────────────────────┐
 │ Flutter: Kern-Visualisierung,  │   per QR gepinnt      │ Mobile-Gateway :8767 (nur Mobile-Pfade)       │
 │ Chat, Aktivität, Gedächtnis,   │──────────────────────►│  ├ /api/mobile/* Kopplung, Tokens, Anrufe     │
 │ Anrufbildschirm, Einstellungen │   REST + /ws          │  ├ /api/chat, /api/tasks, /api/graph, /ws …   │
 │                                │                       │  └ /rtc → LiveKit-Signalisierung (Proxy)      │
 │ Kotlin: Keystore-Schlüssel,    │   wss://…/rtc         │ Core :8765 (127.0.0.1) – Orchestrator, Agent, │
 │ Tokens, Core-Telecom, CallStyle│──────────────────────►│  Gedächtnis, Werkzeuge, Freigaben             │
 │ LiveKit (nativ), FCM, Auto     │   WebRTC (DTLS-SRTP)  │ LiveKit-Server :7880 (lokal) UDP 50000–50100  │
 └───────────────▲────────────────┘◄─────────────────────►│ Jarvis-Sprachagent im Raum: Whisper → Claude  │
                 │ FCM (Inhalt AES-GCM verschlüsselt)     │  → Fish-Audio-Streaming (dieselbe Stimme)     │
                 └──────────── Google FCM ◄───────────────│ Benachrichtigungs- & Anruf-Engine, FCM HTTP v1│
                                                          └──────────────────────────────────────────────┘
```

## 1. Installation

APKs liegen nach dem Build in `apps/jarvis_mobile/dist/` (nicht im Git):

| Datei | Wofür |
|---|---|
| `jarvis-mobile-1.0.0-arm64-release.apk` (42 MB) | **empfohlen** – fast alle aktuellen Android-Telefone |
| `jarvis-mobile-1.0.0-universal-release.apk` (112 MB) | ältere 32-Bit-Geräte (armeabi-v7a) zusätzlich |
| `jarvis-mobile-1.0.0-arm64-debug.apk` (108 MB) | Debug-Build (Paket `systems.pje.jarvis.debug`, parallel installierbar) |

```bash
# Telefon per USB, Entwickleroptionen → USB-Debugging an
adb install -r apps/jarvis_mobile/dist/jarvis-mobile-1.0.0-arm64-release.apk
# neu bauen
cd apps/jarvis_mobile && flutter build apk --release --split-per-abi
```

Mindestversion Android 8.0 (API 26, wegen Core-Telecom), Ziel Android 16 (API 36).
Die Release-APKs sind derzeit mit dem Debug-Schlüssel signiert (siehe §12 für einen eigenen Schlüssel).

## 2. Kopplung

1. Core läuft (J.A.R.V.I.S.-App am Mac oder `scripts/start.sh`).
2. Am Mac: `cd core && uv run jarvisctl mobile-pair` → QR-Code im Terminal (10 Minuten gültig, einmalig).
3. App öffnen → **QR-Code scannen**. Alternativ den ausgegebenen Text unter „Kopplungstext einfügen“ einfügen
   oder als Link `jarvis://pair?p=<base64url>` öffnen (die App fragt vor dem Koppeln nach).
4. Danach führt die App durch die Berechtigungen (Benachrichtigungen, Mikrofon, Bluetooth, Vollbild-Anrufe, Akku).

Was passiert: Das Telefon erzeugt im **Android Keystore** (StrongBox, wenn vorhanden) einen EC-P-256-Schlüssel, der das
Gerät nie verlässt, und signiert damit die Kopplungsanfrage. Der Core legt ein Gerät mit der Rolle `mobile_owner` an,
erzeugt einen Geräteschlüssel für Ende-zu-Ende-verschlüsselte Pushes (AES-256, wird in den Keystore importiert) und
gibt ein Token-Paar aus. Geräte verwalten: App › Einstellungen › Gekoppelte Telefone oder `jarvisctl mobile-status`.

## 3. Firebase (für Benachrichtigungen und eingehende Anrufe)

Ohne Firebase funktionieren Chat, Aktivität, Gedächtnis und **Anrufe an Jarvis**. Damit **Jarvis anrufen** und
Benachrichtigungen schicken kann, braucht es FCM (kostenlos, Spark-Tarif genügt). Die APK enthält bewusst keine
`google-services.json`: Die App bekommt die (nicht geheime) Client-Konfiguration nach der Kopplung vom Core.

1. <https://console.firebase.google.com> → Projekt anlegen (Analytics nicht nötig).
2. Projekt › Android-App hinzufügen: Paketname **`systems.pje.jarvis`** (für den Debug-Build zusätzlich
   `systems.pje.jarvis.debug`). `google-services.json` herunterladen – daraus braucht man vier Werte.
3. `config/jarvis.yaml` › `mobile.firebase`:
   ```yaml
   firebase:
     project_id: "<project_info.project_id>"
     sender_id: "<project_info.project_number>"
     app_id: "<client[0].client_info.mobilesdk_app_id>"
     api_key: "<client[0].api_key[0].current_key>"
   ```
4. Projekteinstellungen › Dienstkonten › „Neuen privaten Schlüssel generieren“ → JSON-Datei, dann
   `uv run jarvisctl secret set fcm-service-account` und den **kompletten JSON-Inhalt** einfügen (liegt danach nur im
   Schlüsselbund; die Datei löschen).
5. Core neu starten, App öffnen → Einstellungen zeigt „Push: eingerichtet“ und „Push auf diesem Gerät: registriert“.
6. **Testbenachrichtigung** und **Testanruf** in den Einstellungen.

Push-Inhalte sind mit dem Geräteschlüssel AES-GCM-verschlüsselt (Google sieht nur Chiffretext), tragen eine ID und ein
Ablaufdatum; das Telefon verwirft Duplikate und abgelaufene Nachrichten. FCM stellt an ausgeschaltete Telefone zu,
sobald sie online sind (bis zur TTL: 24 h für Benachrichtigungen, Klingeldauer für Anrufe).

## 4. Netzwerk: LiveKit, LAN, Tailscale, VPS

Der Core startet und überwacht einen selbst gehosteten **LiveKit-Server** (`brew install livekit`, Open Source).

| Port | Bindung | Zweck |
|---|---|---|
| 8765/TCP | 127.0.0.1 | Core-API (unverändert, nur lokal) |
| 8767/TCP | 0.0.0.0 | **Mobile-Gateway** (TLS, nur Mobile-Pfade – alles andere 404) |
| 7880/TCP | 127.0.0.1 | LiveKit-Signalisierung, für das Telefon über `wss://<gateway>/rtc` getunnelt |
| 7881/TCP, 50000–50100/UDP | alle Schnittstellen | WebRTC-Medien (DTLS-SRTP, immer verschlüsselt) |

**Zuhause (WLAN):** funktioniert ohne Einrichtung. Die Adressen im QR-Code sind die LAN-IP und `<mac>.fritz.box`.
Ändert sich die IP, probiert die App alle Adressen; das Zertifikat bleibt gültig (gepinnt wird der Fingerabdruck,
nicht der Hostname).

**Unterwegs (mobile Daten):** Der Mac ist aus dem Internet nicht erreichbar. Empfohlen – kostenlos für Privatnutzer,
keine Portfreigaben:
1. Tailscale auf Mac und Telefon installieren und im selben Tailnet anmelden (Konto nötig → Ihre Entscheidung).
2. Core neu starten: Die Tailscale-IP (100.x.y.z) wird automatisch erkannt; neu koppeln oder in
   `mobile.gateway.advertise_urls` zusätzlich `https://100.x.y.z:8767` eintragen.
3. LiveKit bietet alle lokalen Schnittstellen als ICE-Kandidaten an – Medien laufen dann über das Tailnet.

Optional mit echtem Zertifikat: `tailscale serve --bg --https=443 https+insecure://localhost:8767` und
`mobile.gateway.tls: external`, `advertise_urls: ["https://<mac>.<tailnet>.ts.net"]`.

**VPS (später):** Gateway-Pfade hinter einen TLS-Reverse-Proxy (Caddy), `mobile.livekit.public_url` auf
`wss://livekit.<domain>` mit öffentlicher IP, `turn_enabled: true` (TURN/TLS über 443 für restriktive Netze, braucht
Domain + Zertifikat). Die Module sind getrennt konfigurierbar: Gateway, Push (FCM-Sender), LiveKit, Sprachagent und
Agent laufen im Core-Prozess, LiveKit als eigener Prozess (`manage_process: false` für einen externen Server).

**Mac schläft / ist aus:** Die App zeigt „OFFLINE – Der Mac schläft oder ist offline“, der Anrufknopf ist
ausgegraut und erklärt den Grund. Jarvis kann dann weder anrufen noch Pushes senden (der Push-Versand läuft im Core).
Für Erreichbarkeit rund um die Uhr müsste der Core auf einen VPS (siehe oben). Beim Wiederhochfahren schickt der Core
eine stille Meldung „J.A.R.V.I.S. ist wieder online“.

## 5. Android-Berechtigungen

| Berechtigung | Wofür | Hinweis |
|---|---|---|
| `POST_NOTIFICATIONS` | Benachrichtigungen, Anrufbenachrichtigung | Android 13+: fragt die App |
| `RECORD_AUDIO` | Gespräche, Diktat | |
| `MANAGE_OWN_CALLS` | selbstverwaltete VoIP-Anrufe über Telecom | automatisch |
| `FOREGROUND_SERVICE_PHONE_CALL` / `_MICROPHONE` | laufender Anruf im Hintergrund | Telecom delegiert den Vordergrunddienst |
| `USE_FULL_SCREEN_INTENT` | Anruf im Vollbild auf dem Sperrbildschirm | Android 14+: in den Einstellungen widerrufbar |
| `BLUETOOTH_CONNECT` | Namen von Headset/Auto in der Audioauswahl | |
| `CAMERA` | nur QR-Scan bei der Kopplung | |
| Akku-Optimierung | optional | bei Herstellern mit aggressivem Energiesparen (Xiaomi, Huawei …) |
| `SYSTEM_ALERT_WINDOW` | Sprachfenster nach „Hey Jarvis“ über anderen Apps öffnen | nur in den Einstellungen erteilbar; unnötig, wenn Jarvis Standard-Assistent ist (§14) |
| `RECEIVE_BOOT_COMPLETED` | Hinweis „Hey Jarvis wieder aktivieren“ nach Neustart | automatisch |

## 6. Anrufe

**Sir → Jarvis:** Anrufknopf (Startbildschirm, Gespräch, Benachrichtigungsaktion „Jarvis anrufen“, Android Auto).
Der Knopf ist nur aktiv, wenn der Core meldet, dass er tatsächlich antworten kann (`/api/mobile/calls/availability`:
LiveKit läuft, Agent verfügbar, Whisper erreichbar, kein Not-Aus, kein anderes Gespräch).

**Jarvis → Sir:** nur
- auf ausdrücklichen Auftrag: „Ruf mich an, sobald die Website fertig ist“ → der Agent startet den Auftrag und merkt
  mit `mobile_call_when` einen Rückruf für genau diesen Coding-Job/diese Aufgabe vor (sichtbar in der App unter
  Aktivität › Läuft; ein Zeitpunkt geht auch). „Ruf mich jetzt an“ → `mobile_call_owner`.
- oder wenn Sie eine Kategorie in den Einstellungen auf **Anruf** stellen (Standard: alle Push, System still).

Die Engine (`core/src/jarvis/mobile/alerts.py`) entscheidet je Ereignis: nichts / still / Push / Anruf.
Garantien: höchstens **ein Anruf pro Ereignis**, max. 4 Anrufe/Stunde, 3 min Abstand, Ruhezeit (Push → still,
Anruf → Push; Kritisches darf durch, abschaltbar), Duplikate (gleicher Ereignisschlüssel in 24 h) werden verworfen.
Kann nicht angerufen werden (Agent/LiveKit/Push fehlt, Gerät hat keinen Push-Token, Gespräch läuft), kommt eine
Benachrichtigung mit Begründung statt eines Anrufs, der nicht zustande käme. Verpasst/abgelehnt → die Nachricht folgt
als Benachrichtigung („Verpasster Anruf von J.A.R.V.I.S.“) – nie ein zweiter Anruf. Klingeldauer 45 s.

Im Gespräch: Jarvis begrüßt **einmal** kurz mit Tageszeit + Anlass („Guten Abend, Sir. Die Website ist fertig.“; ohne
Statusbericht, eine Begrüßung in der Nachricht wird nicht verdoppelt, `persona.call_opening`). Aus dem laufenden
Gespräch heraus kann Jarvis nicht erneut anrufen (`mobile_call_owner` wird dort abgewiesen), eigene, über den
Lautsprecher zurückgehörte Sätze beantwortet er nicht, und ein „ruf mich an, sobald …“-Auftrag löst genau einmal aus.
Die Begrüßung ist
vorab synthetisiert (≈1,3–1,6 s nach Gesprächsbeginn hörbar). Danach normale Unterhaltung mit demselben Agenten,
Gedächtnis und denselben Werkzeugen; echte Werkzeug-/Gedächtnis-Ereignisse erscheinen als Hinweis („Prüfe den
Kalender …“). Unterbrechen per Stimme funktioniert (das Telefon entfernt Jarvis' Stimme per Echounterdrückung).
„Tschüss“/„leg auf“ beendet das Gespräch. Max. 30 Minuten. Transkripte: App › Aktivität, `GET /api/mobile/calls`.

Worauf Sir antwortet: Die Frage, mit der Jarvis angerufen hat (Nachricht, Auslöser, Task-/Job-/Freigabe-ID), wird mit
dem Anruf gespeichert (`CallSession.context`, `core/src/jarvis/mobile/call_context.py`) und jeder Gesprächsrunde als
Kontext mitgegeben. Ein bloßes „ja“ ohne offene Frage führt keine Aktion aus; Jarvis fragt dann konkret nach.

Anrufsignalisierung: FCM-Push `call` → `CallService` registriert den Anruf über **Jetpack Core-Telecom
(`CallsManager.addCall`)** → System-Klingeln, CallStyle-Benachrichtigung (Annehmen/Ablehnen), Vollbild auf dem
Sperrbildschirm, Steuerung durch Bluetooth-Headset/Uhr/Auto (Telecom-Callbacks), Audio-Routing über Telecom
(Hörer/Lautsprecher/Bluetooth), Halten bei einem Mobilfunkanruf. Audio nativ über das LiveKit-Android-SDK mit
`NoAudioHandler` (Telecom besitzt Audiofokus und Routing, wie von Google gefordert).

## 7. Benachrichtigungen

Kategorien = Android-Kanäle (einzeln in den Android-Einstellungen anpassbar): Aufgaben, Termine, Kunden, Fehler,
Freigaben, Erinnerungen, Systemstatus, Still (Ruhezeit), Eingehende/Laufende Anrufe. Gruppiert je Kategorie.

| Aktion | Wirkung | Sicherheit |
|---|---|---|
| Öffnen / Tippen | Deep Link auf die Detailansicht (`jarvis://job/<id>` Coding-Job, `…/task/<id>`, `…/approval/<id>`, `…/call/<id>`, `…/chat/<id>`); der Push trägt dafür `link`, `target_type`, `target_id`. Ohne Ziel (z. B. Systemstatus, Termin) öffnet die Aktivität. Beim Kaltstart wartet der Link, bis die Kopplung geladen ist. | |
| Genehmigen | öffnet die Freigabe → Fingerabdruck/PIN → signierte Anfrage | Gerätesignatur + Nonce + Zeitstempel, serverseitig geprüft; **kritische Aktionen nur am Mac** |
| Ablehnen | direkt aus der Benachrichtigung | signiert mit dem Geräteschlüssel |
| Später erinnern | in 30 min erneut (als Erinnerung) | |
| Antworten | Inline-Antwort geht als Nachricht an Jarvis | |
| Jarvis anrufen | startet einen Anruf | |

Quellen: Aufgaben/Coding-Jobs fertig oder fehlgeschlagen, Angaben benötigt, Freigaben, kritische Systemereignisse
(Regeln in `notifications.rules`, Kanal `mobile`), anstehende Kalendertermine (Google-Connector, Vorlauf 30 min),
„Jarvis wieder online“, sowie der Agent selbst (`mobile_notify`, z. B. für Kundenanfragen). Für „Neue Kundenanfrage“
gibt es im Core noch kein eigenes Ereignis – der Agent kann sie melden, ein automatischer Mail-Watcher fehlt.

### Standortfreigabe

Standardmäßig aus. Einschalten in der App unter **Einstellungen › Standortfreigabe** („Standort mit Jarvis teilen“);
die App fragt dabei nach dem Ortungsrecht und danach nach „Immer zulassen“. Ist die Freigabe an, sendet das Handy
Breiten-/Längengrad, Genauigkeit und Messzeitpunkt (`PUT /api/mobile/location`, gepinntes TLS, Zugriffstoken)
- beim Einschalten, bei jedem Öffnen der App und über „Jetzt senden“,
- wenn Jarvis fragt: Das Werkzeug `mobile_get_location` schickt bei einem Standort älter als 2 min einen
  verschlüsselten Push `location_request` (ohne Inhalt) und wartet bis zu 12 s auf die Antwort. Bei geschlossener App
  geht das nur mit „Immer zulassen“.

Der Core hält **nur den letzten Standort im Arbeitsspeicher** (keine Historie, keine Datenbank, kein Log-/Audit-Eintrag
mit Koordinaten; nach einem Neustart ist er weg). Ausschalten in der App, Abmelden und Entfernen des Geräts löschen
ihn (`DELETE /api/mobile/location`; ist Jarvis gerade nicht erreichbar, beim nächsten Öffnen der App). Ortung über
den Android-`LocationManager`, keine Google-Play-Dienste. `mobile_get_location` liefert den Standort mit Alter in
Minuten und einen Hinweis, wenn er älter als 10 min ist oder fehlt. Eine Fahrzeitberechnung gibt es nicht: Im Projekt
ist kein Routendienst vorhanden, der ohne zusätzliche Zugangsdaten nutzbar wäre, und jeder externe Dienst würde den
Standort an Dritte geben.

### Handy-Steuerung durch Jarvis (`phone_*`)

Jarvis bedient das Handy wie den Mac (`mac_*`): „Jarvis, öffne WhatsApp auf meinem Handy“. Der System-Prompt
(`agent/persona.py`) sagt ihm, bei „auf meinem Handy“ ausschließlich `phone_*` zu benutzen.

| Werkzeug | Zweck | Risiko |
|---|---|---|
| `phone_list_apps` | installierte, startbare Apps (Name + Package) | read |
| `phone_open_app` | App per Launch-Intent starten; Name unscharf aufgelöst („WhatsApp“ → `com.whatsapp`) | low |
| `phone_ui_tree` | Bildschirm lesen (Text, Beschreibung, Klickbarkeit, Bounds, `element_id`) | read (Ergebnis gilt als nicht vertrauenswürdig) |
| `phone_screenshot` | Screenshot via `AccessibilityService.takeScreenshot` (Android 11+, ohne MediaProjection) + Bildanalyse | low |
| `phone_scroll` | scrollen (up/down/left/right) | low |
| `phone_tap` | Element (`element_id`) oder Position (x/y) antippen | **high** (Freigabe) |
| `phone_type_text` | Text in ein Eingabefeld schreiben | **high** (Freigabe) |
| `phone_key` | back / home / recents / notifications | **high** (Freigabe) |

**Weg der Befehle (derselbe Rückkanal wie bei der Standortabfrage, kein zweiter Mechanismus):** Der Core schickt einen
Ende-zu-Ende-verschlüsselten Push `phone_command` (`cmd_id`, `action`, `args`); die App führt ihn aus
(`phone/PhoneControl.kt`, `phone/PhoneControlService.kt`) und antwortet per `POST /api/mobile/phone/result`
(gepinntes TLS, Zugriffstoken). Der Core nimmt nur eine Antwort von einem Handy an, an das der Befehl ging, und nur
einmal. Befehle laufen nacheinander. Bildschirminhalte werden nirgends geloggt oder in der Datenbank gespeichert.

**Ehrliche Fehlermeldungen:** Kein gekoppeltes Handy / Push fehlt → „Handy nicht verbunden …“; keine Antwort in
25 s → „Handy nicht erreichbar …“; Dienst aus → „Bedienungshilfe in der Jarvis-App nicht aktiviert …“.

**Passwortfelder** (`isPassword` bzw. Passwort-Eingabetyp) werden nie ausgelesen (im Baum nur `password: true`, ohne
Text/Beschreibung) und nie befüllt (`password_field`). `element_id`s sind Pfade im Bildschirmbaum mit Fingerabdruck;
hat sich der Bildschirm geändert, meldet die App `stale_element`, statt etwas Falsches anzutippen.

**Einrichtung auf dem Handy (einmalig):** App neu installieren/aktualisieren → **Einstellungen › Handy-Steuerung durch
Jarvis › „Bedienungshilfen öffnen“** → „J.A.R.V.I.S.“ › „Handy-Steuerung durch Jarvis“ einschalten. Bei einer
Sideload-Installation (nicht aus dem Play Store) ist der Schalter ab Android 13 ausgegraut: App-Info › ⋮ ›
„Eingeschränkte Einstellungen zulassen“, dann erneut einschalten. Die Zeile in der App zeigt „aktiv/inaktiv“.

**Grenzen:** `phone_open_app` und `phone_list_apps` brauchen die Bedienungshilfe nicht, aber Android kann App-Starts aus
dem Hintergrund blockieren; mit aktivem Dienst prüft die App nach dem Start, ob die App im Vordergrund ist
(`foreground`). Bei gesperrtem Bildschirm, Apps mit `FLAG_SECURE` (Screenshot) oder Apps, die Bedienungshilfen aussperren
(manche Banking-Apps), liefert die Steuerung nichts bzw. einen Fehler. Texte sind auf 600 Zeichen je Befehl begrenzt
(Größe des Push-Kanals).

## 8. Sicherheit (Kurzfassung – Details in SECURITY.md › Smartphone)

- Kopplung per Einmalcode im QR; Gerätebindung durch Keystore-Schlüssel (nicht exportierbar).
- Zugriffstoken 10 min (HMAC), Refresh-Token rotierend, Wiederverwendung eines alten Refresh-Tokens widerruft die
  ganze Sitzungsfamilie; Erneuerung nur mit Gerätesignatur; Widerruf in der App oder am Mac wirkt sofort.
- TLS mit Zertifikats-Pinning; App erlaubt keinen Klartext (`network_security_config`), keine Backups der Zugangsdaten.
- Gateway-Listener liefert nur Mobile-Pfade aus (Allowlist), Tunnel-Verkehr nie; Freigaben nur signiert über
  `/api/mobile/approvals`. Keine Business-API-Schlüssel in der App – alles läuft über den Core.
- Sensible Erinnerungen sind auf dem Telefon nicht einsehbar; Live-Ereignisse an das Telefon ohne Titel aus Abgleichen.

## 9. Android Auto – was offiziell geht (Stand Oktober 2026, geprüft)

Quellen: <https://developer.android.com/training/cars/communication/calling>,
<https://developer.android.com/develop/connectivity/telecom/voip-app/telecom>.

- Drittanbieter-Telefonie auf Android Auto ist bei Google **Beta**. Voraussetzungen: Integration über die
  **Telecom-Jetpack-Bibliothek** inkl. der Remote-Surface-Callbacks (`onAnswer`, `onDisconnect`, `onSetActive`,
  `onSetInactive`), dauerhaft (nicht nur im Auto), plus ein **`CarAppService` mit Kategorie
  `androidx.car.app.category.CALLING`**. Während eines Gesprächs zeigt Android Auto **seine eigene In-Call-Ansicht**.
- Verteilung: Apps mit Calling-Kategorie dürfen bei Google Play nur in **interne/geschlossene Tests**; Produktion
  ist noch nicht erlaubt (Early-Access per Nominierung). Seitengeladene Apps erscheinen in Android Auto nur mit
  aktivierten Entwicklereinstellungen › „Unbekannte Quellen“.
- Umgesetzt: Core-Telecom mit allen vier Callbacks (jeder Anruf, immer), `JarvisCarAppService` (CALLING) mit
  Vorlagen-Bildschirm „J.A.R.V.I.S. anrufen“ / Status / Auflegen, `automotive_app_desc.xml`.
- Unabhängig von der Auto-Oberfläche: Weil der Anruf über Telecom läuft, behandelt Android ihn wie ein Telefonat –
  Audio über die per Bluetooth verbundene Freisprecheinrichtung (HFP), Annehmen/Auflegen über Lenkradtasten, soweit
  Fahrzeug und Telefon das für selbstverwaltete Anrufe unterstützen. Das ist die sichere Rückfallebene.
- **Nicht getestet** (kein Fahrzeug, kein Android-Auto-Konto auf dem Testgerät, Desktop Head Unit braucht die
  Android-Auto-App auf dem Telefon). Ob ein eingehender Jarvis-Anruf auf dem Fahrzeugdisplay erscheint, ist daher
  **nicht belegt** – laut Doku wird es bei Beta-Freischaltung über die Telecom-Integration angezeigt.

So testen Sie es: Android-Auto-App › Version 10× antippen → Entwicklereinstellungen → „Unbekannte Quellen“ an;
Desktop Head Unit: `sdkmanager "extras;google;auto"`, auf dem Telefon „Head Unit Server starten“,
`adb forward tcp:5277 tcp:5277 && $ANDROID_HOME/extras/google/auto/desktop-head-unit`.

## 10. Testmatrix (ehrlich)

Legende: ✅ automatisch getestet · 📱 auf Gerät getestet (**Android-Emulator, Android 16 / API 36.1 mit Google Play
Services** – kein physisches Telefon war angeschlossen) · 🚗 Android Auto · ⬜ noch nicht getestet · ⛔ plattformbedingt
nicht möglich

| Funktion | Status | Nachweis / Anmerkung |
|---|---|---|
| Kopplung (QR-Inhalt/Link, Keystore-Schlüssel, Signatur) | ✅ 📱 | Integrationstest + Emulator über das TLS-Gateway |
| Token-Rotation, Wiederverwendung → Familienwiderruf, Widerruf | ✅ | `test_mobile_auth.py` (fand einen echten Rollback-Fehler, behoben) |
| Gerätesignatur, Replay (Nonce/Zeit), Rollen-Manipulation | ✅ | `test_mobile_security.py` |
| Zertifikats-Pinning (Kotlin), SHA-256 identisch zum Core | ✅ | `NativeSecurityTest` (JVM) |
| Gateway-Allowlist / Tunnel-Sperre | ✅ | 14 Pfade + Guard-Test |
| Push-Verschlüsselung (AES-GCM, an Gerät gebunden) | ✅ | Unit-Test; Entschlüsselung im Keystore ⬜ (ohne FCM) |
| FCM-Zustellung (App offen/Hintergrund/beendet) | ⬜ | Firebase-Projekt fehlt |
| Benachrichtigung rendern, Kanäle, Gruppen, Aktionen | 📱 | über Test-Hook (gleicher Codepfad wie FCM) |
| „Später erinnern“ / „Antworten“ / Ablehnen-Aktion | 📱 / 📱 / ⬜ | Snooze + Antwort im Core angekommen |
| Freigabe mit Biometrie + Signatur | ⬜ | Emulator ohne Fingerabdruck; Server-Prüfung ✅ |
| Engine: Dedupe, Ruhezeit, Anruf-Fallback, verpasst → Push, Snooze | ✅ | Unit- + Integrationstests |
| Jarvis anrufen (Telecom + LiveKit nativ + Begrüßung) | ✅ 📱 | E2E-Test (Sprache rein/raus) + Emulator verbunden, Begrüßung + Transkript |
| Gesprächsfrage → Antwort mit Audio | ✅ | E2E: „3 plus 4“ → „… 7, Sir“; erster Ton 3,6 s (einmal 8,9 s) nach Frageende |
| Eingehender Anruf: App offen / Sperrbildschirm / App beendet | 📱 / 📱 / 📱 | Test-Hook statt FCM; „beendet“ mit FCM-gleicher Ausnahme (`deviceidle tempwhitelist`) |
| CallStyle-Benachrichtigung Annehmen/Ablehnen | 📱 | |
| Ablehnen, Verpasst (45 s) + Folgebenachrichtigung, Auflegen in der Begrüßung | 📱 | Status/Transkript im Core geprüft |
| Lautsprecher/Hörer/Bluetooth-Auswahl | 📱 / ⬜ / ⬜ | Emulator kennt nur „Speaker“ |
| Stummschalten | ⬜ | implementiert (LiveKit-Mikro + Telecom-Mute-Sync) |
| Mikrofon des Telefons im Gespräch | ✅ / ⬜ | E2E mit simuliertem Telefon; echtes Gerätemikro ungetestet |
| Barge-in per Stimme | ✅ / ⬜ | Unit-Tests (Wörter des Unterbrechers bleiben erhalten, Klick und stetiges Rest-Echo lösen nichts aus); Echo-/Rauschunterdrückung des Telefon-Mikros ist im LiveKit-Track ausdrücklich eingeschaltet; im E2E/auf dem Gerät nicht ausgelöst |
| Anrufbildschirm ohne Overflow (kleine Displays, große Systemschrift, Querformat) | ✅ | Widget-Tests mit Stopp-Button in drei Bildschirmgrößen |
| Stopp-Button im Anruf (`POST /api/mobile/calls/{id}/interrupt`) | ✅ / ⬜ | Unit-Test Core + Widget-Test; auf dem Gerät nicht probiert |
| „Stopp“ per Stimme (Anruf, Overlay, Mac): Jarvis schweigt, Gespräch bleibt offen | ✅ | Unit-Tests; „Gespräch beenden“/„Tschüss“ legen weiterhin auf |
| Einstellung „Jarvis ins Wort fallen“ (Standard an; Overlay + Anruf) | ✅ / ⬜ | Widget-Test, Core-Unit-Test (Anruf: aus → keine Unterbrechung, Stopp-Button geht); Gerät nicht probiert |
| Overlay („Hey Jarvis“): Stopp-Button, Barge-in während des Vorlesens | ⬜ | `BargeInDetectorTest` (Kotlin) geschrieben; Kotlin in der Entstehungssitzung nicht gebaut/ausgeführt |
| Halten bei Mobilfunkanruf | ⬜ | implementiert (`onSetInactive`) |
| Netzwechsel WLAN ↔ mobil / Reconnect | ⬜ | LiveKit-Reconnect + Adress-Neuauswahl implementiert |
| Unterwegs über Tailscale | ⬜ | Tailscale nicht installiert |
| Chat (Streaming über `/ws`), Verlauf geräteübergreifend | 📱 | |
| Diktat (Whisper) | ⬜ | Emulator ohne Mikrofon-Freigabe |
| Aktivität, Gedächtnis-Graph, Einstellungen | 📱 | echte Daten |
| Live-Hervorhebung bei Gedächtnisabruf | ✅ / ⬜ | Reducer-Tests; visuell nicht provoziert |
| Offline-Anzeige | ✅ | Reducer-Test; auf Gerät nicht provoziert |
| Android Auto Anzeige/Lenkrad | ⬜ 🚗 | siehe §9 |
| Play-Production mit Calling-Kategorie | ⛔ | Google erlaubt derzeit nur interne/geschlossene Tests |
| „Hey Jarvis“ (sherpa-onnx), Sprachfenster, Standard-Assistent | ⚠️ | Erkennung gebaut und am 19.10.2026 auf dem Galaxy S24 Ultra getestet (Test-Ton ausgelöst, Mikrofon frei, Wiederaufnahme); Sprachfenster/Assistent-Geste noch nicht per Gerät geprüft – siehe §14 › Stand |
| Sprachaufbereitung fürs Vorlesen (Markdown/Links) | ✅ | `AssistSpeechTest` (JVM) |

Testsummen (2026-10-17): Core 108 Unit + 46 Integration + E2E (Sprache, Mobile-Anruf), Flutter 10, Kotlin 5.

## 11. Fehlerbehebung

| Symptom | Ursache / Lösung |
|---|---|
| „Jarvis ist nicht erreichbar“ | Mac schläft/aus, anderes WLAN, Gateway blockiert. `jarvisctl mobile-status` (gateway.running), `curl -k https://<ip>:8767/api/health` |
| Kopplung „Kopplungscode ungültig“ | Code abgelaufen (10 min) oder schon benutzt → `jarvisctl mobile-pair` neu |
| „Gerätesignatur ungültig“ beim Refresh | App neu installiert (Keystore-Schlüssel weg) → neu koppeln |
| Anruf verbindet nicht („Audioverbindung fehlgeschlagen“) | UDP 50000–50100/TCP 7881 blockiert (Firewall/Gäste-WLAN); `~/Library/Logs/Jarvis/livekit.log` |
| Jarvis ruft nicht an | Einstellungen › „Jarvis ruft an“ nennt den Grund (Push, Agent, LiveKit, Limit, Ruhezeit) |
| Klingelt nicht auf dem Sperrbildschirm | Android 14+: Einstellungen › Apps › J.A.R.V.I.S. › Vollbild-Benachrichtigungen erlauben |
| Pushes kommen verspätet | Akku-Optimierung für die App ausschalten (Herstellerabhängig) |
| Chat-Antwort fehlt in der App, am Mac sichtbar | Seit 2026-10-21 behoben: Nach 2 h Pause (`session.idle_timeout_minutes`) antwortet der Core in einer neuen Sitzung – die App wechselt jetzt dorthin; ein still abgerissener `/ws` wird nach spätestens ~60 s (bzw. 5 s nach dem Öffnen der App) ersetzt und der Verlauf nachgeladen. Antworten auf eine Benachrichtigung kommen als Benachrichtigung „Antwort von Jarvis“ zurück (Tippen öffnet das Gespräch) |
| Anruf ohne Ton in der Begrüßung | Fish Audio prüfen (`/api/system/status` › tts); Fallback ist die macOS-Stimme |
| Gerät verloren | Mac: App-Einstellungen oder `DELETE /api/mobile/devices/<id>` – Tokens sofort ungültig |

Gerätetest ohne Firebase (nur Entwickler, Mac + adb):
```bash
T=$(cat ~/Library/Application\ Support/Jarvis/desktop.token)
CID=$(curl -s -X POST -H "Authorization: Bearer $T" http://127.0.0.1:8765/api/mobile/test/ring-local | jq -r .call_id)
adb shell am broadcast -a systems.pje.jarvis.DEV_PUSH -n systems.pje.jarvis/systems.pje.jarvis_mobile.push.DevPushReceiver \
  --es payload "'{\"type\":\"call\",\"call_id\":\"$CID\",\"subject\":\"Test\",\"ring_timeout_s\":45}'"
```
Der Empfänger ist durch `android.permission.DUMP` geschützt (nur System/adb-Shell), der Endpunkt nur für das
Owner-Gerät über Loopback erreichbar.

## 12. Release-Build

```bash
keytool -genkeypair -v -keystore ~/jarvis-release.jks -keyalg RSA -keysize 4096 -validity 10000 -alias jarvis
cat > apps/jarvis_mobile/android/key.properties <<EOF
storeFile=/Users/<sie>/jarvis-release.jks
storePassword=…
keyAlias=jarvis
keyPassword=…
EOF
cd apps/jarvis_mobile && flutter build apk --release --split-per-abi   # oder: flutter build appbundle
```
`key.properties` und `*.jks` sind in `.gitignore`. Release: R8-Minify + Ressourcen-Shrinking, Proguard-Regeln für
LiveKit/WebRTC und die Car App Library. Wechsel des Signierschlüssels = Neuinstallation = neu koppeln.
Im Alltag baut der Core die Release-APK selbst und verteilt sie – siehe §15 App-Updates. `key.properties` ist nur
noch für Builds von Hand nötig.

## 13. Bekannte Grenzen

- Kein physisches Telefon und kein Fahrzeug getestet (siehe Matrix); FCM ohne Firebase-Projekt ungetestet.
- Bild-Anhänge im Chat: das aktive Gehirn (Claude Code CLI) nimmt im Core keine Bilder entgegen → kein Anhang-Knopf.
- Rive wurde nicht verwendet (keine passenden Assets); der Kern ist ein eigener Fragment-Shader (Impeller) mit
  adaptiver Qualität, Reduced-Motion-Unterstützung und CustomPainter-Rückfall.
- Erste Graph-Anordnung auf sehr langsamen Geräten dauert einige Sekunden (läuft im Hintergrund-Isolate).
- Mehrere Telefone: ein Anruf klingelt auf allen, das erste nimmt an, die anderen hören auf (implementiert, nicht
  mit zwei Geräten getestet).
- „Hey Jarvis“: siehe §14 › Grenzen.

## 14. „Hey Jarvis“ – Sprachassistent wie Google Assistant

Sir sagt **„Hey Jarvis“** → über dem aktuellen Bildschirm öffnet sich unten ein Sprachfenster („Ja, Sir?“, kurzer Ton)
→ Frage sprechen → Jarvis zeigt die Antwort und liest sie vor. Fragt Jarvis zurück, hört das Fenster direkt weiter.
Dasselbe Fenster öffnet die Assistent-Geste, wenn J.A.R.V.I.S. Standard-Assistent ist.

```
 sherpa-onnx Keyword-Spotter (on-device, Foreground-Service „microphone“)
   └─ Wake-Word ─► Standard-Assistent? ─ ja ─► VoiceInteractionSession (Systemfenster, ohne Zusatzrecht)
                                       └ nein ► AssistOverlayActivity (braucht „Über anderen Apps einblenden“,
                                                 sonst Heads-up-/Vollbild-Benachrichtigung „Ja, Sir?“ zum Antippen)
 Sprachfenster: Mikrofon (16 kHz, Pausenerkennung) → /api/voice/stt (Whisper im Core)
               → /api/chat {channel: mobile, wait: true} (derselbe Agent, dieselbe Sitzung 10 min lang)
               → Text anzeigen + Android-TTS (Deutsch, auf dem Gerät) · „In der App“ öffnet die Unterhaltung
```

Beide Core-Pfade stehen schon auf der Gateway-Allowlist – am Core ändert sich nichts.

### Erkennung: sherpa-onnx, ohne Konto und Schlüssel

Die Wake-Word-Erkennung ist komplett offen und kostenlos: der **sherpa-onnx Keyword-Spotter** (Apache-2.0) mit einem
kleinen Streaming-Zipformer-Modell (~6 MB, `assets/kws/`). Kein AccessKey, kein Konto, auch beim ersten Start kein
Internet. Die Phrase „Hey Jarvis“ ist Text (BPE-Stücke in `assets/kws/keywords.txt`), es wird nichts trainiert.
`WakeDetector.kt` liest das Mikrofon (16 kHz) und füttert den Spotter; `WakeWordService` kümmert sich um Pausen
(Anruf, Sprachfenster, Bildschirm aus) und Fehler.

- **Bibliothek:** Das AAR ist nicht auf Maven Central. `app/build.gradle.kts` lädt es beim ersten Build von GitHub
  nach `android/app/libs/` (nicht in Git) und prüft den festen SHA-256.
- **Empfindlichkeit:** `WakeWord.sensitivity` (Standard 0,6) wird zur Schwelle des Spotters (0,6 → 0,15). Im TTS-Test
  erkannten drei von vier Stimmen „Hey Jarvis“; „Hello Jarvis“, „Hey Travis“, „Hey Siri“, „Jarvis“ allein und
  normale Sprache lösten nicht aus. Ein Schalter in den Einstellungen existiert noch nicht.
- **Andere Phrase:** mit dem `bpe.model` des Modells tokenisieren (Anleitung in `assets/kws/README.md`) und
  `PHRASE` in `WakeDetector.kt` sowie `keywords.txt` ändern.
- Das Modell ist englisch trainiert (GigaSpeech); „Hey Jarvis“ wird mit deutschem Akzent schlechter erkannt als mit
  englischem. Bei Fehlauslösungen oder Aussetzern die Schwelle in `keywordLine` anpassen.

**Status in Einstellungen › Hey Jarvis:** `läuft · hört zu`, `läuft · pausiert (Grund)`, `gestoppt · Grund`
(z. B. „Mikrofonberechtigung fehlt“) oder `Fehler · Grund`; dazu „Zuletzt erkannt“. Vorübergehende Fehler
(Mikrofon belegt) wiederholt der Dienst selbst (30 s, dann länger, höchstens alle 15 min).

**Fehlersuche per adb:**

```
adb logcat -s JarvisWake:V AndroidRuntime:E
```

Erwartet beim Einschalten: `starting wake word service` → `service created` → `foreground service started (type
microphone)` → `state: listen` → `keyword spotter loaded` → `listening`; beim Sprechen `keyword HEY_JARVIS` →
`wake word detected`.

### Einrichtung auf dem Telefon (einmalig)

1. App › Einstellungen › **Hey Jarvis**: Schalter **„Hey Jarvis“ erkennen** an. Die App erklärt
   vorher, was passiert, und fragt nach **Mikrofon** und **Benachrichtigungen**.
2. **Entweder** „Über anderen Apps“ › **Erlauben** (Systemseite → J.A.R.V.I.S. einschalten)
   **oder** „Standard-Assistent“ › **Festlegen** → „Digitaler Assistent“ / „Standard-Assistent-App“ → **J.A.R.V.I.S.**
   Mit Standard-Assistent funktionieren zusätzlich die Assistent-Geste (Ecke wischen, Ein-Taste/Home lang drücken).
   Hinweis: Damit ersetzt Jarvis Google Assistant/Gemini für diese Geste.
3. **Sprachfenster testen** prüft Mikrofon, Whisper und Antwort ohne Wake-Word.
4. Empfohlen: Akku-Optimierung für J.A.R.V.I.S. ausschalten (Zeile „Akku-Optimierung aus“ › **Öffnen**), sonst
   beenden manche Hersteller den Dienst.

### Akku und Verhalten

- Der Spotter braucht nur wenige Prozent eines Kerns (ein Thread); Audio verlässt das Telefon erst nach dem Aktivierungswort.
- **Standard: nur bei eingeschaltetem Bildschirm** – bei ausgeschaltetem Bildschirm ist das Mikrofon ganz aus.
  Abschaltbar in den Einstellungen (dann auch mit Bildschirm aus, kostet mehr Akku).
- Pausiert automatisch während Jarvis-Anrufen und solange das Sprachfenster offen ist (gibt das Mikrofon frei).
  Anrufe selbst sind unverändert.
- Benachrichtigung „„Hey Jarvis“ ist aktiv“ (still): Antippen öffnet das Sprachfenster, Aktionen **Pausieren** /
  **Ausschalten**.
- Musik/Podcasts pausieren, solange Jarvis zuhört und spricht.
- **Gesperrtes Telefon:** Das Fenster bittet zuerst ums Entsperren; vorher wird nichts aufgenommen und nichts gezeigt.
- **Nach Neustart / App-Update:** Android erlaubt keinen Mikrofondienst aus dem Hintergrund. Es erscheint der Hinweis
  „„Hey Jarvis“ wieder aktivieren“ – einmal antippen (oder die App öffnen), dann läuft die Erkennung wieder.

### Grenzen / Stand

- **Getestet (19.10.2026, Galaxy S24 Ultra, Debug-Build):** Modell lädt in <1 s, „Hey Jarvis“ über Lautsprecher löst
  aus, Mikrofon wird frei, Dienst hört nach 5 s wieder zu. **Noch offen:** Akkuverbrauch über Stunden, Erkennung mit
  Jarvis' Besitzer-Stimme in lauter Umgebung, Sprachfenster/Assistent-Geste auf dem Gerät.
- Echtes „Always-on“ bei ausgeschaltetem Bildschirm über den Hotword-DSP (wie Google) ist Drittanbietern verwehrt
  (`AlwaysOnHotwordDetector` braucht Systemrechte) – deshalb der Spotter im Vordergrunddienst.
- Die Antwort wird mit der Android-Stimme (TTS auf dem Gerät) vorgelesen, nicht mit Jarvis' Fish-Audio-Stimme:
  `/api/voice/tts` steht bewusst nicht auf der Mobile-Allowlist. Für Jarvis' Stimme lieber anrufen.
- Lange Aufträge: Das Fenster wartet bis zu 3 Minuten; danach „Jarvis antwortet nicht rechtzeitig“ – der Auftrag
  läuft im Core weiter, das Ergebnis steht in der App (Chat).
- `ACTION_ASSIST`-Aktivität und Spracherkennungsdienst sind exportiert (Android verlangt das); jede Aufnahme ist
  sichtbar und erst nach dem Entsperren möglich.

## 15. App-Updates (OTA)

Jarvis baut, signiert und verteilt die Android-App selbst; die App aktualisiert sich daraufhin von allein – im WLAN
und unterwegs über Tailscale. Core: `core/src/jarvis/mobile/ota/`, App: `android/…/update/AppUpdater.kt`,
Konfiguration: `mobile.app_update` in `config/jarvis.yaml`.

```
 Änderung an apps/jarvis_mobile auf main          Core (Mac)                                   Handy
 (Coding-Job oder Push von woanders) ──► Prüfung ──► Checkout (eigener Worktree) ──► flutter build apk
                                          │          ──► apksigner/aapt2 prüfen ──► Release ──► Push „app_update“
                                          │                                                     │
                                          └─ alle 10 min, beim Start, nach jedem Coding-Job      ▼
                                                                  /latest ─► Download (fortsetzbar) ─► Prüfung
                                                                  ─► PackageInstaller ─► „installiert“ an den Core
```

**Core-Seite**

- **Erkennung:** nach jedem abgeschlossenen Coding-Job, alle `poll_interval_minutes` (`git fetch origin main`) und
  beim Start. Gebaut wird nur, wenn sich seit dem letzten Release Dateien geändert haben, die in der APK landen
  (`apps/jarvis_mobile/…` ohne Tests, Doku, Lint-Einstellungen). Mehrere Änderungen kurz hintereinander werden
  zusammengefasst (`debounce_s`); es läuft immer nur ein Build, und zwar immer der neueste Stand.
- **Build:** in einem eigenen Worktree (`<data_dir>/app-updates/checkout`), nie in Sirs Checkout. `flutter` muss in
  der Shell-Allowlist stehen und wird ohne Shell aufgerufen; Android-SDK und JDK (Android Studio) werden selbst
  gefunden, sonst nennt der Status genau, was fehlt (`flutter_missing`, `android_sdk_missing` …) – fehlende
  Werkzeuge werden nach dem Nachinstallieren automatisch erneut versucht. Build-Log: `<log_dir>/android-build.log`.
- **Signierung:** genau ein Release-Schlüssel (RSA 4096, PKCS12) in `<data_dir>/android-signing/` (Ordner 0700),
  Passwort nur im Schlüsselbund (`android-release-keystore`), an Gradle per Umgebung (`JARVIS_SIGNING_*`). Er wird
  beim ersten Build erzeugt und **nie automatisch ersetzt** – fehlt er später, baut Jarvis nicht und sagt das (aus
  der Sicherung wiederherstellen: Datei + Schlüsselbund-Eintrag). Mit anderem Schlüssel signierte Builds werden
  verworfen.
- **Versionierung:** versionCode strikt steigend ab 3000 (über allen Hand-Builds), nie wiederverwendet;
  versionName `1.0.0+3001.ab12cd3` (pubspec-Version + Code + Commit).
- **Prüfung vor der Freigabe:** apksigner (v2/v3-Signatur, genau der Release-Schlüssel), aapt2 (Paket, versionCode,
  versionName), SHA-256. Erst dann entsteht ein Release (`<data_dir>/app-updates/releases/<code>/`), die letzten
  `keep_releases` bleiben (nie das, was ein Handy gerade noch hat).
- **Verteilung:** verschlüsselter Push `app_update` an alle gekoppelten Handys; ohne Push findet die App das Update
  beim Öffnen, beim Zurückkehren in den Vordergrund und alle 6 Stunden selbst.

**App-Seite**

- Fragt `/api/mobile/app-update/latest` über die gepinnte Gateway-Verbindung (LAN oder Tailscale) und nimmt nur ein
  Release mit **höherem versionCode, eigenem Paket und demselben Signaturzertifikat** wie die installierte App.
- Download in den App-Speicher (ohne Backup), bei Abbruch per HTTP-Range fortgesetzt; danach SHA-256, Größe, Paket,
  versionCode und Signatur der APK geprüft. Ein Fehler löscht die Datei, der nächste Versuch folgt mit wachsender
  Pause (5 min … 6 h).
- Installation über den Android-PackageInstaller: **ab Android 12 ohne Rückfrage**, sobald Sir die App verlassen hat
  (das Update beendet die laufende App) und nie während eines Anrufs. „Jetzt installieren“ in den Einstellungen
  installiert sofort. Ältere Android-Versionen oder Hersteller, die nachfragen: eine Benachrichtigung „Update bereit
  – tippen“.
- Daten, Kopplung, Einstellungen und Berechtigungen bleiben (gleiches Paket, gleicher Schlüssel). Nach dem Update
  meldet die App „installiert“ (auch ohne geöffnet zu werden) und zeigt eine kurze Benachrichtigung. „Hey Jarvis“
  muss danach einmal angetippt werden (siehe §14 › Akku und Verhalten).
- Status in der App: **Einstellungen › App-Updates** (Version, Fortschritt, Fehler, „Jetzt prüfen“), ein Hinweis auf
  der Startseite nur, wenn Sir etwas tun muss.

**Status in der Jarvis-Oberfläche:** Desktop › System › **Handy-App-Updates** – Build-Status mit Log-Auszug,
jedes Handy mit Version und Fortschritt, Releases, Knöpfe *Prüfen*, *Jetzt bauen / Wiederholen*, *Verteilen*,
*Installationslink*. Benachrichtigungen `app_update.ready` / `app_update.failed`.

**Erste Installation (einmalig)**

1. Desktop › System › Handy-App-Updates › **Jetzt bauen** (dauert beim ersten Mal einige Minuten).
2. **Installationslink** → QR-Code mit dem Handy scannen (WLAN oder Tailscale). Der Link gilt 24 h und höchstens
   20 Downloads; der Browser warnt vor Jarvis' eigenem Zertifikat (Fingerabdruck steht im Dialog) → *Weiter*.
3. APK öffnen, dem Browser einmal „Apps installieren“ erlauben, installieren. War eine J.A.R.V.I.S.-App mit anderem
   Schlüssel installiert (Debug-Build, `key.properties`), meldet Android einen Konflikt: diese zuerst deinstallieren
   und danach neu koppeln.
4. In der App unter Einstellungen › App-Updates **Installation erlauben** („Unbekannte Apps installieren“ für
   J.A.R.V.I.S.). Ab jetzt kommen Updates automatisch.

**Recovery**

- Fehlerhaftes Release: *Zurückziehen* – die Handys bekommen es nicht mehr (wer es schon hat, behält es).
- Zurück auf einen älteren Stand: *Wiederherstellen* baut dessen Commit erneut mit **höherem** versionCode (Android
  installiert nie eine niedrigere Version über eine höhere).
- Fehlgeschlagener Build: Ursache im Status und in der Benachrichtigung; derselbe Commit wird nicht endlos neu
  gebaut, sondern erst bei der nächsten App-Änderung oder über *Wiederholen*. Ein Core-Neustart mitten im Build
  wird erkannt und neu geprüft.
- Auf dem Handy: jedes Problem lässt die laufende App unberührt (Android installiert atomar).

**Test ohne Firebase:** `adb shell am broadcast -a systems.pje.jarvis.DEV_PUSH -n systems.pje.jarvis/systems.pje.jarvis_mobile.push.DevPushReceiver --es payload '{"type":"app_update"}'`

