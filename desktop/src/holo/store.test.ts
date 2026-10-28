// Live brain store: steps from the socket, sequence gaps, connection loss and automatic resynchronisation.
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { BusEvent } from "../lib/types";
import { fx, resetFx } from "./graph/fx";
import { emptyLive, type ActivitySnapshot, type ActivityStep } from "./graph/live";
import { nodeVisible, setSnapshotLoader, useBrain } from "./store";

const step = (seq: number, over: Partial<ActivityStep> = {}): ActivityStep => ({
  phase: "tool",
  state: "start",
  status: "running",
  key: `call:c${seq}`,
  label: "Starte Browser",
  tool: "browser_open",
  parent: "turn:s1",
  nodes: ["tool:browser_open"],
  t: Date.now(),
  seq,
  boot: "b1",
  ...over,
});
const event = (s: ActivityStep): BusEvent => {
  const { nodes, ...data } = s;
  return { id: `e${s.seq}`, type: "activity.step", ts: "", nodes, data: data as unknown as Record<string, unknown> };
};
const snapshot = (over: Partial<ActivitySnapshot> = {}): ActivitySnapshot => ({ boot: "b1", seq: 0, complete: true, running: [], steps: [], tools: {}, agents: {}, ...over });

beforeEach(() => {
  resetFx();
  setSnapshotLoader(null);
  useBrain.setState({ live: emptyLive(), view: "brain", hidden: new Set(), hiddenClasses: new Set(), highlight: null });
});

describe("live steps from the socket", () => {
  it("applies contiguous steps and queues their effects", () => {
    useBrain.getState().onEvent(event(step(1)));
    useBrain.getState().onEvent(event(step(2)));
    const live = useBrain.getState().live;
    expect(live.seq).toBe(2);
    expect(Object.keys(live.processes)).toEqual(["call:c1", "call:c2"]);
    expect(fx.impulses.length).toBeGreaterThan(0);
  });

  it("fetches the missing steps on a sequence gap instead of guessing", async () => {
    const all = [step(1), step(2), step(3), step(4)];
    const load = vi.fn(async (since: number) => snapshot({ seq: 4, steps: all.filter((s) => s.seq > since), running: all }));
    setSnapshotLoader(load);
    useBrain.getState().onEvent(event(step(1)));
    useBrain.getState().onEvent(event(step(4))); // 2 and 3 were lost
    await vi.waitFor(() => expect(useBrain.getState().live.seq).toBe(4));
    expect(load).toHaveBeenCalledWith(1, "b1");
    expect(Object.keys(useBrain.getState().live.processes).sort()).toEqual(["call:c1", "call:c2", "call:c3", "call:c4"]);
  });

  it("pauses while disconnected and resynchronises on reconnect", async () => {
    const load = vi.fn(async () =>
      snapshot({ seq: 3, steps: [step(2), step(3, { state: "end", status: "ok", key: "call:c1", label: "Ergebnis von browser_open", duration_ms: 800 })], running: [step(2)] }),
    );
    setSnapshotLoader(load);
    useBrain.getState().onEvent(event(step(1)));
    useBrain.getState().setConnection("reconnecting");
    expect(useBrain.getState().live.sync).toBe("offline");
    useBrain.getState().setConnection("open");
    await vi.waitFor(() => expect(useBrain.getState().live.sync).toBe("live"));
    const live = useBrain.getState().live;
    expect(live.processes["call:c1"]?.status).toBe("ok");
    expect(live.processes["call:c2"]?.until).toBeUndefined();
    expect(live.lines.filter((l) => l.replayed)).toHaveLength(2);
  });

  it("stays usable when the snapshot endpoint fails (older core): steps are applied as they come", async () => {
    const load = vi.fn(async () => Promise.reject(new Error("404")));
    setSnapshotLoader(load);
    useBrain.getState().onEvent(event(step(1)));
    useBrain.getState().setConnection("open");
    await new Promise((r) => setTimeout(r, 0));
    expect(load).toHaveBeenCalledOnce();
    expect(useBrain.getState().live.sync).toBe("live");
    useBrain.getState().onEvent(event(step(5))); // a gap, but no way to fetch it
    expect(useBrain.getState().live.seq).toBe(5);
  });

  it("ignores unrelated events and keeps voice levels", () => {
    useBrain.getState().onEvent({ id: "x", type: "tool.started", ts: "", data: { tool: "x" } });
    expect(useBrain.getState().live.rev).toBe(0);
    useBrain.getState().onEvent({ id: "y", type: "voice.level", ts: "", data: { level: 0.4, output: 0.1 } });
    expect(useBrain.getState().level.input).toBe(0.4);
  });
});

describe("visibility", () => {
  const mem = { id: "memory:a", kind: "technical", category: "memory" as const };
  const tool = { id: "tool:x", kind: "tool", category: "tool" as const };
  it("filters by class, category, view and explorer matches", () => {
    const b = { hidden: new Set(), hiddenClasses: new Set(), view: "brain", highlight: null, selected: null } as unknown as Parameters<typeof nodeVisible>[0];
    expect(nodeVisible(b, tool)).toBe(true);
    expect(nodeVisible({ ...b, hiddenClasses: new Set(["tools"]) }, tool)).toBe(false);
    expect(nodeVisible({ ...b, view: "explorer" }, tool)).toBe(false); // the explorer shows memories only
    expect(nodeVisible({ ...b, view: "explorer", highlight: new Set(["memory:b"]) }, mem)).toBe(false);
    expect(nodeVisible({ ...b, view: "explorer", highlight: new Set(["memory:a"]) }, mem)).toBe(true);
    expect(nodeVisible({ ...b, hidden: new Set(["memory"]), selected: "memory:a" }, mem)).toBe(true); // selection stays
  });
});
