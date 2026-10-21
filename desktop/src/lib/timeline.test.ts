import { describe, expect, it } from "vitest";
import {
  addOptimisticUser,
  applyChatEvent,
  applyDeltas,
  buildRows,
  currentStep,
  emptyThread,
  hasModelReasoning,
  mergeServer,
  toolSpans,
  type AnswerRow,
  type ChatThread,
  type MessageData,
  type ProcessData,
  type TimelineEvent,
  type ToolData,
} from "./timeline";
import type { BusEvent } from "./types";

const SID = "s1";
let busId = 0;
const bus = (type: string, data: Record<string, unknown>): BusEvent => ({
  id: `b${(busId += 1)}`,
  type,
  ts: "2026-10-21T10:00:00Z",
  data: { session_id: SID, ...data },
});
const item = (id: string, seq: number, kind: TimelineEvent["kind"], data: Record<string, unknown>, turn = "t1"): TimelineEvent =>
  ({ id, session_id: SID, turn_id: turn, seq, ts: 1_700_000_000_000 + seq, kind, data }) as unknown as TimelineEvent;
const upsert = (ev: TimelineEvent) => bus("chat.event", { event: ev });
const fold = (t: ChatThread, ...events: BusEvent[]) => events.reduce(applyChatEvent, t);
const text = (t: ChatThread) =>
  t.events.map((e) => {
    const d = e.data as unknown as Record<string, string | undefined>;
    return `${e.kind}:${d.role ?? d.name ?? d.step ?? d.status}${d.text ? `=${d.text}` : ""}`;
  });

describe("chat timeline reducer", () => {
  it("streams text into the segment named by the delta and keeps tool calls between the segments", () => {
    let t = addOptimisticUser(emptyThread(SID), "Route nach Potsdam?");
    const optimisticKey = t.events[0]?.key;
    t = fold(
      t,
      upsert(item("u", 1, "message", { role: "user", text: "Route nach Potsdam?", status: "done" })),
      upsert(item("turn", 2, "turn", { status: "running" })),
      upsert(item("m1", 3, "message", { role: "assistant", text: "", status: "streaming" })),
      bus("chat.delta", { event_id: "m1", text: "Ich " }),
      bus("chat.delta", { event_id: "m1", text: "rechne." }),
      upsert(item("m1", 3, "message", { role: "assistant", text: "Ich rechne.", status: "done" })),
      upsert(item("tool1", 4, "tool", { name: "route_plan", status: "running", started_ts: 10 })),
      upsert(item("m2", 6, "message", { role: "assistant", text: "", status: "streaming" })),
      bus("chat.delta", { event_id: "m2", text: "35 km." }),
    );
    expect(text(t)).toEqual([
      "message:user=Route nach Potsdam?",
      "turn:running",
      "message:assistant=Ich rechne.",
      "tool:route_plan",
      "message:assistant=35 km.",
    ]);
    // the server copy replaced the optimistic message and kept its React key: no re-mount
    expect(t.events.filter((e) => (e.data as MessageData).role === "user")).toHaveLength(1);
    expect(t.events[0]?.key).toBe(optimisticKey);
    expect(t.running).toBe(true);
    t = fold(t, upsert(item("turn", 2, "turn", { status: "done" })), bus("chat.done", {}));
    expect(t.running).toBe(false);
  });

  it("orders by seq even when events arrive out of order", () => {
    const t = fold(
      emptyThread(SID),
      upsert(item("c", 3, "message", { role: "assistant", text: "C" })),
      upsert(item("a", 1, "message", { role: "user", text: "A" })),
      upsert(item("b", 2, "tool", { name: "b", status: "ok" })),
    );
    expect(t.events.map((e) => e.id)).toEqual(["a", "b", "c"]);
  });

  it("keeps parallel tool calls apart and finishes each by its own id", () => {
    let t = fold(
      emptyThread(SID),
      upsert(item("ta", 4, "tool", { name: "web_search", tool_use_id: "a", status: "running", started_ts: 1000 })),
      upsert(item("tb", 5, "tool", { name: "calendar_list", tool_use_id: "b", status: "running", started_ts: 1010 })),
    );
    // b finishes first
    t = fold(t, upsert(item("tb", 5, "tool", { name: "calendar_list", tool_use_id: "b", status: "ok", started_ts: 1010, ended_ts: 1300, duration_ms: 290 })));
    t = fold(t, upsert(item("ta", 4, "tool", { name: "web_search", tool_use_id: "a", status: "failed", started_ts: 1000, ended_ts: 2000, duration_ms: 1000, error: "Zeitüberschreitung" })));
    const tools = t.events as TimelineEvent<ToolData>[];
    expect(tools.map((e) => [e.id, e.data.status])).toEqual([
      ["ta", "failed"],
      ["tb", "ok"],
    ]);
    const spans = toolSpans(tools);
    expect(spans.get("ta")).toMatchObject({ offset: 0, width: 1, parallel: true });
    expect(spans.get("tb")?.offset).toBeCloseTo(0.01);
    expect(spans.get("tb")?.parallel).toBe(true);
    const sequential = toolSpans([
      item("x", 1, "tool", { name: "x", status: "ok", started_ts: 0, ended_ts: 100 }) as TimelineEvent<ToolData>,
      item("y", 2, "tool", { name: "y", status: "ok", started_ts: 100, ended_ts: 300 }) as TimelineEvent<ToolData>,
    ]);
    expect([sequential.get("x")?.parallel, sequential.get("y")?.parallel]).toEqual([false, false]);
  });

  it("restores the same chronology after a reload and does not let a stale snapshot shorten live text", () => {
    const live = fold(
      emptyThread(SID),
      upsert(item("m1", 3, "message", { role: "assistant", text: "", status: "streaming" })),
      bus("chat.delta", { event_id: "m1", text: "Schon weiter" }),
    );
    const server = [
      item("u", 1, "message", { role: "user", text: "Hallo" }),
      item("turn", 2, "turn", { status: "running" }),
      item("m1", 3, "message", { role: "assistant", text: "Scho", status: "streaming" }),
    ];
    const merged = mergeServer(live, server, false);
    expect(text(merged)).toEqual(["message:user=Hallo", "turn:running", "message:assistant=Schon weiter"]);
    expect(merged.running).toBe(true);
    // a fresh client (reload) gets the stored order
    const fresh = mergeServer(emptyThread(SID), server, true);
    expect(fresh.events.map((e) => e.seq)).toEqual([1, 2, 3]);
    expect(fresh.hasMore).toBe(true);
    // older page goes in front; a later refresh of the newest page keeps "more" from the older page
    const older = mergeServer(fresh, [item("old", -2, "message", { role: "user", text: "Alt" }, "legacy-1")], false, true);
    expect(older.events[0]?.id).toBe("old");
    expect(older.hasMore).toBe(false);
    expect(mergeServer(older, server, true).hasMore).toBe(false);
  });

  it("applies batched deltas and checklist toggles from other devices", () => {
    let t = fold(emptyThread(SID), upsert(item("m", 1, "message", { role: "assistant", text: "- [ ] a", status: "streaming" })));
    t = applyDeltas(t, new Map([["m", "\n- [ ] b"]]));
    expect((t.events[0]?.data as MessageData).text).toBe("- [ ] a\n- [ ] b");
    t = fold(t, bus("chat.check", { event_id: "m", index: 1, checked: true }));
    expect((t.events[0]?.data as MessageData).checks).toEqual({ "1": true });
    expect(applyChatEvent(t, { ...bus("chat.delta", { event_id: "m", text: "x" }), data: { session_id: "other", event_id: "m", text: "x" } })).toBe(t);
  });

  it("groups rows per turn: user, answer (text + tools + process), system note", () => {
    const events = [
      item("u", 1, "message", { role: "user", text: "Hi" }),
      item("turn", 2, "turn", { status: "done", reasoning: false }),
      item("p1", 3, "process", { step: "model", source: "protocol", title: "Modell: Sonnet" }),
      item("m1", 4, "message", { role: "assistant", text: "A" }),
      item("t1", 5, "tool", { name: "x", status: "ok" }),
      item("p2", 6, "process", { step: "tool", source: "protocol", title: "Werkzeug: x", ref: "t1" }),
      item("m2", 7, "message", { role: "assistant", text: "B" }),
      item("s", 8, "message", { role: "system", text: "Antwort wurde unterbrochen.", level: "warning" }),
      item("p3", 9, "process", { step: "summary", source: "summary", title: "Ablauf" }),
      item("u2", 10, "message", { role: "user", text: "Danke" }, "t2"),
    ];
    const rows = buildRows(events);
    expect(rows.map((r) => r.type)).toEqual(["user", "answer", "system", "user"]);
    const answer = rows[1] as AnswerRow;
    expect(answer.items.map((i) => i.id)).toEqual(["m1", "t1", "m2"]);
    expect(answer.process.map((p) => p.id)).toEqual(["p1", "p2", "p3"]);
    expect(answer.turn?.id).toBe("turn");
    expect(currentStep(answer.process)?.id).toBe("p2");
    expect(hasModelReasoning(answer.process)).toBe(false);
    expect(hasModelReasoning([item("r", 1, "process", { step: "reasoning", source: "model", title: "", text: "…" }) as TimelineEvent<ProcessData>])).toBe(true);
  });
});
