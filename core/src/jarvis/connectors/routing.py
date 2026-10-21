"""Routendienst: Route berechnen (Adresse oder Koordinate), Entfernung/Fahrzeit, Alternativen.

Standard ist OpenStreetMap ohne API-Schlüssel: Geocoding über Nominatim, Routing über OSRM (FOSSGIS-Server
für Auto/Fahrrad/Fußweg) oder Valhalla, Kartenkacheln von tile.openstreetmap.org. Die Anbieter liegen hinter
den Schnittstellen in ``jarvis.routing.providers`` und sind über die Connector-Einstellungen austauschbar.

Das Modell bekommt nur die Kennzahlen; die Geometrie für die Karte geht als Anzeige-Artefakt
(``jarvis.tools.artifacts``) direkt an das Tool-Ereignis im Chat.
"""

from __future__ import annotations

from typing import Any

import httpx

from jarvis.connectors.base import Connector, Health, Manifest
from jarvis.routing.providers import (
    DEFAULT_NOMINATIM,
    DEFAULT_OSRM,
    DEFAULT_OSRM_PATH_PROFILE,
    DEFAULT_VALHALLA,
    Geocoder,
    MinInterval,
    NominatimGeocoder,
    Place,
    Profile,
    Route,
    RoutingError,
    RoutingProvider,
    TTLCache,
    build_router,
    parse_coordinate,
)
from jarvis.tools.artifacts import artifacts
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext, ToolSpec

MODES: dict[str, Profile] = {"auto": "car", "fahrrad": "bike", "fussweg": "foot"}
MODE_LABEL = {"auto": "Auto", "fahrrad": "Fahrrad", "fussweg": "Fußweg"}
DEFAULT_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
DEFAULT_ATTRIBUTION = "© OpenStreetMap-Mitwirkende"
DEFAULT_TIMEOUT_S = 12.0


def format_distance(m: float) -> str:
    if m < 1000:
        return f"{round(m / 10) * 10:.0f} m"
    km = m / 1000
    return f"{km:.1f} km".replace(".", ",") if km < 100 else f"{km:.0f} km"


def format_duration(s: float) -> str:
    minutes = max(1, round(s / 60))
    if minutes < 60:
        return f"{minutes} min"
    h, m = divmod(minutes, 60)
    return f"{h} h {m:02d} min" if m else f"{h} h"


class RoutingConnector(Connector):
    manifest = Manifest(
        id="routing",
        name="Routendienst",
        description="Routen für Auto, Fahrrad und Fußweg mit Entfernung, Fahrzeit und Alternativen – "
        "OpenStreetMap-basiert (Nominatim, OSRM/Valhalla), ohne API-Schlüssel. Im Chat als Karte.",
        category="web",
        auth="none",
        settings_schema={
            "type": "object",
            "properties": {
                "provider": {"type": "string", "enum": ["osrm", "valhalla"], "description": "Routing-Dienst"},
                "osrm_base_url": {
                    "type": "string",
                    "description": f"OSRM-Basis-URL, {{profile}} = car/bike/foot (Standard: {DEFAULT_OSRM})",
                },
                "osrm_path_profile": {
                    "type": "string",
                    "description": f"Profil im OSRM-Pfad, {{profile}} erlaubt (Standard: {DEFAULT_OSRM_PATH_PROFILE})",
                },
                "valhalla_base_url": {"type": "string", "description": f"Standard: {DEFAULT_VALHALLA}"},
                "nominatim_base_url": {"type": "string", "description": f"Standard: {DEFAULT_NOMINATIM}"},
                "contact_email": {
                    "type": "string",
                    "description": "Kontakt im User-Agent/Nominatim-Parameter (Nutzungsregeln von OSM)",
                },
                "country_codes": {"type": "string", "description": "Adresssuche eingrenzen, z. B. de,at,ch"},
                "timeout_s": {"type": "number", "minimum": 2, "maximum": 60, "description": "Zeitlimit je Anfrage"},
                "tile_url": {"type": "string", "description": f"Kartenkacheln (Standard: {DEFAULT_TILES})"},
                "tile_attribution": {"type": "string", "description": "Quellenangabe der Karte"},
            },
        },
        setup_instructions="Funktioniert ohne Schlüssel mit den öffentlichen OpenStreetMap-Diensten (faire Nutzung: "
        "höchstens eine Adresssuche pro Sekunde, Ergebnisse werden zwischengespeichert). Für viele Anfragen eigene "
        "OSRM-/Valhalla-/Nominatim-Instanzen eintragen. Eigene Kachelserver müssen zusätzlich in der CSP der "
        "Desktop-App erlaubt werden.",
        docs_url="https://operations.osmfoundation.org/policies/nominatim/",
        mcp_note="Kein offizieller MCP-Server nötig: die Dienste sind schlüssellose HTTP-APIs.",
        default_enabled=True,
    )

    def __init__(self, state: Any, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(state)
        self._transport = transport  # tests inject httpx.MockTransport
        self._client: httpx.AsyncClient | None = None
        self._client_key: tuple[Any, ...] | None = None
        # survive re-configuration: one limiter + cache per process for the public Nominatim
        self._limiter = MinInterval(1.0)
        self._cache = TTLCache()

    # ------------------------------------------------------------------ providers
    def user_agent(self) -> str:
        contact = str(self.settings.get("contact_email") or "").strip()
        return (
            f"JARVIS-Routendienst/{self.manifest.version} (persoenlicher Assistent{'; ' + contact if contact else ''})"
        )

    def _timeout(self) -> float:
        try:
            return float(self.settings.get("timeout_s") or DEFAULT_TIMEOUT_S)
        except (TypeError, ValueError):
            return DEFAULT_TIMEOUT_S

    def client(self) -> httpx.AsyncClient:
        key = (self.user_agent(), self._timeout())
        if self._client is None or self._client_key != key:
            self._client = httpx.AsyncClient(
                timeout=self._timeout(),
                headers={"User-Agent": self.user_agent(), "Accept-Language": "de"},
                transport=self._transport,
                follow_redirects=True,
            )
            self._client_key = key
        return self._client

    def geocoder(self) -> Geocoder:
        return NominatimGeocoder(
            self.client(),
            str(self.settings.get("nominatim_base_url") or DEFAULT_NOMINATIM),
            email=str(self.settings.get("contact_email") or ""),
            country_codes=str(self.settings.get("country_codes") or ""),
            limiter=self._limiter,
            cache=self._cache,
        )

    def router(self) -> RoutingProvider:
        return build_router(str(self.settings.get("provider") or "osrm"), self.client(), self.settings)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def health(self) -> Health:
        base = str(self.settings.get("nominatim_base_url") or DEFAULT_NOMINATIM).rstrip("/")
        try:
            r = await self.client().get(f"{base}/status", params={"format": "json"})
        except httpx.HTTPError as e:
            return Health("error", f"Adresssuche nicht erreichbar ({type(e).__name__})")
        if r.status_code >= 400:
            return Health("degraded", f"Adresssuche meldet HTTP {r.status_code}")
        return Health("ok", f"Routing: {self.router().name.upper()}, Adresssuche: Nominatim")

    # ------------------------------------------------------------------ logic
    async def resolve(self, text: str) -> Place:
        place = parse_coordinate(text)
        if place is not None:
            return place
        try:
            hits = await self.geocoder().geocode(text, limit=1)
        except RoutingError as e:
            raise ToolUserError(str(e)) from e
        if not hits:
            raise ToolUserError(f"Adresse nicht gefunden: „{text[:120]}“. Bitte genauer angeben (Ort, Straße).")
        return hits[0]

    async def plan(self, start_text: str, dest_text: str, mode: str, alternatives: bool) -> dict[str, Any]:
        profile = MODES.get(mode)
        if profile is None:
            raise ToolUserError(f"Unbekanntes Verkehrsmittel „{mode}“ (auto, fahrrad, fussweg).")
        # sequential on purpose: Nominatim allows one request per second anyway
        start = await self.resolve(start_text)
        dest = await self.resolve(dest_text)
        router = self.router()
        try:
            routes = await router.route(start, dest, profile, 2 if alternatives else 0)
        except RoutingError as e:
            raise ToolUserError(str(e)) from e
        return self.result(start, dest, mode, routes, router.name)

    def result(self, start: Place, dest: Place, mode: str, routes: list[Route], provider: str) -> dict[str, Any]:
        display = {
            "mode": mode,
            "mode_label": MODE_LABEL.get(mode, mode),
            "provider": provider,
            "start": start.to_dict(),
            "destination": dest.to_dict(),
            "routes": [r.to_display() for r in routes],
            "tiles": {
                "url": str(self.settings.get("tile_url") or DEFAULT_TILES),
                "attribution": str(self.settings.get("tile_attribution") or DEFAULT_ATTRIBUTION),
            },
        }
        artifact_id = artifacts.put("route", display)
        fastest = min(range(len(routes)), key=lambda i: routes[i].duration_s)
        return {
            "start": start.to_dict(),
            "destination": dest.to_dict(),
            "mode": mode,
            "provider": provider,
            "routes": [
                {
                    "index": i + 1,
                    "distance_km": round(r.distance_m / 1000, 1),
                    "duration_min": round(r.duration_s / 60),
                    "distance": format_distance(r.distance_m),
                    "duration": format_duration(r.duration_s),
                    "via": r.summary,
                }
                for i, r in enumerate(routes)
            ],
            "fastest": fastest + 1,
            "artifact_id": artifact_id,
            "note": "Sir sieht die Route als Karte im Chat (mit Alternativen). Fahrzeiten ohne aktuelle "
            "Verkehrslage. Nenne Entfernung und Dauer kurz; Koordinaten und artifact_id nicht wiederholen.",
        }

    # ------------------------------------------------------------------ tools
    def tools(self) -> list[ToolSpec]:
        async def route_plan(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await self.plan(a["start"], a["destination"], a.get("mode", "auto"), a.get("alternatives", True))

        async def route_geocode(ctx: ToolContext, a: dict[str, Any]) -> Any:
            place = parse_coordinate(a["query"])
            if place is not None:
                return {"results": [place.to_dict()]}
            try:
                hits = await self.geocoder().geocode(a["query"], limit=int(a.get("limit", 5)))
            except RoutingError as e:
                raise ToolUserError(str(e)) from e
            return {"results": [h.to_dict() for h in hits]}

        place_desc = "Adresse/Ort (z. B. „Marienplatz, München“) oder Koordinate „Breite, Länge“"
        return [
            ToolSpec(
                "route_plan",
                "Berechnet eine Route von Start zu Ziel mit Entfernung und Fahrzeit, optional mit Alternativen. "
                "Verkehrsmittel: auto, fahrrad, fussweg. Sir sieht das Ergebnis als Karte im Chat.",
                {
                    "type": "object",
                    "properties": {
                        "start": {"type": "string", "minLength": 2, "maxLength": 300, "description": place_desc},
                        "destination": {"type": "string", "minLength": 2, "maxLength": 300, "description": place_desc},
                        "mode": {"type": "string", "enum": list(MODES), "default": "auto"},
                        "alternatives": {"type": "boolean", "default": True},
                    },
                    "required": ["start", "destination"],
                    "additionalProperties": False,
                },
                route_plan,
                "read",
                "routing",
                timeout_s=60,
                summarize=lambda a: f"Route ({a.get('mode', 'auto')}): {a.get('start')} → {a.get('destination')}",
            ),
            ToolSpec(
                "route_geocode",
                "Sucht Orte/Adressen und liefert Koordinaten (OpenStreetMap/Nominatim).",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 2, "maxLength": 300},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                route_geocode,
                "read",
                "routing",
                timeout_s=30,
                summarize=lambda a: f"Ort suchen: {a.get('query')}",
            ),
        ]
