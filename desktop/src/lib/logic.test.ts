import { describe, expect, it } from "vitest";
import { buildUrl, describeErrorBody } from "./api";
import { describeCron, validateCron } from "./cron";
import { formatBytes, formatDuration, healthTone } from "./format";
import { backoffDelay, buildWsUrl, parseFrame } from "./realtime";
import { addOptimisticUser, applyChatEvent, emptyThread, filterLogs, pushBounded } from "./reducers";
import type { BusEvent } from "./types";

const ev = (type: string, data: Record<string, unknown>, id = Math.random().toString(36)): BusEvent =>
  ({ id, type, ts: "2026-10-15T10:00:00Z", data }) as BusEvent;

describe("chat reducer", () => {
  it("streams deltas into one assistant message and finishes on chat.done", () => {
    let t = addOptimisticUser(emptyThread("s1"), "Hallo");
    t = applyChatEvent(t, ev("chat.user", { session_id: "s1", text: "Hallo" }));
    expect(t.messages).toHaveLength(1);
    expect(t.messages[0]?.pending).toBe(false);
    t = applyChatEvent(t, ev("chat.delta", { session_id: "s1", text: "Guten " }));
    t = applyChatEvent(t, ev("chat.delta", { session_id: "s1", text: "Tag, Sir." }));
    t = applyChatEvent(t, ev("chat.done", { session_id: "s1", text: "Guten Tag, Sir." }));
    const last = t.messages[t.messages.length - 1];
    expect(last?.role).toBe("assistant");
    expect(last?.text).toBe("Guten Tag, Sir.");
    expect(last?.streaming).toBe(false);
    expect(t.running).toBe(false);
  });

  it("ignores events of other sessions", () => {
    const t = emptyThread("s1");
    expect(applyChatEvent(t, ev("chat.delta", { session_id: "other", text: "x" }))).toBe(t);
  });

  it("records tool chips", () => {
    let t = emptyThread("s1");
    t = applyChatEvent(t, ev("chat.tool_started", { session_id: "s1", data: { name: "mac_open_app" } }));
    expect(t.messages[0]?.tools[0]?.name).toBe("mac_open_app");
  });
});

describe("cron helpers", () => {
  it("validates and describes", () => {
    expect(validateCron("0 8 * * 1-5")).toBeNull();
    expect(validateCron("bad")).not.toBeNull();
    expect(describeCron("0 8 * * 1-5")).toContain("werktags");
    expect(describeCron("30 7 * * *")).toBe("täglich um 07:30 Uhr");
  });
});

describe("formatting", () => {
  it("formats bytes and durations", () => {
    expect(formatBytes(1536)).toMatch(/1[,.]5 KB/);
    expect(formatDuration(3725)).toMatch(/1 ?h/);
    expect(healthTone("ok")).toBe("ok");
    expect(healthTone("not_configured")).not.toBe("ok");
  });
});

describe("api + realtime utils", () => {
  it("builds urls without undefined params", () => {
    expect(buildUrl("http://h:1", "/tasks", { status: undefined, limit: 5 })).toBe("http://h:1/api/tasks?limit=5");
  });
  it("describes error bodies", () => {
    expect(describeErrorBody(403, { detail: "owner only" })).toContain("owner only");
  });
  it("builds ws url with token and log flag", () => {
    const u = new URL(buildWsUrl("http://127.0.0.1:8765", "jdv_x", true));
    expect(u.protocol).toBe("ws:");
    expect(u.searchParams.get("token")).toBe("jdv_x");
    expect(u.searchParams.get("logs")).toBe("1");
  });
  it("bounds backoff", () => {
    expect(backoffDelay(0, 500, 15000, () => 0.5)).toBeLessThanOrEqual(15000);
    expect(backoffDelay(20, 500, 15000, () => 1)).toBeLessThanOrEqual(15000);
  });
  it("parses frames safely", () => {
    expect(() => parseFrame("not json")).not.toThrow();
  });
});

describe("logs", () => {
  it("bounds buffers and filters by level", () => {
    expect(pushBounded([1, 2, 3], [4, 5], 3)).toEqual([3, 4, 5]);
    const logs = [
      { ts: "1", level: "INFO", logger: "a", msg: "start" },
      { ts: "2", level: "ERROR", logger: "b", msg: "boom" },
    ];
    expect(filterLogs(logs, "WARNING", "")).toHaveLength(1);
    expect(filterLogs(logs, "DEBUG", "sta")).toHaveLength(1);
  });
});
