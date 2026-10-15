// Pure reducers that fold bus events into UI state. No React, no IO: easy to unit-test.
import type { BusEvent, ChatMessageRow, LogEntry, Task, VoiceState } from "./types";

// ------------------------------------------------------------------ chat

export type ToolChipStatus = "running" | "ok" | "failed";

export interface ToolChip {
  key: string;
  name: string;
  status: ToolChipStatus;
  args?: string;
  preview?: string;
  error?: string;
  durationMs?: number;
}

export interface UiMessage {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  tools: ToolChip[];
  streaming: boolean;
  error?: string | null;
  interrupted?: boolean;
  /** Optimistic user message not yet echoed by the core. */
  pending?: boolean;
  ts: string;
}

export interface ChatThread {
  sessionId: string;
  messages: UiMessage[];
  running: boolean;
}

let localSeq = 0;
const localId = (prefix: string) => `${prefix}-${Date.now().toString(36)}-${(localSeq += 1)}`;

export function emptyThread(sessionId: string): ChatThread {
  return { sessionId, messages: [], running: false };
}

/** Converts stored history rows into UI messages (tool chips from meta.tools). */
export function threadFromHistory(sessionId: string, rows: ChatMessageRow[]): ChatThread {
  const messages: UiMessage[] = [];
  for (const r of rows) {
    if (r.role !== "user" && r.role !== "assistant") continue;
    const chips: ToolChip[] = [];
    for (const t of r.meta?.tools ?? []) {
      if (!t.name) continue;
      if (t.event === "tool_started") {
        chips.push({ key: `${t.name}-${chips.length}`, name: t.name, status: "ok" });
      } else if (t.event === "tool_failed") {
        const last = [...chips].reverse().find((c) => c.name === t.name);
        if (last) last.status = "failed";
        else chips.push({ key: `${t.name}-${chips.length}`, name: t.name, status: "failed" });
      }
    }
    messages.push({
      id: r.id,
      role: r.role,
      text: r.content,
      tools: chips,
      streaming: false,
      error: (r.meta?.error as string | null | undefined) ?? null,
      ts: r.created_at,
    });
  }
  return { sessionId, messages, running: false };
}

export function addOptimisticUser(thread: ChatThread, text: string): ChatThread {
  const msg: UiMessage = {
    id: localId("local"),
    role: "user",
    text,
    tools: [],
    streaming: false,
    pending: true,
    ts: new Date().toISOString(),
  };
  return { ...thread, messages: [...thread.messages, msg], running: true };
}

function withStreamingAssistant(thread: ChatThread, ts: string): { thread: ChatThread; index: number } {
  const last = thread.messages[thread.messages.length - 1];
  if (last && last.role === "assistant" && last.streaming) {
    return { thread, index: thread.messages.length - 1 };
  }
  const msg: UiMessage = { id: localId("stream"), role: "assistant", text: "", tools: [], streaming: true, ts };
  return { thread: { ...thread, messages: [...thread.messages, msg] }, index: thread.messages.length };
}

function patchAt(thread: ChatThread, index: number, fn: (m: UiMessage) => UiMessage): ChatThread {
  const messages = thread.messages.slice();
  const cur = messages[index];
  if (!cur) return thread;
  messages[index] = fn(cur);
  return { ...thread, messages };
}

function toolName(data: Record<string, unknown>): string {
  const inner = (data.data ?? {}) as Record<string, unknown>;
  return String(inner.name ?? inner.tool ?? data.name ?? data.tool ?? "Werkzeug");
}

/** Folds one `chat.*` event into the thread of its session. Returns the same object when irrelevant. */
export function applyChatEvent(thread: ChatThread, ev: BusEvent): ChatThread {
  const d = ev.data as Record<string, unknown>;
  if (d.session_id !== thread.sessionId) return thread;
  const text = typeof d.text === "string" ? d.text : "";
  const inner = (d.data ?? {}) as Record<string, unknown>;

  switch (ev.type) {
    case "chat.user": {
      const idx = thread.messages.findIndex((m) => m.pending && m.role === "user" && m.text === text);
      if (idx >= 0) return { ...patchAt(thread, idx, (m) => ({ ...m, pending: false })), running: true };
      const msg: UiMessage = { id: ev.id, role: "user", text, tools: [], streaming: false, ts: ev.ts };
      return { ...thread, messages: [...thread.messages, msg], running: true };
    }
    case "chat.delta": {
      if (!text) return thread;
      const { thread: t, index } = withStreamingAssistant(thread, ev.ts);
      return { ...patchAt(t, index, (m) => ({ ...m, text: m.text + text })), running: true };
    }
    case "chat.tool_started": {
      const { thread: t, index } = withStreamingAssistant(thread, ev.ts);
      const name = toolName(d);
      return patchAt(t, index, (m) => ({
        ...m,
        tools: [
          ...m.tools,
          {
            key: `${name}-${m.tools.length}-${ev.id}`,
            name,
            status: "running",
            args: typeof inner.args === "string" ? inner.args : undefined,
            preview: typeof inner.preview === "string" ? inner.preview : undefined,
          },
        ],
      }));
    }
    case "chat.tool_completed":
    case "chat.tool_failed": {
      const { thread: t, index } = withStreamingAssistant(thread, ev.ts);
      const name = toolName(d);
      const failed = ev.type === "chat.tool_failed";
      return patchAt(t, index, (m) => {
        const tools = m.tools.slice();
        let pos = -1;
        for (let i = tools.length - 1; i >= 0; i -= 1) {
          if (tools[i]?.name === name && tools[i]?.status === "running") {
            pos = i;
            break;
          }
        }
        const patch: Partial<ToolChip> = {
          status: failed ? "failed" : "ok",
          durationMs: typeof inner.duration_ms === "number" ? inner.duration_ms : undefined,
          error: typeof inner.error === "string" ? inner.error : undefined,
        };
        const existing = pos >= 0 ? tools[pos] : undefined;
        if (existing) tools[pos] = { ...existing, ...patch };
        else tools.push({ key: `${name}-${tools.length}-${ev.id}`, name, status: patch.status ?? "ok", ...patch });
        return { ...m, tools };
      });
    }
    case "chat.done": {
      const error = typeof d.error === "string" && d.error ? d.error : null;
      const last = thread.messages[thread.messages.length - 1];
      if (last && last.role === "assistant" && last.streaming) {
        return {
          ...patchAt(thread, thread.messages.length - 1, (m) => ({
            ...m,
            text: text || m.text,
            streaming: false,
            error,
            interrupted: m.interrupted || error === "cancelled",
            tools: m.tools.map((c) => (c.status === "running" ? { ...c, status: error ? "failed" : "ok" } : c)),
          })),
          running: false,
        };
      }
      if (!text) return { ...thread, running: false };
      const msg: UiMessage = { id: ev.id, role: "assistant", text, tools: [], streaming: false, error, ts: ev.ts };
      return { ...thread, messages: [...thread.messages, msg], running: false };
    }
    case "chat.interrupted": {
      const last = thread.messages[thread.messages.length - 1];
      if (last && last.role === "assistant" && last.streaming) {
        return patchAt(thread, thread.messages.length - 1, (m) => ({ ...m, interrupted: true }));
      }
      return thread;
    }
    default:
      return thread;
  }
}

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
