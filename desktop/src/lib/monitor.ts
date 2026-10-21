// Agent monitor model: turns job snapshots, live tool events and pending approvals into one chronological
// timeline that can be filtered and grouped. Pure functions, no React: easy to unit-test.
import type { Tone } from "./format";
import type { LiveToolCall } from "./reducers";
import type { Approval, JobMonitor } from "./types";

/** Normalised state of one timeline entry. Every state has its own label and icon, never only a color. */
export type EntryStatus = "running" | "waiting" | "paused" | "queued" | "ok" | "error" | "aborted" | "note";

export const ENTRY_STATUS: Record<EntryStatus, { label: string; tone: Tone }> = {
  running: { label: "Läuft", tone: "info" },
  waiting: { label: "Wartet auf Freigabe", tone: "warn" },
  paused: { label: "Pausiert", tone: "warn" },
  queued: { label: "Wartet", tone: "idle" },
  ok: { label: "Erledigt", tone: "ok" },
  error: { label: "Fehler", tone: "bad" },
  aborted: { label: "Abgebrochen", tone: "idle" },
  note: { label: "Nachricht", tone: "idle" },
};

/** Maps the status strings of steps, tool events, tasks and jobs onto the monitor states. */
export function entryStatus(s: string | null | undefined): EntryStatus {
  switch (s) {
    case "running":
    case "in_progress":
      return "running";
    case "waiting_approval":
    case "approval_required":
    case "pending":
      return "waiting";
    case "paused":
    case "waiting_input":
    case "waiting_external":
      return "paused";
    case "queued":
      return "queued";
    case "ok":
    case "completed":
    case "executed":
    case "duplicate":
      return "ok";
    case "aborted":
    case "cancelled":
    case "skipped":
      return "aborted";
    default:
      return "error";
  }
}

export interface MonitorEntry {
  id: string;
  kind: "tool" | "message" | "approval";
  /** ISO time the entry started (null: unknown) */
  at: string | null;
  /** tool name; for messages/approvals a short label */
  tool: string;
  /** one-line description (already shortened by the core, the UI truncates further) */
  summary: string;
  status: EntryStatus;
  durationS: number | null;
  /** full parameters / result for the expandable details */
  params?: string;
  result?: string;
  risk?: string;
  /** conversation or actor the call belongs to (live tool calls) */
  scope?: string;
}

const byTime = (a: MonitorEntry, b: MonitorEntry) => {
  const ta = a.at ? Date.parse(a.at) : Number.NaN;
  const tb = b.at ? Date.parse(b.at) : Number.NaN;
  if (Number.isNaN(ta) || Number.isNaN(tb)) return 0; // keep the given order for unknown times
  return ta - tb;
};

/** Oldest-first timeline of one coding job: its tool calls plus the messages Sir sent to the agent. */
export function jobEntries(m: JobMonitor): MonitorEntry[] {
  const steps: MonitorEntry[] = m.steps.map((s) => ({
    id: s.id,
    kind: "tool",
    at: s.started_at,
    tool: s.tool,
    summary: s.detail,
    status: entryStatus(s.status),
    durationS: s.duration_s,
    params: s.detail || undefined,
    result: s.result || undefined,
  }));
  const notes: MonitorEntry[] = m.messages.map((x, i) => ({
    id: `msg-${i}-${x.ts ?? ""}`,
    kind: "message",
    at: x.ts,
    tool: x.immediate ? "Nachricht (sofort)" : "Nachricht",
    summary: x.text,
    status: "note",
    durationS: null,
    params: x.text,
  }));
  // stable sort: entries with the same second keep the log order
  return [...steps, ...notes].sort(byTime);
}

/** Oldest-first timeline of the tool calls in conversations, with pending approvals as waiting entries. */
export function liveEntries(calls: LiveToolCall[], approvals: Approval[] = []): MonitorEntry[] {
  const tools: MonitorEntry[] = [...calls].reverse().map((c) => ({
    id: c.key,
    kind: "tool",
    at: c.startedAt,
    tool: c.tool,
    summary: c.message ?? "",
    status: entryStatus(c.status),
    durationS: c.durationMs != null ? c.durationMs / 1000 : null,
    result: c.message,
    risk: c.risk,
    scope: c.actor,
  }));
  const waiting: MonitorEntry[] = approvals
    .filter((a) => a.status === "pending")
    .map((a) => ({
      id: `approval-${a.id}`,
      kind: "approval",
      at: a.created_at,
      tool: a.tool,
      summary: a.summary,
      status: "waiting",
      durationS: null,
      params: Object.keys(a.args ?? {}).length ? JSON.stringify(a.args, null, 2) : undefined,
      result: a.reason || undefined,
      risk: a.risk,
      scope: a.requested_by,
    }));
  return [...tools, ...waiting].sort(byTime);
}

// ------------------------------------------------------------------ filter

export type StatusFilter = "all" | "active" | "error";

export interface MonitorFilter {
  status: StatusFilter;
  /** only this tool (null: every tool) */
  tool: string | null;
}

export const NO_FILTER: MonitorFilter = { status: "all", tool: null };

const ACTIVE: EntryStatus[] = ["running", "waiting", "paused", "queued"];

function matchesStatus(e: MonitorEntry, f: StatusFilter): boolean {
  if (f === "error") return e.status === "error";
  if (f === "active") return ACTIVE.includes(e.status);
  return true;
}

export function filterEntries(entries: MonitorEntry[], f: MonitorFilter): MonitorEntry[] {
  return entries.filter((e) => matchesStatus(e, f.status) && (f.tool == null || (e.kind !== "message" && e.tool === f.tool)));
}

/** Counts per status tab, within the selected tool. */
export function statusCounts(entries: MonitorEntry[], tool: string | null = null): Record<StatusFilter, number> {
  const scoped = filterEntries(entries, { status: "all", tool });
  return {
    all: scoped.length,
    active: scoped.filter((e) => matchesStatus(e, "active")).length,
    error: scoped.filter((e) => e.status === "error").length,
  };
}

/** Tools that occur in the timeline, most used first (ties alphabetical). */
export function toolOptions(entries: MonitorEntry[]): { tool: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const e of entries) if (e.kind !== "message") counts.set(e.tool, (counts.get(e.tool) ?? 0) + 1);
  return [...counts]
    .map(([tool, count]) => ({ tool, count }))
    .sort((a, b) => b.count - a.count || a.tool.localeCompare(b.tool));
}

// ------------------------------------------------------------------ grouping

export interface EntryGroup {
  id: string;
  tool: string;
  entries: MonitorEntry[];
  /** most important state inside the group (error > waiting > running > …) */
  status: EntryStatus;
  at: string | null;
  durationS: number | null;
}

export type TimelineItem = { type: "entry"; entry: MonitorEntry } | { type: "group"; group: EntryGroup };

const SEVERITY: EntryStatus[] = ["error", "waiting", "running", "paused", "queued", "aborted", "ok", "note"];

export function worstStatus(statuses: EntryStatus[]): EntryStatus {
  return SEVERITY.find((s) => statuses.includes(s)) ?? "ok";
}

/**
 * Folds runs of consecutive calls of the same tool in the same scope ("Read" ×6) into one group, so a long
 * job reads as a handful of phases instead of hundreds of rows. Runs shorter than `min` stay single rows.
 */
export function groupEntries(entries: MonitorEntry[], min = 3): TimelineItem[] {
  const out: TimelineItem[] = [];
  let run: MonitorEntry[] = [];
  const flush = () => {
    if (run.length >= min) {
      const first = run[0] as MonitorEntry;
      const durations = run.map((e) => e.durationS).filter((d): d is number => d != null);
      out.push({
        type: "group",
        group: {
          id: `group-${first.id}`,
          tool: first.tool,
          entries: run,
          status: worstStatus(run.map((e) => e.status)),
          at: first.at,
          durationS: durations.length ? durations.reduce((a, b) => a + b, 0) : null,
        },
      });
    } else for (const entry of run) out.push({ type: "entry", entry });
    run = [];
  };
  for (const e of entries) {
    const prev = run[run.length - 1];
    const joins = prev && e.kind === "tool" && prev.kind === "tool" && prev.tool === e.tool && prev.scope === e.scope;
    if (!joins) flush();
    run.push(e);
  }
  flush();
  return out;
}

// ------------------------------------------------------------------ headline

export interface JobPhase {
  status: EntryStatus;
  /** "Läuft", "Denkt nach", "Fehlgeschlagen" … */
  title: string;
  /** the tool running right now */
  tool: string | null;
  /** what it does (command, file …) */
  detail: string;
  /** ISO start of the current step (live counter) */
  since: string | null;
}

/** The one line that answers "what is the agent doing right now?". */
export function jobPhase(m: JobMonitor): JobPhase {
  const c = m.current;
  const step = m.running_tools[m.running_tools.length - 1];
  switch (c.kind) {
    case "tool":
      return { status: "running", title: "Läuft", tool: c.tool ?? step?.tool ?? null, detail: step?.detail ?? c.label, since: c.since ?? null };
    case "thinking":
      return { status: "running", title: "Denkt nach", tool: null, detail: "", since: c.since ?? null };
    case "waiting":
      return { status: "queued", title: "Wartet auf Start", tool: null, detail: "", since: null };
    case "paused":
      return { status: "paused", title: "Pausiert", tool: c.tool ?? null, detail: step?.detail ?? "", since: null };
    default: {
      const status = entryStatus(m.status);
      return { status, title: c.label || ENTRY_STATUS[status].label, tool: null, detail: "", since: null };
    }
  }
}
