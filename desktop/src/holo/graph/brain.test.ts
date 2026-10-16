// Graph model, live activity from real events, interface state, camera math and layout.
import { describe, expect, it } from "vitest";
import type { BusEvent } from "../../lib/types";
import { boundingSphere, fitDistance, focusPose, pickNearest } from "./camera";
import { createLayout } from "./layout";
import { applyLiveEvent, deriveJarvisState, emptyLive, expireLive } from "./live";
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

const ev = (type: string, data: Record<string, unknown>, extra: Partial<BusEvent> = {}): BusEvent => ({
  id: Math.random().toString(36),
  type,
  ts: new Date().toISOString(),
  data,
  ...extra,
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

describe("live layer", () => {
  it("lights recalled memories and connects them to the tool that looked them up", () => {
    const s = applyLiveEvent(
      emptyLive(),
      ev("memory.recalled", { via: "memory_search", purpose: "search", hits: [{ title: "Westside Taxi UG" }] }, { nodes: ["memory:a", "session:s1"], session_id: "s1" }),
      1000,
    );
    expect(s.nodes["memory:a"]?.status).toBe("recalled");
    expect(s.nodes["session:s1"]).toBeUndefined();
    expect(s.streams["recall:s1"]?.path).toEqual(["agent:jarvis", "tool:memory_search", "memory:a"]);
    expect(s.lines.at(-1)?.text).toContain("Westside Taxi UG");
    expect(expireLive(s, 1000 + 10_000).nodes["memory:a"]).toBeUndefined();
  });

  it("follows a tool call from start to success and to failure", () => {
    let s = applyLiveEvent(emptyLive(), ev("tool.started", { tool: "lexware_invoice_create", connector: "lexware" }, { session_id: "s1" }), 0);
    expect(s.nodes["tool:lexware_invoice_create"]?.status).toBe("active");
    expect(s.streams["tool:s1"]).toMatchObject({ status: "running", path: ["agent:jarvis", "connector:lexware", "tool:lexware_invoice_create"] });
    expect(deriveJarvisState({ connection: "open", pendingApprovals: 0, agentRunning: true, live: s, now: 10 })).toBe("tools");
    const ok = applyLiveEvent(s, ev("tool.finished", { tool: "lexware_invoice_create", connector: "lexware", status: "ok" }, { session_id: "s1" }), 50);
    expect(ok.nodes["tool:lexware_invoice_create"]?.status).toBe("ok");
    s = applyLiveEvent(s, ev("tool.finished", { tool: "lexware_invoice_create", status: "error", message: "401" }, { session_id: "s1" }), 50);
    expect(s.nodes["tool:lexware_invoice_create"]).toMatchObject({ status: "error", detail: "401" });
    expect(deriveJarvisState({ connection: "open", pendingApprovals: 0, agentRunning: false, live: s, now: 100 })).toBe("error");
  });

  it("tracks coding jobs, tasks and approvals; ignores unrelated events", () => {
    let s = applyLiveEvent(emptyLive(), ev("coding.updated", { job_id: "j1", status: "running", title: "Website" }), 0);
    expect(s.streams["coding:j1"]?.status).toBe("running");
    expect(deriveJarvisState({ connection: "open", pendingApprovals: 0, agentRunning: false, live: s, now: 1 })).toBe("coding");
    s = applyLiveEvent(s, ev("task.updated", { status: "waiting_approval", title: "Neukunde" }, { task_id: "t1" }), 2);
    expect(s.nodes["task:t1"]?.status).toBe("approval");
    s = applyLiveEvent(s, ev("approval.requested", { tool: "lexware_invoice_create", summary: "Rechnung 299 €" }), 3);
    expect(s.nodes["tool:lexware_invoice_create"]?.status).toBe("approval");
    expect(deriveJarvisState({ connection: "open", pendingApprovals: 1, agentRunning: false, live: s, now: 4 })).toBe("approval");
    expect(applyLiveEvent(s, ev("log", {}), 5)).toBe(s);
  });

  it("derives voice states with priority", () => {
    const live = emptyLive();
    const base = { connection: "open", pendingApprovals: 0, agentRunning: false, live, now: 0 };
    expect(deriveJarvisState({ ...base, voice: "speaking" })).toBe("speaking");
    expect(deriveJarvisState({ ...base, voice: "recording" })).toBe("listening");
    expect(deriveJarvisState({ ...base, voice: "thinking" })).toBe("thinking");
    expect(deriveJarvisState({ ...base, agentRunning: true, live: { ...live, lastRecall: 100 }, now: 500 })).toBe("retrieving");
    expect(deriveJarvisState({ ...base, agentRunning: true, now: 500 })).toBe("thinking"); // no recall yet
    expect(deriveJarvisState({ ...base, voice: "speaking", pendingApprovals: 2 })).toBe("approval");
    expect(deriveJarvisState({ ...base, connection: "closed" })).toBe("error");
    expect(deriveJarvisState(base)).toBe("idle");
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
