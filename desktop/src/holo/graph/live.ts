// Live intelligence layer: folds the core's filtered activity steps (bus type "activity.step", see
// core/src/jarvis/activity.py) into running processes, per-node activity, the event stream and visual effects.
// Nothing here is simulated – every process, line and impulse exists because the core reported a real step.
import type { BusEvent } from "../../lib/types";
import type { RegionGroup } from "./anatomy";

export type Phase = "request" | "llm" | "response" | "tool" | "memory.retrieve" | "memory.store" | "agent" | "approval" | "system";
export type StepStatus = "running" | "waiting" | "ok" | "error" | "cancelled";

/** One activity step as sent by the core (only ids, names, statuses, durations – never content). */
export interface ActivityStep {
  phase: Phase;
  state: "start" | "progress" | "end";
  status: StepStatus;
  key: string;
  label: string;
  parent?: string | null;
  session_id?: string | null;
  task_id?: string | null;
  nodes: string[];
  t: number;
  seq: number;
  boot: string;
  channel?: string;
  model?: string;
  tool?: string;
  connector?: string;
  risk?: string;
  parallel?: number;
  duration_ms?: number;
  error?: string;
  purpose?: string;
  via?: string;
  hits?: { id: string; kind?: string | null; score?: number | null }[];
  count?: number;
  change?: string;
  origin?: string;
  agent?: string;
  kind?: string;
}

export interface ToolStat {
  calls: number;
  errors: number;
  avg_ms: number | null;
  last_ms: number | null;
  last_status: string | null;
  last_at: number | null;
  running: number;
}

export interface ActivitySnapshot {
  boot: string;
  seq: number;
  complete: boolean;
  running: ActivityStep[];
  steps: ActivityStep[];
  tools: Record<string, ToolStat>;
  agents: Record<string, { runs: number; errors: number }>;
}

export type ActivityStatus = "active" | "ok" | "error" | "recalled" | "new" | "approval" | "pulse";

export interface NodeActivity {
  status: ActivityStatus;
  since: number;
  /** Fades out after this time (ms); undefined = until a later step ends it. */
  until?: number;
  detail?: string;
}

/** Something that is running (or just finished): a turn, a tool call, an agent job, an approval. */
export interface Process {
  key: string;
  phase: Phase;
  label: string;
  status: StepStatus;
  since: number;
  updated: number;
  until?: number;
  /** wall clock (ms) when it started – for "läuft seit" after a resync */
  startedAt: number;
  tool?: string;
  agent?: string;
  parent?: string | null;
  channel?: string;
  model?: string;
  parallel?: number;
  durationMs?: number;
  error?: string;
  /** node ids along which data flows for this process (for edge highlighting) */
  path: string[];
}

export interface ActivityLine {
  id: string;
  ts: number;
  /** wall clock of the step (ms) */
  at: number;
  phase: Phase;
  text: string;
  detail?: string;
  tone: "info" | "ok" | "bad" | "warn";
  node?: string;
  nodes?: string[];
  replayed?: boolean;
}

/** Visual effects; `path` holds node ids or "region:<group>" anchors. */
export type Effect =
  | { kind: "impulse"; path: string[]; tone: Tone; count: number }
  | { kind: "glow"; group: RegionGroup; tone: Tone; strength: number }
  | { kind: "wave"; at: string; tone: Tone; strength: number }
  | { kind: "grow"; node: string };

export type Tone = "signal" | "data" | "memory" | "ok" | "error" | "agent" | "warn";

export type SyncState = "live" | "offline" | "resyncing";

export interface LiveState {
  nodes: Record<string, NodeActivity>;
  processes: Record<string, Process>;
  lines: ActivityLine[];
  lastRecall: number;
  lastToolStart: number;
  lastError: number;
  tools: Record<string, ToolStat>;
  agents: Record<string, { runs: number; errors: number }>;
  seq: number;
  boot: string | null;
  sync: SyncState;
  /** Bumped on every change – the 3D layer re-uploads attributes only when this changes. */
  rev: number;
}

export const emptyLive = (): LiveState => ({
  nodes: {},
  processes: {},
  lines: [],
  lastRecall: 0,
  lastToolStart: 0,
  lastError: 0,
  tools: {},
  agents: {},
  seq: 0,
  boot: null,
  sync: "live",
  rev: 0,
});

const FADE = { recalled: 5000, new: 8000, ok: 2600, error: 7000, pulse: 1800, process: 4500 };
const MAX_LINES = 80;
const MAX_IMPULSE_HITS = 12;

function isStep(v: unknown): v is ActivityStep {
  const s = v as ActivityStep;
  return Boolean(s && typeof s.phase === "string" && typeof s.key === "string" && typeof s.state === "string");
}

/** The step inside a bus event (the core sends the graph nodes in the envelope). */
export function stepFromEvent(ev: BusEvent): ActivityStep | null {
  if (ev.type !== "activity.step") return null;
  const d = ev.data as unknown as ActivityStep;
  const step = { ...d, nodes: ev.nodes ?? d.nodes ?? [] };
  return isStep(step) ? step : null;
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0).replace(".", ",")} s`;
  const m = Math.floor(ms / 60_000);
  return `${m} min ${Math.round((ms % 60_000) / 1000)} s`;
}

function anchorOf(s: LiveState, parent: string | null | undefined): string {
  if (!parent) return "agent:jarvis";
  if (parent.startsWith("call:")) {
    const p = s.processes[parent];
    return p?.tool ? `tool:${p.tool}` : "agent:jarvis";
  }
  if (parent.startsWith("task:")) return parent;
  return "agent:jarvis"; // a turn is processed by J.A.R.V.I.S.
}

function setNodes(map: Record<string, NodeActivity>, ids: (string | undefined | null)[], act: NodeActivity): Record<string, NodeActivity> {
  const next = { ...map };
  for (const id of ids) if (id) next[id] = act;
  return next;
}

function pushLine(lines: ActivityLine[], line: ActivityLine): ActivityLine[] {
  return [...lines.slice(-(MAX_LINES - 1)), line];
}

function toneOf(status: StepStatus): ActivityLine["tone"] {
  return status === "error" ? "bad" : status === "waiting" ? "warn" : status === "ok" ? "ok" : "info";
}

export interface StepResult {
  state: LiveState;
  effects: Effect[];
}

/**
 * Applies one activity step. Pure. ``replay`` (steps fetched after a reconnect) updates state and the stream
 * but produces no effects – old events must not fire impulses as if they were happening now.
 */
export function applyStep(s: LiveState, step: ActivityStep, now: number, replay = false): StepResult {
  if (s.boot === step.boot && step.seq <= s.seq) return { state: s, effects: [] }; // duplicate
  const fx: Effect[] = [];
  const at = typeof step.t === "number" ? step.t : Date.now();
  let nodes = s.nodes;
  const processes = { ...s.processes };
  let lines = s.lines;
  let { lastRecall, lastToolStart, lastError } = s;
  const tools = { ...s.tools };
  const line = (text: string, tone: ActivityLine["tone"], extra: Partial<ActivityLine> = {}) => {
    lines = pushLine(lines, { id: `${step.boot}:${step.seq}`, ts: now, at, phase: step.phase, text, tone, replayed: replay || undefined, ...extra });
  };
  const ended = step.state === "end";
  const prev = processes[step.key];
  const upsert = (p: Partial<Process> & { path: string[] }) => {
    processes[step.key] = {
      key: step.key,
      phase: step.phase,
      label: step.label,
      status: step.status,
      since: prev?.since ?? now,
      startedAt: prev?.startedAt ?? at,
      updated: now,
      until: ended ? now + FADE.process : undefined,
      parent: step.parent ?? prev?.parent,
      channel: step.channel ?? prev?.channel,
      model: step.model ?? prev?.model,
      tool: step.tool ?? prev?.tool,
      agent: step.agent ?? prev?.agent,
      parallel: step.parallel ?? prev?.parallel,
      ...p,
      path: p.path.length ? p.path : (prev?.path ?? []),
    };
  };

  switch (step.phase) {
    case "request": {
      const session = step.session_id ? `session:${step.session_id}` : undefined;
      const entry = step.channel === "voice" || step.channel === "phone" ? "agent:voice" : "region:input";
      upsert({ path: [session ?? "", "agent:jarvis"].filter(Boolean) });
      nodes = setNodes(nodes, ["agent:jarvis", session], { status: "active", since: now });
      fx.push({ kind: "glow", group: "input", tone: "signal", strength: 1 });
      fx.push({ kind: "impulse", path: [entry, ...(session ? [session] : []), "agent:jarvis"], tone: "signal", count: 3 });
      line(step.label, "info", { node: session, detail: step.channel === "voice" ? "per Stimme" : step.channel === "phone" ? "per Telefon" : undefined });
      break;
    }
    case "llm": {
      upsert({ path: prev?.path ?? [], label: step.label, status: "running", phase: prev?.phase ?? "llm" });
      fx.push({ kind: "wave", at: "agent:jarvis", tone: "signal", strength: 1 });
      fx.push({ kind: "glow", group: "core", tone: "signal", strength: 1 });
      line(step.label, "info", { node: "agent:jarvis", detail: step.model ? `Modell ${step.model}` : undefined });
      break;
    }
    case "response": {
      if (!ended) {
        upsert({ path: prev?.path ?? [], label: step.label, status: "running", phase: prev?.phase ?? "response" });
        fx.push({ kind: "impulse", path: ["agent:jarvis", "region:output"], tone: "signal", count: 2 });
        fx.push({ kind: "glow", group: "output", tone: "signal", strength: 0.7 });
        line(step.label, "info", { node: "agent:jarvis" });
        break;
      }
      const failed = step.status === "error";
      upsert({ path: prev?.path ?? [], durationMs: step.duration_ms, error: step.error });
      const session = step.session_id ? `session:${step.session_id}` : undefined;
      nodes = setNodes(nodes, ["agent:jarvis", session], { status: failed ? "error" : "ok", since: now, until: now + (failed ? FADE.error : FADE.ok), detail: step.error });
      // tool calls of this turn that never reported an end (core restart) end with it
      for (const [k, p] of Object.entries(processes)) {
        if (p.parent === step.key && !p.until && p.status === "running") processes[k] = { ...p, status: "cancelled", until: now + FADE.process };
      }
      if (failed) {
        lastError = now;
        fx.push({ kind: "glow", group: "core", tone: "error", strength: 1 });
      } else if (step.status === "ok") {
        fx.push({ kind: "impulse", path: ["agent:jarvis", "region:output"], tone: "ok", count: 4 });
        fx.push({ kind: "glow", group: "output", tone: "ok", strength: 1 });
      }
      line(step.label, toneOf(step.status), { node: session, detail: failed ? step.error : formatDuration(step.duration_ms) });
      break;
    }
    case "tool": {
      const tool = step.tool ?? "";
      const toolId = `tool:${tool}`;
      const connId = step.connector ? `connector:${step.connector}` : undefined;
      const source = anchorOf(s, step.parent);
      const path = [source, ...(connId ? [connId] : []), toolId];
      const stat: ToolStat = tools[tool] ?? { calls: 0, errors: 0, avg_ms: null, last_ms: null, last_status: null, last_at: null, running: 0 };
      if (!ended) {
        upsert({ path, tool, parallel: step.parallel });
        nodes = setNodes(nodes, [toolId, connId], { status: "active", since: now });
        lastToolStart = now;
        fx.push({ kind: "impulse", path, tone: "signal", count: 2 });
        fx.push({ kind: "glow", group: "tools", tone: "signal", strength: 0.9 });
        line(step.label, "info", { node: toolId, detail: (step.parallel ?? 1) > 1 ? `${tool} · ${step.parallel} parallel` : tool });
        tools[tool] = { ...stat, running: stat.running + 1 };
      } else {
        const ok = step.status === "ok";
        upsert({ path, tool, durationMs: step.duration_ms, error: step.error });
        nodes = setNodes(nodes, [toolId, connId], { status: ok ? "ok" : "error", since: now, until: now + (ok ? FADE.ok : FADE.error), detail: step.error });
        if (!ok) lastError = now;
        fx.push({ kind: "impulse", path: [...path].reverse(), tone: ok ? "data" : "error", count: ok ? 3 : 1 });
        line(step.label, ok ? "ok" : "bad", { node: toolId, detail: ok ? formatDuration(step.duration_ms) : step.error || formatDuration(step.duration_ms) });
        const calls = stat.calls + 1;
        const total = (stat.avg_ms ?? 0) * stat.calls + (step.duration_ms ?? 0);
        tools[tool] = {
          calls,
          errors: stat.errors + (ok ? 0 : 1),
          avg_ms: Math.round(total / calls),
          last_ms: step.duration_ms ?? null,
          last_status: ok ? "ok" : "error",
          last_at: at,
          running: Math.max(0, stat.running - 1),
        };
      }
      break;
    }
    case "memory.retrieve": {
      const hits = step.nodes.filter((n) => n.startsWith("memory:"));
      const source = anchorOf(s, step.parent);
      nodes = setNodes(nodes, hits, { status: "recalled", since: now, until: now + FADE.recalled });
      if (step.purpose !== "extraction") lastRecall = now;
      fx.push({ kind: "glow", group: "memory", tone: "memory", strength: hits.length ? 1 : 0.5 });
      for (const h of hits.slice(0, MAX_IMPULSE_HITS)) fx.push({ kind: "impulse", path: [source, "region:memory", h], tone: "memory", count: 1 });
      if (hits.length) fx.push({ kind: "wave", at: "region:memory", tone: "memory", strength: 0.6 });
      processes[step.key] = {
        key: step.key, phase: step.phase, label: step.label, status: "ok", since: now, startedAt: at, updated: now,
        until: now + FADE.recalled, parent: step.parent, durationMs: step.duration_ms, path: [source, ...hits.slice(0, MAX_IMPULSE_HITS)],
      };
      line(step.label, "info", {
        node: hits[0],
        nodes: hits,
        detail: hits.length ? `${hits.length} ${hits.length === 1 ? "Treffer" : "Treffer"}${step.duration_ms !== undefined ? ` · ${formatDuration(step.duration_ms)}` : ""}` : "keine Treffer",
      });
      break;
    }
    case "memory.store": {
      const id = step.nodes[0];
      if (step.change === "deleted" || step.status === "waiting" || !id) {
        line(step.label, step.status === "waiting" ? "warn" : "info");
        break;
      }
      nodes = setNodes(nodes, [id], { status: "new", since: now, until: now + FADE.new });
      fx.push({ kind: "grow", node: id });
      fx.push({ kind: "impulse", path: [anchorOf(s, step.parent), "region:memory", id], tone: "memory", count: 2 });
      fx.push({ kind: "glow", group: "memory", tone: "memory", strength: 0.8 });
      line(step.label, "ok", { node: id, detail: step.origin === "vault" ? "im Obsidian-Vault" : undefined });
      break;
    }
    case "agent": {
      const agent = step.agent ?? "";
      const agentNode = agent === "tasks" ? "agent:jarvis" : `agent:${agent}`;
      const own = step.key; // coding:<job>, task:<id>, schedule:<id>
      const path = [...(agentNode === "agent:jarvis" ? ["agent:jarvis"] : ["agent:jarvis", agentNode]), own];
      upsert({ path, agent });
      const act: NodeActivity = ended
        ? { status: step.status === "ok" ? "ok" : "error", since: now, until: now + (step.status === "ok" ? FADE.ok : FADE.error) }
        : step.status === "waiting" ? { status: "approval", since: now } : { status: "active", since: now };
      nodes = setNodes(nodes, [agentNode === "agent:jarvis" ? undefined : agentNode, own], act);
      fx.push({ kind: "glow", group: "agents", tone: step.status === "error" ? "error" : "agent", strength: step.state === "progress" ? 0.5 : 1 });
      if (step.state === "start") fx.push({ kind: "impulse", path, tone: "agent", count: 3 });
      else if (step.state === "progress" && step.status === "running") fx.push({ kind: "impulse", path: path.slice(-2), tone: "agent", count: 1 });
      else if (ended) fx.push({ kind: "impulse", path: [...path].reverse(), tone: step.status === "ok" ? "ok" : "error", count: 2 });
      if (ended && step.status === "error") lastError = now;
      line(step.label, toneOf(step.status), { node: own });
      break;
    }
    case "approval": {
      const toolId = step.tool ? `tool:${step.tool}` : undefined;
      upsert({ path: [toolId ?? ""].filter(Boolean), tool: step.tool });
      if (!ended) {
        nodes = setNodes(nodes, [toolId], { status: "approval", since: now });
        fx.push({ kind: "glow", group: "tools", tone: "warn", strength: 0.8 });
        line(step.label, "warn", { node: toolId, detail: step.tool });
      } else {
        const ok = step.status === "ok";
        nodes = setNodes(nodes, [toolId], { status: ok ? "ok" : step.status === "error" ? "error" : "pulse", since: now, until: now + FADE.ok });
        if (ok && toolId) fx.push({ kind: "impulse", path: [toolId, "agent:jarvis"], tone: "data", count: 2 });
        line(step.label, toneOf(step.status), { node: toolId, detail: step.tool });
      }
      break;
    }
    case "system": {
      if (step.key === "voice") {
        if (ended) delete processes.voice;
        else upsert({ path: ["agent:voice"], agent: "voice" });
        if (!ended) {
          nodes = setNodes(nodes, ["agent:voice"], { status: "active", since: now });
          fx.push({ kind: "glow", group: step.label === "Spricht" ? "output" : "input", tone: "signal", strength: 0.6 });
        } else {
          nodes = setNodes(nodes, ["agent:voice"], { status: "pulse", since: now, until: now + FADE.pulse });
        }
        // voice state changes are frequent and visible in the status mark – not every one goes into the stream
        break;
      }
      line(step.label, toneOf(step.status));
      if (step.status === "error") fx.push({ kind: "glow", group: "core", tone: "error", strength: 1 });
      break;
    }
  }

  return {
    state: {
      ...s,
      nodes,
      processes,
      lines,
      lastRecall,
      lastToolStart,
      lastError,
      tools,
      seq: Math.max(s.boot === step.boot ? s.seq : 0, step.seq),
      boot: step.boot,
      rev: s.rev + 1,
    },
    effects: replay ? [] : fx,
  };
}

/** Folds a resync snapshot in. Incomplete (restart, buffer overrun): state is rebuilt from what runs now. */
export function applySnapshot(s: LiveState, snap: ActivitySnapshot, now: number): LiveState {
  const wall = Date.now();
  const missed = snap.steps.filter((st) => !(s.boot === snap.boot && st.seq <= s.seq));
  let base: LiveState = s;
  if (!snap.complete || s.boot !== snap.boot) {
    // keep the visible stream, but forget processes and the seq of the previous core process
    base = { ...emptyLive(), lines: s.lines, nodes: {}, boot: snap.boot, seq: 0 };
    for (const st of snap.steps.slice(-30)) base = applyStep(base, st, now, true).state;
  } else {
    for (const st of missed) base = applyStep(base, st, now, true).state;
  }
  // what the core reports as running wins; local processes it does not know any more have ended
  const running = new Set(snap.running.map((r) => r.key));
  const processes: Record<string, Process> = {};
  for (const [k, p] of Object.entries(base.processes)) {
    if (p.until !== undefined || running.has(k)) processes[k] = p;
  }
  let nodes = { ...base.nodes };
  for (const r of snap.running) {
    const age = Math.max(0, wall - r.t);
    const p = processes[r.key];
    processes[r.key] = {
      key: r.key,
      phase: r.phase,
      label: r.label,
      status: r.status,
      since: p?.since ?? now - age,
      startedAt: p?.startedAt ?? r.t,
      updated: now,
      tool: r.tool,
      agent: r.agent,
      parent: r.parent,
      channel: r.channel,
      parallel: r.parallel,
      path: p?.path ?? r.nodes,
    };
    for (const n of r.nodes) if (!nodes[n] || nodes[n]!.until !== undefined) nodes = setNodes(nodes, [n], { status: r.status === "waiting" ? "approval" : "active", since: now - age });
  }
  for (const [id, a] of Object.entries(nodes)) {
    // "active" without anything running behind it any more
    if (a.status === "active" && a.until === undefined && !snap.running.some((r) => r.nodes.includes(id))) delete nodes[id];
  }
  return {
    ...base,
    processes,
    nodes,
    tools: snap.tools ?? base.tools,
    agents: snap.agents ?? base.agents,
    seq: snap.seq,
    boot: snap.boot,
    sync: "live",
    rev: s.rev + 1,
  };
}

/** Drops faded activity (called a few times per second). */
export function expireLive(s: LiveState, now: number): LiveState {
  let changed = false;
  const nodes: Record<string, NodeActivity> = {};
  for (const [k, v] of Object.entries(s.nodes)) {
    if (v.until !== undefined && v.until < now) changed = true;
    else nodes[k] = v;
  }
  const processes: Record<string, Process> = {};
  for (const [k, v] of Object.entries(s.processes)) {
    // a running process without news for 15 minutes was lost (e.g. core restart without reconnect)
    if ((v.until !== undefined && v.until < now) || (v.until === undefined && now - v.updated > 900_000)) changed = true;
    else processes[k] = v;
  }
  return changed ? { ...s, nodes, processes, rev: s.rev + 1 } : s;
}

export function setSync(s: LiveState, sync: SyncState): LiveState {
  return s.sync === sync ? s : { ...s, sync, rev: s.rev + 1 };
}

// ------------------------------------------------------------------ headline for the event stream
export interface Headline {
  text: string;
  detail?: string;
  tone: "idle" | "busy" | "warn" | "bad";
  since?: number;
}

/** One sentence for "what is happening right now", by priority. */
export function headline(s: LiveState, now: number): Headline {
  if (s.sync === "offline") return { text: "Verbindung zum Core unterbrochen", detail: "Anzeige pausiert – synchronisiert sich automatisch", tone: "bad" };
  if (s.sync === "resyncing") return { text: "Synchronisiere …", tone: "warn" };
  const running = Object.values(s.processes).filter((p) => p.until === undefined);
  const approvals = running.filter((p) => p.phase === "approval");
  if (approvals.length) return { text: "Wartet auf Freigabe", detail: approvals.map((p) => p.tool).filter(Boolean).join(", "), tone: "warn", since: approvals[0]!.since };
  const tools = running.filter((p) => p.phase === "tool");
  if (tools.length) {
    const first = tools.reduce((a, b) => (a.since < b.since ? a : b));
    return {
      text: tools.length > 1 ? `Warte auf ${tools.length} Tool-Ergebnisse` : "Warte auf Tool-Ergebnis",
      detail: tools.map((p) => p.tool).join(" · "),
      tone: "busy",
      since: first.since,
    };
  }
  const turn = running.find((p) => p.key.startsWith("turn:"));
  if (turn) return { text: turn.label, detail: turn.model ? `Modell ${turn.model}` : undefined, tone: "busy", since: turn.since };
  const agent = running.find((p) => p.phase === "agent" && p.status === "running");
  if (agent) return { text: agent.label, tone: "busy", since: agent.since };
  const voice = running.find((p) => p.key === "voice");
  if (voice) return { text: voice.label, tone: "busy" };
  const last = s.lines[s.lines.length - 1];
  if (last && now - last.ts < 8000) return { text: last.text, detail: last.detail, tone: last.tone === "bad" ? "bad" : "idle" };
  return { text: "Bereit", tone: "idle" };
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
  const running = Object.values(i.live.processes).filter((p) => p.until === undefined && p.status === "running");
  if (running.some((p) => p.phase === "tool")) return "tools";
  if (i.agentRunning && i.live.lastRecall > 0 && i.now - i.live.lastRecall < 1500) return "retrieving";
  if (i.agentRunning || i.voice === "thinking") return "thinking";
  if (running.some((p) => p.phase === "agent" && p.agent === "coding")) return "coding";
  return "idle";
}
