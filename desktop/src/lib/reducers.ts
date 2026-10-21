// Pure reducers that fold bus events into UI state. No React, no IO: easy to unit-test.
// The chat itself lives in ./timeline.ts.
import type { BusEvent, LogEntry, Task, VoiceState } from "./types";

// ------------------------------------------------------------------ live tool calls (agent monitor)

export interface LiveToolCall {
  key: string;
  tool: string;
  risk?: string;
  actor?: string;
  sessionId?: string | null;
  status: "running" | "ok" | "error" | string;
  startedAt: string;
  durationMs?: number;
  message?: string;
}

/** Folds `tool.started` / `tool.finished` into a newest-first list capped at `max`. */
export function applyToolEvent(list: LiveToolCall[], ev: BusEvent, max = 200): LiveToolCall[] {
  const d = ev.data as Record<string, unknown>;
  const tool = String(d.tool ?? "?");
  if (ev.type === "tool.started") {
    const entry: LiveToolCall = {
      key: ev.id,
      tool,
      risk: typeof d.risk === "string" ? d.risk : undefined,
      actor: typeof d.actor === "string" ? d.actor : undefined,
      sessionId: (d.session_id as string | null | undefined) ?? null,
      status: "running",
      startedAt: ev.ts,
    };
    return [entry, ...list].slice(0, max);
  }
  if (ev.type === "tool.finished") {
    const idx = list.findIndex((c) => c.tool === tool && c.status === "running" && c.actor === d.actor);
    const patch = {
      status: String(d.status ?? "ok"),
      durationMs: typeof d.duration_ms === "number" ? d.duration_ms : undefined,
      message: typeof d.message === "string" && d.message ? d.message : undefined,
    };
    if (idx < 0) {
      return [{ key: ev.id, tool, actor: d.actor as string | undefined, startedAt: ev.ts, ...patch }, ...list].slice(0, max);
    }
    const next = list.slice();
    const cur = next[idx];
    if (cur) next[idx] = { ...cur, ...patch };
    return next;
  }
  return list;
}

// ------------------------------------------------------------------ tasks

/** Applies `task.updated` / `task.step` to a cached task list. Unknown ids signal a refetch. */
export function applyTaskEvent(tasks: Task[], ev: BusEvent): { tasks: Task[]; unknown: boolean } {
  const d = ev.data as Record<string, unknown>;
  const tid = typeof d.task_id === "string" ? d.task_id : null;
  if (!tid) return { tasks, unknown: false };
  const idx = tasks.findIndex((t) => t.id === tid);
  if (idx < 0) return { tasks, unknown: ev.type.startsWith("task.") };
  const cur = tasks[idx] as Task;
  let next: Task;
  if (ev.type === "task.updated") {
    next = {
      ...cur,
      status: typeof d.status === "string" ? d.status : cur.status,
      title: typeof d.title === "string" && d.title ? d.title : cur.title,
      updated_at: ev.ts,
    };
  } else if (ev.type === "task.step") {
    const stepIdx = typeof d.step === "number" ? d.step : -1;
    next = {
      ...cur,
      updated_at: ev.ts,
      steps: cur.steps.map((s) =>
        s.idx === stepIdx
          ? {
              ...s,
              status: typeof d.status === "string" ? d.status : s.status,
              started_at: d.status === "running" ? ev.ts : s.started_at,
              finished_at: d.status === "completed" || d.status === "skipped" ? ev.ts : s.finished_at,
              error: typeof d.note === "string" ? d.note : s.error,
            }
          : s,
      ),
    };
  } else if (ev.type === "task.input_required") {
    next = { ...cur, status: "waiting_input", question: typeof d.question === "string" ? d.question : cur.question };
  } else {
    return { tasks, unknown: false };
  }
  const out = tasks.slice();
  out[idx] = next;
  return { tasks: out, unknown: false };
}

// ------------------------------------------------------------------ logs

export const LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] as const;
export type LogLevel = (typeof LOG_LEVELS)[number];

export function levelRank(level: string): number {
  const i = LOG_LEVELS.indexOf(level.toUpperCase() as LogLevel);
  return i < 0 ? 1 : i;
}

/** Appends to a bounded buffer (oldest dropped). */
export function pushBounded<T>(buf: T[], items: T | T[], max: number): T[] {
  const add = Array.isArray(items) ? items : [items];
  const next = buf.concat(add);
  return next.length > max ? next.slice(next.length - max) : next;
}

/** Deduplicates REST backlog against live entries by (ts, logger, msg). */
export function mergeLogs(backlog: LogEntry[], live: LogEntry[], max: number): LogEntry[] {
  const seen = new Set<string>();
  const out: LogEntry[] = [];
  for (const e of [...backlog, ...live]) {
    const k = `${e.ts}|${e.logger}|${e.msg}`;
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(e);
  }
  out.sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0));
  return out.length > max ? out.slice(out.length - max) : out;
}

export function filterLogs(entries: LogEntry[], minLevel: string, query: string): LogEntry[] {
  const min = levelRank(minLevel);
  const q = query.trim().toLowerCase();
  return entries.filter(
    (e) => levelRank(e.level) >= min && (!q || e.msg.toLowerCase().includes(q) || e.logger.toLowerCase().includes(q)),
  );
}

// ------------------------------------------------------------------ voice

export function applyVoiceStatus(prev: VoiceState, ev: BusEvent): VoiceState {
  if (ev.type !== "voice.status") return prev;
  const d = ev.data as VoiceState;
  const level = typeof d.level === "number" ? Math.max(0, Math.min(1, d.level)) : prev.level;
  return { ...prev, ...d, level, connected: true, updated_at: ev.ts };
}
