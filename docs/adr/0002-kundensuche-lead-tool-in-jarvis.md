# ADR 0002 – Kundensuche: das PJE-Sales-AI-Lead-Tool in Jarvis

Status: umgesetzt (2026-11-13) · Betrifft: `core/src/jarvis/sales/`, `core/src/jarvis/mcp/client.py` (nur Namensfunktion),
`core/src/jarvis/main.py`, `core/src/jarvis/mobile/gateway.py`, `core/src/jarvis/commands.py`, `config/mcp_servers.yaml`,
Desktop-App (`desktop/src/views/SalesView.tsx`, `lib/sales.ts`), Handy-App (`apps/jarvis_mobile/lib/…/sales*`)

## Ziel
Leads, Kampagnen, Freigabe-Warteschlange, Entwürfe und Postausgang des Lead-Tools
(`/Users/paul/Documents/PJE Systems/Lead-Tool`) direkt in Jarvis zeigen, als Bereich „Kundensuche“ auf dem Mac, im
Browser-/Remote-Client und in der Android-App. Das Lead-Tool bleibt eigenständig lauffähig und unverändert.

## Ausgangslage
| Teil | Technik |
|---|---|
| Jarvis Core | Python 3.13, FastAPI, eigene Postgres-DB, `MCPClientHub` für externe MCP-Server |
| Lead-Tool | TypeScript-Monorepo (Next.js 16, Drizzle, pg-boss), eigene Postgres-DB (Docker, Port 5433) |
| Brücke heute | `config/mcp_servers.yaml › pje_sales_ai` startet `pnpm --filter @pje/mcp start` (stdio). Die Werkzeuge `ext_pje_sales_ai_*` laufen durch den `ToolExecutor` (Risiko, Freigaben, Audit). |

Das MCP-Paket `apps/mcp` des Lead-Tools ist eine dünne Hülle um dessen eigene Bibliotheken (`@pje/db`, `@pje/core`,
`@pje/orchestrator/client`). Es bietet: `status`, `list_campaigns`, `get_campaign`, `list_leads`, `get_lead`,
`review_queue`, `find_industry`, `export_leads`, `create_campaign`, `request_redraft`. Freigeben, Versenden,
Als-kontaktiert-Markieren und Sperren gibt es dort bewusst **nicht**.

## Entscheidung
**Abweichung von „Lead-Tool als Bibliothek im Core“:** Der Core ist Python, das Lead-Tool TypeScript. Ein direkter
Import ist nicht möglich. Die Alternativen wären eine Kopie der Abfragen in Python (direkter Zugriff auf die
Lead-Tool-Datenbank, Datenbank-URL als zusätzliches Geheimnis im Core, Schema-Kopplung) oder ein eigener Node-Sidecar im
Jarvis-Repo, der die `@pje/*`-Pakete über Pfade importiert (bricht CI, Windows/Linux-Installer und das pnpm-Workspace
des Lead-Tools). Beides widerspricht „keine Kopie des Codes“ stärker als der gewählte Weg.

Gewählt: **Der vorhandene MCP-Server des Lead-Tools ist die Bibliotheksgrenze.** Er läuft bereits als Kindprozess des
Core und nutzt die Bibliotheken des Lead-Tools selbst; Jarvis kopiert keine Abfrage und kennt keine Datenbank-URL.
Der Core bekommt ein schmales Modul `jarvis.sales`, das diese Werkzeuge in eine stabile REST-API für alle Clients
übersetzt.

```
Desktop (Tauri/Browser) ─┐                    ┌─ lesen:   ExternalServer.call(tool)   ─┐
Handy (Flutter, TLS-GW) ─┼─ /api/sales/* ── SalesService                              ├─ stdio ─ @pje/mcp ─ @pje/db … ─ Lead-Tool-DB
                         │                    └─ handeln: ToolExecutor.execute(ext_…) ─┘
```

### Datenfluss
* **Lesen** (Status, Kampagnen, Leads, Lead-Akte mit Entwurf, Warteschlange, Statusliste, Branchensuche): `SalesService`
  ruft das MCP-Werkzeug direkt über die bestehende Verbindung des `MCPClientHub` auf, parst das JSON und wandelt die
  deutschen Schlüssel in eine stabile Form. Wie bei UpTik/Social laufen reine Lesezugriffe der Oberfläche nicht durch
  den Executor (kein Audit-Eintrag je Listenaufruf). Ist ein Lese-Werkzeug in Jarvis deaktiviert, gilt es auch hier als
  nicht verfügbar.
* **Handeln** (`Entwurf neu formulieren`, `Kampagne starten`): ausschließlich über
  `ToolExecutor.execute("ext_pje_sales_ai_…")` – also Not-Aus, Rollen-Obergrenze, Rate-Limit, Freigabe-Modus, Ledger und
  Audit wie bei einem Agent-Aufruf. Verlangt der Modus eine Freigabe, antwortet die API mit `approval_required`; die
  Aktion läuft erst nach der Freigabe in Jarvis.
* **Freigeben/Versenden eines Entwurfs:** gibt es in Jarvis nicht. Die Oberfläche bietet dafür nur den Sprung in das
  Lead-Tool (`web_url`, Standard `http://127.0.0.1:3100`), wo die bisherige Freigabelogik (Gates, Opt-outs, Cooldown,
  `draft_only`) unverändert gilt.
* **Analyse:** aus `list_campaigns` und `status` berechnet (aktueller Stand je Station, je Kampagne, offene Aufgaben).
  Kein neues Rechenverfahren des Lead-Tools, nur Summen vorhandener Zahlen.
* **Postausgang:** Das MCP liefert nur die Zahl der Vorschauen. Die Liste braucht ein neues Lese-Werkzeug im Lead-Tool
  (siehe Offene Punkte); bis dahin zeigt die Oberfläche die Zahl und den Sprung ins Lead-Tool.

### Schnittstellen (`/api/sales`, nur Rollen `owner`/`mobile_owner`)
| Methode | Pfad | Quelle |
|---|---|---|
| GET | `/status` | Verbindung, Fähigkeiten, `status` |
| GET | `/statuses` | Enum aus dem Schema von `list_leads`, Bezeichnungen und Stationen |
| GET | `/campaigns`, `/campaigns/{id}` | `list_campaigns`, `get_campaign` |
| GET | `/leads?campaign_id&status&limit&offset`, `/leads/{id}` | `list_leads`, `get_lead` |
| GET | `/review?limit` | `review_queue` |
| GET | `/outbox` | Zahl aus `status`, `available: false` |
| GET | `/analytics` | berechnet aus `list_campaigns` + `status` |
| GET | `/industries?q=` | `find_industry` |
| POST | `/leads/{id}/redraft` `{note, request_id}` | Executor → `ext_pje_sales_ai_request_redraft` |
| POST | `/campaigns` `{region, industry_key, …, request_id}` | Executor → `ext_pje_sales_ai_create_campaign` |

`request_id` (vom Client je Absenden erzeugt) bildet den Idempotenzschlüssel: Wiederholungen derselben Absendung
laufen nicht doppelt, eine neue Absendung mit derselben Anweisung schon.

Fehler: Server nicht eingerichtet → 409, nicht verbunden → 503, „nicht gefunden“ → 404, sonstige Werkzeugfehler → 502.
Fehlermeldungen werden vor der Ausgabe von Zugangsdaten in URLs bereinigt.

### Clients
* **Desktop:** neuer Eintrag „Kundensuche“ im Systempanel (`SYSTEM_VIEWS`), gleiche Bausteine wie UpTik/Social
  (`Tabs`, `Section`, `StatusText`, `EmptyState`, `ErrorState`, `Dialog`, `useQuery`/`useAction`). Slash-Befehl
  `/kundensuche`.
* **Handy:** Bildschirm `/sales`, gleiche Endpunkte über die Allowlist des TLS-Gateways, Bausteine aus `hud.dart`.
* **Browser-/Remote-Client:** nutzt die Desktop-Oberfläche; der Sprung ins Lead-Tool erscheint nur, wenn `web_url`
  vom Client aus erreichbar ist (nicht `127.0.0.1` bei Remote).

### Keine neuen Tabellen, keine Migrationen
Weder in der Jarvis- noch in der Lead-Tool-Datenbank. Jarvis schreibt nur über die zwei vorhandenen MCP-Aktionen.

## Risiken
| Risiko | Umgang |
|---|---|
| MCP-Antworten sind für ein Sprachmodell geschrieben (deutsche Schlüssel) und können sich ändern | Übersetzung an einer Stelle (`sales/views.py`), fehlende Felder werden `null`, Unit-Tests mit den heutigen Antworten |
| Statusbezeichnungen und Stationen sind in `sales/views.py` nachgebildet (das MCP liefert keine Bezeichnungen) | Unbekannte Status zeigen den Schlüssel; die Statusliste selbst kommt aus dem Schema des Lead-Tools |
| `list_campaigns` liefert höchstens 50 Kampagnen | Analyse nennt die Grundlage ausdrücklich |
| MCP-Prozess stirbt (z. B. Lead-Tool-DB aus) | `/status` meldet `connected: false` mit Grund; Neuverbindung wie bisher über einen Core-Neustart |
| Lead-Tool läuft nur auf dem Mac | Kundensuche funktioniert nur, wenn der Core auf dem Mac mit dem Lead-Tool läuft; andere Clients sehen den Grund |

## Offene Punkte
1. **Postausgang als Liste und vollständige Analyse:** Im Lead-Tool (`apps/mcp/src/index.ts`) fehlen Lese-Werkzeuge
   `list_outbox` (outbound_messages mit Status, Kanal, Empfänger, Betreff, Zeitpunkten) und `analytics`
   (Kosten, Website-Befunde, Erreichte-Stationen-Trichter wie `/analytics`). Beides gehört in einen eigenen Job im
   Lead-Tool-Repository; Jarvis braucht danach nur je einen Endpunkt.
2. **Freigeben in Jarvis selbst:** bewusst nicht umgesetzt, weil das Lead-Tool kein Freigabe-Werkzeug anbietet. Wenn
   gewünscht: ein MCP-Werkzeug `approve_draft` im Lead-Tool (mit dessen Gates), in Jarvis mit Risiko `high` oder
   `always_ask`, damit jede Freigabe zusätzlich über das Jarvis-Freigabesystem läuft.
