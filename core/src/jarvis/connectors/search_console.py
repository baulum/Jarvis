"""Google Search Console (kostenlose Search Console API): Suchanalyse, URL-Prüfung und Sitemaps.

Das Modul legt **keine zweite Anmeldung** an: Es nutzt den OAuth-Client und das Refresh-Token des Google-Connectors
(``google-oauth-client`` / ``google-oauth-token`` im Keychain) und bekommt von dort nur das kurzlebige Access-Token.
Dafür braucht die Google-Autorisierung die Scopes ``webmasters.readonly`` (Lesen) und ``webmasters`` (Sitemaps
einreichen); beide stehen in ``jarvis.connectors.google.SCOPES``. Ein Token, das vor dieser Erweiterung erteilt wurde,
hat sie nicht – dann muss Sir den Google-Connector einmal neu verbinden (Connectoren › Google › „Mit Google verbinden“).

Werkzeuge:

* ``gsc_list_sites`` (read) – Properties des Kontos mit Typ und Berechtigungsstufe
* ``gsc_search_analytics`` (read) – Klicks, Impressionen, CTR, Position nach Dimensionen
* ``gsc_inspect_url`` (read) – Indexierungsstatus einer URL
* ``gsc_list_sitemaps`` (read) – Sitemaps einer Property mit Status
* ``gsc_submit_sitemap`` (**high**, läuft über die Freigabe) – reicht eine Sitemap bei Google ein

Datensparsamkeit: An Google gehen nur Property-URL, Datumsbereich, Dimensionen, Filter, die zu prüfende URL und der
Sitemap-Pfad. Access-Token und Secrets tauchen weder in Logs noch in Fehlermeldungen oder Ergebnissen auf.
"""

from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from jarvis.agent.persona import TZ
from jarvis.connectors.base import Connector, Health, Manifest
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext, ToolSpec

logger = logging.getLogger(__name__)

WEBMASTERS_API = "https://www.googleapis.com/webmasters/v3"
INSPECT_API = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"
SCOPE_READONLY = "https://www.googleapis.com/auth/webmasters.readonly"
SCOPE_WRITE = "https://www.googleapis.com/auth/webmasters"

DIMENSIONS = ("query", "page", "country", "device", "date")
FILTER_DIMENSIONS = ("query", "page")
FILTER_OPERATORS = ("contains", "equals", "notContains", "notEquals", "includingRegex", "excludingRegex")
MAX_ROW_LIMIT = 25_000
DEFAULT_ROW_LIMIT = 100
DEFAULT_DAYS = 28
DATA_DELAY_DAYS = 2  # Search-Console-Daten sind frühestens nach ca. 2 Tagen vollständig

REQUEST_TIMEOUT = 25.0
MAX_RETRIES = 3  # nach dem ersten Versuch; Wartezeit 1 s, 2 s, 4 s (bzw. Retry-After, höchstens 30 s)
MAX_RETRY_WAIT_S = 30.0
_RETRY_STATUS = {429, 500, 502, 503, 504}

_PERMISSION_DE = {
    "siteOwner": "Inhaber",
    "siteFullUser": "Vollzugriff",
    "siteRestrictedUser": "Eingeschränkter Zugriff",
    "siteUnverifiedUser": "Nicht verifiziert",
}
_VERDICT_DE = {
    "PASS": "Indexiert",
    "NEUTRAL": "Nicht indexiert (von Google ausgeschlossen)",
    "FAIL": "Nicht indexiert (Fehler)",
    "VERDICT_UNSPECIFIED": "Unbekannt",
}
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _obj(props: dict[str, Any], req: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": req or [], "additionalProperties": False}


def scope_message(write: bool) -> str:
    scope = "webmasters" if write else "webmasters.readonly"
    need = "Schreibzugriff (Sitemaps einreichen)" if write else "Lesezugriff"
    return (
        f"Die Google-Verbindung von Jarvis hat noch keine Berechtigung für die Search Console ({need}, "
        f"Scope „{scope}“). Bitte Jarvis einmal neu mit Google verbinden (Connectoren › Google › „Mit Google "
        f"verbinden“) und der Search-Console-Berechtigung zustimmen."
    )


def _google_error(resp: httpx.Response) -> tuple[str, str]:
    """(Meldung, Gründe in Kleinbuchstaben) aus einer Google-Fehlerantwort; nie der rohe Body."""
    try:
        err = resp.json().get("error", {})
    except (ValueError, AttributeError):
        return "", ""
    if not isinstance(err, dict):
        return "", ""
    message = str(err.get("message", ""))
    reasons = [str(err.get("status", ""))]
    reasons += [str(e.get("reason", "")) for e in err.get("errors", []) if isinstance(e, dict)]
    reasons += [str(d.get("reason", "")) for d in err.get("details", []) if isinstance(d, dict)]
    return message, " ".join(reasons).lower()


def map_http_error(resp: httpx.Response, *, write: bool = False) -> ToolUserError:
    """Übersetzt eine Fehlerantwort der Search Console API in eine deutsche Meldung mit nächstem Schritt."""
    status = resp.status_code
    message, reasons = _google_error(resp)
    text = f"{message} {reasons}".lower()
    if status == 401:
        return ToolUserError(
            "Die Google-Autorisierung ist abgelaufen oder wurde widerrufen. Bitte Jarvis neu mit Google verbinden "
            "(Connectoren › Google › „Mit Google verbinden“)."
        )
    if status == 403:
        if "insufficient" in text and ("scope" in text or "permission" in text or "authentication" in text):
            return ToolUserError(scope_message(write))
        if "service_disabled" in text or "accessnotconfigured" in text or "has not been used" in text:
            return ToolUserError(
                "Die Google Search Console API ist im Google-Cloud-Projekt von Jarvis nicht aktiviert. In der Cloud "
                "Console unter APIs & Dienste › Bibliothek „Google Search Console API“ aktivieren (kostenlos) und "
                "danach erneut versuchen."
            )
        return ToolUserError(
            "Das Google-Konto hat für diese Property bzw. Aktion keine ausreichende Berechtigung. Mit "
            "gsc_list_sites die erreichbaren Properties und Berechtigungsstufen prüfen "
            "(Sitemaps einreichen braucht mindestens Vollzugriff)."
        )
    if status == 404:
        return ToolUserError(
            "Property, Sitemap oder URL nicht gefunden. Schreibweise prüfen: Domain-Properties heißen "
            "„sc-domain:beispiel.de“, URL-Präfix-Properties enden mit „/“ (z. B. „https://beispiel.de/“). "
            "gsc_list_sites zeigt die vorhandenen Properties."
        )
    if status == 400:
        return ToolUserError(f"Ungültige Anfrage an die Search Console: {message[:200] or 'keine Details'}")
    if status == 429:
        return ToolUserError(
            "Das Anfragelimit der Search Console ist erreicht (auch nach Wiederholungen). "
            "Bitte später erneut versuchen."
        )
    if status >= 500:
        return ToolUserError(
            f"Die Search Console ist gerade nicht erreichbar (HTTP {status}, auch nach Wiederholungen). "
            "Bitte später erneut versuchen."
        )
    return ToolUserError(f"Search-Console-Fehler (HTTP {status}): {message[:200] or 'keine Details'}")


def _domain_of(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def url_in_property(url: str, prop: str) -> bool:
    """Gehört ``url`` zur Property? (Domain-Property: Host oder Subdomain; URL-Präfix: Präfix-Vergleich.)"""
    if prop.startswith("sc-domain:"):
        domain = prop.removeprefix("sc-domain:").lower()
        host = _domain_of(url)
        return host == domain or host.endswith("." + domain)
    return url.startswith(prop)


def _parse_date(value: Any, name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ToolUserError(f"{name} muss im Format JJJJ-MM-TT angegeben werden (z. B. 2026-11-01).") from None


def resolve_range(a: dict[str, Any], today: date | None = None) -> tuple[date, date]:
    """Standard: letzte 28 Tage bis vorgestern (Daten der letzten 2 Tage sind noch unvollständig)."""
    today = today or datetime.now(TZ).date()
    latest = today - timedelta(days=DATA_DELAY_DAYS)
    end = _parse_date(a["endDate"], "endDate") if a.get("endDate") else latest
    start = _parse_date(a["startDate"], "startDate") if a.get("startDate") else end - timedelta(days=DEFAULT_DAYS - 1)
    if start > end:
        raise ToolUserError("startDate liegt nach endDate.")
    if end > today:
        raise ToolUserError("endDate liegt in der Zukunft.")
    return start, end


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _row(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "keys": raw.get("keys", []),
        "clicks": int(_num(raw.get("clicks"))),
        "impressions": int(_num(raw.get("impressions"))),
        "ctr": round(_num(raw.get("ctr")), 4),
        "position": round(_num(raw.get("position")), 1),
    }


def _totals(raw_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summen über die gelieferten Zeilen (aus den ungerundeten Rohwerten; Position nach Impressionen gewichtet)."""
    clicks = sum(int(_num(r.get("clicks"))) for r in raw_rows)
    impressions = sum(int(_num(r.get("impressions"))) for r in raw_rows)
    weighted = sum(_num(r.get("position")) * int(_num(r.get("impressions"))) for r in raw_rows)
    return {
        "clicks": clicks,
        "impressions": impressions,
        "ctr": round(clicks / impressions, 4) if impressions else 0.0,
        "position": round(weighted / impressions, 1) if impressions else 0.0,
    }


def summarize_inspection(res: dict[str, Any]) -> dict[str, Any]:
    """Kompakte, deutschsprachig kommentierte Sicht auf ein ``inspectionResult``."""
    idx = res.get("indexStatusResult") or {}
    verdict = idx.get("verdict", "VERDICT_UNSPECIFIED")
    issues: list[str] = []
    fetch = idx.get("pageFetchState")
    if fetch and fetch not in ("SUCCESSFUL", "PAGE_FETCH_STATE_UNSPECIFIED"):
        issues.append(f"Seitenabruf: {fetch}")
    robots = idx.get("robotsTxtState")
    if robots == "DISALLOWED":
        issues.append("Durch robots.txt für Google gesperrt")
    indexing = idx.get("indexingState")
    if indexing and indexing not in ("INDEXING_ALLOWED", "INDEXING_STATE_UNSPECIFIED"):
        issues.append(f"Indexierung eingeschränkt: {indexing}")
    google_canonical, user_canonical = idx.get("googleCanonical"), idx.get("userCanonical")
    if google_canonical and user_canonical and google_canonical != user_canonical:
        issues.append("Google wählt eine andere Canonical-URL als die auf der Seite angegebene")
    if verdict == "FAIL" and idx.get("coverageState"):
        issues.append(f"Abdeckung: {idx['coverageState']}")
    for block, label in (("mobileUsabilityResult", "Mobile Nutzbarkeit"), ("richResultsResult", "Rich-Results")):
        part = res.get(block) or {}
        for issue in part.get("issues", []) or []:
            issues.append(f"{label}: {issue.get('message') or issue.get('issueType', 'Problem')}")
        for item in part.get("detectedItems", []) or []:
            for sub in item.get("items", []) or []:
                for issue in sub.get("issues", []) or []:
                    issues.append(
                        f"{label} ({item.get('richResultType', '?')}): {issue.get('issueMessage', 'Problem')}"
                    )
    return {
        "verdict": verdict,
        "summary": f"{_VERDICT_DE.get(verdict, verdict)}: {idx.get('coverageState', 'ohne Angabe')}",
        "coverage_state": idx.get("coverageState"),
        "indexing_state": indexing,
        "robots_txt_state": robots,
        "page_fetch_state": fetch,
        "last_crawl_time": idx.get("lastCrawlTime"),
        "crawled_as": idx.get("crawledAs"),
        "google_canonical": google_canonical,
        "user_canonical": user_canonical,
        "sitemaps": idx.get("sitemap", []),
        "issues": issues[:20],
        "report_link": res.get("inspectionResultLink"),
    }


def _int(value: Any) -> int:
    return int(_num(value))


def summarize_sitemap(sm: dict[str, Any]) -> dict[str, Any]:
    errors, warnings = _int(sm.get("errors")), _int(sm.get("warnings"))
    pending = bool(sm.get("isPending"))
    status = "Fehler" if errors else "Warnungen" if warnings else "Ausstehend" if pending else "OK"
    return {
        "path": sm.get("path"),
        "status": status,
        "type": sm.get("type"),
        "is_sitemaps_index": bool(sm.get("isSitemapsIndex")),
        "is_pending": pending,
        "last_submitted": sm.get("lastSubmitted"),
        "last_downloaded": sm.get("lastDownloaded"),
        "errors": errors,
        "warnings": warnings,
        "contents": [
            {"type": c.get("type"), "submitted": _int(c.get("submitted")), "indexed": _int(c.get("indexed"))}
            for c in sm.get("contents", []) or []
        ],
    }


class SearchConsoleConnector(Connector):
    manifest = Manifest(
        id="search_console",
        name="Google Search Console",
        description="SEO-Lage der eigenen Websites beobachten: Suchanalyse (Klicks, Impressionen, CTR, Position), "
        "URL-Indexierung prüfen, Sitemaps ansehen und (nach Freigabe) einreichen. Kostenlose API; nutzt die "
        "Anmeldung des Google-Connectors.",
        category="web",
        auth="oauth2",
        secrets=[],  # keine eigenen Zugangsdaten: OAuth-Client und Token gehören dem Google-Connector
        setup_instructions="Voraussetzung: Google-Connector eingerichtet und verbunden. In der Google Cloud Console "
        "die „Google Search Console API“ aktivieren. Fehlt der Search-Console-Scope (Token aus der Zeit davor), "
        "Jarvis einmal neu mit Google verbinden und der Search-Console-Berechtigung zustimmen.",
        docs_url="https://developers.google.com/webmaster-tools/v1/api_reference_index",
        mcp_note="Kein offizieller Google-MCP-Server für die Search Console; Jarvis nutzt die REST-API direkt.",
        default_enabled=True,
    )

    # ------------------------------------------------------------------ Google-Anmeldung (geteilt)
    def _google(self) -> Any:
        google = self.state.connectors.connectors.get("google")
        if google is None:
            raise ToolUserError("Der Google-Connector fehlt – Search Console nutzt dessen Anmeldung.")
        return google

    def missing_secrets(self) -> list[str]:
        try:
            return self._google().missing_secrets()
        except ToolUserError:
            return ["google-oauth-client"]

    async def _authorize(self, *, write: bool) -> str:
        """Access-Token des Google-Connectors, nachdem der benötigte Scope geprüft wurde."""
        google = self._google()
        scopes = await google.granted_scopes()  # wirft ToolUserError, wenn Google nicht verbunden ist
        if scopes is not None:
            allowed = SCOPE_WRITE in scopes or (not write and SCOPE_READONLY in scopes)
            if not allowed:
                raise ToolUserError(scope_message(write))
        return await google.access_token()

    async def health(self) -> Health:
        try:
            scopes = await self._google().granted_scopes()
        except ToolUserError as e:
            return Health("error", str(e))
        if scopes is None:
            return Health("ok", "Google-Verbindung steht (Scopes von Google nicht gemeldet)")
        have_read = SCOPE_READONLY in scopes or SCOPE_WRITE in scopes
        if not have_read:
            return Health("degraded", scope_message(False))
        if SCOPE_WRITE not in scopes:
            return Health(
                "degraded", "Lesezugriff vorhanden; zum Einreichen von Sitemaps fehlt der Scope „webmasters“."
            )
        return Health("ok", "Search-Console-Zugriff vorhanden (Lesen und Sitemaps)")

    # ------------------------------------------------------------------ HTTP
    async def _request(self, method: str, url: str, *, write: bool = False, json: dict[str, Any] | None = None) -> Any:
        token = await self._authorize(write=write)
        headers = {"Authorization": f"Bearer {token}"}
        attempt = 0
        while True:
            try:
                async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as c:
                    resp = await c.request(method, url, headers=headers, json=json)
            except httpx.HTTPError as e:
                if attempt >= MAX_RETRIES:
                    raise ToolUserError(
                        f"Die Search Console ist nicht erreichbar ({type(e).__name__}). Internetverbindung prüfen "
                        "und später erneut versuchen."
                    ) from None
                wait = 2.0**attempt
            else:
                if resp.status_code < 400:
                    if not resp.content:
                        return {}
                    try:
                        return resp.json()
                    except ValueError:
                        raise ToolUserError("Die Search Console lieferte eine unlesbare Antwort.") from None
                if resp.status_code not in _RETRY_STATUS or attempt >= MAX_RETRIES:
                    raise map_http_error(resp, write=write)
                wait = _retry_wait(resp, attempt)
            attempt += 1
            logger.info("Search Console: Wiederholung %d/%d in %.0f s", attempt, MAX_RETRIES, wait)
            await asyncio.sleep(wait)

    async def _list_properties(self) -> list[dict[str, Any]]:
        data = await self._request("GET", f"{WEBMASTERS_API}/sites")
        return [s for s in data.get("siteEntry", []) or [] if isinstance(s, dict) and s.get("siteUrl")]

    async def _property(self, raw: Any) -> str:
        """Property wie von Google erwartet: ``sc-domain:…`` oder URL-Präfix mit ``https://…/``.

        Eine nackte Domain (``pje-systems.de``) wird gegen die Properties des Kontos aufgelöst.
        """
        prop = str(raw or "").strip()
        if not prop:
            raise ToolUserError("property fehlt (z. B. „sc-domain:pje-systems.de“ oder „https://pje-systems.de/“).")
        if prop.startswith("sc-domain:"):
            if not _DOMAIN_RE.match(prop.removeprefix("sc-domain:").lower()):
                raise ToolUserError(f"Ungültige Domain-Property: {prop}")
            return prop
        if prop.startswith(("http://", "https://")):
            return prop if prop.endswith("/") else prop + "/"
        domain = prop.lower().removeprefix("www.")
        if not _DOMAIN_RE.match(domain):
            raise ToolUserError(
                f"Ungültige Property „{prop[:80]}“. Erlaubt: „sc-domain:beispiel.de“, „https://beispiel.de/“ "
                "oder eine Domain wie „beispiel.de“."
            )
        known = {s["siteUrl"] for s in await self._list_properties()}
        for candidate in (f"sc-domain:{domain}", f"https://{domain}/", f"https://www.{domain}/", f"http://{domain}/"):
            if candidate in known:
                return candidate
        raise ToolUserError(
            f"Für „{prop[:80]}“ gibt es im Google-Konto keine Search-Console-Property. Vorhanden: "
            + (", ".join(sorted(known)) or "keine")
        )

    @staticmethod
    def _site_path(prop: str) -> str:
        return f"{WEBMASTERS_API}/sites/{urllib.parse.quote(prop, safe='')}"

    # ------------------------------------------------------------------ Werkzeuge
    def tools(self) -> list[ToolSpec]:
        S = "search_console"

        async def list_sites(ctx: ToolContext, a: dict[str, Any]) -> Any:
            sites = []
            for s in await self._list_properties():
                level = s.get("permissionLevel", "")
                url = s["siteUrl"]
                sites.append(
                    {
                        "property": url,
                        "type": "Domain-Property" if url.startswith("sc-domain:") else "URL-Präfix-Property",
                        "permission_level": level,
                        "permission": _PERMISSION_DE.get(level, level),
                        "can_query": level != "siteUnverifiedUser",
                        "can_submit_sitemaps": level in ("siteOwner", "siteFullUser"),
                    }
                )
            return {"count": len(sites), "sites": sites}

        async def search_analytics(ctx: ToolContext, a: dict[str, Any]) -> Any:
            prop = await self._property(a.get("property"))
            start, end = resolve_range(a)
            dims = a.get("dimensions", ["query"])
            if not isinstance(dims, list) or any(d not in DIMENSIONS for d in dims) or len(set(dims)) != len(dims):
                raise ToolUserError(f"dimensions: eindeutige Werte aus {', '.join(DIMENSIONS)} angeben.")
            limit = a.get("rowLimit", DEFAULT_ROW_LIMIT)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_ROW_LIMIT:
                raise ToolUserError(f"rowLimit muss eine ganze Zahl von 1 bis {MAX_ROW_LIMIT} sein.")
            body: dict[str, Any] = {
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "dimensions": dims,
                "rowLimit": limit,
            }
            flt = a.get("filter")
            if flt:
                if not isinstance(flt, dict):
                    raise ToolUserError("filter muss ein Objekt mit dimension, expression und optional operator sein.")
                dim, expr = flt.get("dimension"), str(flt.get("expression", "")).strip()
                op = flt.get("operator", "contains")
                if dim not in FILTER_DIMENSIONS or op not in FILTER_OPERATORS or not expr:
                    raise ToolUserError(
                        f"filter: dimension ({', '.join(FILTER_DIMENSIONS)}), expression und operator "
                        f"({', '.join(FILTER_OPERATORS)}) angeben."
                    )
                body["dimensionFilterGroups"] = [
                    {"groupType": "and", "filters": [{"dimension": dim, "operator": op, "expression": expr[:500]}]}
                ]
            data = await self._request("POST", f"{self._site_path(prop)}/searchAnalytics/query", json=body)
            raw_rows = [r for r in data.get("rows", []) or [] if isinstance(r, dict)]
            rows = [_row(r) for r in raw_rows]
            return {
                "property": prop,
                "startDate": body["startDate"],
                "endDate": body["endDate"],
                "dimensions": dims,
                "row_count": len(rows),
                "totals_of_returned_rows": _totals(raw_rows),
                "rows": rows,
                "note": "ctr ist ein Anteil (0.05 = 5 %); position ist die durchschnittliche Ranking-Position.",
            }

        async def inspect_url(ctx: ToolContext, a: dict[str, Any]) -> Any:
            prop = await self._property(a.get("property"))
            url = str(a.get("inspectionUrl", "")).strip()
            if not url.startswith(("http://", "https://")):
                raise ToolUserError("inspectionUrl muss eine vollständige URL mit https:// sein.")
            if not url_in_property(url, prop):
                raise ToolUserError(f"Die URL gehört nicht zur Property {prop} – Google würde die Prüfung ablehnen.")
            data = await self._request(
                "POST",
                INSPECT_API,
                json={"inspectionUrl": url, "siteUrl": prop, "languageCode": "de-DE"},
            )
            return {"property": prop, "url": url, **summarize_inspection(data.get("inspectionResult") or {})}

        async def list_sitemaps(ctx: ToolContext, a: dict[str, Any]) -> Any:
            prop = await self._property(a.get("property"))
            data = await self._request("GET", f"{self._site_path(prop)}/sitemaps")
            maps = [summarize_sitemap(s) for s in data.get("sitemap", []) or [] if isinstance(s, dict)]
            return {"property": prop, "count": len(maps), "sitemaps": maps}

        async def submit_sitemap(ctx: ToolContext, a: dict[str, Any]) -> Any:
            prop = await self._property(a.get("property"))
            sitemap = str(a.get("sitemapUrl", "")).strip()
            if not sitemap.startswith(("http://", "https://")):
                raise ToolUserError(
                    "sitemapUrl muss eine vollständige URL sein (z. B. https://pje-systems.de/sitemap.xml)."
                )
            if not url_in_property(sitemap, prop):
                raise ToolUserError(f"Die Sitemap-URL gehört nicht zur Property {prop}.")
            quoted = urllib.parse.quote(sitemap, safe="")
            await self._request("PUT", f"{self._site_path(prop)}/sitemaps/{quoted}", write=True)
            return {
                "submitted": True,
                "property": prop,
                "sitemap": sitemap,
                "note": "Google verarbeitet die Sitemap asynchron. Status später mit gsc_list_sitemaps prüfen.",
            }

        prop_param = {
            "type": "string",
            "description": "Property: „sc-domain:pje-systems.de“ (Domain) oder „https://pje-systems.de/“ "
            "(URL-Präfix). Eine nackte Domain wird gegen die Properties des Kontos aufgelöst.",
        }
        return [
            ToolSpec(
                "gsc_list_sites",
                "Listet die Google-Search-Console-Properties des verbundenen Google-Kontos mit Property-Typ "
                "(Domain oder URL-Präfix) und Berechtigungsstufe.",
                _obj({}),
                list_sites,
                "read",
                S,
                timeout_s=120,
            ),
            ToolSpec(
                "gsc_search_analytics",
                "Suchanalyse einer Property aus der Google Search Console: Klicks, Impressionen, CTR und "
                "durchschnittliche Position, gruppiert nach Dimensionen (query, page, country, device, date). "
                "Standardzeitraum: die letzten 28 Tage bis vorgestern (Daten der letzten 2 Tage sind noch "
                "unvollständig). Optional Filter auf Suchanfrage oder Seite.",
                _obj(
                    {
                        "property": prop_param,
                        "startDate": {
                            "type": "string",
                            "description": "Beginn, JJJJ-MM-TT (Standard: 28 Tage vor endDate)",
                        },
                        "endDate": {"type": "string", "description": "Ende, JJJJ-MM-TT (Standard: heute minus 2 Tage)"},
                        "dimensions": {
                            "type": "array",
                            "items": {"type": "string", "enum": list(DIMENSIONS)},
                            "maxItems": len(DIMENSIONS),
                            "description": "Gruppierung (Standard: query). Leere Liste = nur Gesamtsumme.",
                        },
                        "rowLimit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": MAX_ROW_LIMIT,
                            "description": f"Maximale Zeilenzahl (Standard {DEFAULT_ROW_LIMIT}, max. {MAX_ROW_LIMIT})",
                        },
                        "filter": {
                            "type": "object",
                            "description": "Optionaler Filter auf Seite oder Suchanfrage",
                            "properties": {
                                "dimension": {"type": "string", "enum": list(FILTER_DIMENSIONS)},
                                "operator": {
                                    "type": "string",
                                    "enum": list(FILTER_OPERATORS),
                                    "description": "Standard: contains",
                                },
                                "expression": {"type": "string", "maxLength": 500},
                            },
                            "required": ["dimension", "expression"],
                            "additionalProperties": False,
                        },
                    },
                    ["property"],
                ),
                search_analytics,
                "read",
                S,
                untrusted_output=True,  # Suchanfragen und Seiten-URLs stammen von Dritten
                timeout_s=120,
            ),
            ToolSpec(
                "gsc_inspect_url",
                "Prüft eine URL über die URL-Prüfung der Google Search Console: Indexierungsstatus, "
                "Coverage-Status, letzter Crawl, von Google gewählte Canonical-URL, robots.txt-Status und Fehler. "
                "Die URL muss zur Property gehören.",
                _obj(
                    {
                        "property": prop_param,
                        "inspectionUrl": {
                            "type": "string",
                            "description": "Vollständige URL, z. B. https://pje-systems.de/blog/x",
                        },
                    },
                    ["property", "inspectionUrl"],
                ),
                inspect_url,
                "read",
                S,
                timeout_s=120,
            ),
            ToolSpec(
                "gsc_list_sitemaps",
                "Listet die bei der Google Search Console eingereichten Sitemaps einer Property mit Status, "
                "Einreichungs- und Abrufdatum, Fehlern, Warnungen und Anzahl eingereichter/indexierter URLs.",
                _obj({"property": prop_param}, ["property"]),
                list_sitemaps,
                "read",
                S,
                timeout_s=120,
            ),
            ToolSpec(
                "gsc_submit_sitemap",
                "[Risiko: high] Reicht eine Sitemap bei der Google Search Console ein (verändert Daten bei Google, "
                "läuft über die Freigabe). Die Sitemap-URL muss zur Property gehören; braucht mindestens "
                "Vollzugriff und den Scope „webmasters“.",
                _obj(
                    {
                        "property": prop_param,
                        "sitemapUrl": {
                            "type": "string",
                            "description": "Vollständige Sitemap-URL, z. B. https://pje-systems.de/sitemap.xml",
                        },
                    },
                    ["property", "sitemapUrl"],
                ),
                submit_sitemap,
                "high",
                S,
                writes=True,
                timeout_s=120,
                summarize=lambda a: (
                    f"Sitemap {a.get('sitemapUrl')} bei Google Search Console für {a.get('property')} einreichen"
                ),
            ),
        ]


def _retry_wait(resp: httpx.Response, attempt: int) -> float:
    try:
        hinted = float(resp.headers.get("retry-after", ""))
    except ValueError:
        hinted = 0.0
    return min(max(hinted, 2.0**attempt), MAX_RETRY_WAIT_S)
