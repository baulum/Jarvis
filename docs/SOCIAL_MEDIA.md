# Social Media – tägliches PJE-Systems-Video

Jarvis produziert jeden Tag ein neues 9:16-Kurzvideo für PJE Systems: Idee → Skript/Storyboard → Remotion-Render →
Sounddesign (FFmpeg) → Qualitätskontrolle → Captions/Hashtags → Videobibliothek → E-Mail an Sir → optional Autopost.
Keine eigene Marketing-Engine: Der Ablauf ist ein normales Jarvis-Playbook (`social_daily_video`), gestartet vom
normalen Zeitplaner, sichtbar in **Aufgaben** und **Zeitpläne**, gesteuert in der App unter **Social Media**.

```
 Zeitplaner (Postgres, cron)            Task-Engine (persistente Schritte, Leases, Resume)
 „PJE Systems Daily Social Video“ ──►  social_daily_video
   08:00 Europe/Berlin                   1 Tageslauf prüfen      (ein Auto-Video pro Tag, Idempotenz-Key)
   daily_key + catch_up: same_day        2 Idee entwickeln       (Claude Opus 5.5, Effort medium: Konzept, Skript, Storyboard)
                                         3 Storyboard prüfen     (Schema, Lesezeit, Fakten, Anti-Wiederholung)
                                         4 Video rendern         (node social/render.mjs → FFmpeg-Mix)
                                         5 Qualitätskontrolle    (ffprobe, Lautheit, Frames per Claude Vision)
                                         6 Captions & Hashtags   (Instagram, TikTok, Facebook, YouTube + Titel)
                                         7 In Bibliothek         (<data_dir>/social-videos)
                                         8 E-Mail an Sir         (Tool social_email_video, Gmail-Connector)
                                         9 Veröffentlichen       (Tool social_publish_video, nur wenn aktiviert)
```

## Bausteine

| Teil | Wo |
|---|---|
| Pipeline, Einstellungen, Zeitplan | `core/src/jarvis/social/service.py` |
| Modelle | `social_media.concept_model` / `qa_model` = `opus`, `effort: medium`: Idee, Skript, Storyboard, Captions und Bildprüfung (Sirs Vorgabe 2026-10-28) |
| Konzept + Anti-Wiederholung | `concept.py`, `variety.py` (Kategorie nie zweimal hintereinander, Grundkonzept 30 Tage gesperrt, Hooks/Bausteine/Übergänge/Farben/Musik rotieren) |
| Storyboard-Vertrag | `storyboard.py` (Python-Schema) ↔ `social/src/types.ts` (Remotion) – beide synchron halten |
| Render | `render.py` → `social/render.mjs` (Remotion 4, Chrome Headless Shell) |
| Sounddesign | `sfx.py` – Effekte und Musikbett werden mit FFmpeg synthetisiert (keine Samples, keine Lizenzfragen) |
| Qualitätskontrolle | `qa.py` – Auflösung, Länge, Codec, Lautheit (Mix auf −14 LUFS, Prüfung −20…−9 LUFS), Lesezeit, Rechtschreibung, Frames per Vision; Fehler → automatische Korrektur und Neu-Render (max. 3) |
| Captions | `captions.py` – je Plattform, Zeichenlimits, 3–5 Hashtags, keine Preise außer aus `brand.yaml` |
| E-Mail | `emailer.py` – HTML im PJE-Look, MP4 als Anhang (≤ 18 MB) oder signierter Download-Link |
| Plattformen | `platforms.py` – Instagram, Facebook, TikTok, YouTube (offizielle APIs, OAuth, Keychain) |
| Markenfakten | `config/social/brand.yaml` – **nur was hier steht, darf ein Video behaupten** |
| Inspiration | `config/social/inspiration.yaml` – 60 Formate, nur Anregung, keine Rotationsliste |
| UI | Desktop `desktop/src/views/SocialView.tsx`, Handy `apps/jarvis_mobile/lib/ui/screens/social_screen.dart` |

Remotion-Bausteine (`social/src/scenes`): Kinetic Type, POV, Chat, Fehlermeldung, Systemabsturz, Browser
(alt/neu/laden/404), Tab-Chaos, Sperrbildschirm-Mitteilung, Terminal, Suche, Split-Screen, Glass-Stack,
3D-Browser, Cursor-Story, Countdown, Ladebalken, Fakt, Wetter-, Bewertungs- und Dating-Parodie, Endkarte; zwölf
Übergänge. Das Konzept-LLM kombiniert sie frei; Satire-Szenen tragen automatisch „Satire · fiktiv“.

## Einrichtung (einmalig je Rechner)

```bash
cd social && npm ci && npm run browser   # Remotion + Browser (macOS: Chrome for Testing ~190 MB, sonst Headless Shell)
brew install ffmpeg                       # Linux: apt install ffmpeg · Windows: winget install Gyan.FFmpeg
```

`scripts/setup.sh` und der Installer erledigen beides. Fehlt etwas, zeigt **Social Media › Dashboard** genau, was
(„Abhängigkeiten“), statt einen fehlgeschlagenen Lauf zu produzieren. Läuft der Core auf einem Linux-Server, braucht
nur dieser Node, FFmpeg und das Remotion-Projekt; App und Desktop zeigen Status und steuern über die API.

E-Mail nutzt den Google-Connector (Gmail, `send_mail`). Mails an `social_media.owner_email` laufen ohne
Einzelfreigabe (Sirs Freigabe vom 2026-10-28), jeder andere Empfänger braucht eine Freigabe.

## Bedienung

* **Dashboard** – nächster Termin, Status der Aufgabe, laufende Produktion mit Fortschritt, letztes Video,
  letzte Veröffentlichungen, Fehler und Protokoll.
* **Bibliothek** – alle Videos mit Player (Range-Streaming), MP4-Download, Captions/Hashtags kopieren,
  neu generieren (gleicher Auftrag, neue Idee), per Mail senden, manuell veröffentlichen oder als manuell gepostet markieren.
* **Automatisierung** – täglich an/aus, Uhrzeit, E-Mail an/aus, Voiceover, Themenwunsch, Veröffentlichungszeit,
  „Jetzt produzieren“.
* **Connector** – Plattformen verbinden, Autopost global und je Plattform. **Autopost ist standardmäßig aus.**

Agent-Werkzeuge: `social_produce_video`, `social_list_videos`, `social_email_video`, `social_publish_video`
(Risiko *high* → Freigabe, außer Sir erlaubt es ausdrücklich).

Zuverlässigkeit: Der Zeitplan liegt in Postgres; jeder Schritt ist persistent und wird nach einem Neustart
fortgesetzt. Der Tageslauf hat den Idempotenz-Key `social-daily:<Datum>` und eine eindeutige `auto_date`-Spalte –
auch zwei Core-Prozesse oder „Jetzt ausführen“ erzeugen kein zweites Auto-Video. Ein verpasster Lauf wird nur am
selben Tag nachgeholt.

## Plattformen (offizielle APIs, alle kostenlos)

| Plattform | API | Voraussetzungen | Einschränkungen |
|---|---|---|---|
| Instagram | Graph API, Reels (`media_type=REELS`, resumable Upload über `rupload.facebook.com`) | Business-/Creator-Konto, mit Facebook-Seite verknüpft; Meta-App mit `instagram_content_publish` (App Review für Fremdkonten, eigene Konten im Entwicklungsmodus) | ~50 API-Posts / 24 h |
| Facebook | Graph API, Video Reels der Seite | dieselbe Meta-App, `pages_manage_posts`; Sir ist Admin der Seite | Reels 3–90 s, 9:16 |
| TikTok | Content Posting API (Direct Post, `FILE_UPLOAD`) | TikTok-Developer-App mit `video.publish` | **ohne Audit durch TikTok nur privat (`SELF_ONLY`)**; danach Sichtbarkeit wählbar. Bis dahin: manuell posten (Caption aus der Bibliothek kopieren) |
| YouTube | Data API v3 `videos.insert` (resumable) | Google-Cloud-Projekt mit YouTube Data API v3, OAuth-Client wie beim Google-Connector | 1 600 Quota-Einheiten je Upload (Standard 10 000/Tag); **ungeprüfte Projekte laden nur privat hoch** bis zum API-Audit. Shorts: ≤ 3 min, 9:16 |

Zugangsdaten (App-ID/Secret, Tokens) liegen ausschließlich im Keychain (`secret_store`), nie in Logs, Fehlern oder
der Datenbank. Der OAuth-Callback läuft über die lokale Core-Adresse `/api/social/oauth/<plattform>/callback` und
ist von außen (Mobile-Gateway) gesperrt.

Statistiken: Alle 6 h holt der Core über dieselben APIs Kennzahlen der Beiträge der letzten 14 Tage
(Aufrufe, Reichweite, Likes, Kommentare, Shares, Speicherungen, soweit die Plattform sie liefert). Der Konzeptgenerator bekommt sie als
„Bisherige Wirkung“ – gewichtet nach Reichweite, Website-Besuchen und Anfragen, nicht nach Views allein.

## Recherche und Technikwahl (2026-10-28)

* **Remotion 4.0.539** (React → MP4, lokal, headless): passt zum bestehenden React/TypeScript-Stack, deterministisch
  Frame für Frame, programmatisch aus JSON-Storyboards steuerbar. Lizenz: frei für Einzelpersonen und Firmen mit
  höchstens drei Mitarbeitern (PJE Systems erfüllt das; ab vier Mitarbeitern ist eine Company License nötig).
* **Offizielle Remotion-Skills** (`github.com/remotion-dev/skills`, Version 4.0.539, geprüft: nur Markdown und
  Beispielcode) global installiert: `remotion-best-practices`, `remotion-markup`, `remotion-render`,
  `remotion-multimedia`. Daraus übernommen: Animation nur über `useCurrentFrame()`/`interpolate()` (keine CSS-Transitions),
  `scale`/`translate` statt `transform`, Fonts lokal über `@remotion/fonts`.
* **FFmpeg** für Ton, Lautheitsmessung (EBU R128), Prüfung und ggf. Verkleinerung.
* Bewusst verworfen: Cloud-Video-APIs und KI-Videogeneratoren (Kosten, keine exakte Typografie/Marke),
  Lambda-Rendering (unnötig bei einem Video pro Tag), Drittanbieter-Scheduler (Buffer, Later … kostenpflichtig;
  die offiziellen APIs reichen).

## Grenzen

* Echte Kundenprojekte erscheinen erst, wenn in `brand.yaml` `allowed: true` gesetzt ist (Zustimmung des Kunden).
* Aktionspreise (z. B. 299 €) nur als aktives Angebot mit Zeitraum in `brand.yaml`.
* Voiceover nutzt die lokale Jarvis-TTS; standardmäßig aus.
* `npm run browser` lädt den Browser für Remotion: unter macOS **Chrome for Testing** (ein echtes `.app`-Bundle),
  unter Linux/Windows die Chrome Headless Shell. Grund: Vom Core gestartet bricht der GPU-Prozess der Headless Shell
  unter macOS mit „icudtl.dat not found in bundle“ ab und reißt den Browser mit („Target closed“); aus einem Terminal
  läuft sie – das sah wie Zufall aus. Bei einem Fehler steht das volle Protokoll (Stack + Browser-Konsole) in
  `<data_dir>/social-work/<video>/render-error.log`; „Abhängigkeiten prüfen“ startet den Browser wirklich.
