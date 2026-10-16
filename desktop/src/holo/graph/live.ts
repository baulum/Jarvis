// Live intelligence layer: folds REAL bus events (tool calls, memory lookups, coding jobs, tasks, approvals)
// into per-node activity and activity streams. Nothing here is simulated – a node lights up only because
// an observable event named it.
import type { BusEvent } from "../../lib/types";

export type ActivityStatus = "active" | "ok" | "error" | "recalled" | "new" | "approval" | "pulse";

export interface NodeActivity {
  status: ActivityStatus;
  since: number;
  /** Fades out after this time (ms); undefined = until a later event ends it. */
  until?: number;
  detail?: string;
}

export interface Stream {
  id: string;
  kind: "session" | "task" | "coding" | "schedule" | "voice";
  /** Node ids along which signals travel (agent → connector → tool → memory …). */
  path: string[];
  status: "running" | "ok" | "error" | "waiting";
  since: number;
  until?: number;
  label: string;
}

export interface ActivityLine {
  ts: number;
  text: string;
  tone: "info" | "ok" | "bad" | "warn";
  node?: string;
}

export interface LiveState {
  nodes: Record<string, NodeActivity>;
  streams: Record<string, Stream>;
  lines: ActivityLine[];
  lastRecall: number;
  lastToolStart: number;
  lastError: number;
  /** Bumped on every change – the 3D layer re-uploads attributes only when this changes. */
  rev: number;
}

export const emptyLive = (): LiveState => ({
  nodes: {},
  streams: {},
  lines: [],
  lastRecall: 0,
  lastToolStart: 0,
  lastError: 0,
  rev: 0,
});

const FADE = { recalled: 4500, new: 7000, ok: 2600, error: 6000, pulse: 1600, stream: 3200 };

function str(v: unknown): string | undefined {
  return typeof v === "string" && v ? v : undefined;
}

function line(s: LiveState, now: number, text: string, tone: ActivityLine["tone"], node?: string): ActivityLine[] {
  return [...s.lines.slice(-39), { ts: now, text, tone, node }];
}

function setNodes(
  map: Record<string, NodeActivity>,
  ids: (string | undefined)[],
  act: NodeActivity,
): Record<string, NodeActivity> {
  const next = { ...map };
  for (const id of ids) if (id) next[id] = act;
  return next;
}

/** Applies one bus event. Pure: returns the same object when the event is irrelevant. */
export function applyLiveEvent(s: LiveState, ev: BusEvent, now: number): LiveState {
  const d = (ev.data ?? {}) as Record<string, unknown>;
  const nodes = ev.nodes ?? [];
  const sid = ev.session_id ?? str(d.session_id);
  const type = ev.type;

  if (type === "memory.recalled") {
    const hits = nodes.filter((n) => n.startsWith("memory:"));
    if (!hits.length) return s;
    const via = str(d.via);
    const path = ["agent:jarvis", ...(via ? [`tool:${via}`] : []), ...hits];
    const streams = sid
      ? { ...s.streams, [`recall:${sid}`]: { id: `recall:${sid}`, kind: "session" as const, path, status: "ok" as const, since: now, until: now + FADE.recalled, label: "Gedächtnis" } }
      : s.streams;
    const purpose = str(d.purpose);
    const text =
      purpose === "extraction"
        ? `Abgleich mit ${hits.length} vorhandenen Erinnerungen`
        : `Erinnert sich: ${((d.hits as { title?: string }[] | undefined) ?? []).slice(0, 2).map((h) => h.title).join(", ")}`;
    return {
      ...s,
      nodes: setNodes(s.nodes, hits, { status: "recalled", since: now, until: now + FADE.recalled }),
      streams,
      lines: line(s, now, text, "info", hits[0]),
      lastRecall: purpose === "extraction" ? s.lastRecall : now,
      rev: s.rev + 1,
    };
  }

  if (type === "memory.changed") {
    const id = nodes.find((n) => n.startsWith("memory:"));
    if (!id) return s;
    const change = str(d.change) ?? "";
    if (change === "deleted") return { ...s, lines: line(s, now, "Erinnerung gelöscht", "info"), rev: s.rev + 1 };
    const verb = change === "added" ? "Neue Erinnerung" : change === "corrected" ? "Korrigiert" : "Aktualisiert";
    return {
      ...s,
      nodes: setNodes(s.nodes, [id], { status: "new", since: now, until: now + FADE.new }),
      lines: line(s, now, `${verb}: ${str(d.title) ?? str(d.path) ?? ""}`, "ok", id),
      rev: s.rev + 1,
    };
  }

  if (type === "tool.started" || type === "tool.finished") {
    const tool = str(d.tool);
    if (!tool) return s;
    const toolId = `tool:${tool}`;
    const connId = str(d.connector) ? `connector:${d.connector}` : undefined;
    const key = `tool:${sid ?? ev.task_id ?? "system"}`;
    const owner = ev.task_id ? `task:${ev.task_id}` : "agent:jarvis";
    const path = [owner, ...(connId ? [connId] : []), toolId];
    if (type === "tool.started") {
      return {
        ...s,
        nodes: setNodes(s.nodes, [toolId, connId], { status: "active", since: now }),
        streams: { ...s.streams, [key]: { id: key, kind: ev.task_id ? "task" : "session", path, status: "running", since: now, label: tool } },
        lines: line(s, now, `Werkzeug: ${tool}`, "info", toolId),
        lastToolStart: now,
        rev: s.rev + 1,
      };
    }
    const ok = d.status === "ok";
    const msg = str(d.message);
    return {
      ...s,
      nodes: setNodes(s.nodes, [toolId, connId], { status: ok ? "ok" : "error", since: now, until: now + (ok ? FADE.ok : FADE.error), detail: msg }),
      streams: { ...s.streams, [key]: { id: key, kind: ev.task_id ? "task" : "session", path, status: ok ? "ok" : "error", since: now, until: now + FADE.stream, label: tool } },
      lines: line(s, now, ok ? `${tool} erledigt` : `${tool} fehlgeschlagen${msg ? `: ${msg}` : ""}`, ok ? "ok" : "bad", toolId),
      lastError: ok ? s.lastError : now,
      rev: s.rev + 1,
    };
  }

  if (type === "chat.user" && sid) {
    const key = `session:${sid}`;
    return {
      ...s,
      nodes: setNodes(s.nodes, ["agent:jarvis", key], { status: "active", since: now }),
      streams: { ...s.streams, [key]: { id: key, kind: "session", path: [key, "agent:jarvis"], status: "running", since: now, label: "Gespräch" } },
      rev: s.rev + 1,
    };
  }
  if (type === "chat.done" && sid) {
    const key = `session:${sid}`;
    const err = str(d.error);
    const failed = Boolean(err && err !== "cancelled");
    const streams = { ...s.streams };
    for (const [k, st] of Object.entries(streams)) {
      if ((k === key || k === `tool:${sid}`) && st.status === "running") {
        streams[k] = { ...st, status: failed ? "error" : "ok", until: now + FADE.stream };
      }
    }
    if (streams[key]) streams[key] = { ...streams[key], status: failed ? "error" : "ok", until: now + FADE.stream };
    return {
      ...s,
      nodes: setNodes(s.nodes, ["agent:jarvis", key], { status: failed ? "error" : "ok", since: now, until: now + FADE.ok }),
      streams,
      lines: failed ? line(s, now, `Fehler: ${err}`, "bad", "agent:jarvis") : s.lines,
      lastError: failed ? now : s.lastError,
      rev: s.rev + 1,
    };
  }

  if (type === "coding.updated" || type === "coding.progress") {
    const job = str(d.job_id);
    if (!job) return s;
    const jid = `coding:${job}`;
    const key = `coding:${job}`;
    const status = str(d.status);
    const done = status === "completed" || status === "failed" || status === "cancelled";
    const path = ["agent:jarvis", "agent:coding", jid, ...nodes.filter((n) => n.startsWith("memory:"))];
    const nodeStatus: ActivityStatus = !done ? "active" : status === "completed" ? "ok" : "error";
    const text =
      type === "coding.progress"
        ? `Claude Code: ${str(d.tool) ?? str(d.text)?.slice(0, 80) ?? "arbeitet"}`
        : `Coding-Auftrag ${status === "running" ? "läuft" : status === "completed" ? "fertig" : status ?? ""}: ${str(d.title) ?? ""}`;
    return {
      ...s,
      nodes: setNodes(s.nodes, ["agent:coding", jid], { status: nodeStatus, since: now, until: done ? now + FADE.error : undefined }),
      streams: { ...s.streams, [key]: { id: key, kind: "coding", path: s.streams[key]?.path.length ? s.streams[key].path : path, status: done ? (status === "completed" ? "ok" : "error") : "running", since: s.streams[key]?.since ?? now, until: done ? now + FADE.stream : undefined, label: str(d.title) ?? s.streams[key]?.label ?? "Coding" } },
      lines: type === "coding.progress" && !str(d.tool) && !str(d.text) ? s.lines : line(s, now, text, status === "failed" ? "bad" : "info", jid),
      rev: s.rev + 1,
    };
  }

  if (type.startsWith("task.")) {
    const tid = ev.task_id ?? str(d.task_id);
    if (!tid) return s;
    const id = `task:${tid}`;
    const status = str(d.status);
    const done = status === "completed" || status === "failed" || status === "cancelled";
    const waiting = status === "waiting_approval" || status === "waiting_input" || type === "task.input_required";
    const act: NodeActivity = done
      ? { status: status === "completed" ? "ok" : "error", since: now, until: now + FADE.error }
      : waiting
        ? { status: "approval", since: now }
        : { status: "active", since: now };
    const step = str(d.name);
    return {
      ...s,
      nodes: setNodes(s.nodes, [id], act),
      streams: { ...s.streams, [id]: { id, kind: "task", path: ["agent:jarvis", id], status: done ? (status === "completed" ? "ok" : "error") : waiting ? "waiting" : "running", since: s.streams[id]?.since ?? now, until: done ? now + FADE.stream : undefined, label: str(d.title) ?? s.streams[id]?.label ?? "Aufgabe" } },
      lines: step || status ? line(s, now, `Aufgabe${str(d.title) ? ` „${d.title}“` : ""}: ${step ? `${step} ${status ?? ""}` : status}`, done && status !== "completed" ? "bad" : waiting ? "warn" : "info", id) : s.lines,
      rev: s.rev + 1,
    };
  }

  if (type === "approval.requested" || type === "approval.decided") {
    const tool = str(d.tool);
    if (!tool) return s;
    const id = `tool:${tool}`;
    if (type === "approval.requested") {
      return { ...s, nodes: setNodes(s.nodes, [id], { status: "approval", since: now, detail: str(d.summary) }), lines: line(s, now, `Wartet auf Freigabe: ${str(d.summary) ?? tool}`, "warn", id), rev: s.rev + 1 };
    }
    const ok = d.status === "executed";
    return { ...s, nodes: setNodes(s.nodes, [id], { status: ok ? "ok" : d.approved ? "error" : "pulse", since: now, until: now + FADE.ok }), rev: s.rev + 1 };
  }

  if (nodes.length && (type.startsWith("schedule.") || type.startsWith("telephony.") || type === "session.created")) {
    return { ...s, nodes: setNodes(s.nodes, nodes, { status: "pulse", since: now, until: now + FADE.pulse }), rev: s.rev + 1 };
  }
  return s;
}

/** Drops faded activity (called a few times per second). */
export function expireLive(s: LiveState, now: number): LiveState {
  let changed = false;
  const nodes: Record<string, NodeActivity> = {};
  for (const [k, v] of Object.entries(s.nodes)) {
    if (v.until !== undefined && v.until < now) changed = true;
    else nodes[k] = v;
  }
  const streams: Record<string, Stream> = {};
  for (const [k, v] of Object.entries(s.streams)) {
    // a running stream without news for 10 minutes was lost (e.g. core restart)
    if ((v.until !== undefined && v.until < now) || (v.status === "running" && now - v.since > 600_000)) changed = true;
    else streams[k] = v;
  }
  return changed ? { ...s, nodes, streams, rev: s.rev + 1 } : s;
}

// ------------------------------------------------------------------ adaptive interface state

export type JarvisState =
  | "idle"
  | "listening"
  | "thinking"
  | "retrieving"
  | "tools"
  | "coding"
  | "speaking"
  | "approval"
  | "error";

export const STATE_LABEL: Record<JarvisState, string> = {
  idle: "Bereit",
  listening: "Hört zu",
  thinking: "Denkt nach",
  retrieving: "Durchsucht das Gedächtnis",
  tools: "Arbeitet mit Werkzeugen",
  coding: "Programmiert",
  speaking: "Spricht",
  approval: "Wartet auf Freigabe",
  error: "Fehler",
};

export interface StateInputs {
  voice?: string | null;
  connection: string;
  pendingApprovals: number;
  agentRunning: boolean;
  live: LiveState;
  now: number;
}

/** One state for the whole interface, by priority (what Sir most needs to know right now). */
export function deriveJarvisState(i: StateInputs): JarvisState {
  const recentError = i.live.lastError > 0 && i.now - i.live.lastError < 4000;
  if (i.connection === "unauthorized" || i.connection === "closed" || recentError) return "error";
  if (i.pendingApprovals > 0) return "approval";
  if (i.voice === "speaking") return "speaking";
  if (i.voice === "listening" || i.voice === "recording") return "listening";
  const streams = Object.values(i.live.streams);
  const toolRunning = streams.some((s) => s.status === "running" && (s.kind === "session" || s.kind === "task") && s.id.startsWith("tool:"));
  if (toolRunning) return "tools";
  if (i.agentRunning && i.live.lastRecall > 0 && i.now - i.live.lastRecall < 1500) return "retrieving";
  if (i.agentRunning || i.voice === "thinking") return "thinking";
  if (streams.some((s) => s.kind === "coding" && s.status === "running")) return "coding";
  return "idle";
}
