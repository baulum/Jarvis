// Chat timeline: the conversation as one list of items ordered by `seq` (core/src/jarvis/agent/timeline.py).
// Pure functions only – the store folds bus events in, the views turn the list into rows.
import type { BusEvent } from "./types";

export type TimelineKind = "message" | "tool" | "process" | "turn";

export interface MessageData {
  role: "user" | "assistant" | "system";
  text: string;
  status?: "streaming" | "done";
  /** task-list state by item index ("- [ ]"), set by clicking a checkbox */
  checks?: Record<string, boolean>;
  level?: "warning" | "error";
  error?: string;
  channel?: string;
  legacy?: boolean;
}

export interface RoutePoint {
  label: string;
  lat: number;
  lon: number;
}

export interface RouteDisplay {
  kind: "route";
  mode: string;
  mode_label?: string;
  provider?: string;
  start: RoutePoint;
  destination: RoutePoint;
  routes: { distance_m: number; duration_s: number; summary?: string; geometry: [number, number][] }[];
  tiles: { url: string; attribution: string };
}

/** A file made by image_generate / video_generate, loaded via GET /api/media/{media}/{file}. */
export interface MediaDisplay {
  kind: "image" | "video";
  media: "images" | "videos";
  file: string;
  prompt?: string;
  aspect_ratio?: string;
  duration_s?: number;
}

export interface ToolData {
  tool_use_id?: string;
  name: string;
  status: "running" | "ok" | "failed";
  started_ts?: number;
  ended_ts?: number;
  duration_ms?: number;
  input?: string;
  result?: string;
  error?: string;
  display?: RouteDisplay | MediaDisplay | { kind: string };
  legacy?: boolean;
  redacted?: boolean;
}

export type ProcessSource = "model" | "protocol" | "summary";
export interface ProcessData {
  step: "reasoning" | "model" | "memory" | "search" | "tool" | "plan" | "action" | "summary" | string;
  source: ProcessSource;
  title: string;
  text?: string;
  status?: string;
  items?: string[];
  ref?: string;
  result?: string;
  redacted?: boolean;
}

export interface TurnData {
  status: "running" | "done" | "failed" | "cancelled";
  model?: string;
  reasoning?: boolean;
  started_ts?: number;
  ended_ts?: number;
  duration_ms?: number;
  error?: string;
  channel?: string;
}

export interface TimelineEvent<D = MessageData | ToolData | ProcessData | TurnData> {
  id: string;
  session_id: string;
  turn_id: string | null;
  seq: number;
  /** creation time, ms since epoch */
  ts: number;
  kind: TimelineKind;
  data: D;
  /** React key: stays the same when an optimistic item is replaced by the server's */
  key?: string;
  local?: boolean;
}

export interface ChatThread {
  sessionId: string;
  events: TimelineEvent[];
  running: boolean;
  /** older turns exist on the server */
  hasMore: boolean;
  loaded: boolean;
}

export const isMessage = (e: TimelineEvent): e is TimelineEvent<MessageData> => e.kind === "message";
export const isTool = (e: TimelineEvent): e is TimelineEvent<ToolData> => e.kind === "tool";
export const isProcess = (e: TimelineEvent): e is TimelineEvent<ProcessData> => e.kind === "process";
export const isTurn = (e: TimelineEvent): e is TimelineEvent<TurnData> => e.kind === "turn";

export function emptyThread(sessionId: string): ChatThread {
  return { sessionId, events: [], running: false, hasMore: false, loaded: false };
}

let localSeq = 0;

/** Optimistic user message: sorts after everything the server has sent until the server's copy replaces it. */
export function addOptimisticUser(thread: ChatThread, text: string): ChatThread {
  localSeq += 1;
  const id = `local-${Date.now().toString(36)}-${localSeq}`;
  const ev: TimelineEvent<MessageData> = {
    id,
    key: id,
    local: true,
    session_id: thread.sessionId,
    turn_id: null,
    seq: Number.MAX_SAFE_INTEGER - 1_000_000 + localSeq,
    ts: Date.now(),
    kind: "message",
    data: { role: "user", text, status: "done" },
  };
  return { ...thread, events: [...thread.events, ev], running: true };
}

function insertSorted(events: TimelineEvent[], ev: TimelineEvent): TimelineEvent[] {
  let i = events.length;
  while (i > 0 && (events[i - 1] as TimelineEvent).seq > ev.seq) i -= 1;
  return [...events.slice(0, i), ev, ...events.slice(i)];
}

/** Insert or replace one item; ordering is by `seq` only, never by arrival. */
export function upsertEvent(events: TimelineEvent[], incoming: TimelineEvent): TimelineEvent[] {
  const idx = events.findIndex((e) => e.id === incoming.id);
  if (idx >= 0) {
    const cur = events[idx] as TimelineEvent;
    let data = incoming.data;
    // a snapshot of a still-streaming segment may lag behind the deltas already applied locally
    if (isMessage(cur) && incoming.kind === "message") {
      const inc = incoming.data as MessageData;
      if (inc.status === "streaming" && cur.data.text.length > inc.text.length) data = { ...inc, text: cur.data.text };
    }
    if (isProcess(cur) && incoming.kind === "process") {
      const inc = incoming.data as ProcessData;
      if (inc.status === "running" && (cur.data.text ?? "").length > (inc.text ?? "").length) data = { ...inc, text: cur.data.text };
    }
    const next = { ...incoming, data, key: cur.key ?? cur.id };
    if (next.seq === cur.seq) {
      const out = events.slice();
      out[idx] = next;
      return out;
    }
    return insertSorted([...events.slice(0, idx), ...events.slice(idx + 1)], next);
  }
  if (isMessage(incoming) && incoming.data.role === "user") {
    // the server's copy of an optimistic message takes its place (and keeps its key: no re-mount)
    const local = events.findIndex((e) => e.local && isMessage(e) && e.data.role === "user" && e.data.text === incoming.data.text);
    if (local >= 0) {
      const key = (events[local] as TimelineEvent).key;
      return insertSorted([...events.slice(0, local), ...events.slice(local + 1)], { ...incoming, key });
    }
  }
  return insertSorted(events, { ...incoming, key: incoming.id });
}

/** Appends streamed text to a message segment or a reasoning block. Unknown ids are ignored (the snapshot follows). */
export function appendText(events: TimelineEvent[], id: string, text: string): TimelineEvent[] {
  const idx = events.findIndex((e) => e.id === id);
  if (idx < 0 || !text) return events;
  const cur = events[idx] as TimelineEvent;
  if (!isMessage(cur) && !isProcess(cur)) return events;
  const out = events.slice();
  out[idx] = { ...cur, data: { ...cur.data, text: (cur.data.text ?? "") + text } } as TimelineEvent;
  return out;
}

/** Server timeline (REST) merged into what is already shown live. */
export function mergeServer(thread: ChatThread, events: TimelineEvent[], hasMore: boolean, older = false): ChatThread {
  let out = thread.events;
  for (const ev of events) out = upsertEvent(out, ev);
  const lastTurn = [...out].reverse().find(isTurn);
  return {
    ...thread,
    events: out,
    // a refresh of the newest turns says nothing about older pages already loaded
    hasMore: older || !thread.loaded ? hasMore : thread.hasMore,
    loaded: true,
    running: lastTurn ? lastTurn.data.status === "running" : thread.running,
  };
}

export function setCheck(thread: ChatThread, id: string, index: number, checked: boolean): ChatThread {
  const idx = thread.events.findIndex((e) => e.id === id);
  const cur = idx >= 0 ? thread.events[idx] : undefined;
  if (!cur || !isMessage(cur)) return thread;
  const events = thread.events.slice();
  events[idx] = { ...cur, data: { ...cur.data, checks: { ...(cur.data.checks ?? {}), [String(index)]: checked } } };
  return { ...thread, events };
}

/** Folds one bus event into the thread of its session. Returns the same object when irrelevant. */
export function applyChatEvent(thread: ChatThread, ev: BusEvent): ChatThread {
  const d = ev.data as Record<string, unknown>;
  if (d.session_id !== thread.sessionId) return thread;
  switch (ev.type) {
    case "chat.event": {
      const item = d.event as TimelineEvent | undefined;
      if (!item || typeof item.id !== "string") return thread;
      const events = upsertEvent(thread.events, item);
      let running = thread.running;
      if (isTurn(item)) running = item.data.status === "running";
      return { ...thread, events, running };
    }
    case "chat.delta":
    case "chat.thinking": {
      const id = typeof d.event_id === "string" ? d.event_id : null;
      if (!id) return thread;
      return { ...thread, events: appendText(thread.events, id, typeof d.text === "string" ? d.text : ""), running: true };
    }
    case "chat.check":
      return typeof d.event_id === "string" && typeof d.index === "number"
        ? setCheck(thread, d.event_id, d.index, Boolean(d.checked))
        : thread;
    case "chat.user":
      return thread.running ? thread : { ...thread, running: true };
    case "chat.done":
      return thread.running ? { ...thread, running: false } : thread;
    default:
      return thread;
  }
}

/** Applies a batch of buffered text deltas (`event id -> text`) in one update (streaming is throttled). */
export function applyDeltas(thread: ChatThread, deltas: Map<string, string>): ChatThread {
  let events = thread.events;
  for (const [id, text] of deltas) events = appendText(events, id, text);
  return events === thread.events ? thread : { ...thread, events };
}

// ------------------------------------------------------------------ rows

export interface AnswerRow {
  type: "answer";
  key: string;
  turnId: string;
  turn?: TimelineEvent<TurnData>;
  /** assistant text segments and tool calls, in event order */
  items: (TimelineEvent<MessageData> | TimelineEvent<ToolData>)[];
  process: TimelineEvent<ProcessData>[];
  tools: TimelineEvent<ToolData>[];
}
export type Row =
  | { type: "user"; key: string; event: TimelineEvent<MessageData> }
  | { type: "system"; key: string; event: TimelineEvent<MessageData> }
  | AnswerRow;

/** Groups the ordered items into what the chat shows: Sir's messages, Jarvis' answers (per turn), system notes. */
export function buildRows(events: TimelineEvent[]): Row[] {
  const rows: Row[] = [];
  const answers = new Map<string, AnswerRow>();
  const answer = (ev: TimelineEvent): AnswerRow => {
    const turnId = ev.turn_id ?? `solo-${ev.id}`;
    let row = answers.get(turnId);
    if (!row) {
      row = { type: "answer", key: `turn-${turnId}`, turnId, items: [], process: [], tools: [] };
      answers.set(turnId, row);
      rows.push(row);
    }
    return row;
  };
  for (const ev of events) {
    if (isMessage(ev)) {
      if (ev.data.role === "user") rows.push({ type: "user", key: ev.key ?? ev.id, event: ev });
      else if (ev.data.role === "system") rows.push({ type: "system", key: ev.key ?? ev.id, event: ev });
      else answer(ev).items.push(ev);
    } else if (isTool(ev)) {
      const row = answer(ev);
      row.items.push(ev);
      row.tools.push(ev);
    } else if (isProcess(ev)) answer(ev).process.push(ev);
    else if (isTurn(ev)) answer(ev).turn = ev;
  }
  return rows;
}

export interface ToolSpan {
  /** 0..1 within the window of all tool calls of the answer */
  offset: number;
  width: number;
  parallel: boolean;
}

/** Position of every tool call on a shared time axis; overlapping calls are marked parallel. */
export function toolSpans(tools: TimelineEvent<ToolData>[], now: number = Date.now()): Map<string, ToolSpan> {
  const timed = tools.filter((t) => typeof t.data.started_ts === "number");
  const out = new Map<string, ToolSpan>();
  if (timed.length === 0) return out;
  const end = (t: TimelineEvent<ToolData>) => t.data.ended_ts ?? (t.data.status === "running" ? now : (t.data.started_ts as number));
  const lo = Math.min(...timed.map((t) => t.data.started_ts as number));
  const hi = Math.max(...timed.map(end), lo + 1);
  for (const t of timed) {
    const s = t.data.started_ts as number;
    const e = end(t);
    const parallel = timed.some((o) => o !== t && (o.data.started_ts as number) < e && s < end(o));
    out.set(t.id, { offset: (s - lo) / (hi - lo), width: Math.max(0.015, (e - s) / (hi - lo)), parallel });
  }
  return out;
}

/** The step to show on the collapsed "Gedankenprozess" bar: the latest one that is not the closing summary. */
export function currentStep(process: TimelineEvent<ProcessData>[]): TimelineEvent<ProcessData> | undefined {
  for (let i = process.length - 1; i >= 0; i -= 1) {
    const p = process[i] as TimelineEvent<ProcessData>;
    if (p.data.step !== "summary") return p;
  }
  return undefined;
}

export const hasModelReasoning = (process: TimelineEvent<ProcessData>[]): boolean =>
  process.some((p) => p.data.source === "model" && Boolean(p.data.text));

export const SOURCE_LABEL: Record<ProcessSource, string> = {
  model: "Modell",
  protocol: "Protokoll",
  summary: "Zusammenfassung",
};

export function isRouteDisplay(d: ToolData["display"]): d is RouteDisplay {
  return Boolean(d && d.kind === "route" && Array.isArray((d as RouteDisplay).routes));
}

// Same rule as the core (kie_creative.resolve_media_file): a bare file name, never a path.
const MEDIA_NAME = /^[A-Za-z0-9][A-Za-z0-9_-]{0,100}\.[a-z0-9]{2,5}$/;
const MEDIA_SUFFIX = { image: /\.(png|jpe?g|webp)$/i, video: /\.(mp4|webm|mov)$/i } as const;

/** A generated image or video is shown only when its attachment is well-formed. */
export function isMediaDisplay(d: ToolData["display"]): d is MediaDisplay {
  if (!d || (d.kind !== "image" && d.kind !== "video")) return false;
  const m = d as MediaDisplay;
  return (
    m.media === (m.kind === "image" ? "images" : "videos") &&
    typeof m.file === "string" &&
    MEDIA_NAME.test(m.file) &&
    MEDIA_SUFFIX[m.kind].test(m.file)
  );
}

const clock = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
/** 12:03:04,120 */
export function formatClockMs(ms: number | undefined): string {
  if (ms === undefined || !Number.isFinite(ms)) return "–";
  return `${clock.format(new Date(ms))},${String(Math.floor(ms % 1000)).padStart(3, "0")}`;
}
