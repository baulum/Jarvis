// Agent monitor model: status mapping, chronological timeline, filters, grouping, headline.
import { describe, expect, it } from "vitest";
import {
  entryStatus,
  filterEntries,
  groupEntries,
  jobEntries,
  jobPhase,
  liveEntries,
  statusCounts,
  toolOptions,
  worstStatus,
  type MonitorEntry,
} from "./monitor";
import type { LiveToolCall } from "./reducers";
import type { Approval, JobMonitor, MonitorStep } from "./types";

const at = (s: number) => new Date(Date.UTC(2026, 0, 1, 12, 0, s)).toISOString();

const step = (id: string, tool: string, status: MonitorStep["status"], s: number, extra: Partial<MonitorStep> = {}): MonitorStep => ({
  id,
  tool,
  detail: `${tool.toLowerCase()} ${id}`,
  status,
  started_at: at(s),
  finished_at: null,
  duration_s: status === "running" ? null : 1,
  result: status === "error" ? "boom" : "ok",
  ...extra,
});

const monitor = (over: Partial<JobMonitor> = {}): JobMonitor => ({
  job_id: "j1",
  title: "T",
  status: "running",
  model: null,
  effort: null,
  branch: null,
  created_at: at(0),
  finished_at: null,
  elapsed_s: 10,
  snapshot_at: at(10),
  current: { kind: "thinking", label: "Denkt nach …" },
  steps: [],
  step_count: 0,
  running_tools: [],
  last_text: "",
  messages: [],
  pending_messages: 0,
  usage: { input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_creation_tokens: 0, total_tokens: 0, cost_usd: null, cost_final: false },
  error: null,
  can_pause: true,
  can_resume: false,
  can_cancel: true,
  can_message: true,
  ...over,
});

const entry = (id: string, tool: string, status: MonitorEntry["status"], kind: MonitorEntry["kind"] = "tool", scope?: string): MonitorEntry => ({
  id,
  kind,
  at: null,
  tool,
  summary: "",
  status,
  durationS: 1,
  scope,
});

describe("entryStatus", () => {
  it("maps step, tool, task and job states; unknown failures count as errors", () => {
    expect(entryStatus("running")).toBe("running");
    expect(entryStatus("in_progress")).toBe("running");
    expect(entryStatus("waiting_approval")).toBe("waiting");
    expect(entryStatus("approval_required")).toBe("waiting");
    expect(entryStatus("paused")).toBe("paused");
    expect(entryStatus("waiting_input")).toBe("paused");
    expect(entryStatus("queued")).toBe("queued");
    expect(entryStatus("completed")).toBe("ok");
    expect(entryStatus("duplicate")).toBe("ok");
    expect(entryStatus("cancelled")).toBe("aborted");
    expect(entryStatus("failed")).toBe("error");
    expect(entryStatus("denied")).toBe("error");
  });
});

describe("jobEntries", () => {
  it("merges steps and messages oldest first and keeps parameters and results for the details", () => {
    const m = monitor({
      steps: [step("a", "Read", "ok", 1), step("b", "Bash", "error", 5)],
      messages: [{ text: "Bitte Tests", ts: at(3), immediate: true }],
    });
    const out = jobEntries(m);
    expect(out.map((e) => e.id)).toEqual(["a", expect.stringMatching(/^msg-/), "b"]);
    expect(out[1]).toMatchObject({ kind: "message", status: "note", tool: "Nachricht (sofort)", summary: "Bitte Tests" });
    expect(out[2]).toMatchObject({ status: "error", params: "bash b", result: "boom" });
  });
});

describe("liveEntries", () => {
  it("turns the newest-first live list into a timeline and adds pending approvals as waiting", () => {
    const calls: LiveToolCall[] = [
      { key: "2", tool: "mail.send", status: "error", startedAt: at(4), durationMs: 1500, message: "SMTP down", actor: "chat" },
      { key: "1", tool: "web.search", status: "ok", startedAt: at(1), durationMs: 200, actor: "chat" },
    ];
    const approvals = [
      { id: "ap1", tool: "files.delete", summary: "Löscht x", risk: "high", reason: "schreibt", status: "pending", args: { path: "x" }, requested_by: "chat", created_at: at(2) },
      { id: "ap2", tool: "old", summary: "", risk: "low", reason: "", status: "approved", args: {}, requested_by: "chat", created_at: at(0) },
    ] as unknown as Approval[];
    const out = liveEntries(calls, approvals);
    expect(out.map((e) => e.id)).toEqual(["1", "approval-ap1", "2"]);
    expect(out[0]?.durationS).toBe(0.2);
    expect(out[1]).toMatchObject({ kind: "approval", status: "waiting", risk: "high" });
    expect(out[1]?.params).toContain('"path": "x"');
    expect(out[2]).toMatchObject({ status: "error", summary: "SMTP down" });
  });
});

describe("filter", () => {
  const list = [
    entry("1", "Read", "ok"),
    entry("2", "Bash", "error"),
    entry("3", "Nachricht", "note", "message"),
    entry("4", "Bash", "running"),
    entry("5", "Edit", "waiting"),
  ];

  it("filters by status and by tool; messages only appear without a tool filter", () => {
    expect(filterEntries(list, { status: "error", tool: null }).map((e) => e.id)).toEqual(["2"]);
    expect(filterEntries(list, { status: "active", tool: null }).map((e) => e.id)).toEqual(["4", "5"]);
    expect(filterEntries(list, { status: "all", tool: "Bash" }).map((e) => e.id)).toEqual(["2", "4"]);
    expect(filterEntries(list, { status: "all", tool: "Nachricht" })).toEqual([]);
  });

  it("counts per tab within the chosen tool and lists tools by use", () => {
    expect(statusCounts(list)).toEqual({ all: 5, active: 2, error: 1 });
    expect(statusCounts(list, "Bash")).toEqual({ all: 2, active: 1, error: 1 });
    expect(toolOptions(list)).toEqual([
      { tool: "Bash", count: 2 },
      { tool: "Edit", count: 1 },
      { tool: "Read", count: 1 },
    ]);
  });
});

describe("groupEntries", () => {
  it("folds runs of the same tool and scope, keeps short runs and messages as rows", () => {
    const list = [
      entry("r1", "Read", "ok"),
      entry("r2", "Read", "error"),
      entry("r3", "Read", "ok"),
      entry("b1", "Bash", "ok"),
      entry("b2", "Bash", "ok"),
      entry("m", "Nachricht", "note", "message"),
      entry("g1", "Grep", "ok", "tool", "a"),
      entry("g2", "Grep", "ok", "tool", "b"),
      entry("g3", "Grep", "ok", "tool", "b"),
    ];
    const items = groupEntries(list);
    expect(items.map((i) => (i.type === "group" ? `G:${i.group.entries.length}` : i.entry.id))).toEqual([
      "G:3",
      "b1",
      "b2",
      "m",
      "g1",
      "g2",
      "g3",
    ]);
    const g = items[0];
    expect(g?.type === "group" && g.group).toMatchObject({ tool: "Read", status: "error", durationS: 3 });
    expect(groupEntries(list, 2).filter((i) => i.type === "group")).toHaveLength(3);
  });

  it("picks the most important state of a group", () => {
    expect(worstStatus(["ok", "running", "aborted"])).toBe("running");
    expect(worstStatus(["ok", "waiting", "error"])).toBe("error");
    expect(worstStatus([])).toBe("ok");
  });
});

describe("jobPhase", () => {
  it("names the running tool and what it does", () => {
    const running = step("b", "Bash", "running", 3, { detail: "pytest -q" });
    const p = jobPhase(monitor({ current: { kind: "tool", label: "Bash: pytest -q", tool: "Bash", since: at(3) }, running_tools: [running] }));
    expect(p).toEqual({ status: "running", title: "Läuft", tool: "Bash", detail: "pytest -q", since: at(3) });
  });

  it("reports thinking, queued, paused and the final result", () => {
    expect(jobPhase(monitor()).title).toBe("Denkt nach");
    expect(jobPhase(monitor({ status: "queued", current: { kind: "waiting", label: "Wartet auf Start" } })).status).toBe("queued");
    expect(jobPhase(monitor({ status: "paused", current: { kind: "paused", label: "Pausiert", tool: "Bash" } }))).toMatchObject({ status: "paused", tool: "Bash" });
    expect(jobPhase(monitor({ status: "failed", current: { kind: "done", label: "Fehlgeschlagen" } }))).toMatchObject({ status: "error", title: "Fehlgeschlagen" });
    expect(jobPhase(monitor({ status: "completed", current: { kind: "done", label: "Abgeschlossen" } })).status).toBe("ok");
  });
});
