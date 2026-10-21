"""Routendienst: Geocoding, Routing, Alternativen und Fehlerfälle – alle HTTP-Antworten gemockt."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from jarvis.connectors.routing import RoutingConnector, format_distance, format_duration
from jarvis.routing.providers import (
    MinInterval,
    NominatimGeocoder,
    Place,
    RoutingError,
    TTLCache,
    ValhallaRouter,
    decode_polyline,
    parse_coordinate,
    simplify,
)
from jarvis.tools.artifacts import artifacts
from jarvis.tools.executor import ToolUserError

BERLIN = {"lat": "52.5170365", "lon": "13.3888599", "display_name": "Berlin, Deutschland"}
POTSDAM = {"lat": "52.4009309", "lon": "13.0591397", "display_name": "Potsdam, Brandenburg, Deutschland"}


def _osrm_route(distance: float, duration: float, summary: str) -> dict[str, Any]:
    return {
        "distance": distance,
        "duration": duration,
        "geometry": {"type": "LineString", "coordinates": [[13.3888, 52.517], [13.2, 52.45], [13.0591, 52.4009]]},
        "legs": [{"summary": summary}],
    }


class Backend:
    """Records requests and answers like Nominatim + OSRM would."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.osrm: Any = {
            "code": "Ok",
            "routes": [_osrm_route(35200.0, 2460.0, "A 115"), _osrm_route(38900.0, 2700.0, "B 1")],
        }
        self.osrm_status = 200
        self.fail: Exception | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail is not None:
            raise self.fail
        if request.url.path.endswith("/search"):
            q = request.url.params["q"].lower()
            rows = [BERLIN] if "berlin" in q else [POTSDAM] if "potsdam" in q else []
            return httpx.Response(200, json=rows)
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"status": 0})
        if "/route/v1/" in request.url.path:
            return httpx.Response(self.osrm_status, json=self.osrm)
        return httpx.Response(404)


def _connector(backend: Backend, **settings: Any) -> RoutingConnector:
    conn = RoutingConnector(SimpleNamespace(), transport=httpx.MockTransport(backend))
    conn.settings = settings

    async def no_sleep(_s: float) -> None:
        return None

    conn._limiter = MinInterval(1.0, sleep=no_sleep)
    return conn


async def _call(conn: RoutingConnector, tool: str, **args: Any) -> Any:
    spec = next(t for t in conn.tools() if t.name == tool)
    return await spec.handler(SimpleNamespace(), args)  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------------- pure helpers
def test_parse_coordinate_accepts_lat_lon_and_rejects_text() -> None:
    p = parse_coordinate("52.52, 13.405")
    assert p is not None and (p.lat, p.lon, p.source) == (52.52, 13.405, "coordinate")
    assert parse_coordinate("48.1374;11.5755") is not None
    assert parse_coordinate("Hauptstraße 5, Berlin") is None
    assert parse_coordinate("95, 13") is None  # latitude out of range


def _encode(points: list[tuple[float, float]], precision: int = 6) -> str:
    """Reference encoder (Google polyline algorithm) to build Valhalla shapes."""
    out, prev = [], (0, 0)
    for lat, lon in points:
        cur = (round(lat * 10**precision), round(lon * 10**precision))
        for delta in (cur[0] - prev[0], cur[1] - prev[1]):
            v = ~(delta << 1) if delta < 0 else delta << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        prev = cur
    return "".join(out)


SHAPE = _encode([(52.5, 13.4), (52.45, 13.2), (52.4, 13.0)])


def test_decode_polyline6() -> None:
    pts = decode_polyline(SHAPE, 6)
    assert pts == [pytest.approx((52.5, 13.4)), pytest.approx((52.45, 13.2)), pytest.approx((52.4, 13.0))]
    assert decode_polyline(_encode([(-33.86, 151.2)], 5), 5)[0] == pytest.approx((-33.86, 151.2))


def test_simplify_caps_geometry() -> None:
    line = [(52.0 + i * 1e-4, 13.0 + (i % 7) * 1e-4) for i in range(5000)]
    out = simplify(line, 1000)
    assert len(out) <= 1000 and out[0] == line[0] and out[-1] == line[-1]


def test_formatting_is_german() -> None:
    assert format_distance(35200) == "35,2 km"
    assert format_distance(420) == "420 m"
    assert format_duration(2460) == "41 min"
    assert format_duration(3 * 3600 + 5 * 60) == "3 h 05 min"


# ------------------------------------------------------------------------------------------- geocoding
async def test_geocoder_sets_user_agent_rate_limits_and_caches() -> None:
    backend = Backend()
    slept: list[float] = []
    now = [100.0]

    async def fake_sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    limiter = MinInterval(1.0, clock=lambda: now[0], sleep=fake_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(backend), headers={"User-Agent": "JARVIS-Test/1"}) as c:
        geo = NominatimGeocoder(c, limiter=limiter, cache=TTLCache())
        a = await geo.geocode("Berlin")
        b = await geo.geocode("  berlin ")  # same query → cache, no second request
        await geo.geocode("Potsdam")  # immediately afterwards → waits for the 1 s interval
    assert a[0].label == "Berlin, Deutschland" and b[0].lat == a[0].lat
    searches = [r for r in backend.requests if r.url.path.endswith("/search")]
    assert len(searches) == 2
    assert all(r.headers["User-Agent"] == "JARVIS-Test/1" for r in searches)
    assert searches[0].url.params["format"] == "jsonv2"
    assert slept and slept[0] == pytest.approx(1.0)


async def test_geocoder_rate_limited_response() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429))) as c:
        geo = NominatimGeocoder(c, limiter=MinInterval(0))
        with pytest.raises(RoutingError) as e:
            await geo.geocode("Berlin")
    assert e.value.kind == "rate_limited"


# ------------------------------------------------------------------------------------------- route_plan
async def test_route_plan_returns_metrics_alternatives_and_map_artifact() -> None:
    backend = Backend()
    conn = _connector(backend)
    out = await _call(conn, "route_plan", start="Berlin", destination="Potsdam", mode="fahrrad")
    assert out["mode"] == "fahrrad"
    assert [r["distance"] for r in out["routes"]] == ["35,2 km", "38,9 km"]
    assert out["routes"][0]["duration"] == "41 min" and out["routes"][0]["via"] == "A 115"
    assert out["fastest"] == 1
    route_req = next(r for r in backend.requests if "/route/v1/" in r.url.path)
    # FOSSGIS layout: profile in the host path, "driving" in the OSRM path, lon,lat order
    assert route_req.url.host == "routing.openstreetmap.de"
    assert route_req.url.path.startswith("/routed-bike/route/v1/driving/13.388860,52.517037;")
    assert route_req.url.params["alternatives"] == "2"
    # the geometry is not in the model's result but in the display artifact
    assert "geometry" not in json.dumps(out)
    display = artifacts.take_from_text(json.dumps(out))
    assert len(display) == 1 and display[0]["kind"] == "route"
    assert display[0]["routes"][0]["geometry"][0] == [52.517, 13.3888]
    assert display[0]["tiles"]["url"].startswith("https://tile.openstreetmap.org/")
    assert display[0]["start"]["label"] == "Berlin, Deutschland"


async def test_route_plan_with_coordinates_skips_geocoding() -> None:
    backend = Backend()
    conn = _connector(backend)
    await _call(conn, "route_plan", start="52.5, 13.4", destination="52.4,13.06", mode="fussweg", alternatives=False)
    assert not [r for r in backend.requests if r.url.path.endswith("/search")]
    route_req = next(r for r in backend.requests if "/route/v1/" in r.url.path)
    assert route_req.url.path.startswith("/routed-foot/")
    assert route_req.url.params["alternatives"] == "false"


async def test_route_plan_errors_are_clear() -> None:
    backend = Backend()
    conn = _connector(backend)
    backend.osrm = {"code": "NoRoute", "message": "Impossible route"}
    backend.osrm_status = 400
    with pytest.raises(ToolUserError, match="keine Route gefunden"):
        await conn.plan("Berlin", "Potsdam", "auto", True)
    with pytest.raises(ToolUserError, match="Adresse nicht gefunden"):
        await conn.plan("Atlantis", "Potsdam", "auto", True)
    with pytest.raises(ToolUserError, match="Unbekanntes Verkehrsmittel"):
        await conn.plan("Berlin", "Potsdam", "boot", True)


@pytest.mark.parametrize(
    ("exc", "text"),
    [
        (httpx.ReadTimeout("slow"), "Zeitüberschreitung"),
        (httpx.ConnectError("refused"), "nicht erreichbar"),
    ],
)
async def test_route_plan_timeout_and_unreachable(exc: Exception, text: str) -> None:
    backend = Backend()
    conn = _connector(backend)
    backend.fail = exc
    with pytest.raises(ToolUserError, match=text):
        await conn.plan("52.5,13.4", "52.4,13.0", "auto", False)


async def test_server_error_is_reported_as_unreachable() -> None:
    backend = Backend()
    backend.osrm_status = 503
    backend.osrm = {}
    conn = _connector(backend)
    with pytest.raises(ToolUserError, match="HTTP 503"):
        await conn.plan("52.5,13.4", "52.4,13.0", "auto", False)


async def test_custom_osrm_base_url_and_profile_path() -> None:
    backend = Backend()
    conn = _connector(backend, osrm_base_url="http://osrm.local:5000", osrm_path_profile="{profile}")
    await conn.plan("52.5,13.4", "52.4,13.0", "auto", False)
    route_req = next(r for r in backend.requests if "/route/v1/" in r.url.path)
    assert str(route_req.url).startswith("http://osrm.local:5000/route/v1/car/")


async def test_valhalla_provider_alternatives_and_no_route() -> None:
    shape = SHAPE
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if body["locations"][0]["lat"] == 0:
            return httpx.Response(400, json={"error_code": 442, "error": "No path could be found for input"})
        trip = {"legs": [{"shape": shape}], "summary": {"length": 30.5, "time": 1800}}
        alt = {"legs": [{"shape": shape}], "summary": {"length": 33.0, "time": 1900}}
        return httpx.Response(200, json={"trip": trip, "alternates": [{"trip": alt}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        router = ValhallaRouter(c)
        routes = await router.route(Place(52.5, 13.4, "a"), Place(52.4, 13.0, "b"), "bike", 2)
        with pytest.raises(RoutingError) as e:
            await router.route(Place(0, 0, "a"), Place(52.4, 13.0, "b"), "car", 0)
    assert [round(r.distance_m) for r in routes] == [30500, 33000]
    assert seen[0]["costing"] == "bicycle" and seen[0]["alternates"] == 2
    assert e.value.kind == "no_route"


async def test_geocode_tool_and_health() -> None:
    backend = Backend()
    conn = _connector(backend)
    out = await _call(conn, "route_geocode", query="Potsdam")
    assert out["results"][0]["label"].startswith("Potsdam")
    h = await conn.health()
    assert h.status == "ok"
    await conn.stop()


async def test_tools_are_read_only_and_registered_under_connector() -> None:
    conn = _connector(Backend())
    specs = {t.name: t for t in conn.tools()}
    assert set(specs) == {"route_plan", "route_geocode"}
    assert all(t.risk == "read" and t.connector == "routing" and not t.writes for t in specs.values())
    assert specs["route_plan"].parameters["properties"]["mode"]["enum"] == ["auto", "fahrrad", "fussweg"]
