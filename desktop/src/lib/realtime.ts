// WebSocket client for the core event bus (/ws) with auto-reconnect and exponential backoff.
import type { BusEvent, LogEntry } from "./types";

export type ConnectionState = "idle" | "connecting" | "open" | "reconnecting" | "unauthorized" | "closed";

/** Exponential backoff with full jitter, capped. `random` is injectable for tests. */
export function backoffDelay(attempt: number, baseMs = 500, maxMs = 15_000, random: () => number = Math.random): number {
  const exp = Math.min(maxMs, baseMs * 2 ** Math.max(0, attempt));
  // keep at least half the window so reconnect storms are spread but never instant
  return Math.round(exp / 2 + random() * (exp / 2));
}

export function buildWsUrl(httpBase: string, token: string, logs: boolean): string {
  const u = new URL("/ws", httpBase);
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  u.searchParams.set("token", token);
  if (logs) u.searchParams.set("logs", "1");
  return u.toString();
}

type Incoming =
  | { kind: "event"; event: BusEvent }
  | { kind: "log"; entry: LogEntry }
  | { kind: "hello"; principal: string; role: string }
  | { kind: "pong" }
  | { kind: "unknown" };

/** Classifies one raw frame from the socket. Pure so it can be unit-tested. */
export function parseFrame(raw: string): Incoming {
  let msg: unknown;
  try {
    msg = JSON.parse(raw);
  } catch {
    return { kind: "unknown" };
  }
  if (!msg || typeof msg !== "object" || !("type" in msg)) return { kind: "unknown" };
  const m = msg as { type: unknown; data?: unknown; id?: unknown; ts?: unknown };
  if (m.type === "pong") return { kind: "pong" };
  if (m.type === "hello") {
    const d = (m.data ?? {}) as { principal?: string; role?: string };
    return { kind: "hello", principal: d.principal ?? "", role: d.role ?? "" };
  }
  if (m.type === "log") return { kind: "log", entry: m.data as LogEntry };
  if (typeof m.type === "string" && typeof m.id === "string") {
    // the envelope (seq, source, session/task attribution, graph nodes) travels next to the data
    const env = msg as { seq?: unknown; source?: unknown; session_id?: unknown; task_id?: unknown; nodes?: unknown };
    const event: BusEvent = {
      id: m.id,
      type: m.type,
      ts: typeof m.ts === "string" ? m.ts : new Date().toISOString(),
      data: (m.data ?? {}) as Record<string, unknown>,
    };
    if (typeof env.seq === "number") event.seq = env.seq;
    if (typeof env.source === "string") event.source = env.source;
    if (typeof env.session_id === "string") event.session_id = env.session_id;
    if (typeof env.task_id === "string") event.task_id = env.task_id;
    if (Array.isArray(env.nodes)) event.nodes = env.nodes.filter((n): n is string => typeof n === "string");
    return { kind: "event", event };
  }
  return { kind: "unknown" };
}

export interface RealtimeOptions {
  baseUrl: string;
  getToken: () => string | null;
  logs?: boolean;
  onEvent: (ev: BusEvent) => void;
  onLog?: (entry: LogEntry) => void;
  onState?: (state: ConnectionState, info?: { attempt: number; nextInMs?: number }) => void;
  /** Called after every successful (re)connect so views can resync missed state. */
  onOpen?: (hello: { principal: string; role: string }) => void;
  WebSocketImpl?: typeof WebSocket;
  pingIntervalMs?: number;
  staleAfterMs?: number;
}

export class RealtimeClient {
  private ws: WebSocket | null = null;
  private attempt = 0;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private lastFrameAt = 0;
  private stopped = true;
  private readonly opts: RealtimeOptions;

  constructor(opts: RealtimeOptions) {
    this.opts = opts;
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    this.clearTimers();
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
    this.setState("closed");
  }

  /** Forces a fresh connection immediately (e.g. after the token was reloaded). */
  reconnectNow(): void {
    this.attempt = 0;
    this.clearTimers();
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
    if (!this.stopped) this.connect();
  }

  private setState(state: ConnectionState, info?: { attempt: number; nextInMs?: number }) {
    this.opts.onState?.(state, info);
  }

  private clearTimers() {
    if (this.retryTimer) clearTimeout(this.retryTimer);
    if (this.pingTimer) clearInterval(this.pingTimer);
    this.retryTimer = null;
    this.pingTimer = null;
  }

  private connect() {
    const token = this.opts.getToken();
    if (!token) {
      this.setState("unauthorized", { attempt: this.attempt });
      this.scheduleRetry();
      return;
    }
    this.setState(this.attempt === 0 ? "connecting" : "reconnecting", { attempt: this.attempt });
    const Impl = this.opts.WebSocketImpl ?? WebSocket;
    let ws: WebSocket;
    try {
      ws = new Impl(buildWsUrl(this.opts.baseUrl, token, this.opts.logs ?? false));
    } catch {
      this.scheduleRetry();
      return;
    }
    this.ws = ws;
    ws.onmessage = (msg: MessageEvent) => {
      this.lastFrameAt = Date.now();
      const frame = parseFrame(typeof msg.data === "string" ? msg.data : "");
      switch (frame.kind) {
        case "hello":
          this.attempt = 0;
          this.setState("open", { attempt: 0 });
          this.opts.onOpen?.({ principal: frame.principal, role: frame.role });
          break;
        case "event":
          this.opts.onEvent(frame.event);
          break;
        case "log":
          this.opts.onLog?.(frame.entry);
          break;
        default:
          break;
      }
    };
    ws.onopen = () => {
      this.lastFrameAt = Date.now();
      this.startPing();
    };
    ws.onclose = (ev: CloseEvent) => {
      this.ws = null;
      this.clearTimers();
      if (this.stopped) return;
      if (ev.code === 4401) this.setState("unauthorized", { attempt: this.attempt });
      this.scheduleRetry(ev.code === 4401);
    };
    ws.onerror = () => {
      // onclose follows and handles the retry
    };
  }

  private startPing() {
    const every = this.opts.pingIntervalMs ?? 20_000;
    const staleAfter = this.opts.staleAfterMs ?? 60_000;
    this.pingTimer = setInterval(() => {
      const ws = this.ws;
      if (!ws || ws.readyState !== 1) return;
      if (Date.now() - this.lastFrameAt > staleAfter) {
        ws.close(); // half-open connection; onclose reconnects
        return;
      }
      ws.send("ping");
    }, every);
  }

  private scheduleRetry(slow = false) {
    if (this.stopped) return;
    const delay = slow ? 15_000 : backoffDelay(this.attempt);
    this.attempt += 1;
    if (!slow) this.setState("reconnecting", { attempt: this.attempt, nextInMs: delay });
    this.retryTimer = setTimeout(() => this.connect(), delay);
  }
}
