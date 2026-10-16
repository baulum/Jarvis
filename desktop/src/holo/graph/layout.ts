// 3D force layout (d3-force-3d). Runs in a Web Worker in the app; pure enough to unit-test in node.
// Links pull related knowledge together, many-body repulsion keeps it readable, a cluster force keeps
// each kind of knowledge in its region of the brain. J.A.R.V.I.S. itself is pinned to the centre.
import { forceLink, forceManyBody, forceSimulation, type SimNode, type Simulation } from "d3-force-3d";
import { groupAnchor } from "./model";

export interface LayoutNodeIn {
  id: string;
  group: string;
  degree: number;
  pinned?: boolean;
}

export interface LayoutLinkIn {
  source: number;
  target: number;
  weight: number;
  relation: string;
}

interface N extends SimNode {
  id: string;
  ax: number;
  ay: number;
  az: number;
  k: number;
}

export interface Layout {
  tick(n?: number): number;
  positions(out: Float32Array): void;
  reheat(alpha: number): void;
  alpha(): number;
}

function clusterForce(strength: number) {
  let nodes: N[] = [];
  const force = (alpha: number) => {
    for (const n of nodes) {
      const k = strength * n.k * alpha;
      n.vx = (n.vx ?? 0) + (n.ax - (n.x ?? 0)) * k;
      n.vy = (n.vy ?? 0) + (n.ay - (n.y ?? 0)) * k;
      n.vz = (n.vz ?? 0) + (n.az - (n.z ?? 0)) * k;
    }
  };
  force.initialize = (ns: N[]) => {
    nodes = ns;
  };
  return force;
}

export function createLayout(
  nodesIn: LayoutNodeIn[],
  links: LayoutLinkIn[],
  positions: Float32Array,
  alpha = 1,
): Layout {
  const nodes: N[] = nodesIn.map((n, i) => {
    const [ax, ay, az] = groupAnchor(n.group);
    const node: N = {
      id: n.id,
      x: positions[i * 3] ?? 0,
      y: positions[i * 3 + 1] ?? 0,
      z: positions[i * 3 + 2] ?? 0,
      ax,
      ay,
      az,
      // well-connected notes may drift towards their neighbours, loose ones stay home
      k: n.group === "system" ? 1.4 : 1 / (1 + n.degree * 0.15),
    };
    if (n.pinned) {
      node.fx = node.x;
      node.fy = node.y;
      node.fz = node.z;
    }
    return node;
  });
  const sim: Simulation<N> = forceSimulation(nodes, 3)
    .alpha(alpha)
    .alphaDecay(0.028)
    .alphaMin(0.004)
    .velocityDecay(0.42)
    .force(
      "link",
      forceLink<N, { source: number; target: number; weight: number; relation: string }>(links.map((l) => ({ ...l })))
        .distance((l) => (l.relation === "gehört zu" ? 14 : l.relation === "ähnlich" ? 48 : l.relation === "kann" ? 70 : 34))
        .strength((l) => Math.min(1, 0.25 + l.weight * 0.6) * (l.relation === "nutzt" ? 0.3 : 1)),
    )
    .force(
      "charge",
      forceManyBody<N>()
        .strength((n) => (n.id === "agent:jarvis" ? -600 : n.id.startsWith("tool:") ? -14 : -60))
        .theta(0.9)
        .distanceMax(420),
    )
    .force("cluster", clusterForce(0.05))
    .stop();
  return {
    tick(n = 1) {
      sim.tick(n);
      return sim.alpha();
    },
    positions(out: Float32Array) {
      nodes.forEach((n, i) => {
        out[i * 3] = n.x ?? 0;
        out[i * 3 + 1] = n.y ?? 0;
        out[i * 3 + 2] = n.z ?? 0;
      });
    },
    reheat(a: number) {
      sim.alpha(Math.max(sim.alpha(), a));
    },
    alpha: () => sim.alpha(),
  };
}

export type LayoutRequest = {
  type: "init";
  nodes: LayoutNodeIn[];
  links: LayoutLinkIn[];
  positions: Float32Array;
  alpha: number;
};
export type LayoutResponse = { type: "tick"; positions: Float32Array; alpha: number } | { type: "settled" };
