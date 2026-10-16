# Telefonie mit Twilio: Schritt für Schritt

Ziel: Du rufst Jarvis an (PIN-Abfrage, dann sprichst du mit ihm), und Jarvis ruft dich an (Erinnerungen,
kritische Ereignisse, „Ruf mich an, wenn der Build fertig ist“).

**Ohne Terminal:** Alles wird in der Jarvis-App eingetragen. Die öffentliche Adresse (Cloudflare-Tunnel) und
die Webhooks bei Twilio richtet Jarvis selbst ein. Du brauchst keine eigene Domain und kein Cloudflare-Konto.

> Lies vorher den Abschnitt [Gibt es etwas Kostenloses oder Einfacheres?](#alternativen). Kurz gesagt: Twilio
> kostet etwas Geld, und eine **deutsche** Twilio-Nummer gibt es nur für Firmen mit Nachweisen.

---

## Überblick

| Schritt | Wo | Dauer |
|---|---|---|
| 1. Twilio-Konto anlegen | twilio.com | 5 Min. |
| 2. Konto aufladen (Upgrade) | Twilio Console | 5 Min. |
| 3. Regulatory Bundle für deutsche Nummer | Twilio Console | 15 Min. + 1–3 Werktage Prüfung |
| 4. Nummer kaufen | Twilio Console | 2 Min. |
| 5. Länderfreigabe Deutschland prüfen | Twilio Console | 2 Min. |
| 6. In Jarvis eintragen | Jarvis-App › Connectoren | 3 Min. |
| 7. Testen | Telefon | 2 Min. |

**Kosten** (Twilio-Preise in US-Dollar, Stand Oktober 2026; aktuelle Preise: <https://www.twilio.com/en-us/voice/pricing/de>):

| Posten | Preis |
|---|---|
| Deutsche Festnetznummer (lokal) | ca. 1,35 $ / Monat |
| Eingehende Anrufe auf diese Nummer | ca. 0,01 $ / Minute |
| Jarvis ruft dein deutsches Handy an | ca. 0,04–0,38 $ / Minute (je nach Netz und Herkunft) |
| Jarvis ruft eine deutsche Festnetznummer an | ca. 0,03 $ / Minute |
| Cloudflare-Tunnel | kostenlos |

Bei normaler Nutzung sind das **etwa 2–5 $ im Monat**. Das Aufladen beginnt bei 20 $.

---

## 1. Twilio-Konto anlegen

1. <https://www.twilio.com/try-twilio> öffnen und registrieren (E-Mail bestätigen).
2. Deine **Handynummer per SMS bestätigen**. Twilio fragt das bei der Registrierung ab. Diese Nummer gilt
   danach als „Verified Caller ID“.
3. Die Fragen im Assistenten beliebig beantworten (z. B. *Voice* › *Make and receive calls* › *With code*).
4. Du landest in der **Console**: <https://console.twilio.com>

Testkonto (Trial): Twilio schenkt dir rund 15 $. Für Jarvis reicht ein Testkonto aber nur eingeschränkt:
- Anrufe dauern höchstens 10 Minuten.
- Vor jedem Gespräch läuft eine Twilio-Ansage.
- Jarvis kann nur bestätigte Nummern anrufen.
- Eine deutsche Nummer ist wegen Schritt 3 praktisch nicht zu bekommen.

Plane deshalb Schritt 2 ein. Details: <https://www.twilio.com/docs/usage/trials>

## 2. Konto aufladen (Upgrade)

1. Console › oben rechts **Upgrade** (oder <https://console.twilio.com/us1/billing/manage-billing/upgrade>).
2. Rechnungsadresse eintragen. Du kannst als Firma (PJE Systems) oder privat buchen; für eine deutsche Nummer
   brauchst du in Schritt 3 die **Firma**.
3. Zahlungsmittel hinterlegen und mindestens **20 $** aufladen.
4. Optional, aber empfohlen: **Auto-Recharge aus** und unter *Billing › Usage Triggers* eine Benachrichtigung
   bei z. B. 10 $ Verbrauch anlegen (<https://console.twilio.com/us1/billing/usage-triggers>).

## 3. Regulatory Bundle für eine deutsche Nummer

In Deutschland verlangt die Bundesnetzagentur einen Nachweis, wer die Nummer nutzt. Twilio vergibt deutsche
Nummern derzeit **nur an Firmen**, nicht an Privatpersonen
([Twilio-Blog](https://www.twilio.com/de-de/blog/wie-kaufe-ich-eine-twilio-rufnummer-mit-regulatory-bundles)).

1. Console › **Phone Numbers › Regulatory Compliance › Bundles** › **Create a Regulatory Bundle**
   (<https://console.twilio.com/us1/develop/phone-numbers/regulatory-compliance/bundles>).
2. **Country:** Germany · **Number type:** Local · **End-user type:** Business.
3. Angaben zur Firma: Name (PJE Systems), Anschrift, Ansprechpartner.
4. Dokumente hochladen. Typisch sind:
   - **Gewerbeanmeldung** oder **Handelsregisterauszug**
   - **Adressnachweis** (z. B. Strom- oder Telefonrechnung, nicht älter als 3–6 Monate)
   - ggf. Ausweis des Geschäftsführers

   Die genaue Liste zeigt dir der Assistent.
5. **Wichtig:** Die Adresse muss in der **Vorwahl der gewünschten Nummer** liegen. Wer in Hamburg sitzt, bekommt
   eine 040-Nummer; eine Berliner Adresse reicht dafür nicht.
6. **Submit for review**. Die Prüfung dauert meist 1–3 Werktage, und du bekommst eine E-Mail. Den Status siehst du in
   derselben Liste („Twilio-approved“).

## 4. Nummer kaufen

1. Console › **Phone Numbers › Manage › Buy a number**
   (<https://console.twilio.com/us1/develop/phone-numbers/manage/search>).
2. **Country:** Germany. Bei **Capabilities** muss **Voice** angehakt sein; die Vorwahl suchst du über *Number* › *Starts with*,
   z. B. `+4940` für Hamburg.
3. **Buy** und dein freigegebenes Bundle auswählen.
4. Die Nummer im Format **+49…** notieren (ohne Leerzeichen), z. B. `+494012345678`.

Webhooks („A call comes in“) musst du **nicht** eintragen, das erledigt Jarvis.

## 5. Länderfreigabe Deutschland prüfen

Damit Jarvis dein Handy anrufen darf:

1. Console › **Voice › Settings › Geo permissions**
   (<https://console.twilio.com/us1/develop/voice/settings/geo-permissions>).
2. **Germany** anhaken. Für deutsche Handynummern ggf. auch **High Risk** aktivieren (nur möglich, wenn das Konto
   aufgeladen ist) und **Save**.
3. Mit dem *Phone Number Permission Check* auf derselben Seite deine Handynummer testen.

## 6. In Jarvis eintragen

1. Jarvis-App › **Connectoren** › **Telefon (Twilio)** › **Einrichten**.
2. Ausfüllen:

   | Feld | Wert | Woher |
   |---|---|---|
   | Twilio Account SID | beginnt mit `AC…` | Console-Startseite › *Account Info* |
   | Twilio Auth Token | 32 Zeichen | Console-Startseite › *Account Info* › *Auth Token* (auf „Show“ klicken) |
   | Telefon-PIN | 4–8 Ziffern, frei wählbar | Diese PIN fragt Jarvis bei jedem Anruf ab |
   | from_number | `+49…` | die gekaufte Twilio-Nummer aus Schritt 4 |
   | owner_number | `+49151…` | deine eigene Handynummer |
   | public_base_url | **leer lassen** | Jarvis startet den kostenlosen Tunnel selbst |

3. **Speichern** und den Schalter **Aktiv** einschalten.
4. Etwa 10–20 Sekunden warten, dann **Testen**. Erwartet wird: *„Konto „…“, Nummer +49…, erreichbar über Cloudflare-Tunnel“*.
   Was die Meldungen bedeuten, steht unter [Fehlerbehebung](#fehlerbehebung).

Was Jarvis dabei automatisch tut:
- Er startet einen Cloudflare-Schnelltunnel (`https://….trycloudflare.com`).
- Bei deiner Twilio-Nummer trägt er *A call comes in* → `…/api/telephony/twilio/voice` und den Status-Callback ein.
- Die Adresse des Schnelltunnels ändert sich bei jedem Neustart. Jarvis trägt die neue Adresse jedes Mal selbst bei Twilio ein.

## 7. Testen

**Jarvis ruft dich an:** In der Connector-Karte **Testanruf an mich** klicken. Dein Handy klingelt, und Jarvis
sagt, dass die Telefonie funktioniert. Danach kannst du im Chat zum Beispiel „Ruf mich in 5 Minuten an und erinnere mich an
den Termin“ sagen.

**Du rufst Jarvis an:**
1. Die Twilio-Nummer anrufen.
2. Jarvis meldet sich und fragt nach der **PIN**. Tippe die PIN gefolgt von `#` oder sprich die Ziffern.
3. Danach sprichst du normal mit ihm, zum Beispiel: „Welche Termine habe ich morgen?“

Ohne richtige PIN kann ein Anrufer nichts tun, selbst wenn er deine Handynummer fälscht. Nach drei falschen Versuchen
legt Jarvis auf. Alle Anrufe stehen in der App unter *Systemstatus › Telefonie* und im Audit-Log.

---

## Fehlerbehebung

| Meldung beim Testen | Ursache und Lösung |
|---|---|
| *Twilio lehnt Account SID / Auth Token ab (401)* | SID oder Token vertauscht oder falsch kopiert. Neu eintragen. Nach „Auth Token neu erzeugen“ bei Twilio muss er auch in Jarvis erneuert werden. |
| *+49… gehört nicht zu diesem Twilio-Konto* | Tippfehler in `from_number`, oder die Nummer liegt in einem anderen Twilio-Projekt. |
| *Tunnel startet noch …* | 20 Sekunden warten und erneut testen. |
| *cloudflared ist nicht installiert* | `scripts/setup.sh` erneut ausführen (installiert `cloudflared`). |
| *Öffentliche Adresse noch nicht erreichbar* | Der Tunnel ist frisch, und das DNS braucht oft bis zu einer Minute. Erneut testen. |
| *Webhook nicht gesetzt: …* | Zugangsdaten prüfen; im Twilio-Konto muss die Nummer für **Voice** aktiv sein. |
| Handy klingelt nicht beim Testanruf | Schritt 5 (Geo Permissions) prüfen. Im Testkonto muss deine Handynummer bestätigt sein. |
| „Dieser Anschluss ist noch nicht eingerichtet.“ | Der Connector ist aus oder unvollständig. *Aktiv* einschalten. |
| Fehler 13227 / 21215 in Twilio | Länderfreigabe fehlt (Schritt 5). |

Logs: App › *Live-Logs* (Filter „telephony“) und Twilio Console › *Monitor › Logs › Calls*
(<https://console.twilio.com/us1/monitor/logs/calls>).

## Sicherheit

- **Signatur:** Jede Anfrage von Twilio wird mit deinem Auth Token geprüft (`X-Twilio-Signature`). Gefälschte
  Anfragen werden mit 403 abgelehnt.
- **Tunnel:** Über den Tunnel sind **nur** die Telefonie-Endpunkte erreichbar. Alles andere (Chat, API, MCP, Gedächtnis)
  antwortet aus dem Internet mit 404. Das prüft Jarvis selbst, unabhängig vom Tunnel.
- **Rufnummer ≠ Identität:** Erst die PIN schaltet Aktionen frei. Auch dann gelten die Freigabe-Regeln.
  Kritische Aktionen wie Geld oder Löschen sind am Telefon nie möglich.
- **Zugangsdaten** liegen nur im macOS-Schlüsselbund.

---

<a id="alternativen"></a>
## Gibt es etwas Kostenloses oder Einfacheres?

| Möglichkeit | Kosten | Aufwand für dich | Rufnummer | Status in Jarvis |
|---|---|---|---|---|
| **FRITZ!Box als Telefonanlage** | **kostenlos** (deine Festnetz-Flat) | **gering:** ein „IP-Telefon“ in der FRITZ!Box anlegen, Benutzername und Passwort in Jarvis eintragen | deine **vorhandene Festnetznummer** | noch nicht eingebaut, kann ich nachrüsten |
| **Twilio** (diese Anleitung) | ca. 2–5 $/Monat | mittel: Firmen-Nachweise, 1–3 Tage Prüfung | neue deutsche Nummer | **fertig eingebaut** |
| Twilio mit US-Nummer | ca. 1,15 $/Monat + Auslandsminuten | gering: keine Nachweise | US-Nummer (+1); Anrufe aus Deutschland kosten dich Auslandstarif | eingebaut, aber für dich unpraktisch |
| sipgate | kein Gratis-Tarif mehr für Neukunden; Business ab ca. 13 €/Nutzer | gering | deutsche Nummer | nicht eingebaut |
| Telegram-Anruf | kostenlos | – | – | geht nicht: Telegram-Bots können nicht telefonieren |

**Meine Empfehlung: die FRITZ!Box.** Dein Netzwerk läuft über eine FRITZ!Box (`fritz.box`). Hat sie einen
Festnetzanschluss, gilt:
- Jarvis meldet sich bei ihr wie ein schnurloses Telefon an.
- Er nimmt Anrufe auf deine normale Nummer an, auf Wunsch nur auf eine bestimmte, und kann von dort aus anrufen.
- Es fallen keine Zusatzkosten an. Du brauchst keinen Tunnel, keine öffentliche Adresse und keine Nachweise. Alles bleibt in deinem Heimnetz.

Für dich wäre das ein einziger Schritt in der FRITZ!Box: *Telefonie › Telefoniegeräte › Neues Gerät einrichten
› Telefon (mit und ohne Anrufbeantworter) › LAN/WLAN (IP-Telefon)*. Dabei vergibst du Benutzername und Passwort, die
du dann in Jarvis einträgst. Auch hier schützt die PIN, und ohne sie geht nichts.

Quellen: [Twilio-Preise Deutschland](https://www.twilio.com/en-us/voice/pricing/de) ·
[Twilio Regulatory Bundles (DE)](https://www.twilio.com/de-de/blog/wie-kaufe-ich-eine-twilio-rufnummer-mit-regulatory-bundles) ·
[Twilio-Testkonto](https://www.twilio.com/docs/usage/trials) ·
[Twilio Geo Permissions](https://www.twilio.com/docs/voice/tutorials/voice-dialing-geographic-permissions) ·
[sipgate-Tarif eingestellt (teltarif)](https://www.teltarif.de/arch/2022/kw37/s89339.html)
