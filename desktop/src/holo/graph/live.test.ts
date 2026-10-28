// Live layer: filtered activity steps → processes, node activity, event stream, effects; resync; error states.
import { beforeEach, describe, expect, it } from "vitest";
import type { BusEvent } from "../../lib/types";
import { fx, heldGlow, pushEffects, resetFx, stepFx } from "./fx";
import {
  applySnapshot,
  applyStep,
  deriveJarvisState,
  emptyLive,
  expireLive,
  formatDuration,
  headline,
  stepFromEvent,
  type ActivitySnapshot,
  type ActivityStep,
  type LiveState,
} from "./live";

let seq = 0;
const step = (s: Partial<ActivityStep> & Pick<ActivityStep, "phase" | "state" | "status" | "key" | "label">): ActivityStep => ({
  nodes: [],
  t: Date.now(),
  seq: ++seq,
  boot: "b1",
  ...s,
});

/** The steps the core sends for one turn with two parallel tools (see core/tests/unit/test_activity_pipeline.py). */
function turn(): ActivityStep[] {
  return [
    step({ phase: "request", state: "start", status: "running", key: "turn:s1", label: "Analysiere Anfrage", session_id: "s1", nodes: ["agent:jarvis", "session:s1"], channel: "desktop" }),
    step({ phase: "memory.retrieve", state: "end", status: "ok", key: "recall:1", label: "Durchsuche Erinnerungen", parent: "turn:s1", nodes: ["memory:a", "memory:b"], count: 2, duration_ms: 12, purpose: "context" }),
    step({ phase: "llm", state: "progress", status: "running", key: "turn:s1", label: "Sprachmodell verarbeitet die Anfrage", model: "sonnet", nodes: ["agent:jarvis"] }),
    step({ phase: "response", state: "progress", status: "running", key: "turn:s1", label: "Generiere Antwort", nodes: ["agent:jarvis"] }),
    step({ phase: "tool", state: "start", status: "running", key: "call:c1", label: "Starte Browser", parent: "turn:s1", tool: "browser_open", connector: "browser", parallel: 1, nodes: ["tool:browser_open", "connector:browser"] }),
    step({ phase: "tool", state: "start", status: "running", key: "call:c2", label: "Durchsuche Erinnerungen", parent: "turn:s1", tool: "memory_search", connector: "memory", parallel: 2, nodes: ["tool:memory_search", "connector:memory"] }),
    step({ phase: "memory.retrieve", state: "end", status: "ok", key: "recall:2", label: "Durchsuche Erinnerungen", parent: "call:c2", nodes: ["memory:c"], count: 1, purpose: "search" }),
  ];
}

function fold(s: LiveState, steps: ActivityStep[], now = 1000) {
  const effects = [];
  for (const st of steps) {
    const r = applyStep(s, st, now);
    s = r.state;
    effects.push(...r.effects);
  }
  return { s, effects };
}

beforeEach(() => {
  seq = 0;
  resetFx();
});

describe("activity steps", () => {
  it("reads the step from the bus envelope", () => {
    const ev: BusEvent = { id: "e", type: "activity.step", ts: "", nodes: ["memory:a"], data: { phase: "memory.retrieve", state: "end", status: "ok", key: "recall:x", label: "x", seq: 1, boot: "b", t: 0 } };
    expect(stepFromEvent(ev)?.nodes).toEqual(["memory:a"]);
    expect(stepFromEvent({ ...ev, type: "tool.started" })).toBeNull();
    expect(stepFromEvent({ ...ev, data: { nope: 1 } })).toBeNull();
  });

  it("follows a turn: request, recall, model, streaming and two parallel tool calls", () => {
    const { s, effects } = fold(emptyLive(), turn());
    expect(s.processes["turn:s1"]).toMatchObject({ label: "Generiere Antwort", status: "running", model: "sonnet" });
    expect(s.processes["call:c1"]).toMatchObject({ tool: "browser_open", path: ["agent:jarvis", "connector:browser", "tool:browser_open"] });
    expect(s.nodes["memory:a"]?.status).toBe("recalled");
    expect(s.nodes["tool:browser_open"]?.status).toBe("active");
    expect(s.lines.map((l) => l.text)).toContain("Starte Browser");
    expect(s.lines.find((l) => l.text === "Durchsuche Erinnerungen")?.detail).toBe("2 Treffer · 12 ms");
    // effects follow the real path: input → session → core, core → tool, memory_search → hippocampus → hit
    const impulses = effects.filter((e) => e.kind === "impulse");
    expect(impulses[0]).toMatchObject({ path: ["region:input", "session:s1", "agent:jarvis"] });
    expect(impulses).toContainEqual({ kind: "impulse", path: ["agent:jarvis", "region:memory", "memory:a"], tone: "memory", count: 1 });
    expect(impulses).toContainEqual({ kind: "impulse", path: ["tool:memory_search", "region:memory", "memory:c"], tone: "memory", count: 1 });
    expect(effects).toContainEqual({ kind: "wave", at: "agent:jarvis", tone: "signal", strength: 1 });
    const h = headline(s, 2000);
    expect(h.text).toBe("Warte auf 2 Tool-Ergebnisse");
    expect(h.detail).toBe("browser_open · memory_search");
    expect(deriveJarvisState({ connection: "open", pendingApprovals: 0, agentRunning: true, live: s, now: 2000 })).toBe("tools");
  });

  it("ends tool calls with results flowing back, records runtimes and errors", () => {
    let { s } = fold(emptyLive(), turn());
    let r = applyStep(s, step({ phase: "tool", state: "end", status: "ok", key: "call:c1", label: "Ergebnis von browser_open", parent: "turn:s1", tool: "browser_open", connector: "browser", duration_ms: 2300, nodes: ["tool:browser_open"] }), 3000);
    s = r.state;
    expect(r.effects[0]).toEqual({ kind: "impulse", path: ["tool:browser_open", "connector:browser", "agent:jarvis"], tone: "data", count: 3 });
    expect(s.tools.browser_open).toMatchObject({ calls: 1, errors: 0, avg_ms: 2300, last_ms: 2300, running: 0 });
    r = applyStep(s, step({ phase: "tool", state: "end", status: "error", key: "call:c2", label: "memory_search fehlgeschlagen", parent: "turn:s1", tool: "memory_search", error: "Zeitüberschreitung nach 30s", duration_ms: 30000, nodes: ["tool:memory_search"] }), 3100);
    s = r.state;
    expect(s.nodes["tool:memory_search"]).toMatchObject({ status: "error", detail: "Zeitüberschreitung nach 30s" });
    expect(s.lines.at(-1)).toMatchObject({ tone: "bad", detail: "Zeitüberschreitung nach 30s" });
    expect(deriveJarvisState({ connection: "open", pendingApprovals: 0, agentRunning: true, live: s, now: 3200 })).toBe("error");
    r = applyStep(s, step({ phase: "response", state: "end", status: "ok", key: "turn:s1", label: "Antwort gesendet", session_id: "s1", duration_ms: 4200, nodes: ["agent:jarvis", "session:s1"] }), 3500);
    expect(r.state.processes["turn:s1"]).toMatchObject({ status: "ok", durationMs: 4200 });
    expect(r.effects).toContainEqual({ kind: "impulse", path: ["agent:jarvis", "region:output"], tone: "ok", count: 4 });
    expect(r.state.lines.at(-1)?.detail).toBe("4,2 s");
    // everything fades out after a while
    const later = expireLive(r.state, 3500 + 60_000);
    expect(Object.keys(later.processes)).toHaveLength(0);
    expect(later.nodes["memory:a"]).toBeUndefined();
  });

  it("marks failed and cancelled answers and closes orphaned tool calls with the turn", () => {
    let s = fold(emptyLive(), turn().slice(0, 5)).s;
    s = applyStep(s, step({ phase: "response", state: "end", status: "error", key: "turn:s1", label: "Antwort fehlgeschlagen", error: "Claude API: api_key=***", session_id: "s1", nodes: ["agent:jarvis"] }), 2000).state;
    expect(s.processes["call:c1"]?.status).toBe("cancelled");
    expect(s.lastError).toBe(2000);
    expect(s.lines.at(-1)).toMatchObject({ tone: "bad", detail: "Claude API: api_key=***" });
  });

  it("shows agents, approvals and new memories; ignores duplicates", () => {
    const steps = [
      step({ phase: "agent", state: "start", status: "running", key: "coding:j1", label: "Coding-Agent arbeitet", agent: "coding", nodes: ["agent:coding", "coding:j1"] }),
      step({ phase: "approval", state: "start", status: "waiting", key: "approval:a1", label: "Wartet auf Freigabe", tool: "gmail_send", nodes: ["tool:gmail_send"] }),
      step({ phase: "memory.store", state: "end", status: "ok", key: "memory:m9", label: "Speichere neue Erinnerung", change: "added", nodes: ["memory:m9"] }),
    ];
    const { s, effects } = fold(emptyLive(), steps);
    expect(s.nodes["coding:j1"]?.status).toBe("active");
    expect(effects).toContainEqual({ kind: "impulse", path: ["agent:jarvis", "agent:coding", "coding:j1"], tone: "agent", count: 3 });
    expect(s.nodes["tool:gmail_send"]?.status).toBe("approval");
    expect(headline(s, 0).text).toBe("Wartet auf Freigabe");
    expect(s.nodes["memory:m9"]?.status).toBe("new");
    expect(effects).toContainEqual({ kind: "grow", node: "memory:m9" });
    expect(deriveJarvisState({ connection: "open", pendingApprovals: 0, agentRunning: false, live: s, now: 0 })).toBe("coding");
    // the same step again (e.g. replayed by a resync) changes nothing
    expect(applyStep(s, steps[2]!, 5).state).toBe(s);
  });

  it("keeps state but produces no effects for replayed steps", () => {
    const r = applyStep(emptyLive(), turn()[0]!, 0, true);
    expect(r.effects).toEqual([]);
    expect(r.state.processes["turn:s1"]).toBeDefined();
    expect(r.state.lines[0]?.replayed).toBe(true);
  });

  it("derives voice states with priority", () => {
    const live = emptyLive();
    const base = { connection: "open", pendingApprovals: 0, agentRunning: false, live, now: 0 };
    expect(deriveJarvisState({ ...base, voice: "speaking" })).toBe("speaking");
    expect(deriveJarvisState({ ...base, voice: "recording" })).toBe("listening");
    expect(deriveJarvisState({ ...base, voice: "thinking" })).toBe("thinking");
    expect(deriveJarvisState({ ...base, agentRunning: true, live: { ...live, lastRecall: 100 }, now: 500 })).toBe("retrieving");
    expect(deriveJarvisState({ ...base, agentRunning: true, now: 500 })).toBe("thinking");
    expect(deriveJarvisState({ ...base, voice: "speaking", pendingApprovals: 2 })).toBe("approval");
    expect(deriveJarvisState({ ...base, connection: "closed" })).toBe("error");
    expect(deriveJarvisState(base)).toBe("idle");
  });

  it("formats durations", () => {
    expect(formatDuration(340)).toBe("340 ms");
    expect(formatDuration(2345)).toBe("2,3 s");
    expect(formatDuration(42_000)).toBe("42 s");
    expect(formatDuration(125_000)).toBe("2 min 5 s");
    expect(formatDuration(null)).toBe("");
  });
});

describe("resync after a dropped connection", () => {
  const snap = (over: Partial<ActivitySnapshot>): ActivitySnapshot => ({ boot: "b1", seq: 0, complete: true, running: [], steps: [], tools: {}, agents: {}, ...over });

  it("merges missed steps and ends processes the core no longer runs", () => {
    const steps = turn();
    let s = fold(emptyLive(), steps.slice(0, 4)).s; // seq 1..4 seen live
    s = { ...s, sync: "offline" };
    expect(headline(s, 0).tone).toBe("bad");
    const missedTool = steps[4]!; // call:c1 started while offline …
    const end = step({ phase: "tool", state: "end", status: "ok", key: "call:c1", label: "Ergebnis von browser_open", tool: "browser_open", parent: "turn:s1", duration_ms: 900, nodes: ["tool:browser_open"] });
    const running = [{ ...steps[0]!, label: "Generiere Antwort" }];
    const after = applySnapshot(s, snap({ seq: end.seq, steps: [missedTool, end], running, tools: { browser_open: { calls: 1, errors: 0, avg_ms: 900, last_ms: 900, last_status: "ok", last_at: 1, running: 0 } } }), 5000);
    expect(after.sync).toBe("live");
    expect(after.seq).toBe(end.seq);
    expect(after.processes["call:c1"]).toMatchObject({ status: "ok" });
    expect(after.processes["turn:s1"]?.until).toBeUndefined();
    expect(after.tools.browser_open?.calls).toBe(1);
    expect(after.lines.filter((l) => l.replayed).map((l) => l.text)).toEqual(["Starte Browser", "Ergebnis von browser_open"]);
  });

  it("rebuilds from what runs now after a core restart (new boot id)", () => {
    const s = fold(emptyLive(), turn()).s;
    const running = [step({ phase: "agent", state: "start", status: "running", key: "coding:j2", label: "Coding-Agent arbeitet", agent: "coding", boot: "b2", seq: 3, nodes: ["agent:coding", "coding:j2"], t: Date.now() - 4000 })];
    const after = applySnapshot(s, snap({ boot: "b2", seq: 3, complete: false, running }), 10_000);
    expect(after.boot).toBe("b2");
    expect(Object.keys(after.processes)).toEqual(["coding:j2"]); // the old turn and tools are gone
    expect(after.processes["coding:j2"]!.since).toBeLessThan(10_000 - 3000); // runtime counts from its real start
    expect(after.nodes["tool:browser_open"]).toBeUndefined();
    expect(after.nodes["coding:j2"]?.status).toBe("active");
    expect(after.lines.length).toBe(s.lines.length); // the visible stream stays
  });
});

describe("effects queue", () => {
  it("staggers impulse pulses, decays glow and holds it while work runs", () => {
    pushEffects([{ kind: "impulse", path: ["agent:jarvis", "tool:x"], tone: "signal", count: 3 }, { kind: "glow", group: "tools", tone: "signal", strength: 1 }], 10);
    expect(fx.impulses.map((i) => i.born)).toEqual([10, 10.14, 10.28]);
    stepFx(10.5, 0.5);
    expect(fx.glow.tools).toBeLessThan(1);
    stepFx(20, 1);
    expect(fx.impulses).toHaveLength(0);
    const s = fold(emptyLive(), turn()).s;
    const held = heldGlow(s);
    expect(held.tools).toBeGreaterThan(0);
    expect(held.core).toBeGreaterThan(0);
    expect(held.memory).toBe(0); // a finished lookup does not keep glowing
    expect(heldGlow(emptyLive())).toEqual({ input: 0, core: 0, memory: 0, tools: 0, agents: 0, output: 0 });
  });

  it("ignores impulses without a path", () => {
    pushEffects([{ kind: "impulse", path: ["agent:jarvis"], tone: "signal", count: 2 }], 0);
    expect(fx.impulses).toHaveLength(0);
  });
});
