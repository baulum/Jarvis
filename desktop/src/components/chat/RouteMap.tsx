// Route of a `route_plan` call as an interactive map: all alternatives as polylines (the chosen one bright),
// start and destination pins, distance and travel time per alternative. Loaded lazily (Leaflet is ~150 kB).
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useRef, useState } from "react";
import { fastestRoute, formatRouteDistance, formatRouteDuration } from "../../lib/route";
import type { RouteDisplay } from "../../lib/timeline";

const escapeHtml = (s: string) => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const ACTIVE = { color: "#6ad3e4", weight: 5, opacity: 0.95 };
const IDLE = { color: "#3c97a8", weight: 4, opacity: 0.45 };

function pin(letter: string, tone: "start" | "end") {
  return L.divIcon({
    className: "",
    html: `<span class="route-pin route-pin--${tone}">${letter}</span>`,
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  });
}

export default function RouteMap({ display, compact = false }: { display: RouteDisplay; compact?: boolean }) {
  const [selected, setSelected] = useState(() => fastestRoute(display));
  const host = useRef<HTMLDivElement>(null);
  const lines = useRef<L.Polyline[]>([]);

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const map = L.map(el, { zoomControl: true, scrollWheelZoom: false, attributionControl: true });
    map.attributionControl.setPrefix(false);
    L.tileLayer(display.tiles.url, { maxZoom: 19, attribution: escapeHtml(display.tiles.attribution) }).addTo(map);
    // scrolling the chat must not zoom the map: wheel zoom only after Sir clicked into it
    map.on("click", () => map.scrollWheelZoom.enable());
    map.on("mouseout", () => map.scrollWheelZoom.disable());
    lines.current = display.routes.map((r, i) =>
      L.polyline(r.geometry, IDLE)
        .on("click", () => setSelected(i))
        .addTo(map),
    );
    const { start, destination } = display;
    L.marker([start.lat, start.lon], { icon: pin("A", "start"), title: start.label, keyboard: false }).addTo(map);
    L.marker([destination.lat, destination.lon], { icon: pin("B", "end"), title: destination.label, keyboard: false }).addTo(map);
    const bounds = L.latLngBounds([[start.lat, start.lon], [destination.lat, destination.lon]]);
    for (const l of lines.current) bounds.extend(l.getBounds());
    map.fitBounds(bounds, { padding: [24, 24] });
    return () => {
      lines.current = [];
      map.remove();
    };
  }, [display]);

  useEffect(() => {
    lines.current.forEach((l, i) => l.setStyle(i === selected ? ACTIVE : IDLE));
    lines.current[selected]?.bringToFront();
  }, [selected, display]);

  return (
    <figure className="overflow-hidden rounded-lg border border-seam-hi bg-void">
      <div
        ref={host}
        className={`route-map w-full ${compact ? "h-44" : "h-64"}`}
        role="img"
        aria-label={`Karte der Route von ${display.start.label} nach ${display.destination.label}`}
      />
      <figcaption className="flex flex-col gap-2 border-t border-seam px-3 py-2.5">
        <p className="flex min-w-0 items-center gap-2 text-xs text-ink-dim">
          <span className="route-pin route-pin--start route-pin--inline">A</span>
          <span className="truncate" title={display.start.label}>{display.start.label}</span>
        </p>
        <p className="flex min-w-0 items-center gap-2 text-xs text-ink-dim">
          <span className="route-pin route-pin--end route-pin--inline">B</span>
          <span className="truncate" title={display.destination.label}>{display.destination.label}</span>
        </p>
        <div role="radiogroup" aria-label="Routen" className="mt-1 flex flex-wrap gap-1.5">
          {display.routes.map((r, i) => (
            <button
              key={i}
              type="button"
              role="radio"
              aria-checked={i === selected}
              onClick={() => setSelected(i)}
              className={`flex min-w-[8.5rem] flex-col rounded-md border px-2.5 py-1.5 text-left transition-colors ${
                i === selected ? "border-arc-dim bg-arc-deep/50" : "border-seam hover:border-seam-hi"
              }`}
            >
              <span className="flex items-baseline gap-2">
                <span className={`type-data-lg text-[1.05rem] ${i === selected ? "text-white" : "text-ink"}`}>
                  {formatRouteDuration(r.duration_s)}
                </span>
                <span className="type-data text-ink-dim">{formatRouteDistance(r.distance_m)}</span>
              </span>
              <span className="truncate text-2xs text-ink-faint">
                {i === 0 ? "Hauptroute" : `Alternative ${i}`}
                {r.summary ? ` über ${r.summary}` : ""}
              </span>
            </button>
          ))}
        </div>
        <p className="text-2xs text-ink-faint">
          {display.mode_label ?? display.mode}, Fahrzeiten ohne aktuelle Verkehrslage
        </p>
      </figcaption>
    </figure>
  );
}
