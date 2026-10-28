// Graph model, camera math and layout (live activity: live.test.ts, anatomy: anatomy.test.ts).
import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { boundingSphere, fitBoxDistance, fitDistance, focusPose, freeFraction, pickNearest, viewShift } from "./camera";
import { createLayout } from "./layout";
import { buildModel, mergeDTO, searchLocal, seedPosition } from "./model";
import type { GraphDTO } from "./types";

const dto = (extraNodes: GraphDTO["nodes"] = [], extraEdges: GraphDTO["edges"] = []): GraphDTO => ({
  version: 1,
  counts: {},
  truncated: { customer: 3 },
  nodes: [
    { id: "agent:jarvis", kind: "agent", label: "J.A.R.V.I.S.", group: "system", weight: 1, meta: {} },
    { id: "connector:lexware", kind: "connector", label: "Lexware", group: "system", weight: 0.5, meta: {} },
    { id: "tool:lexware_invoice_create", kind: "tool", label: "lexware_invoice_create", group: "system", weight: 0.2, meta: {} },
    { id: "memory:a", kind: "customer", label: "Westside Taxi UG", group: "customer", weight: 0.6, meta: {} },
    { id: "memory:b", kind: "project", label: "Westside Taxi Website", group: "project", weight: 0.5, meta: {} },
    ...extraNodes,
  ],
  edges: [
    { source: "connector:lexware", target: "agent:jarvis", relation: "nutzt", weight: 0.6 },
    { source: "tool:lexware_invoice_create", target: "connector:lexware", relation: "gehört zu", weight: 0.8 },
    { source: "memory:b", target: "memory:a", relation: "betrifft", weight: 1 },
    { source: "memory:b", target: "memory:missing", relation: "betrifft", weight: 1 },
    ...extraEdges,
  ],
});

describe("graph model", () => {
  it("builds indices, neighbours and drops edges to unknown nodes", () => {
    const m = buildModel(dto());
    expect(m.nodes).toHaveLength(5);
    expect(m.edges).toHaveLength(3);
    expect(m.nodes[m.byId.get("memory:b")!]!.degree).toBe(1);
    expect(m.nodes[m.byId.get("tool:lexware_invoice_create")!]!.category).toBe("tool");
    expect(Array.from(m.positions.subarray(0, 3))).toEqual([0, 0, 0]); // J.A.R.V.I.S. pinned at the centre
  });

  it("keeps positions of existing nodes and grows new ones next to a neighbour", () => {
    const first = buildModel(dto());
    first.positions.set([100, 50, -20], first.byId.get("memory:a")! * 3);
    const next = buildModel(
      dto([{ id: "memory:c", kind: "person", label: "Ali Demir", group: "customer", weight: 0.3, meta: {} }], [
        { source: "memory:c", target: "memory:a", relation: "Geschäftsführer", weight: 1 },
      ]),
      first,
      42,
    );
    const a = next.byId.get("memory:a")! * 3;
    const c = next.byId.get("memory:c")! * 3;
    expect(Array.from(next.positions.subarray(a, a + 3))).toEqual([100, 50, -20]);
    expect(Math.hypot(next.positions[c]! - 100, next.positions[c + 1]! - 50, next.positions[c + 2]! + 20)).toBeLessThan(20);
    expect(next.nodes[next.byId.get("memory:c")!]!.bornAt).toBe(42); // spawn animation only for the new one
    expect(next.nodes[next.byId.get("memory:a")!]!.bornAt).toBe(0);
  });

  it("seeds deterministically per group and searches labels", () => {
    expect(seedPosition("memory:x", "customer")).toEqual(seedPosition("memory:x", "customer"));
    const m = buildModel(dto());
    expect(searchLocal(m, "westside").map((n) => n.id)).toEqual(["memory:a", "memory:b"]);
    const merged = mergeDTO(dto(), { nodes: [{ id: "memory:a", kind: "customer", label: "dup", group: "customer", weight: 0, meta: {} }], edges: [] });
    expect(merged.nodes.filter((n) => n.id === "memory:a")).toHaveLength(1);
  });
});

describe("camera math", () => {
  const pos = new Float32Array([0, 0, 0, 10, 0, 0, 0, 10, 0, 1000, 0, 0]);
  it("frames clusters robustly against outliers", () => {
    expect(boundingSphere(pos, [0, 1, 2]).radius).toBeGreaterThan(5);
    const robust = boundingSphere(pos, [0, 1, 2, 3], 0.6);
    const full = boundingSphere(pos, [0, 1, 2, 3]);
    expect(robust.radius).toBeLessThan(full.radius);
    expect(fitDistance(100, 42, 1)).toBeCloseTo(100 / Math.sin((21 * Math.PI) / 180), 3);
  });

  it("fits wide boxes by their width", () => {
    const wide = fitBoxDistance(500, 100, 42, 16 / 9, 1);
    expect(wide).toBeCloseTo(500 / (Math.tan((21 * Math.PI) / 180) * (16 / 9)), 3);
    expect(fitBoxDistance(100, 500, 42, 16 / 9, 1)).toBeCloseTo(500 / Math.tan((21 * Math.PI) / 180), 3);
  });

  it("keeps the viewing direction and shifts the target left of the detail panel", () => {
    const pose = focusPose([0, 0, 500], [0, 0, 0], 100);
    expect(pose.position.map((v) => Math.round(v))).toEqual([0, 0, 100]);
    const shifted = focusPose([0, 0, 500], [0, 0, 0], 100, 0.2);
    expect(shifted.lookAt[0]).not.toBe(0);
  });

  it("picks the node under the cursor (identity projection)", () => {
    const vp = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
    const p = new Float32Array([0, 0, 0, 0.5, 0.5, 0]);
    expect(pickNearest(p, 2, vp, 200, 200, 100, 100, () => 8)).toBe(0);
    expect(pickNearest(p, 2, vp, 200, 200, 150, 50, () => 8)).toBe(1);
    expect(pickNearest(p, 2, vp, 200, 200, 10, 190, () => 8)).toBe(-1);
    expect(pickNearest(p, 2, vp, 200, 200, 100, 100, () => 8, (i) => i !== 0)).toBe(-1); // filtered out
  });

  it("frames the brain in the space the chat dock leaves free", () => {
    expect(freeFraction(0, 800)).toBe(1);
    expect(freeFraction(200, 800)).toBe(0.75);
    expect(freeFraction(790, 800)).toBeCloseTo(0.4); // never squeezed below 40 % of the window
    expect(freeFraction(-5, 800)).toBe(1);
    expect(viewShift(0, 800)).toBe(0);
    expect(viewShift(300, 800)).toBe(150);
    expect(viewShift(100, 0)).toBe(0);
  });

  it("puts the look-at point in the middle of the free area above the dock (real three.js projection)", () => {
    const [w, h, dock] = [1200, 800, 320];
    const cam = new THREE.PerspectiveCamera(42, w / h, 1, 6000);
    cam.position.set(0, 0, 900);
    cam.lookAt(0, 0, 0);
    cam.updateMatrixWorld();
    cam.setViewOffset(w, h, 0, viewShift(dock, h), w, h);
    const v = new THREE.Vector3(0, 0, 0).project(cam);
    const screenY = (-v.y * 0.5 + 0.5) * h;
    expect(screenY).toBeCloseTo((h - dock) / 2, 3); // centre of the free strip, not of the window
    expect((v.x * 0.5 + 0.5) * w).toBeCloseTo(w / 2, 3);
    // a sphere fitted with the free fraction stays inside the free strip
    const r = 100;
    cam.position.set(0, 0, fitDistance(r, 42, freeFraction(dock, h)));
    cam.updateMatrixWorld();
    const top = new THREE.Vector3(0, r, 0).project(cam);
    const bottom = new THREE.Vector3(0, -r, 0).project(cam);
    expect((-top.y * 0.5 + 0.5) * h).toBeGreaterThanOrEqual(-1);
    expect((-bottom.y * 0.5 + 0.5) * h).toBeLessThanOrEqual(h - dock + 1);
  });
});

describe("layout", () => {
  it("settles connected nodes closer than unconnected ones and keeps the pinned centre", () => {
    const m = buildModel(dto());
    const layout = createLayout(
      m.nodes.map((n) => ({ id: n.id, group: n.group, degree: n.degree, pinned: n.id === "agent:jarvis" })),
      m.edges.map((e) => ({ ...e })),
      new Float32Array(m.positions),
    );
    for (let i = 0; i < 300 && layout.alpha() > 0.004; i += 1) layout.tick();
    const out = new Float32Array(m.positions.length);
    layout.positions(out);
    const d = (a: string, b: string) => {
      const i = m.byId.get(a)! * 3;
      const j = m.byId.get(b)! * 3;
      return Math.hypot(out[i]! - out[j]!, out[i + 1]! - out[j + 1]!, out[i + 2]! - out[j + 2]!);
    };
    expect(d("tool:lexware_invoice_create", "connector:lexware")).toBeLessThan(d("tool:lexware_invoice_create", "memory:a"));
    expect(Array.from(out.subarray(0, 3)).map((v) => Math.round(v))).toEqual([0, 0, 0]);
  });
});
