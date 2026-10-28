// Brain anatomy: shape, mesh, neurons, regions, placement of real nodes, the live flow, explorer filters, quality.
import { describe, expect, it } from "vitest";
import {
  brainPlacement,
  brainSdf,
  buildSubstrate,
  buildSurface,
  classOf,
  flowPlacement,
  FLOW_COLUMNS,
  REGIONS,
  regionCenter,
  regionForId,
  regionOf,
  sdfNormal,
} from "./anatomy";
import { exploreMemories, projectsOf, type ExploreFilter } from "./explore";
import { buildModel } from "./model";
import { detectTier, stepTier } from "./quality";
import type { GraphDTO } from "./types";

const node = (id: string, kind: string, group: string, meta: Record<string, unknown> = {}) => ({ id, kind, label: id, group, weight: 0.4, meta });

const DTO: GraphDTO = {
  version: 1,
  counts: {},
  truncated: {},
  nodes: [
    node("agent:jarvis", "agent", "system"),
    node("agent:coding", "agent", "system"),
    node("tool:browser_open", "tool", "system"),
    node("connector:browser", "connector", "system"),
    node("session:s1", "conversation", "conversations"),
    node("memory:p", "project", "project", { updated_at: "2026-10-27T10:00:00Z", access_count: 2 }),
    node("memory:a", "technical", "semantic", { updated_at: "2026-10-28T08:00:00Z", access_count: 9 }),
    node("memory:b", "preference", "preference", { updated_at: "2025-01-01T08:00:00Z" }),
    node("memory:c", "process", "procedural", { updated_at: "2026-10-20T08:00:00Z" }),
    node("entity:ali", "person", "entities"),
  ],
  edges: [
    { source: "memory:a", target: "memory:p", relation: "betrifft", weight: 1 },
    { source: "entity:ali", target: "memory:p", relation: "arbeitet an", weight: 1 },
    { source: "tool:browser_open", target: "connector:browser", relation: "gehört zu", weight: 0.8 },
  ],
};

describe("brain shape", () => {
  it("has two hemispheres separated by the longitudinal fissure, a cerebellum and a brain stem", () => {
    expect(brainSdf(70, 30, 0)).toBeLessThan(-20); // inside the right hemisphere
    expect(brainSdf(-70, 30, 0)).toBeLessThan(-20); // left
    expect(brainSdf(0, 95, 0)).toBeGreaterThan(0); // the fissure is open at the top
    expect(brainSdf(0, 0, -6)).toBeLessThan(0); // closed in the depth (corpus callosum)
    expect(brainSdf(30, -62, -104)).toBeLessThan(0); // cerebellum
    expect(brainSdf(0, -110, -55)).toBeLessThan(0); // brain stem
    expect(brainSdf(0, 0, 240)).toBeGreaterThan(0);
  });

  it("extracts a surface mesh whose vertices lie on the shape", () => {
    const s = buildSurface(28);
    expect(s.positions.length / 3).toBeGreaterThan(1000);
    expect(s.indices.length % 3).toBe(0);
    let worst = 0;
    for (let i = 0; i < s.positions.length; i += 3) worst = Math.max(worst, Math.abs(brainSdf(s.positions[i]!, s.positions[i + 1]!, s.positions[i + 2]!)));
    expect(worst).toBeLessThan(12);
    for (const i of s.indices) expect(i).toBeLessThan(s.positions.length / 3);
  });

  it("winds every triangle counter-clockwise seen from outside (front faces point outwards)", () => {
    const s = buildSurface(28);
    const P = s.positions;
    let out = 0;
    const tris = s.indices.length / 3;
    for (let t = 0; t < tris; t += 1) {
      const [a, b, c] = [s.indices[t * 3]! * 3, s.indices[t * 3 + 1]! * 3, s.indices[t * 3 + 2]! * 3];
      const u = [P[b]! - P[a]!, P[b + 1]! - P[a + 1]!, P[b + 2]! - P[a + 2]!];
      const v = [P[c]! - P[a]!, P[c + 1]! - P[a + 1]!, P[c + 2]! - P[a + 2]!];
      const n = [u[1]! * v[2]! - u[2]! * v[1]!, u[2]! * v[0]! - u[0]! * v[2]!, u[0]! * v[1]! - u[1]! * v[0]!];
      const g = sdfNormal((P[a]! + P[b]! + P[c]!) / 3, (P[a + 1]! + P[b + 1]! + P[c + 1]!) / 3, (P[a + 2]! + P[b + 2]! + P[c + 2]!) / 3);
      if (n[0]! * g[0] + n[1]! * g[1] + n[2]! * g[2] > 0) out += 1;
    }
    expect(out / tris).toBeGreaterThan(0.97);
  });

  it("samples neurons inside the brain, linked to near neighbours, deterministically", () => {
    const a = buildSubstrate(400, 3, 2);
    expect(a.depth.length).toBe(400);
    for (let i = 0; i < 400; i += 1) expect(brainSdf(a.positions[i * 3]!, a.positions[i * 3 + 1]!, a.positions[i * 3 + 2]!)).toBeLessThan(0);
    expect(a.synapses.length).toBeGreaterThan(400);
    for (let k = 0; k < a.synapses.length; k += 2) {
      const i = a.synapses[k]!;
      const j = a.synapses[k + 1]!;
      expect(Math.hypot(a.positions[i * 3]! - a.positions[j * 3]!, a.positions[i * 3 + 1]! - a.positions[j * 3 + 1]!, a.positions[i * 3 + 2]! - a.positions[j * 3 + 2]!)).toBeLessThan(30);
    }
    expect(Array.from(buildSubstrate(400, 3, 2).positions)).toEqual(Array.from(a.positions));
  });

  it("puts every region inside the brain", () => {
    for (const r of Object.values(REGIONS)) {
      expect(brainSdf(...regionCenter(r, 1)), r.id).toBeLessThan(0);
      if (r.mirror) expect(brainSdf(...regionCenter(r, -1)), r.id).toBeLessThan(0);
    }
  });
});

describe("regions and placement", () => {
  const model = buildModel(DTO);

  it("maps real nodes to functional regions and classes", () => {
    const by = (id: string) => model.nodes[model.byId.get(id)!]!;
    expect(regionOf(by("agent:jarvis"))).toBe("thalamus");
    expect(regionOf(by("tool:browser_open"))).toBe("motor");
    expect(regionOf(by("memory:c"))).toBe("cerebellum"); // procedural memory
    expect(regionOf(by("memory:a"))).toBe("temporal");
    expect(regionOf(by("session:s1"))).toBe("visual");
    expect(regionForId("tool:unknown")).toBe("motor");
    expect(regionForId("memory:not-loaded")).toBe("hippocampus");
    expect(classOf(by("session:s1"))).toBe("shortterm");
    expect(classOf(by("memory:a"))).toBe("longterm");
    expect(classOf(by("tool:browser_open"))).toBe("tools");
    expect(classOf({ id: "memory:x", kind: "conversation" })).toBe("longterm"); // a note about a conversation
    expect(regionOf({ id: "memory:x", kind: "conversation", group: "episodic" })).toBe("hippocampus");
    expect(classOf(by("agent:coding"))).toBe("agents");
  });

  it("places nodes inside the brain, in their region, deterministically", () => {
    const p = brainPlacement(model);
    expect(Array.from(brainPlacement(model))).toEqual(Array.from(p));
    for (const n of model.nodes) {
      const [x, y, z] = [p[n.index * 3]!, p[n.index * 3 + 1]!, p[n.index * 3 + 2]!];
      expect(brainSdf(x, y, z), n.id).toBeLessThan(0);
      const r = REGIONS[regionOf(n)];
      const d = Math.min(...[1, -1].map((side) => Math.hypot(x - regionCenter(r, side as 1 | -1)[0], y - r.center[1], z - r.center[2])));
      expect(d, n.id).toBeLessThanOrEqual(r.radius + 23);
    }
    const j = model.byId.get("agent:jarvis")! * 3;
    expect(Array.from(p.subarray(j, j + 3))).toEqual(REGIONS.thalamus.center);
  });

  it("lays out the live view in columns and brings active nodes forward", () => {
    const idle = flowPlacement(model, new Set());
    const active = flowPlacement(model, new Set(["tool:browser_open"]));
    const t = model.byId.get("tool:browser_open")! * 3;
    expect(Math.abs(idle[t]! - FLOW_COLUMNS[3]!.x)).toBeLessThan(150);
    expect(active[t + 2]!).toBeGreaterThan(idle[t + 2]!);
    const s = model.byId.get("session:s1")! * 3;
    expect(idle[s]!).toBeLessThan(idle[t]!); // input left of tools
  });
});

describe("memory explorer", () => {
  const model = buildModel(DTO);
  const base: ExploreFilter = { query: "", semantic: [], categories: new Set(), project: null, range: "all", sort: "recent" };
  const now = Date.parse("2026-10-28T12:00:00Z");
  const ids = (f: Partial<ExploreFilter>) => exploreMemories(model, { ...base, ...f }, now).map((n) => n.id);

  it("lists long-term memories only, newest first", () => {
    expect(ids({})).toEqual(["memory:a", "memory:p", "memory:c", "memory:b", "entity:ali"]);
  });
  it("filters by project through real relations, by kind and by time range", () => {
    expect(ids({ project: "memory:p" }).sort()).toEqual(["entity:ali", "memory:a", "memory:p"]);
    expect(ids({ categories: new Set(["memory"]) })).toEqual(["memory:a", "memory:c", "memory:b"]);
    expect(ids({ range: "7d" })).toEqual(["memory:a", "memory:p"]);
    expect(projectsOf(model).map((n) => n.id)).toEqual(["memory:p"]);
  });
  it("searches titles and semantic hits; sorts by usage", () => {
    expect(ids({ query: "memory:c" })).toEqual(["memory:c"]);
    expect(ids({ query: "zzz", semantic: ["memory:b"], sort: "relevance" })).toEqual(["memory:b"]);
    expect(ids({ sort: "used" })[0]).toBe("memory:a");
  });
});

describe("adaptive quality", () => {
  it("picks a tier per device and steps within bounds", () => {
    expect(detectTier({ coarsePointer: true, width: 1200, cores: 10 })).toBe(0);
    expect(detectTier({ coarsePointer: false, width: 600, cores: 10 })).toBe(0);
    expect(detectTier({ coarsePointer: false, width: 1400, cores: 4 })).toBe(1);
    expect(detectTier({ coarsePointer: false, width: 1400, cores: 10 })).toBe(2);
    expect(stepTier(2, 1, 2)).toBe(2);
    expect(stepTier(0, -1, 2)).toBe(0);
    expect(stepTier(1, 1, 1)).toBe(1);
  });
});
