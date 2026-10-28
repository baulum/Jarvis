// Memory explorer filters (pure): long-term memories by text, semantic hits, kind, project and time range.
// "Project" means real graph relations only: the project note itself and everything directly linked to it.
import { classOf } from "./anatomy";
import type { GNode, GraphModel } from "./model";
import type { Category } from "./types";

export type Range = "all" | "24h" | "7d" | "30d" | "365d";
export type Sort = "relevance" | "recent" | "used";

export const RANGE_LABEL: Record<Range, string> = { all: "Alle", "24h": "24 h", "7d": "7 Tage", "30d": "30 Tage", "365d": "1 Jahr" };
const RANGE_MS: Record<Range, number> = { all: Infinity, "24h": 86_400_000, "7d": 7 * 86_400_000, "30d": 30 * 86_400_000, "365d": 365 * 86_400_000 };

export interface ExploreFilter {
  query: string;
  /** ids ("memory:<uuid>") from the core's hybrid search, best first */
  semantic: string[];
  categories: Set<Category>;
  project: string | null;
  range: Range;
  sort: Sort;
}

export function nodeDate(n: GNode): number | null {
  const v = n.meta.updated_at ?? n.meta.created_at;
  if (typeof v !== "string") return null;
  const t = Date.parse(v);
  return Number.isFinite(t) ? t : null;
}

export function usage(n: GNode): number {
  return typeof n.meta.access_count === "number" ? n.meta.access_count : 0;
}

/** Projects to filter by: project notes and referenced project names. */
export function projectsOf(model: GraphModel): GNode[] {
  return model.nodes.filter((n) => n.kind === "project").sort((a, b) => b.degree - a.degree || a.label.localeCompare(b.label, "de"));
}

export function isFiltering(f: ExploreFilter): boolean {
  return Boolean(f.query.trim() || f.categories.size || f.project || f.range !== "all");
}

export function exploreMemories(model: GraphModel, f: ExploreFilter, nowMs = Date.now()): GNode[] {
  const q = f.query.trim().toLowerCase();
  const semantic = new Map(f.semantic.map((id, i) => [id, i]));
  let members: Set<number> | null = null;
  if (f.project) {
    const p = model.byId.get(f.project);
    members = new Set(p !== undefined ? [p, ...(model.neighbors[p] ?? [])] : []);
  }
  const maxAge = RANGE_MS[f.range];
  const out: GNode[] = [];
  for (const n of model.nodes) {
    if (classOf(n) !== "longterm") continue;
    if (f.categories.size && !f.categories.has(n.category)) continue;
    if (members && !members.has(n.index)) continue;
    if (maxAge !== Infinity) {
      const d = nodeDate(n);
      if (d === null || nowMs - d > maxAge) continue;
    }
    if (q && !n.label.toLowerCase().includes(q) && !semantic.has(n.id)) continue;
    out.push(n);
  }
  const sort = f.sort === "relevance" && !q ? "recent" : f.sort;
  const score = (n: GNode) => {
    const s = semantic.get(n.id);
    const i = n.label.toLowerCase().indexOf(q);
    return (s !== undefined ? s : 50) + (i === 0 ? -0.5 : i > 0 ? 0 : 25);
  };
  out.sort((a, b) => {
    if (sort === "relevance") return score(a) - score(b);
    if (sort === "used") return usage(b) - usage(a) || b.weight - a.weight;
    return (nodeDate(b) ?? 0) - (nodeDate(a) ?? 0) || b.weight - a.weight;
  });
  return out;
}
