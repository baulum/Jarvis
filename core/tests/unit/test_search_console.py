"""Search-Console-Connector: gemockte HTTP-Antworten, keine Live-Aufrufe."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from jarvis.connectors import search_console as sc
from jarvis.connectors.search_console import SCOPE_READONLY, SCOPE_WRITE, SearchConsoleConnector
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext, ToolSpec

SITE = "sc-domain:pje-systems.de"
ENC_SITE = "sc-domain%3Apje-systems.de"
TOKEN = "ya29.geheim-token"


class FakeGoogle:
    def __init__(self, scopes: frozenset[str] | None, *, connected: bool = True) -> None:
        self.scopes = scopes
        self.connected = connected

    async def granted_scopes(self) -> frozenset[str] | None:
        if not self.connected:
            raise ToolUserError("Google ist nicht verbunden (OAuth fehlt).")
        return self.scopes

    async def access_token(self) -> str:
        return TOKEN

    def missing_secrets(self) -> list[str]:
        return []


class Recorder:
    """Beantwortet Anfragen der Reihe nach bzw. über eine Funktion und merkt sie sich."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.queue = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]


def make(
    monkeypatch: pytest.MonkeyPatch,
    rec: Recorder,
    scopes: frozenset[str] | None = frozenset({SCOPE_READONLY, SCOPE_WRITE}),
    sleeps: list[float] | None = None,
    google: FakeGoogle | None = None,
) -> dict[str, ToolSpec]:
    real = httpx.AsyncClient

    def factory(*, timeout: Any = None, **kw: Any) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(rec), timeout=timeout)

    async def fake_sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    monkeypatch.setattr(sc.httpx, "AsyncClient", factory)
    monkeypatch.setattr(sc.asyncio, "sleep", fake_sleep)
    state = SimpleNamespace(connectors=SimpleNamespace(connectors={"google": google or FakeGoogle(scopes)}))
    conn = SearchConsoleConnector(state)  # type: ignore[arg-type]
    return {t.name: t for t in conn.tools()}


def ctx() -> ToolContext:
    return SimpleNamespace()  # type: ignore[return-value]


def ok(data: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=data)


def err(status: int, message: str = "", reason: str = "", headers: dict[str, str] | None = None) -> httpx.Response:
    body = {"error": {"code": status, "message": message, "status": reason, "errors": [{"reason": reason}]}}
    return httpx.Response(status, json=body, headers=headers)


# ---------------------------------------------------------------------- gsc_list_sites
async def test_list_sites_returns_type_and_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(
        ok(
            {
                "siteEntry": [
                    {"siteUrl": "sc-domain:pje-systems.de", "permissionLevel": "siteOwner"},
                    {"siteUrl": "https://apohr-portal.de/", "permissionLevel": "siteRestrictedUser"},
                    {"siteUrl": "https://alt.example/", "permissionLevel": "siteUnverifiedUser"},
                ]
            }
        )
    )
    tools = make(monkeypatch, rec)
    res = await tools["gsc_list_sites"].handler(ctx(), {})
    assert res["count"] == 3
    domain, prefix, unverified = res["sites"]
    assert domain["type"] == "Domain-Property" and domain["permission"] == "Inhaber"
    assert domain["can_submit_sitemaps"] is True
    assert prefix["type"] == "URL-Präfix-Property" and prefix["can_submit_sitemaps"] is False
    assert unverified["can_query"] is False
    req = rec.requests[0]
    assert req.method == "GET" and str(req.url) == "https://www.googleapis.com/webmasters/v3/sites"
    assert req.headers["authorization"] == f"Bearer {TOKEN}"


# ---------------------------------------------------------------------- gsc_search_analytics
async def test_search_analytics_defaults_and_result(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(
        ok(
            {
                "rows": [
                    {"keys": ["webdesign"], "clicks": 10, "impressions": 200, "ctr": 0.05, "position": 4.26},
                    {"keys": ["seo"], "clicks": 0, "impressions": 100, "ctr": 0.0, "position": 12.0},
                ]
            }
        )
    )
    tools = make(monkeypatch, rec)
    res = await tools["gsc_search_analytics"].handler(ctx(), {"property": SITE})
    req = rec.requests[0]
    assert req.method == "POST"
    assert str(req.url).endswith(f"/sites/{ENC_SITE}/searchAnalytics/query")
    body = json.loads(req.content)
    assert body["dimensions"] == ["query"] and body["rowLimit"] == sc.DEFAULT_ROW_LIMIT
    assert "dimensionFilterGroups" not in body
    start, end = date.fromisoformat(body["startDate"]), date.fromisoformat(body["endDate"])
    assert (end - start).days == 27  # 28 Tage inklusive
    assert res["row_count"] == 2
    assert res["rows"][0] == {"keys": ["webdesign"], "clicks": 10, "impressions": 200, "ctr": 0.05, "position": 4.3}
    assert res["totals_of_returned_rows"] == {"clicks": 10, "impressions": 300, "ctr": 0.0333, "position": 6.8}


def test_default_range_ends_two_days_before_today() -> None:
    start, end = sc.resolve_range({}, today=date(2026, 11, 16))
    assert end == date(2026, 11, 14) and start == date(2026, 10, 18)


async def test_search_analytics_passes_parameters_and_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(ok({}))
    tools = make(monkeypatch, rec)
    res = await tools["gsc_search_analytics"].handler(
        ctx(),
        {
            "property": "https://apohr-portal.de/",
            "startDate": "2026-10-01",
            "endDate": "2026-10-31",
            "dimensions": ["page", "device"],
            "rowLimit": 25000,
            "filter": {"dimension": "page", "operator": "contains", "expression": "/blog/"},
        },
    )
    body = json.loads(rec.requests[0].content)
    assert body == {
        "startDate": "2026-10-01",
        "endDate": "2026-10-31",
        "dimensions": ["page", "device"],
        "rowLimit": 25000,
        "dimensionFilterGroups": [
            {"groupType": "and", "filters": [{"dimension": "page", "operator": "contains", "expression": "/blog/"}]}
        ],
    }
    assert "/sites/https%3A%2F%2Fapohr-portal.de%2F/searchAnalytics/query" in str(rec.requests[0].url)
    assert res["row_count"] == 0 and res["rows"] == []


@pytest.mark.parametrize(
    "args",
    [
        {"dimensions": ["foo"]},
        {"dimensions": ["query", "query"]},
        {"rowLimit": 25001},
        {"rowLimit": 0},
        {"startDate": "2026-11-10", "endDate": "2026-11-01"},
        {"startDate": "gestern"},
        {"filter": {"dimension": "country", "expression": "de"}},
        {"filter": {"dimension": "query", "operator": "weird", "expression": "x"}},
        {"filter": {"dimension": "query", "expression": " "}},
    ],
)
async def test_search_analytics_rejects_bad_arguments(monkeypatch: pytest.MonkeyPatch, args: dict[str, Any]) -> None:
    rec = Recorder(ok({}))
    tools = make(monkeypatch, rec)
    with pytest.raises(ToolUserError):
        await tools["gsc_search_analytics"].handler(ctx(), {"property": SITE, **args})
    assert rec.requests == []  # nichts an Google gesendet


async def test_bare_domain_is_resolved_against_account_properties(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(
        ok({"siteEntry": [{"siteUrl": "sc-domain:pje-systems.de", "permissionLevel": "siteOwner"}]}),
        ok({"rows": []}),
    )
    tools = make(monkeypatch, rec)
    res = await tools["gsc_search_analytics"].handler(ctx(), {"property": "pje-systems.de"})
    assert res["property"] == SITE
    assert rec.requests[0].url.path == "/webmasters/v3/sites"
    assert ENC_SITE in str(rec.requests[1].url)


async def test_unknown_bare_domain_lists_available_properties(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(ok({"siteEntry": [{"siteUrl": "sc-domain:pje-systems.de", "permissionLevel": "siteOwner"}]}))
    tools = make(monkeypatch, rec)
    with pytest.raises(ToolUserError, match=r"sc-domain:pje-systems\.de"):
        await tools["gsc_search_analytics"].handler(ctx(), {"property": "fremd.de"})


# ---------------------------------------------------------------------- gsc_inspect_url
INSPECTION = {
    "inspectionResult": {
        "inspectionResultLink": "https://search.google.com/search-console/inspect?x=1",
        "indexStatusResult": {
            "verdict": "PASS",
            "coverageState": "Submitted and indexed",
            "robotsTxtState": "ALLOWED",
            "indexingState": "INDEXING_ALLOWED",
            "lastCrawlTime": "2026-11-12T08:15:00Z",
            "pageFetchState": "SUCCESSFUL",
            "googleCanonical": "https://pje-systems.de/blog/a",
            "userCanonical": "https://pje-systems.de/blog/a",
            "crawledAs": "MOBILE",
            "sitemap": ["https://pje-systems.de/sitemap.xml"],
        },
    }
}


async def test_inspect_url_maps_index_status(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(ok(INSPECTION))
    tools = make(monkeypatch, rec)
    res = await tools["gsc_inspect_url"].handler(
        ctx(), {"property": SITE, "inspectionUrl": "https://www.pje-systems.de/blog/a"}
    )
    req = rec.requests[0]
    assert str(req.url) == sc.INSPECT_API
    assert json.loads(req.content) == {
        "inspectionUrl": "https://www.pje-systems.de/blog/a",
        "siteUrl": SITE,
        "languageCode": "de-DE",
    }
    assert res["verdict"] == "PASS" and res["summary"] == "Indexiert: Submitted and indexed"
    assert res["last_crawl_time"] == "2026-11-12T08:15:00Z"
    assert res["google_canonical"] == "https://pje-systems.de/blog/a"
    assert res["robots_txt_state"] == "ALLOWED"
    assert res["issues"] == []


async def test_inspect_url_reports_problems(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = {
        "inspectionResult": {
            "indexStatusResult": {
                "verdict": "FAIL",
                "coverageState": "Blocked by robots.txt",
                "robotsTxtState": "DISALLOWED",
                "indexingState": "BLOCKED_BY_META_TAG",
                "pageFetchState": "BLOCKED_ROBOTS_TXT",
                "googleCanonical": "https://pje-systems.de/b",
                "userCanonical": "https://pje-systems.de/a",
            },
            "richResultsResult": {
                "detectedItems": [{"richResultType": "FAQ", "items": [{"issues": [{"issueMessage": "Feld fehlt"}]}]}]
            },
        }
    }
    tools = make(monkeypatch, Recorder(ok(bad)))
    res = await tools["gsc_inspect_url"].handler(ctx(), {"property": SITE, "inspectionUrl": "https://pje-systems.de/a"})
    text = " | ".join(res["issues"])
    assert res["summary"].startswith("Nicht indexiert (Fehler)")
    for part in ("robots.txt", "BLOCKED_BY_META_TAG", "BLOCKED_ROBOTS_TXT", "Canonical", "Feld fehlt"):
        assert part in text


@pytest.mark.parametrize(
    ("prop", "url"),
    [
        (SITE, "https://fremd.de/seite"),
        (SITE, "https://pje-systems.de.evil.example/x"),
        ("https://pje-systems.de/", "https://apohr-portal.de/"),
        (SITE, "kein-url"),
    ],
)
async def test_inspect_url_rejects_foreign_urls(monkeypatch: pytest.MonkeyPatch, prop: str, url: str) -> None:
    rec = Recorder(ok(INSPECTION))
    tools = make(monkeypatch, rec)
    with pytest.raises(ToolUserError):
        await tools["gsc_inspect_url"].handler(ctx(), {"property": prop, "inspectionUrl": url})
    assert rec.requests == []


# ---------------------------------------------------------------------- gsc_list_sitemaps
async def test_list_sitemaps(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(
        ok(
            {
                "sitemap": [
                    {
                        "path": "https://pje-systems.de/sitemap.xml",
                        "lastSubmitted": "2026-11-01T10:00:00Z",
                        "lastDownloaded": "2026-11-10T04:00:00Z",
                        "isPending": False,
                        "isSitemapsIndex": False,
                        "type": "sitemap",
                        "warnings": "0",
                        "errors": "2",
                        "contents": [{"type": "web", "submitted": "40", "indexed": "31"}],
                    },
                    {"path": "https://pje-systems.de/blog.xml", "isPending": True, "errors": "0", "warnings": "0"},
                ]
            }
        )
    )
    tools = make(monkeypatch, rec)
    res = await tools["gsc_list_sitemaps"].handler(ctx(), {"property": SITE})
    assert rec.requests[0].method == "GET"
    assert str(rec.requests[0].url).endswith(f"/sites/{ENC_SITE}/sitemaps")
    first, second = res["sitemaps"]
    assert first["status"] == "Fehler" and first["errors"] == 2
    assert first["contents"] == [{"type": "web", "submitted": 40, "indexed": 31}]
    assert second["status"] == "Ausstehend"


# ---------------------------------------------------------------------- gsc_submit_sitemap
async def test_submit_sitemap_is_high_risk_write_and_puts(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(httpx.Response(204))
    tools = make(monkeypatch, rec)
    spec = tools["gsc_submit_sitemap"]
    assert spec.risk == "high" and spec.writes is True
    assert "[Risiko: high]" in spec.description
    for name in ("gsc_list_sites", "gsc_search_analytics", "gsc_inspect_url", "gsc_list_sitemaps"):
        assert tools[name].risk == "read" and tools[name].writes is False
    assert "pje-systems.de/sitemap.xml" in spec.describe(
        {"property": SITE, "sitemapUrl": "https://pje-systems.de/sitemap.xml"}
    )
    res = await spec.handler(ctx(), {"property": SITE, "sitemapUrl": "https://pje-systems.de/sitemap.xml"})
    req = rec.requests[0]
    assert req.method == "PUT"
    assert req.url.raw_path.decode().endswith(f"/sites/{ENC_SITE}/sitemaps/https%3A%2F%2Fpje-systems.de%2Fsitemap.xml")
    assert res["submitted"] is True


async def test_submit_sitemap_rejects_foreign_sitemap(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(httpx.Response(204))
    tools = make(monkeypatch, rec)
    with pytest.raises(ToolUserError, match="gehört nicht zur Property"):
        await tools["gsc_submit_sitemap"].handler(
            ctx(), {"property": SITE, "sitemapUrl": "https://fremd.de/sitemap.xml"}
        )
    assert rec.requests == []


# ---------------------------------------------------------------------- Scopes und Anmeldung
async def test_missing_scope_gives_clear_german_error_without_calling_google(monkeypatch: pytest.MonkeyPatch) -> None:
    gmail_only = frozenset({"https://www.googleapis.com/auth/gmail.readonly"})
    rec = Recorder(ok({}))
    tools = make(monkeypatch, rec, scopes=gmail_only)
    calls = [
        ("gsc_list_sites", {}),
        ("gsc_search_analytics", {"property": SITE}),
        ("gsc_inspect_url", {"property": SITE, "inspectionUrl": "https://pje-systems.de/"}),
        ("gsc_list_sitemaps", {"property": SITE}),
        ("gsc_submit_sitemap", {"property": SITE, "sitemapUrl": "https://pje-systems.de/sitemap.xml"}),
    ]
    for name, args in calls:
        with pytest.raises(ToolUserError, match="neu mit Google verbinden") as exc:
            await tools[name].handler(ctx(), args)
        assert "webmasters" in str(exc.value)
    assert rec.requests == []


async def test_readonly_scope_allows_reads_but_not_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(ok({"siteEntry": []}))
    tools = make(monkeypatch, rec, scopes=frozenset({SCOPE_READONLY}))
    assert (await tools["gsc_list_sites"].handler(ctx(), {}))["count"] == 0
    with pytest.raises(ToolUserError, match="Scope „webmasters“"):
        await tools["gsc_submit_sitemap"].handler(
            ctx(), {"property": SITE, "sitemapUrl": "https://pje-systems.de/sitemap.xml"}
        )
    assert len(rec.requests) == 1


async def test_unknown_scopes_still_try_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(ok({"siteEntry": []}))
    tools = make(monkeypatch, rec, scopes=None)
    assert (await tools["gsc_list_sites"].handler(ctx(), {}))["count"] == 0


async def test_google_scope_error_from_api_is_translated(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(err(403, "Request had insufficient authentication scopes.", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"))
    tools = make(monkeypatch, rec, scopes=None)
    with pytest.raises(ToolUserError, match="neu mit Google verbinden"):
        await tools["gsc_list_sites"].handler(ctx(), {})


async def test_not_connected_to_google(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = make(monkeypatch, Recorder(ok({})), google=FakeGoogle(None, connected=False))
    with pytest.raises(ToolUserError, match="nicht verbunden"):
        await tools["gsc_list_sites"].handler(ctx(), {})


# ---------------------------------------------------------------------- Fehler und Wiederholungen
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (err(401, "Invalid Credentials", "UNAUTHENTICATED"), "abgelaufen oder wurde widerrufen"),
        (
            err(403, "Google Search Console API has not been used in project 1 before", "SERVICE_DISABLED"),
            "nicht aktiviert",
        ),
        (err(403, "User does not have sufficient permission for site", "forbidden"), "keine ausreichende Berechtigung"),
        (err(404, "Not found", "notFound"), "nicht gefunden"),
        (err(400, "Invalid date range", "invalidArgument"), "Search Console: Invalid date range"),
    ],
)
async def test_api_errors_are_german_and_actionable(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response, expected: str
) -> None:
    sleeps: list[float] = []
    tools = make(monkeypatch, Recorder(response), sleeps=sleeps)
    with pytest.raises(ToolUserError) as exc:
        await tools["gsc_list_sitemaps"].handler(ctx(), {"property": SITE})
    assert expected in str(exc.value)
    assert sleeps == []  # 4xx wird nicht wiederholt
    assert TOKEN not in str(exc.value)


async def test_retries_429_and_5xx_with_backoff_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    rec = Recorder(
        err(429, "quota", "rateLimitExceeded"), err(503, "unavailable", "backendError"), ok({"siteEntry": []})
    )
    tools = make(monkeypatch, rec, sleeps=sleeps)
    res = await tools["gsc_list_sites"].handler(ctx(), {})
    assert res["count"] == 0
    assert len(rec.requests) == 3
    assert sleeps == [1.0, 2.0]


async def test_retry_after_header_is_respected_and_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    rec = Recorder(
        err(429, "quota", "x", headers={"Retry-After": "7"}),
        err(429, "q", "x", headers={"Retry-After": "999"}),
        ok({"siteEntry": []}),
    )
    tools = make(monkeypatch, rec, sleeps=sleeps)
    await tools["gsc_list_sites"].handler(ctx(), {})
    assert sleeps == [7.0, sc.MAX_RETRY_WAIT_S]


async def test_gives_up_after_retries_with_clear_message(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    rec = Recorder(err(500, "boom", "backendError"))
    tools = make(monkeypatch, rec, sleeps=sleeps)
    with pytest.raises(ToolUserError, match="später erneut versuchen"):
        await tools["gsc_list_sites"].handler(ctx(), {})
    assert len(rec.requests) == sc.MAX_RETRIES + 1
    assert sleeps == [1.0, 2.0, 4.0]


async def test_network_errors_are_retried_and_reported_without_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns kaputt 10.0.0.1", request=request)

    sleeps: list[float] = []
    tools = make(monkeypatch, boom, sleeps=sleeps)  # type: ignore[arg-type]
    with pytest.raises(ToolUserError) as exc:
        await tools["gsc_list_sites"].handler(ctx(), {})
    assert "nicht erreichbar" in str(exc.value) and "ConnectError" in str(exc.value)
    assert "10.0.0.1" not in str(exc.value) and "Traceback" not in str(exc.value)
    assert len(sleeps) == sc.MAX_RETRIES


# ---------------------------------------------------------------------- Connector-Rahmen
async def test_health_reflects_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    def conn(scopes: frozenset[str] | None, connected: bool = True) -> SearchConsoleConnector:
        state = SimpleNamespace(
            connectors=SimpleNamespace(connectors={"google": FakeGoogle(scopes, connected=connected)})
        )
        return SearchConsoleConnector(state)  # type: ignore[arg-type]

    assert (await conn(frozenset({SCOPE_READONLY, SCOPE_WRITE})).health()).status == "ok"
    ro = await conn(frozenset({SCOPE_READONLY})).health()
    assert ro.status == "degraded" and "webmasters" in ro.message
    none = await conn(frozenset({"x"})).health()
    assert none.status == "degraded" and "neu mit Google verbinden" in none.message
    assert (await conn(None, connected=False).health()).status == "error"


def test_manifest_reuses_google_login_and_scopes() -> None:
    from jarvis.connectors.google import SCOPES

    assert SearchConsoleConnector.manifest.secrets == []  # kein eigener Token-Speicher
    assert SCOPE_READONLY in SCOPES and SCOPE_WRITE in SCOPES
    state = SimpleNamespace(connectors=SimpleNamespace(connectors={}))
    assert SearchConsoleConnector(state).missing_secrets() == ["google-oauth-client"]  # type: ignore[arg-type]


async def test_google_connector_records_granted_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.connectors import google as gmod

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "a", "expires_in": 3600, "scope": f"{SCOPE_READONLY} openid"})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        gmod.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), timeout=kw.get("timeout"))
    )
    g = gmod.GoogleConnector.__new__(gmod.GoogleConnector)
    g._access = None
    g._scopes = None
    secrets = {
        "google-oauth-client": json.dumps({"installed": {"client_id": "id", "client_secret": "s"}}),
        "google-oauth-token": json.dumps({"refresh_token": "r"}),
    }
    g.secret = secrets.get  # type: ignore[method-assign,assignment]
    assert await g.granted_scopes() == frozenset({SCOPE_READONLY, "openid"})
    assert await g.access_token() == "a"
