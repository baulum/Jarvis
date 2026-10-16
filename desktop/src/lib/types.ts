// Response and event shapes of the J.A.R.V.I.S. core (core/src/jarvis/api/routes.py).
// Kept in one place so a core change only needs one edit here.

export type Risk = "read" | "low" | "medium" | "high" | "critical";
export type HealthStatus = "ok" | "degraded" | "error" | "not_configured" | "down" | "offline" | string;

export interface KillSwitchState {
  engaged: boolean;
  reason: string;
}

export interface LoadedModel {
  name: string | null;
  size: number | null;
  size_vram: number | null;
  expires_at: string | null;
}

export interface Resources {
  cpu_percent: number;
  memory_total: number;
  memory_used: number;
  memory_percent: number;
  disk_free: number;
  disk_total: number;
  core_rss: number;
  models_loaded: LoadedModel[];
}

export interface UsageRow {
  purpose: string;
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  avg_latency_ms: number;
}

export interface TtsStatus {
  configured?: string;
  active?: string;
  fish_audio_ready?: boolean;
  fallback?: boolean;
  [k: string]: unknown;
}

export interface TelephonyStatus {
  enabled: boolean;
  provider?: string;
  twilio_configured?: boolean;
  missing?: string[];
  pin_configured?: boolean;
  active_calls?: unknown[];
}

export interface McpServer {
  name: string;
  enabled: boolean;
  connected: boolean;
  error: string | null;
  tools: number;
  transport: "http" | "stdio" | string;
  description: string;
}

export type VoiceStateName = "idle" | "listening" | "recording" | "thinking" | "speaking" | string;

/** Voice daemon state as stored by the core (`voice_state`) and sent via `voice.status`. */
export interface VoiceState {
  connected?: boolean;
  state?: VoiceStateName;
  muted?: boolean;
  wake_word?: boolean;
  mic_permission?: string | null;
  level?: number | null;
  detail?: string | null;
  updated_at?: string;
}

export interface SystemStatus {
  version: string;
  uptime_s: number;
  kill_switch: KillSwitchState;
  components: Record<string, HealthStatus>;
  pending_approvals: number;
  open_tasks: number;
  resources?: Resources;
  usage_today?: UsageRow[];
  tts?: TtsStatus;
  telephony?: TelephonyStatus;
  mcp_servers?: McpServer[];
  voice?: VoiceState;
  hermes_restarts?: number;
  agent?: { provider: string; model?: string; active_sessions?: number; standby_ready?: boolean; last_error?: string | null; routing?: AgentModelStatus };
}

export interface ChatSessionInfo {
  id: string;
  title: string;
  channel: string;
  tainted: boolean;
  last_activity_at: string;
  created_at: string;
}

export interface ChatMessageRow {
  id: string;
  role: "user" | "assistant" | "system" | "tool" | string;
  content: string;
  meta: {
    channel?: string;
    tools?: { event: string; name: string | null; ts: string }[];
    provider?: string;
    error?: string | null;
    [k: string]: unknown;
  };
  created_at: string;
}

export interface ChatAccepted {
  session_id: string;
  accepted: true;
}

export type ApprovalStatus = "pending" | "approved" | "rejected" | "executed" | "failed" | "expired" | string;

export interface Approval {
  id: string;
  tool: string;
  summary: string;
  risk: Risk;
  reason: string;
  status: ApprovalStatus;
  args: Record<string, unknown>;
  requested_by: string;
  channel: string;
  created_at: string;
  expires_at: string;
  decided_by: string | null;
  result: Record<string, unknown> | null;
}

export type TaskStatus =
  | "queued"
  | "running"
  | "waiting_approval"
  | "waiting_input"
  | "waiting_external"
  | "completed"
  | "failed"
  | "cancelled"
  | string;

export interface TaskStep {
  idx: number;
  name: string;
  status: string;
  error: string | null;
  output: unknown;
  approval_id: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface Task {
  id: string;
  title: string;
  kind: string;
  status: TaskStatus;
  params: Record<string, unknown>;
  error: string | null;
  question: string | null;
  result: Record<string, unknown> | null;
  attempts: number;
  created_at: string;
  updated_at: string;
  steps: TaskStep[];
}

export interface Playbook {
  name: string;
  title: string;
  description: string;
  params_schema: JsonSchema;
  steps: string[];
}

export interface JsonSchema {
  type?: string | string[];
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
  enum?: unknown[];
  default?: unknown;
  description?: string;
  minimum?: number;
  maximum?: number;
  additionalProperties?: boolean | JsonSchema;
  [k: string]: unknown;
}

export interface ToolInfo {
  name: string;
  description: string;
  connector: string;
  risk: Risk;
  enabled: boolean;
  writes: boolean;
  untrusted_output: boolean;
  parameters: JsonSchema;
}

export interface HermesSkill {
  name?: string;
  title?: string;
  description?: string;
  [k: string]: unknown;
}

export interface ClaudeSkill {
  id: string;
  name: string;
  description: string;
  source: string;
  enabled: boolean;
}

export interface SkillsResponse {
  claude: ClaudeSkill[];
  hermes: HermesSkill[];
  hermes_error: string | null;
  playbooks: { name: string; title: string; description: string }[];
}

export interface ConnectorSecret {
  name: string;
  label: string;
  help: string;
  required: boolean;
}

export interface ConnectorHealth {
  status: HealthStatus;
  message: string;
  checked_at: string | null;
  details: Record<string, unknown>;
}

export interface Connector {
  id: string;
  name: string;
  description: string;
  category: string;
  auth: string;
  version: string;
  docs_url: string;
  setup_instructions: string;
  mcp_note: string;
  settings_schema: JsonSchema;
  secrets: ConnectorSecret[];
  enabled: boolean;
  configured: boolean;
  missing: string[];
  settings: Record<string, unknown>;
  secrets_present: Record<string, boolean>;
  health: ConnectorHealth;
  tools: { name: string; description: string; risk: Risk }[];
}

export interface ConnectorsResponse {
  connectors: Connector[];
  mcp_servers: McpServer[];
}

export interface ConnectorConfigBody {
  enabled?: boolean;
  settings?: Record<string, unknown>;
  secrets?: Record<string, string>;
}

export interface MemorySearchHit {
  id: string;
  title: string;
  kind: string;
  content: string;
  path: string;
  score: number;
  status: string;
  sensitivity: string;
  entities: string[];
  source: Record<string, unknown>;
  updated_at: string;
}

export interface MemoryItemSummary {
  id: string;
  title: string;
  kind: string;
  path: string;
  status: string;
  sensitivity: string;
  preview: string;
  entities: string[];
  updated_at: string;
}

export interface MemoryItemDetail {
  id: string;
  title: string;
  kind: string;
  content: string;
  path: string;
  status: string;
  sensitivity: string;
  entities: string[];
  source: Record<string, unknown>;
  backlinks: string[];
  superseded_by: string | null;
  created_at: string;
  updated_at: string;
  layer?: string;
  aliases?: string[];
  confidence?: number;
  access_count?: number;
  last_accessed_at?: string | null;
  related?: MemoryRelated[];
}

export interface MemoryRelated {
  id: string | null;
  node: string;
  title: string;
  kind: string;
  relation: string;
  direction: "in" | "out";
}

export interface MemoryGraphNode {
  id: string;
  label: string;
  kind: string;
  path: string | null;
}

export interface MemoryGraphEdge {
  source: string;
  target: string;
  relation: string;
}

export interface MemoryGraph {
  nodes: MemoryGraphNode[];
  edges: MemoryGraphEdge[];
}

export interface Schedule {
  id: string;
  name: string;
  cron: string;
  timezone: string;
  action: Record<string, unknown>;
  enabled: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
  last_status: string | null;
}

export interface ScheduleCreate {
  name: string;
  cron: string;
  action: Record<string, unknown>;
}

export interface NotificationRow {
  id: string;
  event: string;
  level: "info" | "warning" | "error" | "critical" | string;
  title: string;
  body: string;
  read: boolean;
  delivered: string[];
  created_at: string;
  data: Record<string, unknown>;
}

export interface LogEntry {
  ts: string;
  level: string;
  logger: string;
  msg: string;
}

export interface RoleInfo {
  description: string;
  max_risk: Risk;
  auto_approve: Risk;
  can_approve: boolean;
}

export interface PermissionsResponse {
  roles: Record<string, RoleInfo>;
  taint_requires_approval: boolean;
  shell_allowlist: string[];
  rate_limits: Record<string, number>;
  tools: { name: string; connector: string; risk: Risk; enabled: boolean; auto_for_owner: boolean }[];
  macos: {
    bridge?: "ok" | "offline" | string;
    accessibility?: unknown;
    screenRecording?: unknown;
    microphone?: unknown;
    kill_switch?: unknown;
    [k: string]: unknown;
  };
}

export interface Device {
  id: string;
  name: string;
  role: string;
  revoked: boolean;
  last_seen_at: string | null;
  created_at: string;
}

export interface CodingJob {
  id: string;
  title: string;
  status: string;
  repo_path: string;
  branch: string | null;
  worktree_path: string | null;
  result: Record<string, unknown> | null;
  test_command: string | null;
  created_at: string;
  finished_at: string | null;
}

/** Compact Claude Code stream event as written to the job log. */
export interface CodingLogLine {
  type: string;
  ts?: string;
  text?: string;
  tool?: string;
  detail?: string;
  subtype?: string;
  is_error?: boolean;
  num_turns?: number;
  duration_ms?: number;
}

export type SettingsResponse = Record<string, unknown> & {
  personality?: Record<string, unknown>;
  voice?: Record<string, unknown>;
  llm?: Record<string, unknown>;
};

/** Envelope of every bus event sent over the WebSocket. */
/** Unified event envelope (core/src/jarvis/events.py): who, what, when, which graph nodes. */
export interface BusEvent<T = Record<string, unknown>> {
  id: string;
  type: string;
  ts: string;
  data: T;
  seq?: number;
  source?: string;
  session_id?: string | null;
  task_id?: string | null;
  nodes?: string[];
}

export interface AgentModelStatus {
  default: "haiku" | "sonnet" | "opus" | string;
  auto_downgrade: boolean;
  session_override: string | null;
  models: { alias: string; id: string; label: string }[];
}

export type ApprovalModeName = "yolo" | "block_dangerous" | "block_everything" | "custom";
export type RiskLevel = "low" | "medium" | "high" | "critical";
export interface ApprovalMode {
  mode: ApprovalModeName;
  custom_levels: Record<RiskLevel, "auto" | "ask">;
  tool_overrides: Record<string, "auto" | "ask" | "deny">;
  taint_strict: boolean;
  effective_levels: Record<RiskLevel, "auto" | "ask">;
}
