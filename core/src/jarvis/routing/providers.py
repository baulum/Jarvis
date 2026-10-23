"""Geocoding- und Routing-Provider (OpenStreetMap-basiert, ohne API-Schlüssel).

* ``NominatimGeocoder`` – Adresse → Koordinate. Hält die Nutzungsregeln von Nominatim ein: eigener
  User-Agent, höchstens eine Anfrage pro Sekunde (prozessweit je Basis-URL), Ergebnisse im Cache.
* ``OsrmRouter`` / ``ValhallaRouter`` – Route zwischen zwei Punkten inkl. Alternativen, Basis-URL und
  Profile konfigurierbar (Standard: die FOSSGIS-Server, die auch openstreetmap.org nutzt).

Alle Netzwerkfehler werden in ``RoutingError`` mit einer Art (``kind``) und einer deutschen Meldung
übersetzt; der Connector reicht sie als ``ToolUserError`` an Jarvis weiter.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

Profile = Literal["car", "bike", "foot"]
ErrorKind = Literal["timeout", "unreachable", "no_route", "not_found", "rate_limited", "bad_response", "invalid"]

DEFAULT_NOMINATIM = "https://nominatim.openstreetmap.org"
DEFAULT_OSRM = "https://routing.openstreetmap.de/routed-{profile}"
DEFAULT_OSRM_PATH_PROFILE = "driving"  # die FOSSGIS-Server erwarten "driving" für alle drei Profile
DEFAULT_VALHALLA = "https://valhalla1.openstreetmap.de"
VALHALLA_COSTING: dict[str, str] = {"car": "auto", "bike": "bicycle", "foot": "pedestrian"}
MAX_GEOMETRY_POINTS = 1000

_COORD_RE = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*[,;\s]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")


class RoutingError(Exception):
    def __init__(self, kind: ErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(slots=True)
class Place:
    lat: float
    lon: float
    label: str
    source: Literal["coordinate", "geocoder"] = "geocoder"

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "lat": round(self.lat, 6), "lon": round(self.lon, 6), "source": self.source}


@dataclass(slots=True)
class Route:
    distance_m: float
    duration_s: float
    geometry: list[tuple[float, float]]  # (lat, lon)
    summary: str = ""

    def to_display(self) -> dict[str, Any]:
        return {
            "distance_m": round(self.distance_m),
            "duration_s": round(self.duration_s),
            "summary": self.summary,
            "geometry": [[round(lat, 5), round(lon, 5)] for lat, lon in simplify(self.geometry, MAX_GEOMETRY_POINTS)],
        }


class Geocoder(Protocol):
    name: str

    async def geocode(self, query: str, limit: int = 5) -> list[Place]: ...


class RoutingProvider(Protocol):
    name: str

    async def route(self, start: Place, end: Place, profile: Profile, alternatives: int) -> list[Route]: ...


# ---------------------------------------------------------------------------------------------- helpers
def parse_coordinate(text: str) -> Place | None:
    """``"52.52, 13.405"`` → Place (Breite, Länge). Liefert None für alles, was keine gültige Koordinate ist."""
    m = _COORD_RE.match(text)
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return Place(lat, lon, f"{lat:.5f}, {lon:.5f}", source="coordinate")


def decode_polyline(encoded: str, precision: int = 6) -> list[tuple[float, float]]:
    """Google-Polyline-Algorithmus (Valhalla nutzt Präzision 6)."""
    coords: list[tuple[float, float]] = []
    index = lat = lon = 0
    factor = 10**precision
    while index < len(encoded):
        values = []
        for _ in range(2):
            shift = result = 0
            while True:
                if index >= len(encoded):
                    raise RoutingError("bad_response", "Ungültige Routengeometrie vom Routendienst.")
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            values.append(~(result >> 1) if result & 1 else result >> 1)
        lat += values[0]
        lon += values[1]
        coords.append((lat / factor, lon / factor))
    return coords


def _perp_dist(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    if a == b:
        return math.dist(p, a)
    (x, y), (x1, y1), (x2, y2) = p, a, b
    return abs((y2 - y1) * x - (x2 - x1) * y + x2 * y1 - y2 * x1) / math.hypot(y2 - y1, x2 - x1)


def _rdp(points: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        lo, hi = stack.pop()
        best, idx = 0.0, -1
        for i in range(lo + 1, hi):
            d = _perp_dist(points[i], points[lo], points[hi])
            if d > best:
                best, idx = d, i
        if idx >= 0 and best > eps:
            keep[idx] = True
            stack += [(lo, idx), (idx, hi)]
    return [p for p, k in zip(points, keep, strict=True) if k]


def simplify(points: list[tuple[float, float]], max_points: int) -> list[tuple[float, float]]:
    """Douglas-Peucker für die Kartendarstellung (nicht Navigation): höchstens ``max_points`` Punkte.

    Startet mit einer Toleranz relativ zur Ausdehnung der Route und vervierfacht sie höchstens viermal;
    bleiben danach zu viele Punkte (Zickzack-Geometrie), wird gleichmäßig ausgedünnt."""
    if len(points) <= max_points:
        return points
    if len(points) > max_points * 3:  # bound the O(n²) worst case of Douglas-Peucker; dense OSRM points lose nothing
        stride = len(points) / (max_points * 3)
        points = [points[int(i * stride)] for i in range(max_points * 3)] + [points[-1]]
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    eps = max(1e-5, math.hypot(max(lats) - min(lats), max(lons) - min(lons)) / (max_points * 4))
    out = _rdp(points, eps)
    for _ in range(4):
        if len(out) <= max_points:
            return out
        eps *= 4
        out = _rdp(points, eps)
    if len(out) <= max_points:
        return out
    step = (len(out) - 1) / (max_points - 1)
    return [out[round(i * step)] for i in range(max_points)]


class TTLCache:
    """Kleiner LRU-Cache mit Ablaufzeit (Geocoding-Ergebnisse ändern sich selten)."""

    def __init__(self, max_items: int = 512, ttl_s: float = 7 * 24 * 3600, clock: Callable[[], float] = time.monotonic):
        self.max_items = max_items
        self.ttl_s = ttl_s
        self.clock = clock
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        hit = self._data.get(key)
        if hit is None:
            return None
        if self.clock() - hit[0] > self.ttl_s:
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return hit[1]

    def put(self, key: str, value: Any) -> None:
        self._data[key] = (self.clock(), value)
        self._data.move_to_end(key)
        while len(self._data) > self.max_items:
            self._data.popitem(last=False)

    def __len__(self) -> int:
        return len(self._data)


class MinInterval:
    """Höchstens eine Anfrage je ``interval_s`` (Nominatim: 1 req/s); Uhr und Schlaf für Tests austauschbar."""

    def __init__(
        self,
        interval_s: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.interval_s = interval_s
        self.clock = clock
        self.sleep = sleep
        self._lock = asyncio.Lock()
        self._last: float | None = None

    async def wait(self) -> None:
        async with self._lock:
            if self._last is not None:
                delay = self.interval_s - (self.clock() - self._last)
                if delay > 0:
                    await self.sleep(delay)
            self._last = self.clock()


async def _request(client: httpx.AsyncClient, method: str, url: str, service: str, **kw: Any) -> httpx.Response:
    try:
        return await client.request(method, url, **kw)
    except httpx.TimeoutException as e:
        raise RoutingError("timeout", f"{service} antwortet nicht (Zeitüberschreitung).") from e
    except httpx.HTTPError as e:
        raise RoutingError("unreachable", f"{service} ist nicht erreichbar ({type(e).__name__}).") from e


def _json(r: httpx.Response, service: str) -> Any:
    try:
        return r.json()
    except ValueError as e:
        raise RoutingError("bad_response", f"{service} lieferte eine ungültige Antwort (HTTP {r.status_code}).") from e


# ---------------------------------------------------------------------------------------------- geocoding
@dataclass
class NominatimGeocoder:
    client: httpx.AsyncClient
    base_url: str = DEFAULT_NOMINATIM
    email: str = ""
    language: str = "de"
    country_codes: str = ""
    limiter: MinInterval = field(default_factory=lambda: MinInterval(1.0))
    cache: TTLCache = field(default_factory=TTLCache)
    name: str = "nominatim"

    async def geocode(self, query: str, limit: int = 5) -> list[Place]:
        q = " ".join(query.split())
        if not q:
            raise RoutingError("invalid", "Leere Adresse.")
        key = f"{q.lower()}|{limit}|{self.language}|{self.country_codes}"
        cached = self.cache.get(key)
        if cached is not None:
            return [Place(**p) for p in cached]
        params: dict[str, str | int] = {"q": q, "format": "jsonv2", "limit": limit, "accept-language": self.language}
        if self.email:
            params["email"] = self.email
        if self.country_codes:
            params["countrycodes"] = self.country_codes
        await self.limiter.wait()
        r = await _request(self.client, "GET", f"{self.base_url.rstrip('/')}/search", "Die Adresssuche", params=params)
        if r.status_code == 429:
            raise RoutingError(
                "rate_limited", "Die Adresssuche (Nominatim) drosselt gerade Anfragen – bitte kurz warten."
            )
        if r.status_code >= 400:
            raise RoutingError("unreachable", f"Die Adresssuche meldet HTTP {r.status_code}.")
        rows = _json(r, "Die Adresssuche")
        if not isinstance(rows, list):
            raise RoutingError("bad_response", "Die Adresssuche lieferte eine unerwartete Antwort.")
        places = []
        for row in rows[:limit]:
            try:
                places.append(Place(float(row["lat"]), float(row["lon"]), str(row.get("display_name") or q)[:200]))
            except (KeyError, TypeError, ValueError):
                continue
        self.cache.put(key, [{"lat": p.lat, "lon": p.lon, "label": p.label, "source": p.source} for p in places])
        return places


@dataclass
class NominatimReverser:
    """Koordinate → Adresse (Nominatim ``/reverse``). Gleiche Regeln wie beim Geocoder: Limiter und Cache.

    Der Cache-Schlüssel sind auf 5 Nachkommastellen (~1 m) gerundete Koordinaten; Koordinaten werden weder
    geloggt noch in Fehlermeldungen übernommen."""

    client: httpx.AsyncClient
    base_url: str = DEFAULT_NOMINATIM
    email: str = ""
    language: str = "de"
    limiter: MinInterval = field(default_factory=lambda: MinInterval(1.0))
    cache: TTLCache = field(default_factory=lambda: TTLCache(max_items=32, ttl_s=300))
    timeout_s: float = 5.0
    name: str = "nominatim"

    async def reverse(self, lat: float, lon: float) -> dict[str, str]:
        key = f"{lat:.5f},{lon:.5f}|{self.language}"
        cached = self.cache.get(key)
        if cached is not None:
            return dict(cached)
        params: dict[str, str | int] = {
            "lat": f"{lat:.6f}",
            "lon": f"{lon:.6f}",
            "format": "json",
            "addressdetails": 1,
            "zoom": 18,
            "accept-language": self.language,
        }
        if self.email:
            params["email"] = self.email
        await self.limiter.wait()
        r = await _request(
            self.client,
            "GET",
            f"{self.base_url.rstrip('/')}/reverse",
            "Die Adresssuche",
            params=params,
            timeout=self.timeout_s,
        )
        if r.status_code == 429:
            raise RoutingError("rate_limited", "Die Adresssuche (Nominatim) drosselt gerade Anfragen.")
        if r.status_code >= 400:
            raise RoutingError("unreachable", f"Die Adresssuche meldet HTTP {r.status_code}.")
        data = _json(r, "Die Adresssuche")
        if not isinstance(data, dict):
            raise RoutingError("bad_response", "Die Adresssuche lieferte eine unerwartete Antwort.")
        if data.get("error") or not data.get("display_name"):
            raise RoutingError("not_found", "Zu diesem Standort wurde keine Adresse gefunden.")
        raw_address = data.get("address")
        a: dict[str, Any] = raw_address if isinstance(raw_address, dict) else {}
        town = next((a[k] for k in ("city", "town", "village", "municipality", "hamlet", "suburb") if a.get(k)), "")
        result = {
            "display_name": str(data["display_name"])[:300],
            "street": str(a.get("road") or a.get("pedestrian") or a.get("footway") or ""),
            "house_number": str(a.get("house_number") or ""),
            "postcode": str(a.get("postcode") or ""),
            "city": str(town),
            "country": str(a.get("country") or ""),
        }
        self.cache.put(key, result)
        return dict(result)


# ---------------------------------------------------------------------------------------------- routing
@dataclass
class OsrmRouter:
    client: httpx.AsyncClient
    base_url: str = DEFAULT_OSRM  # "{profile}" wird durch car/bike/foot ersetzt
    path_profile: str = DEFAULT_OSRM_PATH_PROFILE  # ebenso; Self-Hosting mit einem Server: "{profile}"
    name: str = "osrm"

    async def route(self, start: Place, end: Place, profile: Profile, alternatives: int) -> list[Route]:
        base = self.base_url.replace("{profile}", profile).rstrip("/")
        path_profile = self.path_profile.replace("{profile}", profile)
        coords = f"{start.lon:.6f},{start.lat:.6f};{end.lon:.6f},{end.lat:.6f}"
        params = {
            "alternatives": str(alternatives) if alternatives > 0 else "false",
            "overview": "full",
            "geometries": "geojson",
            "steps": "false",
        }
        r = await _request(
            self.client, "GET", f"{base}/route/v1/{path_profile}/{coords}", "Der Routendienst", params=params
        )
        if r.status_code == 429:
            raise RoutingError("rate_limited", "Der Routendienst drosselt gerade Anfragen – bitte kurz warten.")
        data = _json(r, "Der Routendienst") if r.status_code < 500 else {}
        code = data.get("code") if isinstance(data, dict) else None
        if code in ("NoRoute", "NoSegment"):
            raise RoutingError("no_route", "Zwischen Start und Ziel wurde keine Route gefunden.")
        if r.status_code >= 400 or code != "Ok":
            detail = f" – {str(data['message'])[:120]}" if isinstance(data, dict) and data.get("message") else ""
            raise RoutingError("unreachable", f"Der Routendienst meldet einen Fehler (HTTP {r.status_code}){detail}.")
        routes = []
        for raw in data.get("routes") or []:
            try:
                geometry = [(float(c[1]), float(c[0])) for c in raw["geometry"]["coordinates"]]
                summary = ", ".join(s for s in (leg.get("summary", "") for leg in raw.get("legs") or []) if s)
                routes.append(Route(float(raw["distance"]), float(raw["duration"]), geometry, summary[:120]))
            except (KeyError, TypeError, ValueError, IndexError):
                continue
        if not routes:
            raise RoutingError("no_route", "Zwischen Start und Ziel wurde keine Route gefunden.")
        return routes[: alternatives + 1]


@dataclass
class ValhallaRouter:
    client: httpx.AsyncClient
    base_url: str = DEFAULT_VALHALLA
    name: str = "valhalla"

    async def route(self, start: Place, end: Place, profile: Profile, alternatives: int) -> list[Route]:
        body: dict[str, Any] = {
            "locations": [{"lat": start.lat, "lon": start.lon}, {"lat": end.lat, "lon": end.lon}],
            "costing": VALHALLA_COSTING[profile],
            "units": "kilometers",
            "directions_type": "none",
        }
        if alternatives > 0:
            body["alternates"] = alternatives
        r = await _request(self.client, "POST", f"{self.base_url.rstrip('/')}/route", "Der Routendienst", json=body)
        if r.status_code == 429:
            raise RoutingError("rate_limited", "Der Routendienst drosselt gerade Anfragen – bitte kurz warten.")
        data = _json(r, "Der Routendienst") if r.status_code < 500 else {}
        if r.status_code >= 400:
            # 442: no path, 171/170: location not routable
            if isinstance(data, dict) and data.get("error_code") in (170, 171, 442, 443):
                raise RoutingError("no_route", "Zwischen Start und Ziel wurde keine Route gefunden.")
            raise RoutingError("unreachable", f"Der Routendienst meldet einen Fehler (HTTP {r.status_code}).")
        trips = [data.get("trip")] + [a.get("trip") for a in data.get("alternates") or [] if isinstance(a, dict)]
        routes = []
        for trip in trips:
            if not isinstance(trip, dict):
                continue
            geometry: list[tuple[float, float]] = []
            for leg in trip.get("legs") or []:
                geometry += decode_polyline(str(leg.get("shape", "")), 6)
            summary = trip.get("summary") or {}
            routes.append(Route(float(summary.get("length", 0)) * 1000, float(summary.get("time", 0)), geometry))
        if not routes:
            raise RoutingError("no_route", "Zwischen Start und Ziel wurde keine Route gefunden.")
        return routes[: alternatives + 1]


def build_router(kind: str, client: httpx.AsyncClient, settings: dict[str, Any]) -> RoutingProvider:
    if kind == "valhalla":
        return ValhallaRouter(client, str(settings.get("valhalla_base_url") or DEFAULT_VALHALLA))
    return OsrmRouter(
        client,
        str(settings.get("osrm_base_url") or DEFAULT_OSRM),
        str(settings.get("osrm_path_profile") or DEFAULT_OSRM_PATH_PROFILE),
    )
