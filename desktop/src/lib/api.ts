// Typed REST client for the J.A.R.V.I.S. core. Every endpoint the desktop uses lives here.
import type { GraphDTO } from "../holo/graph/types";
import type {
  ApprovalMode,
  AgentModelStatus,
  Approval,
  ChatAccepted,
  ChatMessageRow,
  ChatSessionInfo,
  CodingJob,
  CodingLogLine,
  ConnectorConfigBody,
  Connector,
  ConnectorHealth,
  ConnectorsResponse,
  Device,
  KillSwitchState,
  LogEntry,
  MemoryGraph,
  MemoryItemDetail,
  MemoryItemSummary,
  MemorySearchHit,
  NotificationRow,
  PermissionsResponse,
  Playbook,
  Schedule,
  ScheduleCreate,
  SettingsResponse,
  SkillsResponse,
  SystemStatus,
  Task,
  ToolInfo,
} from "./types";

export const CORE_URL = "http://127.0.0.1:8765";

export class ApiError extends Error {
  readonly status: number;
  readonly path: string;

  constructor(status: number, path: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.path = path;
  }

  /** True when the core could not be reached at all (network error, core stopped). */
  get offline(): boolean {
    return this.status === 0;
  }
}

/** Turns FastAPI error bodies ({detail: string | [{msg}]}) into one readable German line. */
export function describeErrorBody(status: number, body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const msgs = detail
        .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : ""))
        .filter(Boolean);
      if (msgs.length) return msgs.join("; ");
    }
  }
  switch (status) {
    case 401:
      return "Nicht autorisiert – das Geräte-Token wurde abgelehnt.";
    case 403:
      return "Für diese Aktion fehlt die Berechtigung.";
    case 404:
      return "Nicht gefunden.";
    case 429:
      return "Zu viele Anfragen – bitte kurz warten.";
    default:
      return `Anfrage fehlgeschlagen (HTTP ${status}).`;
  }
}

type Fetch = typeof fetch;

export interface ApiOptions {
  baseUrl?: string;
  getToken: () => string | null;
  fetchImpl?: Fetch;
  timeoutMs?: number;
}

type Query = Record<string, string | number | boolean | null | undefined>;

export function buildUrl(baseUrl: string, path: string, query?: Query): string {
  const url = new URL(`/api${path}`, baseUrl);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
    }
  }
  return url.toString();
}

export function createApi(opts: ApiOptions) {
  const baseUrl = opts.baseUrl ?? CORE_URL;
  const doFetch: Fetch = opts.fetchImpl ?? ((...args) => fetch(...args));
  const timeoutMs = opts.timeoutMs ?? 20_000;

  async function raw(method: string, path: string, body?: unknown, query?: Query): Promise<Response> {
    const token = opts.getToken();
    if (!token) throw new ApiError(401, path, "Kein Geräte-Token geladen.");
    const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    let res: Response;
    try {
      res = await doFetch(buildUrl(baseUrl, path, query), {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: ctrl.signal,
      });
    } catch (e) {
      const aborted = e instanceof DOMException && e.name === "AbortError";
      throw new ApiError(0, path, aborted ? "Zeitüberschreitung – der Core antwortet nicht." : "Core nicht erreichbar.");
    } finally {
      clearTimeout(timer);
    }
    if (!res.ok) {
      let parsed: unknown;
      try {
        parsed = await res.json();
      } catch {
        parsed = null;
      }
      throw new ApiError(res.status, path, describeErrorBody(res.status, parsed));
    }
    return res;
  }

  async function json<T>(method: string, path: string, body?: unknown, query?: Query): Promise<T> {
    const res = await raw(method, path, body, query);
    return (await res.json()) as T;
  }

  const get = <T>(path: string, query?: Query) => json<T>("GET", path, undefined, query);
  const post = <T>(path: string, body: unknown = {}) => json<T>("POST", path, body);
  const del = <T>(path: string) => json<T>("DELETE", path);
  const put = <T>(path: string, body: unknown = {}) => json<T>("PUT", path, body);
  const id = encodeURIComponent;

  return {
    // system
    health: () => get<{ status: string; version: string }>("/health"),
    systemStatus: () => get<SystemStatus>("/system/status"),
    greeting: () => get<{ text: string }>("/system/greeting"),
    setKillSwitch: (engaged: boolean, reason = "") =>
      post<KillSwitchState>("/system/killswitch", { engaged, reason }),

    // chat
    sendChat: (message: string, sessionId?: string | null) =>
      post<ChatAccepted>("/chat", sessionId ? { message, session_id: sessionId } : { message }),
    interruptChat: () => post<{ interrupted: boolean }>("/chat/interrupt"),
    sessions: (limit = 40) => get<ChatSessionInfo[]>("/sessions", { limit }),
    sessionMessages: (sid: string) => get<ChatMessageRow[]>(`/sessions/${id(sid)}/messages`),

    // approvals
    approvals: (status: "pending" | "all" | string = "pending") => get<Approval[]>("/approvals", { status }),
    approvalMode: () => get<ApprovalMode>("/approvals/mode"),
    setApprovalMode: (body: Partial<Pick<ApprovalMode, "mode" | "custom_levels" | "tool_overrides" | "taint_strict">>) =>
      put<ApprovalMode>("/approvals/mode", body),
    decideApproval: (aid: string, approve: boolean, note?: string) =>
      post<{ status: string; result?: unknown }>(`/approvals/${id(aid)}`, { approve, note: note || null }),

    // tasks
    tasks: (status?: string) => get<Task[]>("/tasks", { status }),
    task: (tid: string) => get<Task>(`/tasks/${id(tid)}`),
    telephonyCallMe: () => post<{ call_id?: string; status?: string }>("/telephony/call-me"),
    taskInput: (tid: string, values: Record<string, unknown>) => post<Task>(`/tasks/${id(tid)}/input`, { values }),
    taskCancel: (tid: string) => post<{ cancelled: boolean }>(`/tasks/${id(tid)}/cancel`),
    taskRetry: (tid: string) => post<{ retried: boolean }>(`/tasks/${id(tid)}/retry`),
    playbooks: () => get<Playbook[]>("/playbooks"),

    // tools / skills / connectors
    tools: () => get<ToolInfo[]>("/tools"),
    skills: () => get<SkillsResponse>("/skills"),
    toggleClaudeSkill: (skillId: string, enabled: boolean) =>
      post<{ id: string; enabled: boolean; active: number }>("/skills/claude", { id: skillId, enabled }),
    connectors: () => get<ConnectorsResponse>("/connectors"),
    configureConnector: (cid: string, body: ConnectorConfigBody) => post<Connector>(`/connectors/${id(cid)}`, body),
    testConnector: (cid: string) => post<ConnectorHealth>(`/connectors/${id(cid)}/test`),
    telegramPair: () => post<{ code: string; instructions: string }>("/connectors/telegram/pair"),
    googleOAuthStart: () => post<{ auth_url: string }>("/connectors/google/oauth/start"),

    // memory
    memorySearch: (q: string, limit = 12) => get<MemorySearchHit[]>("/memory/search", { q, limit }),
    memoryItems: (query: { kind?: string; status?: string; q?: string; limit?: number; offset?: number } = {}) =>
      get<{ total: number; items: MemoryItemSummary[] }>("/memory/items", query),
    memoryItem: (mid: string) => get<MemoryItemDetail>(`/memory/items/${id(mid)}`),
    memoryPendingDecide: (mid: string, approve: boolean) =>
      post<Record<string, unknown>>(`/memory/pending/${id(mid)}`, { approve }),
    memoryGraph: () => get<MemoryGraph>("/memory/graph"),
    graph: (limit = 1500) => get<GraphDTO>("/graph", { limit }),
    graphCluster: (kind: string, offset: number) =>
      get<Pick<GraphDTO, "nodes" | "edges">>(`/graph/cluster/${id(kind)}`, { offset }),
    memoryEdit: (mid: string, body: { title?: string; content?: string; kind?: string }) =>
      put<{ status: string }>(`/memory/items/${id(mid)}`, body),
    memoryDelete: (mid: string) => del<{ status: string }>(`/memory/items/${id(mid)}`),
    voiceMetrics: (limit = 20) => get<Record<string, unknown>[]>("/voice/metrics", { limit }),
    memoryExport: async (): Promise<Uint8Array> => {
      const res = await raw("GET", "/memory/export");
      return new Uint8Array(await res.arrayBuffer());
    },

    // scheduler
    schedules: () => get<Schedule[]>("/schedules"),
    createSchedule: (body: ScheduleCreate) => post<{ id: string }>("/schedules", body),
    setScheduleEnabled: (sid: string, enabled: boolean) =>
      post<{ ok: boolean }>(`/schedules/${id(sid)}/enabled`, { approve: enabled }),
    runSchedule: (sid: string) => post<{ started: boolean }>(`/schedules/${id(sid)}/run`),
    deleteSchedule: (sid: string) => del<{ deleted: boolean }>(`/schedules/${id(sid)}`),

    // notifications
    notifications: (unread = false) => get<NotificationRow[]>("/notifications", { unread: unread || undefined }),
    markNotificationsRead: (ids?: string[]) => post<{ ok: boolean }>("/notifications/read", { ids: ids ?? null }),

    // logs
    logs: (limit = 500, level?: string) => get<LogEntry[]>("/logs", { limit, level }),

    // permissions / devices / settings
    permissions: () => get<PermissionsResponse>("/permissions"),
    requestMacPermission: (kind: "accessibility" | "screenRecording") =>
      post<Record<string, unknown>>(`/permissions/macos/${kind}`),
    devices: () => get<Device[]>("/devices"),
    createDevice: (name: string, role: string) => post<{ id: string; token: string }>("/devices", { name, role }),
    revokeDevice: (did: string) => del<{ revoked: boolean }>(`/devices/${id(did)}`),
    settings: () => get<SettingsResponse>("/settings"),

    // voice
    voiceCommand: (action: "mute" | "unmute" | "ptt_start" | "ptt_stop" | "interrupt") =>
      post<{ ok: boolean }>("/voice/command", { action }),

    // coding
    codingJobs: () => get<CodingJob[]>("/coding/jobs"),
    codingLog: (jid: string, lines = 400) => get<CodingLogLine[]>(`/coding/jobs/${id(jid)}/log`, { lines }),
    agentModel: (sessionId?: string | null) => get<AgentModelStatus>("/agent/model", { session_id: sessionId ?? undefined }),
    setAgentModel: (body: { model: string | null; scope: "default" | "session"; session_id?: string | null; auto_downgrade?: boolean }) =>
      post<AgentModelStatus>("/agent/model", body),
    codingCancel: (jid: string) => post<{ cancelled: boolean }>(`/coding/jobs/${id(jid)}/cancel`),
  };
}

export type Api = ReturnType<typeof createApi>;
