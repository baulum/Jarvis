// Pure graph model: DTO → render arrays, stable seeded positions, cluster anchors, incremental merge.
import { categoryOf, type Category, type GraphDTO, type GraphEdgeDTO, type GraphNodeDTO } from "./types";

export interface GNode {
  id: string;
  index: number;
  kind: string;
  category: Category;
  label: string;
  group: string;
  weight: number;
  meta: Record<string, unknown>;
  degree: number;
  /** Seconds (performance.now()/1000) when the node appeared in this session – spawn animation. */
  bornAt: number;
}

export interface GEdge {
  source: number;
  target: number;
  relation: string;
  weight: number;
}

export interface GraphModel {
  nodes: GNode[];
  edges: GEdge[];
  byId: Map<string, number>;
  neighbors: number[][];
  /** xyz per node (layout target, written by the layout worker). */
  positions: Float32Array;
  version: number;
  truncated: Record<string, number>;
}

/** Groups are spatial regions of the brain; related knowledge clusters inside its region. */
export const GROUP_ORDER = [
  "system",
  "customer",
  "project",
  "work",
  "decision",
  "preference",
  "procedural",
  "conversations",
  "episodic",
  "semantic",
  "entities",
  "skills",
] as const;

export function hash32(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/** Deterministic pseudo-random in [0, 1) from a string and a salt (stable layout across reloads). */
export function rand01(s: string, salt: number): number {
  return (hash32(`${salt}:${s}`) % 100000) / 100000;
}

const SYSTEM_ANCHOR: Record<string, [number, number, number]> = {
  "system:agent": [0, 0, 0],
  "system:connector": [150, 20, 40],
  "system:tool": [-40, -110, -120],
};

/** Layout group of a node: system nodes are split by kind. */
export function layoutGroup(n: { group: string; kind: string }): string {
  if (n.group !== "system") return n.group;
  return n.kind === "tool" ? "system:tool" : n.kind === "connector" ? "system:connector" : "system:agent";
}

/** Cluster anchors on a Fibonacci sphere; the system cluster (agents, tools) sits at the centre. */
export function groupAnchor(group: string, radius = 260): [number, number, number] {
  if (group === "system") return [0, 0, 0];
  // the system region is split by kind so tools, connectors and agents form separate, readable clusters
  const sys = SYSTEM_ANCHOR[group];
  if (sys) return sys;
  const known = GROUP_ORDER.indexOf(group as (typeof GROUP_ORDER)[number]);
  const i = known >= 0 ? known : GROUP_ORDER.length + (hash32(group) % 7);
  const n = GROUP_ORDER.length + 6;
  const y = 1 - (i / (n - 1)) * 2;
  const r = Math.sqrt(1 - y * y);
  const theta = i * Math.PI * (3 - Math.sqrt(5));
  return [Math.cos(theta) * r * radius, y * radius * 0.72, Math.sin(theta) * r * radius];
}

export function seedPosition(id: string, group: string): [number, number, number] {
  const [ax, ay, az] = groupAnchor(group);
  const spread = group.startsWith("system") ? 110 : 70;
  return [
    ax + (rand01(id, 1) - 0.5) * spread,
    ay + (rand01(id, 2) - 0.5) * spread,
    az + (rand01(id, 3) - 0.5) * spread,
  ];
}

/**
 * Builds a model from a DTO. Positions of nodes that existed in ``prev`` are kept, new nodes start next to
 * an already placed neighbour (so a new memory visibly grows out of what it is connected to).
 */
export function buildModel(dto: GraphDTO, prev?: GraphModel | null, now = 0): GraphModel {
  const nodes: GNode[] = [];
  const byId = new Map<string, number>();
  const seen = new Set<string>();
  for (const n of dto.nodes) {
    if (seen.has(n.id)) continue;
    seen.add(n.id);
    const index = nodes.length;
    byId.set(n.id, index);
    const old = prev?.byId.get(n.id);
    nodes.push({
      id: n.id,
      index,
      kind: n.kind,
      category: categoryOf(n.kind),
      label: n.label,
      group: n.group,
      weight: n.weight,
      meta: n.meta ?? {},
      degree: 0,
      bornAt: old !== undefined ? (prev?.nodes[old]?.bornAt ?? 0) : prev ? now : 0,
    });
  }
  const edges: GEdge[] = [];
  const neighbors: number[][] = nodes.map(() => []);
  const edgeKeys = new Set<string>();
  for (const e of dto.edges) {
    const s = byId.get(e.source);
    const t = byId.get(e.target);
    if (s === undefined || t === undefined || s === t) continue;
    const key = s < t ? `${s}-${t}-${e.relation}` : `${t}-${s}-${e.relation}`;
    if (edgeKeys.has(key)) continue;
    edgeKeys.add(key);
    edges.push({ source: s, target: t, relation: e.relation, weight: e.weight });
    neighbors[s]?.push(t);
    neighbors[t]?.push(s);
  }
  for (const n of nodes) n.degree = neighbors[n.index]?.length ?? 0;

  const positions = new Float32Array(nodes.length * 3);
  const placed = new Uint8Array(nodes.length);
  for (const n of nodes) {
    const old = prev?.byId.get(n.id);
    if (prev && old !== undefined) {
      positions.set(prev.positions.subarray(old * 3, old * 3 + 3), n.index * 3);
      placed[n.index] = 1;
    }
  }
  for (const n of nodes) {
    if (placed[n.index]) continue;
    const anchor = (neighbors[n.index] ?? []).find((j) => placed[j]);
    if (anchor !== undefined && prev) {
      for (let k = 0; k < 3; k += 1) {
        positions[n.index * 3 + k] = (positions[anchor * 3 + k] ?? 0) + (rand01(n.id, 10 + k) - 0.5) * 18;
      }
    } else {
      positions.set(seedPosition(n.id, layoutGroup(n)), n.index * 3);
    }
    placed[n.index] = 1;
  }
  const jarvis = byId.get("agent:jarvis");
  if (jarvis !== undefined) positions.set([0, 0, 0], jarvis * 3);
  return { nodes, edges, byId, neighbors, positions, version: dto.version, truncated: dto.truncated ?? {} };
}

/** Adds lazily loaded nodes (cluster expansion) to a DTO without duplicates. */
export function mergeDTO(base: GraphDTO, extra: { nodes: GraphNodeDTO[]; edges: GraphEdgeDTO[] }): GraphDTO {
  const ids = new Set(base.nodes.map((n) => n.id));
  return {
    ...base,
    nodes: [...base.nodes, ...extra.nodes.filter((n) => !ids.has(n.id))],
    edges: [...base.edges, ...extra.edges],
  };
}

/** Node size in world units: importance plus a little for connectedness. */
export function nodeSize(n: GNode): number {
  if (n.id === "agent:jarvis") return 15;
  const base = n.category === "agent" ? 7.5 : n.kind === "connector" ? 5.6 : n.category === "tool" ? 2.6 : 4;
  return base * (0.75 + 0.6 * Math.min(1, n.weight)) + Math.min(3, Math.sqrt(n.degree) * 0.6);
}

export function searchLocal(model: GraphModel, query: string, limit = 8): GNode[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const scored: [number, GNode][] = [];
  for (const n of model.nodes) {
    const l = n.label.toLowerCase();
    const i = l.indexOf(q);
    if (i < 0) continue;
    scored.push([(i === 0 ? 0 : 1) + l.length / 200 - n.weight * 0.2, n]);
  }
  return scored.sort((a, b) => a[0] - b[0]).slice(0, limit).map(([, n]) => n);
}
