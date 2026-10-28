// Shared render state of the 3D layer: displayed positions, the camera, how "anatomical" the view currently is,
// the uniforms every brain material reads, and where an impulse anchor ("tool:x", "region:memory") is right now.
import * as THREE from "three";
import { GROUP_ANCHOR, REGIONS, regionForId, regionOf, type RegionGroup, type Vec3 } from "./anatomy";
import { MAX_GLOW, MAX_WAVES } from "./anatomyShaders";
import type { GraphModel } from "./model";

export interface BrainHandle {
  /** current eased positions (xyz per node) – used by edges, labels, impulses and picking */
  positions: Float32Array;
  /** where the nodes are heading in the current view */
  target: Float32Array;
  model: GraphModel | null;
  camera: THREE.PerspectiveCamera | null;
  size: { width: number; height: number };
  /** 1 = anatomical brain view, 0 = free layouts (eased during view changes) */
  brainness: number;
  /** centroid per region group in the free layouts (refreshed a few times per second) */
  groupCentroid: Partial<Record<RegionGroup, Vec3>>;
}

export const brainHandle: BrainHandle = {
  positions: new Float32Array(0),
  target: new Float32Array(0),
  model: null,
  camera: null,
  size: { width: 1, height: 1 },
  brainness: 1,
  groupCentroid: {},
};

/** Glow centre and reach of each legend group inside the brain. */
export const GLOW_SOURCE: Record<RegionGroup, [number, number, number, number]> = {
  input: [0, 18, -128, 78],
  core: [0, 26, 50, 105],
  memory: [0, -36, 6, 80],
  tools: [0, 90, 0, 105],
  agents: [0, 46, -104, 100],
  output: [-116, 2, 72, 48],
};

const v4 = (n: number) => Array.from({ length: n }, () => new THREE.Vector4());

/** One set of uniforms shared by cortex, neurons and synapses (filled once per frame). */
export const sceneUniforms = {
  uTime: { value: 0 },
  uOpacity: { value: 1 },
  uOffline: { value: 0 },
  uFocus: { value: 900 },
  uPixelRatio: { value: 1 },
  uTint: { value: new THREE.Color(0.42, 0.78, 0.9) },
  uGlowPos: { value: v4(MAX_GLOW) },
  uGlowCol: { value: v4(MAX_GLOW) },
  uWavePos: { value: v4(MAX_WAVES) },
  uWaveCol: { value: v4(MAX_WAVES) },
};

function brainAnchor(group: RegionGroup): Vec3 {
  const r = REGIONS[GROUP_ANCHOR[group]];
  // language (input/output) and the memory relay sit on the left in this brain
  return r.mirror ? [-r.center[0], r.center[1], r.center[2]] : r.center;
}

/** Fallback for free layouts when a group has no nodes: around J.A.R.V.I.S. */
const FREE_OFFSET: Record<RegionGroup, Vec3> = {
  input: [-260, 40, 0],
  core: [0, 0, 0],
  memory: [200, -40, 0],
  tools: [60, -160, -60],
  agents: [-120, 120, -40],
  output: [-260, -60, 40],
};

/** Writes the current world position of an anchor into ``out``. */
export function anchorPosition(anchor: string, out: THREE.Vector3): THREE.Vector3 {
  const h = brainHandle;
  const m = h.model;
  if (!anchor.startsWith("region:")) {
    const i = m?.byId.get(anchor);
    if (i !== undefined && h.positions.length >= (i + 1) * 3) {
      return out.set(h.positions[i * 3]!, h.positions[i * 3 + 1]!, h.positions[i * 3 + 2]!);
    }
    return anchorPosition(`region:${REGIONS[regionForId(anchor)].group}`, out);
  }
  const group = anchor.slice(7) as RegionGroup;
  const b = brainAnchor(group);
  const free = h.groupCentroid[group] ?? jarvisOffset(group);
  const k = h.brainness;
  return out.set(free[0] + (b[0] - free[0]) * k, free[1] + (b[1] - free[1]) * k, free[2] + (b[2] - free[2]) * k);
}

function jarvisOffset(group: RegionGroup): Vec3 {
  const h = brainHandle;
  const i = h.model?.byId.get("agent:jarvis");
  const base: Vec3 = i !== undefined && h.positions.length >= (i + 1) * 3 ? [h.positions[i * 3]!, h.positions[i * 3 + 1]!, h.positions[i * 3 + 2]!] : [0, 0, 0];
  const o = FREE_OFFSET[group];
  return [base[0] + o[0], base[1] + o[1], base[2] + o[2]];
}

/** Recomputes the free-layout centroids of the region groups from the displayed positions. */
export function refreshCentroids(): void {
  const h = brainHandle;
  const m = h.model;
  if (!m || h.positions.length < m.nodes.length * 3) return;
  const sum: Partial<Record<RegionGroup, [number, number, number, number]>> = {};
  for (const n of m.nodes) {
    const g = REGIONS[regionOf(n)].group;
    const s = (sum[g] ??= [0, 0, 0, 0]);
    s[0] += h.positions[n.index * 3]!;
    s[1] += h.positions[n.index * 3 + 1]!;
    s[2] += h.positions[n.index * 3 + 2]!;
    s[3] += 1;
  }
  const out: Partial<Record<RegionGroup, Vec3>> = {};
  for (const [g, s] of Object.entries(sum) as [RegionGroup, [number, number, number, number]][]) {
    out[g] = [s[0] / s[3], s[1] / s[3], s[2] / s[3]];
  }
  // the output has no nodes of its own: the answer leaves next to the conversations it goes to
  if (out.input) out.output = [out.input[0] - 40, out.input[1] - 70, out.input[2] + 30];
  h.groupCentroid = out;
}
