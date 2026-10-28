// Typed REST client for the J.A.R.V.I.S. core. Every endpoint the desktop uses lives here.
import type { ActivitySnapshot } from "../holo/graph/live";
import type { GraphDTO } from "../holo/graph/types";
import type {
  ApprovalMode,
  AppInstallLink,
  AppUpdateRelease,
  AppUpdateStatus,
  AgentModelStatus,
  AgentUsage,
  BackupStatus,
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
  AgentMessageResult,
  AgentPairing,
  Device,
  DeviceAgent,
  JobMonitor,
  KillSwitchState,
  LogEntry,
  MemoryGraph,
  MemoryItemDetail,
  MemoryItemSummary,
  MemorySearchHit,
  NotificationRow,
  PermissionsResponse,
  PersonaStatus,
  Playbook,
  Schedule,
  ScheduleCreate,
  SettingsResponse,
  SkillsResponse,
  SystemCapabilities,
  SystemStatus,
  Task,
  ToolInfo,
  WakeupResult,
  WakeupSettings,
} from "./types";
import type { AttachmentInfo } from "./attachments";
import type {
  WaChat,
  WaConversation,
  WaFile,
  WaLocations,
  WaSend,
  WaSendResult,
  WaStatus,
  WaSuggestion,
} from "./whatsapp";
import type { Platform, PlatformStatus, Publication, SocialSettings, SocialStatus, SocialVideo, SocialVideoDetail } from "./social";
import type { DesktopBuild } from "./build";
import type { SlashCommand } from "./slash";
import type { TimelineEvent } from "./timeline";

/** Files the chat shows: generated images/videos and image attachments (by bare file name only). */
export type ChatFileKind = "images" | "videos" | "attachments";

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
  /** Read on every request: the Core address can change after start (remote Core, browser client). */
  getBaseUrl?: () => string;
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

  async function raw(method: string, path: string, body?: unknown, query?: Query, waitMs = timeoutMs): Promise<Response> {
    const token = opts.getToken();
    if (!token) throw new ApiError(401, path, "Kein Geräte-Token geladen.");
    const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
    // a Blob (file upload) goes out as raw bytes, everything else as JSON
    const binary = body instanceof Blob;
    if (body !== undefined) headers["Content-Type"] = binary ? "application/octet-stream" : "application/json";
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), waitMs);
    let res: Response;
    try {
      res = await doFetch(buildUrl(opts.getBaseUrl?.() ?? baseUrl, path, query), {
        method,
        headers,
        body: body === undefined ? undefined : binary ? body : JSON.stringify(body),
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
    /** Whether this app is older than the UI code the core runs from (see lib/build.ts). */
    desktopBuild: (commit: string) => get<DesktopBuild>("/system/desktop-build", { commit }),
    greeting: () => get<{ text: string }>("/system/greeting"),
    setKillSwitch: (engaged: boolean, reason = "") =>
      post<KillSwitchState>("/system/killswitch", { engaged, reason }),

    // chat
    sendChat: (message: string, sessionId?: string | null, attachments: AttachmentInfo[] = []) =>
      post<ChatAccepted>("/chat", {
        message,
        ...(sessionId ? { session_id: sessionId } : {}),
        ...(attachments.length ? { attachments: attachments.map((a) => ({ file: a.file, name: a.name })) } : {}),
      }),
    /** One attachment as raw bytes; the core checks type, size and signature and returns its stored name. */
    uploadAttachment: async (file: Blob, name: string): Promise<AttachmentInfo> =>
      (await (await raw("POST", "/attachments", file, { name }, 120_000)).json()) as AttachmentInfo,
    interruptChat: () => post<{ interrupted: boolean }>("/chat/interrupt"),
    sessions: (limit = 40) => get<ChatSessionInfo[]>("/sessions", { limit }),
    sessionMessages: (sid: string) => get<ChatMessageRow[]>(`/sessions/${id(sid)}/messages`),
    /** Chronological timeline (messages, tool calls, process steps), newest `turns` exchanges. */
    sessionEvents: (sid: string, opts: { turns?: number; beforeSeq?: number } = {}) =>
      get<{ events: TimelineEvent[]; has_more: boolean }>(`/sessions/${id(sid)}/events`, {
        turns: opts.turns,
        before_seq: opts.beforeSeq,
      }),
    setCheck: (sid: string, eventId: string, index: number, checked: boolean) =>
      post<{ ok: boolean }>(`/sessions/${id(sid)}/events/${id(eventId)}/check`, { index, checked }),
    /** A generated image/video or an image attachment (authenticated, so it is loaded as a Blob, not via <img src>). */
    media: async (kind: ChatFileKind, file: string): Promise<Blob> =>
      (await raw("GET", `/media/${kind}/${id(file)}`, undefined, undefined, 120_000)).blob(),
    /** Shows the file in the Core host's file manager (Finder `open -R`, Explorer, …) after the same name-only check. */
    revealMedia: (kind: ChatFileKind, file: string) => post<{ ok: boolean }>(`/media/${kind}/${id(file)}/reveal`),

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
    /** Live brain resync: running work, filtered activity steps since ``since``, tool statistics. */
    activity: (since: number, boot: string | null) =>
      get<ActivitySnapshot>("/activity", { since, boot: boot ?? undefined }),
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
    /** What the Core host can do (capability flags with reasons) and the connected device agents. */
    capabilities: (refresh = false) => get<SystemCapabilities>("/system/capabilities", { refresh }),
    deviceAgents: () => get<DeviceAgent[]>("/devices/agents"),
    createAgentPairing: (name: string) => post<AgentPairing>("/devices/agents/pairing", { name }),
    revokeAgent: (did: string) => del<{ revoked: boolean }>(`/devices/agents/${id(did)}`),
    deviceCommand: (device: string, action: string, args: Record<string, unknown> = {}) =>
      post<{ ok: boolean; data: unknown }>(`/devices/agents/${id(device)}/command`, { action, args }),
    createDevice: (name: string, role: string) => post<{ id: string; token: string }>("/devices", { name, role }),
    revokeDevice: (did: string) => del<{ revoked: boolean }>(`/devices/${id(did)}`),
    settings: () => get<SettingsResponse>("/settings"),

    // voice
    voiceCommand: (action: "mute" | "unmute" | "ptt_start" | "ptt_stop" | "interrupt") =>
      post<{ ok: boolean }>("/voice/command", { action }),
    wakeup: () => get<WakeupSettings>("/voice/wakeup"),
    setWakeup: (body: Partial<Pick<WakeupSettings, "enabled" | "sound" | "volume" | "apps">>) =>
      put<WakeupSettings>("/voice/wakeup", body),
    /** Runs the routine now (programs + MP3) – the "Testen" button. */
    runWakeup: () => post<WakeupResult>("/voice/wakeup", { play_sound: true }),

    // personality (Jarvis / René Redo …): prompt + voice, switched without a restart
    persona: () => get<PersonaStatus>("/persona"),
    setPersona: (personaId: string) => post<PersonaStatus>("/persona", { id: personaId }),
    /** Hörprobe of a persona in its voice (WAV) – switches nothing. Synthesis can take a few seconds. */
    personaPreview: async (personaId: string): Promise<Blob> =>
      (await raw("POST", "/persona/preview", { id: personaId }, undefined, 60_000)).blob(),

    // coding
    codingJobs: () => get<CodingJob[]>("/coding/jobs"),
    codingLog: (jid: string, lines = 400) => get<CodingLogLine[]>(`/coding/jobs/${id(jid)}/log`, { lines }),
    agentModel: (sessionId?: string | null) => get<AgentModelStatus>("/agent/model", { session_id: sessionId ?? undefined }),
    setAgentModel: (body: { model: string | null; scope: "default" | "session"; session_id?: string | null; auto_downgrade?: boolean; effort?: string }) =>
      post<AgentModelStatus>("/agent/model", body),
    agentUsage: () => get<AgentUsage>("/agent/usage"),
    refreshAgentUsage: () => post<AgentUsage>("/agent/usage/refresh"),
    // OTA updates of the Android app (built, signed and handed to the phones by the core)
    appUpdate: {
      status: () => get<AppUpdateStatus>("/mobile/app-update/status"),
      /** Looks for app changes now; builds only if needed. */
      check: () => post<AppUpdateStatus>("/mobile/app-update/check"),
      /** Builds the branch head now (also: retry after a failure). */
      build: () => post<AppUpdateStatus>("/mobile/app-update/build"),
      distribute: () => post<{ sent: number; message: string }>("/mobile/app-update/distribute"),
      withdraw: (code: number, withdrawn: boolean, reason?: string) =>
        post<AppUpdateRelease>(`/mobile/app-update/releases/${code}/withdraw`, { withdrawn, reason: reason ?? null }),
      /** Recovery: the old release's commit again, with a new (higher) versionCode. */
      rebuild: (code: number) => post<AppUpdateStatus>(`/mobile/app-update/releases/${code}/rebuild`),
      installLink: () => post<AppInstallLink>("/mobile/app-update/install-link"),
    },
    backupStatus: () => get<BackupStatus>("/backup"),
    runBackup: () => post<BackupStatus["last"]>("/backup/run"),
    codingCancel: (jid: string) => post<{ cancelled: boolean }>(`/coding/jobs/${id(jid)}/cancel`),
    codingMonitor: (jid: string) => get<JobMonitor>(`/coding/jobs/${id(jid)}/monitor`),
    codingPause: (jid: string) => post<{ paused: boolean }>(`/coding/jobs/${id(jid)}/pause`),
    codingResume: (jid: string) => post<{ resumed: boolean }>(`/coding/jobs/${id(jid)}/resume`),
    codingMessage: (jid: string, text: string, immediate = false) =>
      post<AgentMessageResult>(`/coding/jobs/${id(jid)}/message`, { text, immediate }),
    taskPause: (tid: string) => post<{ paused: boolean }>(`/tasks/${id(tid)}/pause`),
    taskResume: (tid: string) => post<{ resumed: boolean }>(`/tasks/${id(tid)}/resume`),
    taskMessage: (tid: string, text: string, immediate = false) =>
      post<AgentMessageResult>(`/tasks/${id(tid)}/message`, { text, immediate }),
    commands: () => get<{ version: number; commands: SlashCommand[] }>("/commands"),

    // social media (daily PJE Systems video; the library lives on the Core, the MP4 comes over the API)
    social: {
      status: () => get<SocialStatus>("/social/status"),
      checkDependencies: () => post<SocialStatus["dependencies"]>("/social/dependencies/check"),
      videos: (limit = 60) => get<{ videos: SocialVideo[]; has_more: boolean }>("/social/videos", { limit }),
      video: (vid: string) => get<SocialVideoDetail>(`/social/videos/${id(vid)}`),
      /** The MP4 as a Blob (authenticated; the webview only plays blob: URLs). */
      file: async (vid: string, download = false) =>
        (await raw("GET", `/social/videos/${id(vid)}/file`, undefined, download ? { download: true } : undefined, 300_000)).blob(),
      link: (vid: string, hours = 24) => post<{ path: string; url: string | null; hours: number }>(`/social/videos/${id(vid)}/link`, { hours }),
      produce: (hint = "") => post<{ task_id: string; title: string }>("/social/produce", { hint }),
      regenerate: (vid: string, hint = "") => post<{ task_id: string }>(`/social/videos/${id(vid)}/regenerate`, { hint }),
      email: (vid: string, to?: string) => post<{ status: string; summary?: string }>(`/social/videos/${id(vid)}/email`, to ? { to } : {}),
      publish: (vid: string, platform: Platform) =>
        post<{ status: string; summary?: string; data?: { url?: string } }>(`/social/videos/${id(vid)}/publish`, { platform }),
      markManual: (vid: string, platform: Platform, postUrl: string | null) =>
        post<Publication>(`/social/videos/${id(vid)}/publications/${platform}/manual`, { post_url: postUrl }),
      settings: () => get<SocialSettings>("/social/settings"),
      saveSettings: (
        body: Partial<Omit<SocialSettings, "schedule" | "owner_email" | "platforms">> & {
          platforms?: Partial<Record<Platform, { autopost: boolean }>>;
          daily_enabled?: boolean;
          production_time?: string;
        },
      ) =>
        put<SocialSettings>("/social/settings", body),
      platforms: () => get<PlatformStatus[]>("/social/platforms"),
      oauthStart: (platform: Platform) => post<{ auth_url: string }>(`/social/oauth/${platform}/start`),
      disconnect: (platform: Platform) => post<{ ok: boolean }>(`/social/platforms/${platform}/disconnect`, { confirm: true }),
    },
    // whatsapp (one central connection on the Core; every client sees the same chats)
    whatsapp: {
      status: () => get<WaStatus>("/whatsapp/status"),
      connect: (method: "qr" | "pairing", phone?: string) =>
        post<{ started: boolean; pairing_code: string | null }>("/whatsapp/connect", { method, phone: phone || null }),
      disconnect: () => post<{ ok: boolean }>("/whatsapp/disconnect"),
      logout: () => post<{ ok: boolean }>("/whatsapp/logout"),
      chats: (q = "", limit = 200) => get<WaChat[]>("/whatsapp/chats", { q, limit }),
      messages: (jid: string, before?: string, limit = 60) =>
        get<WaConversation>(`/whatsapp/chats/${id(jid)}/messages`, { before, limit }),
      markRead: (jid: string) => post<WaSendResult>(`/whatsapp/chats/${id(jid)}/read`),
      thumbnail: async (jid: string, msgId: string): Promise<Blob> =>
        (await raw("GET", `/whatsapp/chats/${id(jid)}/messages/${id(msgId)}/thumbnail`)).blob(),
      download: (jid: string, msgId: string) =>
        json<WaSendResult>("POST", `/whatsapp/chats/${id(jid)}/messages/${id(msgId)}/download`, {}, undefined),
      loadHistory: (jid: string) => post<{ requested: boolean; note?: string }>(`/whatsapp/chats/${id(jid)}/history`),
      suggest: (jid: string) => post<{ created: WaSuggestion[] }>(`/whatsapp/chats/${id(jid)}/suggest`),
      forgetChat: (jid: string) => del<{ files: number }>(`/whatsapp/chats/${id(jid)}`),
      files: (query: { q?: string; chat?: string; type?: string; limit?: number } = {}) => get<WaFile[]>("/whatsapp/files", query),
      fileContent: async (fileId: string, download = false): Promise<Blob> =>
        (await raw("GET", `/whatsapp/files/${id(fileId)}/content`, undefined, download ? { download: true } : undefined, 300_000)).blob(),
      saveFile: (fileId: string, body: { destination: string; device?: string; allow_outside?: boolean }) =>
        post<WaSendResult>(`/whatsapp/files/${id(fileId)}/save`, body),
      locations: () => get<WaLocations>("/whatsapp/locations"),
      send: (body: { chat: string; text?: string; files?: string[]; caption?: string; as_document?: boolean; reply_to?: string; force?: boolean }) =>
        post<WaSendResult>("/whatsapp/send", body),
      sends: (limit = 50) => get<WaSend[]>("/whatsapp/sends", { limit }),
      suggestions: (status: "pending" | "all" = "pending") => get<WaSuggestion[]>("/whatsapp/suggestions", { status }),
      decideSuggestion: (sid: string, decision: "accept" | "dismiss") =>
        post<{ status: string }>(`/whatsapp/suggestions/${id(sid)}`, { decision }),
      purge: (olderThanDays: number) => post<{ files: number }>("/whatsapp/purge", { older_than_days: olderThanDays }),
      /** URL + token for the upload with progress (XMLHttpRequest, see lib/whatsapp.ts). */
      uploadTarget: (name: string) => ({ url: buildUrl(opts.getBaseUrl?.() ?? baseUrl, "/whatsapp/uploads", { name }), token: opts.getToken() }),
    },
  };
}

export type Api = ReturnType<typeof createApi>;
