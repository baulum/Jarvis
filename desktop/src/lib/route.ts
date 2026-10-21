// Formatting for the route map (same rules as the core's route_plan result).
import type { RouteDisplay } from "./timeline";

const nf1 = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1, minimumFractionDigits: 1 });

export function formatRouteDistance(m: number): string {
  if (m < 1000) return `${Math.round(m / 10) * 10} m`;
  const km = m / 1000;
  return km < 100 ? `${nf1.format(km)} km` : `${Math.round(km)} km`;
}

export function formatRouteDuration(s: number): string {
  const minutes = Math.max(1, Math.round(s / 60));
  if (minutes < 60) return `${minutes} min`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h} h ${String(m).padStart(2, "0")} min` : `${h} h`;
}

export function fastestRoute(d: RouteDisplay): number {
  let best = 0;
  d.routes.forEach((r, i) => {
    if (r.duration_s < (d.routes[best]?.duration_s ?? Infinity)) best = i;
  });
  return best;
}

/** One-line text version (used where no map is shown). */
export function routeSummary(d: RouteDisplay): string {
  const r = d.routes[fastestRoute(d)];
  if (!r) return `${d.start.label} → ${d.destination.label}`;
  const alt = d.routes.length > 1 ? `, ${d.routes.length - 1} Alternative${d.routes.length > 2 ? "n" : ""}` : "";
  return `${d.start.label} → ${d.destination.label}: ${formatRouteDistance(r.distance_m)}, ${formatRouteDuration(r.duration_s)}${alt}`;
}
