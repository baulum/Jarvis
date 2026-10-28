// Procedural brain anatomy (pure, unit-tested): a signed distance field of two hemispheres with temporal lobes,
// a midline core, cerebellum and brain stem; a surface mesh extracted from it (surface nets); neurons sampled in
// the cortex; and the functional regions every real node and every live event is mapped to.
// Coordinates: x = left (−) / right (+), y = up, z = front (+) / back (−). About 300 units long.
import type { GNode, GraphModel } from "./model";
import { hash32, rand01 } from "./model";

export type Vec3 = [number, number, number];

// ------------------------------------------------------------------ signed distance field
function sdEllipsoid(x: number, y: number, z: number, cx: number, cy: number, cz: number, rx: number, ry: number, rz: number): number {
  const px = (x - cx) / rx;
  const py = (y - cy) / ry;
  const pz = (z - cz) / rz;
  const k0 = Math.hypot(px, py, pz);
  const k1 = Math.hypot(px / rx, py / ry, pz / rz);
  return k1 === 0 ? -Math.min(rx, ry, rz) : (k0 * (k0 - 1)) / k1;
}

function sdCapsule(x: number, y: number, z: number, ax: number, ay: number, az: number, bx: number, by: number, bz: number, r: number): number {
  const pax = x - ax;
  const pay = y - ay;
  const paz = z - az;
  const bax = bx - ax;
  const bay = by - ay;
  const baz = bz - az;
  const h = Math.min(1, Math.max(0, (pax * bax + pay * bay + paz * baz) / (bax * bax + bay * bay + baz * baz)));
  return Math.hypot(pax - bax * h, pay - bay * h, paz - baz * h) - r;
}

/** Smooth minimum (polynomial): blends two shapes over ``k`` units. */
function smin(a: number, b: number, k: number): number {
  const h = Math.min(1, Math.max(0, 0.5 + (0.5 * (b - a)) / k));
  return b + (a - b) * h - k * h * (1 - h);
}

/** One hemisphere, mirrored so the right side is computed for both (``side`` = −1 left, +1 right). */
function hemisphere(x: number, y: number, z: number, side: number): number {
  const sx = x * side;
  // cerebrum: long, highest over the parietal lobe, tapering towards the occipital pole
  let d = sdEllipsoid(sx, y, z, 68, 30 - z * 0.05, -4, 72, 80, 150);
  // the underside of the frontal lobe rises towards the front (orbital surface) …
  d = Math.max(d, (-45 + (z - 20) * 0.33 - y) * 0.95);
  // … and the temporal lobe hangs below it, separated by the Sylvian fissure (small blend radius)
  d = smin(d, sdEllipsoid(sx, y, z, 80, -42, 18, 48, 34, 82), 6);
  d = Math.max(d, 3.5 - sx); // flat medial face: the longitudinal fissure between the hemispheres
  return Math.max(d, -78 - y); // flattened base
}

export function brainSdf(x: number, y: number, z: number): number {
  let d = Math.min(hemisphere(x, y, z, -1), hemisphere(x, y, z, 1));
  // corpus callosum / diencephalon close the fissure in the depth only
  d = smin(d, sdEllipsoid(x, y, z, 0, 0, -6, 28, 26, 70), 9);
  // cerebellum: two lobes under the occipital lobe, a narrow transverse fissure above them
  const cb = smin(sdEllipsoid(x, y, z, -36, -60, -104, 46, 32, 42), sdEllipsoid(x, y, z, 36, -60, -104, 46, 32, 42), 10);
  d = smin(d, cb, 5);
  // brain stem
  return smin(d, sdCapsule(x, y, z, 0, -24, -42, 0, -138, -62, 18), 12);
}

export const BRAIN_BOUNDS: { min: Vec3; max: Vec3 } = { min: [-152, -160, -158], max: [152, 120, 160] };

/** Outward normal (gradient of the distance field). */
export function sdfNormal(x: number, y: number, z: number, e = 0.75): Vec3 {
  const nx = brainSdf(x + e, y, z) - brainSdf(x - e, y, z);
  const ny = brainSdf(x, y + e, z) - brainSdf(x, y - e, z);
  const nz = brainSdf(x, y, z + e) - brainSdf(x, y, z - e);
  const l = Math.hypot(nx, ny, nz) || 1;
  return [nx / l, ny / l, nz / l];
}

// ------------------------------------------------------------------ surface mesh (surface nets)
export interface Surface {
  positions: Float32Array;
  normals: Float32Array;
  indices: Uint32Array;
}

const CORNERS: Vec3[] = [
  [0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0], [0, 0, 1], [1, 0, 1], [0, 1, 1], [1, 1, 1],
];
const EDGES: [number, number][] = [
  [0, 1], [2, 3], [4, 5], [6, 7], [0, 2], [1, 3], [4, 6], [5, 7], [0, 4], [1, 5], [2, 6], [3, 7],
];

/**
 * Extracts the zero level of ``sdf`` as a triangle mesh. ``res`` cells span the longest side of the bounds
 * (≈ 40 on phones, 64 on a desktop GPU).
 */
export function buildSurface(res: number, sdf = brainSdf, bounds = BRAIN_BOUNDS): Surface {
  const [x0, y0, z0] = bounds.min;
  const size = Math.max(bounds.max[0] - x0, bounds.max[1] - y0, bounds.max[2] - z0);
  const h = size / res;
  const nx = Math.ceil((bounds.max[0] - x0) / h) + 1;
  const ny = Math.ceil((bounds.max[1] - y0) / h) + 1;
  const nz = Math.ceil((bounds.max[2] - z0) / h) + 1;
  const at = (i: number, j: number, k: number) => i + nx * (j + ny * k);
  const v = new Float32Array(nx * ny * nz);
  for (let k = 0; k < nz; k += 1) for (let j = 0; j < ny; j += 1) for (let i = 0; i < nx; i += 1) v[at(i, j, k)] = sdf(x0 + i * h, y0 + j * h, z0 + k * h);

  const cellVertex = new Int32Array(nx * ny * nz).fill(-1);
  const pos: number[] = [];
  const val = new Float32Array(8);
  for (let k = 0; k < nz - 1; k += 1) {
    for (let j = 0; j < ny - 1; j += 1) {
      for (let i = 0; i < nx - 1; i += 1) {
        let inside = 0;
        for (let c = 0; c < 8; c += 1) {
          const [a, b, d] = CORNERS[c]!;
          val[c] = v[at(i + a, j + b, k + d)]!;
          if (val[c]! < 0) inside += 1;
        }
        if (inside === 0 || inside === 8) continue;
        let sx = 0;
        let sy = 0;
        let sz = 0;
        let n = 0;
        for (const [ea, eb] of EDGES) {
          const va = val[ea]!;
          const vb = val[eb]!;
          if (va < 0 === vb < 0) continue;
          const t = va / (va - vb);
          const ca = CORNERS[ea]!;
          const cb = CORNERS[eb]!;
          sx += ca[0] + (cb[0] - ca[0]) * t;
          sy += ca[1] + (cb[1] - ca[1]) * t;
          sz += ca[2] + (cb[2] - ca[2]) * t;
          n += 1;
        }
        cellVertex[at(i, j, k)] = pos.length / 3;
        pos.push(x0 + (i + sx / n) * h, y0 + (j + sy / n) * h, z0 + (k + sz / n) * h);
      }
    }
  }

  const idx: number[] = [];
  // winding: counter-clockwise seen from outside (front faces point out of the brain)
  const quad = (a: number, b: number, c: number, d: number, insideFirst: boolean) => {
    if (a < 0 || b < 0 || c < 0 || d < 0) return;
    if (insideFirst) idx.push(a, b, c, a, c, d);
    else idx.push(a, c, b, a, d, c);
  };
  for (let k = 1; k < nz - 1; k += 1) {
    for (let j = 1; j < ny - 1; j += 1) {
      for (let i = 1; i < nx - 1; i += 1) {
        const s0 = v[at(i, j, k)]! < 0;
        // edge along x to (i+1, j, k): shared by the cells (i, j-1..j, k-1..k)
        if (s0 !== v[at(i + 1, j, k)]! < 0) {
          quad(cellVertex[at(i, j - 1, k - 1)]!, cellVertex[at(i, j, k - 1)]!, cellVertex[at(i, j, k)]!, cellVertex[at(i, j - 1, k)]!, s0);
        }
        if (s0 !== v[at(i, j + 1, k)]! < 0) {
          quad(cellVertex[at(i - 1, j, k - 1)]!, cellVertex[at(i - 1, j, k)]!, cellVertex[at(i, j, k)]!, cellVertex[at(i, j, k - 1)]!, s0);
        }
        if (s0 !== v[at(i, j, k + 1)]! < 0) {
          quad(cellVertex[at(i - 1, j - 1, k)]!, cellVertex[at(i, j - 1, k)]!, cellVertex[at(i, j, k)]!, cellVertex[at(i - 1, j, k)]!, s0);
        }
      }
    }
  }
  const positions = new Float32Array(pos);
  const normals = new Float32Array(positions.length);
  for (let p = 0; p < positions.length; p += 3) {
    normals.set(sdfNormal(positions[p]!, positions[p + 1]!, positions[p + 2]!), p);
  }
  return { positions, normals, indices: new Uint32Array(idx) };
}

// ------------------------------------------------------------------ deterministic randomness
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ------------------------------------------------------------------ neurons and synapses (the substrate)
export interface Substrate {
  /** xyz per neuron */
  positions: Float32Array;
  /** 0..1 per neuron: depth below the cortex surface (0 = surface) */
  depth: Float32Array;
  /** neuron index pairs */
  synapses: Uint32Array;
}

/** Neurons mostly in the grey matter (cortex band), some in the depth; each linked to its nearest neighbours. */
export function buildSubstrate(count: number, seed = 7, links = 2): Substrate {
  const rnd = mulberry32(seed);
  const { min, max } = BRAIN_BOUNDS;
  const positions = new Float32Array(count * 3);
  const depth = new Float32Array(count);
  let n = 0;
  let guard = 0;
  while (n < count && guard < count * 400) {
    guard += 1;
    const x = min[0] + rnd() * (max[0] - min[0]);
    const y = min[1] + rnd() * (max[1] - min[1]);
    const z = min[2] + rnd() * (max[2] - min[2]);
    const d = brainSdf(x, y, z);
    if (d > -2) continue;
    if (d < -22 && rnd() > 0.22) continue; // white matter is sparse
    positions[n * 3] = x;
    positions[n * 3 + 1] = y;
    positions[n * 3 + 2] = z;
    depth[n] = Math.min(1, -d / 60);
    n += 1;
  }
  const used = n;
  // spatial hash for nearest neighbours
  const cell = 26;
  const grid = new Map<number, number[]>();
  const key = (x: number, y: number, z: number) => (Math.floor(x / cell) + 64) * 16384 + (Math.floor(y / cell) + 64) * 128 + (Math.floor(z / cell) + 64);
  for (let i = 0; i < used; i += 1) {
    const k = key(positions[i * 3]!, positions[i * 3 + 1]!, positions[i * 3 + 2]!);
    const list = grid.get(k);
    if (list) list.push(i);
    else grid.set(k, [i]);
  }
  const pairs: number[] = [];
  const seen = new Set<number>();
  for (let i = 0; i < used; i += 1) {
    const x = positions[i * 3]!;
    const y = positions[i * 3 + 1]!;
    const z = positions[i * 3 + 2]!;
    const best: [number, number][] = [];
    for (let dx = -1; dx <= 1; dx += 1) {
      for (let dy = -1; dy <= 1; dy += 1) {
        for (let dz = -1; dz <= 1; dz += 1) {
          for (const j of grid.get(key(x + dx * cell, y + dy * cell, z + dz * cell)) ?? []) {
            if (j === i) continue;
            const d = Math.hypot(positions[j * 3]! - x, positions[j * 3 + 1]! - y, positions[j * 3 + 2]! - z);
            if (d < cell * 1.1) best.push([d, j]);
          }
        }
      }
    }
    best.sort((a, b) => a[0] - b[0]);
    for (const [, j] of best.slice(0, links)) {
      const k = i < j ? i * 100000 + j : j * 100000 + i;
      if (seen.has(k)) continue;
      seen.add(k);
      pairs.push(i, j);
    }
  }
  return { positions: positions.subarray(0, used * 3), depth: depth.subarray(0, used), synapses: new Uint32Array(pairs) };
}

// ------------------------------------------------------------------ functional regions
/** What a region stands for in the legend (one colour each). */
export type RegionGroup = "input" | "core" | "memory" | "tools" | "agents" | "output";

export type RegionId =
  | "visual"
  | "auditory"
  | "thalamus"
  | "prefrontal"
  | "hippocampus"
  | "temporal"
  | "orbitofrontal"
  | "dorsolateral"
  | "parietal"
  | "cerebellum"
  | "motor"
  | "sensory"
  | "broca"
  | "coding"
  | "scheduler"
  | "tasks";

export interface Region {
  id: RegionId;
  group: RegionGroup;
  label: string;
  /** centre on the right side; mirrored regions exist in both hemispheres */
  center: Vec3;
  radius: number;
  mirror: boolean;
  /** cortex = nodes sit in the grey matter band, deep = anywhere inside */
  layer: "cortex" | "deep";
}

export const GROUP_LABEL: Record<RegionGroup, string> = {
  input: "Eingang",
  core: "Verarbeitung",
  memory: "Gedächtnis",
  tools: "Werkzeuge",
  agents: "Agenten",
  output: "Ausgabe",
};

export const GROUP_HINT: Record<RegionGroup, string> = {
  input: "Sehrinde und Hörrinde: hier kommen Anfragen an (Gespräche, Stimme, Telefon)",
  core: "Thalamus und Stirnhirn: das Sprachmodell verarbeitet die Anfrage",
  memory: "Hippocampus und Großhirnrinde: Erinnerungen, abgelegt nach Art",
  tools: "Motorischer Kortex: jedes Werkzeug, dahinter die Anbindungen",
  agents: "Eigene Bereiche für Coding-Agent, Aufgaben und Zeitplaner",
  output: "Broca-Areal: hier verlässt die Antwort das Gehirn",
};

export const GROUP_COLOR: Record<RegionGroup, [number, number, number]> = {
  input: [0.6, 0.86, 1.0],
  core: [0.42, 0.83, 0.9],
  memory: [0.64, 0.62, 1.0],
  tools: [0.98, 0.62, 0.32],
  agents: [0.45, 0.95, 0.74],
  output: [0.96, 0.9, 0.7],
};

export const REGIONS: Record<RegionId, Region> = {
  visual: { id: "visual", group: "input", label: "Sehrinde", center: [44, 26, -134], radius: 34, mirror: true, layer: "cortex" },
  auditory: { id: "auditory", group: "input", label: "Hörrinde", center: [124, -14, 18], radius: 26, mirror: true, layer: "cortex" },
  thalamus: { id: "thalamus", group: "core", label: "Thalamus", center: [0, 4, -4], radius: 22, mirror: false, layer: "deep" },
  prefrontal: { id: "prefrontal", group: "core", label: "Stirnhirn", center: [40, 52, 118], radius: 46, mirror: true, layer: "cortex" },
  hippocampus: { id: "hippocampus", group: "memory", label: "Hippocampus", center: [52, -34, 4], radius: 28, mirror: true, layer: "deep" },
  temporal: { id: "temporal", group: "memory", label: "Schläfenlappen", center: [104, -26, 20], radius: 66, mirror: true, layer: "cortex" },
  orbitofrontal: { id: "orbitofrontal", group: "memory", label: "Orbitofrontal", center: [40, 0, 108], radius: 32, mirror: true, layer: "cortex" },
  dorsolateral: { id: "dorsolateral", group: "memory", label: "Stirnhirn seitlich", center: [104, 48, 70], radius: 46, mirror: true, layer: "cortex" },
  parietal: { id: "parietal", group: "memory", label: "Scheitellappen", center: [96, 74, -58], radius: 48, mirror: true, layer: "cortex" },
  cerebellum: { id: "cerebellum", group: "memory", label: "Kleinhirn", center: [36, -62, -104], radius: 40, mirror: true, layer: "deep" },
  motor: { id: "motor", group: "tools", label: "Motorischer Kortex", center: [70, 92, 14], radius: 62, mirror: true, layer: "cortex" },
  sensory: { id: "sensory", group: "tools", label: "Somatosensorik", center: [66, 92, -22], radius: 40, mirror: true, layer: "cortex" },
  broca: { id: "broca", group: "output", label: "Broca-Areal", center: [-118, 2, 70], radius: 24, mirror: false, layer: "cortex" },
  coding: { id: "coding", group: "agents", label: "Coding-Agent", center: [100, 34, -104], radius: 30, mirror: false, layer: "cortex" },
  scheduler: { id: "scheduler", group: "agents", label: "Zeitplaner", center: [-100, 34, -104], radius: 30, mirror: false, layer: "cortex" },
  tasks: { id: "tasks", group: "agents", label: "Aufgaben", center: [20, 30, -62], radius: 26, mirror: false, layer: "deep" },
};

/** Representative point of a legend group (for impulses that start or end in a region, e.g. the answer). */
export const GROUP_ANCHOR: Record<RegionGroup, RegionId> = {
  input: "visual",
  core: "thalamus",
  memory: "hippocampus",
  tools: "motor",
  agents: "tasks",
  output: "broca",
};

const MEMORY_REGION: Record<string, RegionId> = {
  episodic: "hippocampus",
  conversations: "hippocampus",
  semantic: "temporal",
  procedural: "cerebellum",
  preference: "orbitofrontal",
  decision: "orbitofrontal",
  project: "dorsolateral",
  customer: "dorsolateral",
  work: "dorsolateral",
  entities: "parietal",
  skills: "cerebellum",
};

/** Where a real node lives in the brain. */
export function regionOf(n: Pick<GNode, "id" | "kind" | "group">): RegionId {
  if (n.id === "agent:jarvis") return "thalamus";
  if (n.kind === "agent") {
    if (n.id === "agent:coding") return "coding";
    if (n.id === "agent:scheduler") return "scheduler";
    return "auditory"; // voice, telephony: spoken input
  }
  if (n.kind === "tool") return "motor";
  if (n.kind === "connector") return "sensory";
  if (n.kind === "skill") return "prefrontal";
  if (n.id.startsWith("session:")) return "visual"; // a live conversation (a memory note about one is episodic)
  if (n.kind === "task") return "tasks";
  if (n.kind === "coding") return "coding";
  if (n.kind === "schedule") return "scheduler";
  return MEMORY_REGION[n.group] ?? "temporal";
}

/** Fallback region for node ids that are not (yet) in the graph. */
export function regionForId(id: string): RegionId {
  const prefix = id.split(":", 1)[0];
  switch (prefix) {
    case "tool":
      return "motor";
    case "connector":
      return "sensory";
    case "memory":
    case "entity":
      return "hippocampus";
    case "session":
      return "visual";
    case "task":
      return "tasks";
    case "coding":
      return "coding";
    case "schedule":
      return "scheduler";
    default:
      return "thalamus";
  }
}

export function regionCenter(r: Region, side: 1 | -1 = 1): Vec3 {
  return r.mirror ? [r.center[0] * side, r.center[1], r.center[2]] : r.center;
}

/** Four classes the interface distinguishes (legend + filter). */
export type NodeClass = "longterm" | "shortterm" | "tools" | "agents";

export const CLASS_LABEL: Record<NodeClass, string> = {
  longterm: "Langzeitgedächtnis",
  shortterm: "Kurzzeitkontext",
  tools: "Werkzeuge",
  agents: "Agenten",
};

export function classOf(n: Pick<GNode, "id" | "kind">): NodeClass {
  if (n.id.startsWith("session:")) return "shortterm";
  if (n.kind === "tool" || n.kind === "connector" || n.kind === "skill") return "tools";
  if (n.kind === "agent" || n.kind === "task" || n.kind === "coding" || n.kind === "schedule") return "agents";
  return "longterm";
}

// ------------------------------------------------------------------ placement of real nodes
function inBand(r: Region, d: number): boolean {
  return r.layer === "cortex" ? d < -3 && d > -20 : d < -4;
}

/**
 * Brain-view position of every node: inside its region, in the grey matter for cortical regions. Connected nodes
 * of the same region are placed next to each other, so real relations form visible clusters. Deterministic.
 */
export function brainPlacement(model: Pick<GraphModel, "nodes" | "neighbors">): Float32Array {
  const out = new Float32Array(model.nodes.length * 3);
  const placed = new Uint8Array(model.nodes.length);
  const regionIdx = model.nodes.map((n) => regionOf(n));
  // hubs first: their neighbours then cluster around them
  const order = model.nodes.map((n) => n.index).sort((a, b) => (model.nodes[b]!.degree - model.nodes[a]!.degree) || a - b);
  const fixedAgent = (id: string): Vec3 | null => {
    if (id === "agent:jarvis") return REGIONS.thalamus.center;
    if (id === "agent:coding") return REGIONS.coding.center;
    if (id === "agent:scheduler") return REGIONS.scheduler.center;
    if (id === "agent:voice") return [-124, -14, 18];
    if (id === "agent:telephony") return [124, -14, 18];
    return null;
  };
  for (const i of order) {
    const n = model.nodes[i]!;
    const fixed = fixedAgent(n.id);
    if (fixed) {
      out.set(fixed, i * 3);
      placed[i] = 1;
      continue;
    }
    const region = REGIONS[regionIdx[i]!];
    const rnd = mulberry32(hash32(n.id));
    const side: 1 | -1 = region.mirror ? (rand01(n.id, 31) < 0.5 ? -1 : 1) : 1;
    // an already placed neighbour in the same region pulls this node next to it
    const anchor = (model.neighbors[i] ?? []).find((j) => placed[j] && regionIdx[j] === regionIdx[i] && !fixedAgent(model.nodes[j]!.id));
    const c: Vec3 = anchor !== undefined ? [out[anchor * 3]!, out[anchor * 3 + 1]!, out[anchor * 3 + 2]!] : regionCenter(region, side);
    const spread = anchor !== undefined ? Math.min(region.radius, 30) : region.radius;
    let best: Vec3 = regionCenter(region, side);
    for (let t = 0; t < 48; t += 1) {
      // uniform point in a ball
      const u = rnd() * 2 - 1;
      const th = rnd() * Math.PI * 2;
      const r = spread * Math.cbrt(rnd());
      const s = Math.sqrt(1 - u * u);
      const p: Vec3 = [c[0] + r * s * Math.cos(th), c[1] + r * u, c[2] + r * s * Math.sin(th)];
      if (inBand(region, brainSdf(p[0], p[1], p[2]))) {
        best = p;
        break;
      }
    }
    out.set(best, i * 3);
    placed[i] = 1;
  }
  return out;
}

// ------------------------------------------------------------------ live-activity view: a readable flow
/** Columns of the live view, left → right: where work enters, who processes it, what it uses, what it knows. */
export const FLOW_COLUMNS: { id: string; label: string; x: number }[] = [
  { id: "input", label: "Eingang", x: -430 },
  { id: "core", label: "Agenten", x: -220 },
  { id: "work", label: "Aufträge", x: -40 },
  { id: "tools", label: "Werkzeuge", x: 170 },
  { id: "memory", label: "Gedächtnis", x: 430 },
];

function flowColumn(n: GNode): number {
  if (n.id.startsWith("session:") || n.id === "agent:voice" || n.id === "agent:telephony") return 0;
  if (n.kind === "agent") return 1;
  if (n.kind === "task" || n.kind === "coding" || n.kind === "schedule") return 2;
  if (n.kind === "tool" || n.kind === "connector" || n.kind === "skill") return 3;
  return 4;
}

/**
 * Positions for the live-activity view. ``active`` nodes (live activity) come to the front of their column and
 * first in order, so what is happening right now is readable left to right.
 */
export function flowPlacement(model: Pick<GraphModel, "nodes">, active: Set<string>): Float32Array {
  const out = new Float32Array(model.nodes.length * 3);
  const cols: GNode[][] = FLOW_COLUMNS.map(() => []);
  for (const n of model.nodes) cols[flowColumn(n)]!.push(n);
  cols.forEach((list, c) => {
    const x = FLOW_COLUMNS[c]!.x;
    const live = list.filter((n) => active.has(n.id)).sort((a, b) => (a.id === "agent:jarvis" ? -1 : b.id === "agent:jarvis" ? 1 : 0) || b.weight - a.weight || (a.id < b.id ? -1 : 1));
    const rest = list.filter((n) => !active.has(n.id)).sort((a, b) => (a.id === "agent:jarvis" ? -1 : b.id === "agent:jarvis" ? 1 : 0) || b.weight - a.weight || (a.id < b.id ? -1 : 1));
    // what takes part right now stands in front, one below the other, so every label stays readable
    live.forEach((node, k) => out.set([x, 120 - k * 46, 110], node.index * 3));
    if (c === 4) {
      // memory: a calm sphere of knowledge behind the recalled notes
      const n = rest.length;
      const R = Math.min(190, 40 + 7 * Math.cbrt(n) * 4);
      rest.forEach((node, k) => {
        const y = 1 - ((k + 0.5) / Math.max(1, n)) * 2;
        const r = Math.sqrt(1 - y * y);
        const th = k * Math.PI * (3 - Math.sqrt(5));
        out.set([x + 40 + Math.cos(th) * r * R * 0.8, y * R, Math.sin(th) * r * R * 0.8 - 120], node.index * 3);
      });
      return;
    }
    const perRow = c === 3 ? 9 : c === 1 ? 1 : 4;
    const gap = c === 3 ? 26 : c === 1 ? 70 : 42;
    const rows = Math.ceil(rest.length / perRow);
    rest.forEach((node, k) => {
      const row = Math.floor(k / perRow);
      const col = k % perRow;
      out.set([x + (col - (perRow - 1) / 2) * gap, ((rows - 1) / 2 - row) * gap, -60 - (c === 3 ? (col % 2) * 10 : 0)], node.index * 3);
    });
  });
  return out;
}
