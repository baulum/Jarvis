// Global app state (zustand). Bus events are folded in here; views read slices.
import { create } from "zustand";
import { useBrain } from "../holo/store";
import { ApiError, CORE_URL, createApi } from "../lib/api";
import { nativeLog, nativeNotify } from "../lib/native";
import { RealtimeClient, type ConnectionState } from "../lib/realtime";
import {
  applyChatEvent,
  applyToolEvent,
  applyVoiceStatus,
  emptyThread,
  pushBounded,
  type ChatThread,
  type LiveToolCall,
} from "../lib/reducers";
import type { Approval, BusEvent, CodingLogLine, LogEntry, SystemStatus, VoiceState } from "../lib/types";

export type ViewId =
  | "chat"
  | "voice"
  | "tasks"
  | "approvals"
  | "agents"
  | "logs"
  | "memory"
  | "connectors"
  | "skills"
  | "scheduler"
  | "notifications"
  | "settings"
  | "permissions"
  | "system";

export interface Toast {
  id: string;
  tone: "info" | "ok" | "warn" | "bad";
  title: string;
  body?: string;
  action?: { label: string; view: ViewId };
}

/** Event families views can watch to refetch when something relevant changed. */
export type Topic =
  | "task"
  | "approval"
  | "coding"
  | "schedule"
  | "memory"
  | "connector"
  | "notification"
  | "session"
  | "system";

const LIVE_LOG_MAX = 3000;

interface AppState {
  token: string | null;
  tokenError: string | null;
  connection: ConnectionState;
  principal: { label: string; role: string } | null;
  status: SystemStatus | null;
  statusError: string | null;
  greeting: string | null;
  voice: VoiceState;
  view: ViewId;
  threads: Record<string, ChatThread>;
  activeSessionId: string | null;
  liveTools: LiveToolCall[];
  logs: LogEntry[];
  logsPaused: boolean;
  toasts: Toast[];
  pendingApprovals: Approval[];
  versions: Record<Topic, number>;
  taskInputFields: Record<string, string[]>;
  codingLive: Record<string, CodingLogLine[]>;
  /** approval id -> chat session id (the REST approval rows do not carry the session). */
  approvalSessions: Record<string, string>;
  unread: number;
  /** session id -> model chosen for the latest turn (from chat.model events). */
  turnModels: Record<string, { model: string; reason: string }>;

  setView: (v: ViewId) => void;
  setToken: (t: string | null, err?: string | null) => void;
  setActiveSession: (sid: string | null) => void;
  setThread: (t: ChatThread) => void;
  pushToast: (t: Omit<Toast, "id">) => void;
  dismissToast: (id: string) => void;
  setLogsPaused: (p: boolean) => void;
  clearLiveLogs: () => void;
  refreshStatus: () => Promise<void>;
  refreshApprovals: () => Promise<void>;
  refreshUnread: () => Promise<void>;
}

export const useApp = create<AppState>((set, get) => ({
  token: null,
  tokenError: null,
  connection: "idle",
  principal: null,
  status: null,
  statusError: null,
  greeting: null,
  voice: {},
  view: "chat",
  threads: {},
  activeSessionId: null,
  liveTools: [],
  logs: [],
  logsPaused: false,
  toasts: [],
  pendingApprovals: [],
  versions: {
    task: 0,
    approval: 0,
    coding: 0,
    schedule: 0,
    memory: 0,
    connector: 0,
    notification: 0,
    session: 0,
    system: 0,
  },
  taskInputFields: {},
  codingLive: {},
  approvalSessions: {},
  unread: 0,
  turnModels: {},

  // The former views open in the system panel above the brain (toasts and buttons still call setView).
  setView: (view) => {
    set({ view });
    useBrain.getState().openOverlay(view === "chat" ? null : "system", view);
  },
  setToken: (token, err = null) => set({ token, tokenError: err }),
  setActiveSession: (activeSessionId) => set({ activeSessionId }),
  setThread: (t) => set((s) => ({ threads: { ...s.threads, [t.sessionId]: t } })),
  pushToast: (t) => {
    const id = `t-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
    set((s) => ({ toasts: [...s.toasts.slice(-4), { ...t, id }] }));
    setTimeout(() => get().dismissToast(id), t.tone === "bad" ? 9000 : 6000);
  },
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) })),
  setLogsPaused: (logsPaused) => set({ logsPaused }),
  clearLiveLogs: () => set({ logs: [] }),

  refreshStatus: async () => {
    try {
      const status = await api.systemStatus();
      set((s) => ({
        status,
        statusError: null,
        // the REST snapshot is authoritative for connectivity; keep live level from events
        voice: { ...s.voice, ...(status.voice ?? {}), level: s.voice.level ?? status.voice?.level ?? null },
      }));
    } catch (e) {
      set({ statusError: e instanceof ApiError ? e.message : String(e) });
    }
  },
  refreshApprovals: async () => {
    try {
      set({ pendingApprovals: await api.approvals("pending") });
    } catch {
      // surfaced in the approvals view itself
    }
  },
  refreshUnread: async () => {
    try {
      const rows = await api.notifications(true);
      // recover approval -> session links from approval notifications
      const links: Record<string, string> = {};
      for (const n of rows) {
        const aid = n.data?.approval_id;
        const sid = n.data?.session_id;
        if (typeof aid === "string" && typeof sid === "string") links[aid] = sid;
      }
      set((s) => ({ unread: rows.length, approvalSessions: { ...links, ...s.approvalSessions } }));
    } catch {
      // non-critical badge
    }
  },
}));

export const api = createApi({ baseUrl: CORE_URL, getToken: () => useApp.getState().token });

function bump(topic: Topic) {
  useApp.setState((s) => ({ versions: { ...s.versions, [topic]: s.versions[topic] + 1 } }));
}

// Debounce helper so bursts of events trigger one refetch.
const timers = new Map<string, ReturnType<typeof setTimeout>>();
function debounce(key: string, ms: number, fn: () => void) {
  const t = timers.get(key);
  if (t) clearTimeout(t);
  timers.set(
    key,
    setTimeout(() => {
      timers.delete(key);
      fn();
    }, ms),
  );
}

export function handleBusEvent(ev: BusEvent): void {
  useBrain.getState().onEvent(ev); // live layer of the brain (tool calls, recalls, jobs …)
  if (ev.type === "voice.level") return;
  const st = useApp.getState();
  const d = ev.data as Record<string, unknown>;
  const type = ev.type;

  if (type === "chat.model") {
    const sid = typeof d.session_id === "string" ? d.session_id : null;
    if (sid) useApp.setState((s) => ({ turnModels: { ...s.turnModels, [sid]: { model: String(d.model), reason: String(d.reason ?? "") } } }));
    return;
  }
  if (type === "agent.model" || type === "agent.usage" || type === "backup.finished") {
    bump("system");
    return;
  }
  if (type.startsWith("chat.")) {
    const sid = typeof d.session_id === "string" ? d.session_id : null;
    if (sid) {
      const thread = st.threads[sid] ?? emptyThread(sid);
      const next = applyChatEvent(thread, ev);
      if (next !== thread || !st.threads[sid]) st.setThread(next);
    }
    if (type === "chat.done" || type === "chat.user") debounce("sessions", 400, () => bump("session"));
    return;
  }
  if (type === "session.created") {
    bump("session");
    return;
  }
  if (type === "tool.started" || type === "tool.finished") {
    useApp.setState((s) => ({ liveTools: applyToolEvent(s.liveTools, ev) }));
    return;
  }
  if (type === "voice.status") {
    useApp.setState((s) => ({ voice: applyVoiceStatus(s.voice, ev) }));
    return;
  }
  if (type.startsWith("task.")) {
    if (type === "task.input_required" && typeof d.task_id === "string" && Array.isArray(d.fields)) {
      const fields = (d.fields as unknown[]).map(String);
      useApp.setState((s) => ({ taskInputFields: { ...s.taskInputFields, [d.task_id as string]: fields } }));
    }
    bump("task");
    debounce("status", 600, () => void st.refreshStatus());
    return;
  }
  if (type.startsWith("approval.")) {
    bump("approval");
    void st.refreshApprovals();
    debounce("status", 300, () => void st.refreshStatus());
    if (type === "approval.requested") {
      if (typeof d.approval_id === "string" && typeof d.session_id === "string") {
        useApp.setState((s) => ({ approvalSessions: { ...s.approvalSessions, [d.approval_id as string]: d.session_id as string } }));
      }
      st.pushToast({
        tone: "warn",
        title: "Freigabe erforderlich",
        body: String(d.summary ?? d.tool ?? ""),
        action: { label: "Ansehen", view: "approvals" },
      });
    }
    return;
  }
  if (type === "coding.progress" && typeof d.job_id === "string") {
    const line: CodingLogLine = {
      type: String(d.type ?? "event"),
      ts: typeof d.ts === "string" ? d.ts : ev.ts,
      text: typeof d.text === "string" ? d.text : undefined,
      tool: typeof d.tool === "string" ? d.tool : undefined,
      detail: typeof d.detail === "string" ? d.detail : undefined,
    };
    useApp.setState((s) => ({
      codingLive: { ...s.codingLive, [d.job_id as string]: pushBounded(s.codingLive[d.job_id as string] ?? [], line, 500) },
    }));
    return;
  }
  if (type.startsWith("coding.")) {
    bump("coding");
    return;
  }
  if (type.startsWith("schedule.")) {
    bump("schedule");
    return;
  }
  if (type.startsWith("memory.")) {
    debounce("memory", 500, () => bump("memory"));
    return;
  }
  if (type === "connector.health") {
    debounce("connector", 500, () => bump("connector"));
    debounce("status", 800, () => void st.refreshStatus());
    return;
  }
  if (type === "notification") {
    bump("notification");
    useApp.setState((s) => ({ unread: s.unread + 1 }));
    const title = String(d.title ?? "Benachrichtigung");
    const body = String(d.body ?? "");
    const level = String(d.level ?? "info");
    // approval toasts are raised by approval.requested already
    if (d.event !== "approval.requested") {
      st.pushToast({ tone: level === "error" || level === "critical" ? "bad" : level === "warning" ? "warn" : "info", title, body });
    }
    void nativeNotify(title, body).catch(() => undefined);
    return;
  }
  if (type === "system.killswitch") {
    bump("system");
    void st.refreshStatus();
    st.pushToast({
      tone: d.engaged ? "bad" : "ok",
      title: d.engaged ? "Not-Aus aktiviert" : "Not-Aus aufgehoben",
      body: d.engaged ? String(d.reason ?? "") : undefined,
    });
    return;
  }
  if (type.startsWith("telephony.")) {
    debounce("status", 500, () => void st.refreshStatus());
  }
}

export function handleLog(entry: LogEntry): void {
  if (useApp.getState().logsPaused) return;
  useApp.setState((s) => ({ logs: pushBounded(s.logs, entry, LIVE_LOG_MAX) }));
}

let client: RealtimeClient | null = null;

export function startRealtime(): RealtimeClient {
  if (client) return client;
  client = new RealtimeClient({
    baseUrl: CORE_URL,
    logs: true,
    getToken: () => useApp.getState().token,
    onEvent: handleBusEvent,
    onLog: handleLog,
    onState: (connection) => {
      useApp.setState({ connection });
    },
    onOpen: (hello) => {
      useApp.setState({ principal: { label: hello.principal, role: hello.role } });
      nativeLog(`ws connected as role=${hello.role}`);
      // resync anything we may have missed while disconnected
      const st = useApp.getState();
      void st.refreshStatus();
      void st.refreshApprovals();
      void st.refreshUnread();
      (Object.keys(st.versions) as Topic[]).forEach(bump);
    },
  });
  client.start();
  return client;
}
