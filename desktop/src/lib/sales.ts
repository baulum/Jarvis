// Kundensuche (PJE-Sales-AI Lead-Tool in Jarvis): types of /api/sales and the pure helpers the view uses.
// The Core reads the Lead-Tool through its MCP server; redraft and new campaigns run through the Core's approvals.
// Approving and sending a draft stay in the Lead-Tool itself (see docs/adr/0002-kundensuche-lead-tool-in-jarvis.md).
import type { Tone } from "./format";

export type StageKey = "found" | "check" | "prequal" | "research" | "qualified" | "draft" | "review" | "contact" | "out" | "error";

export interface SalesOverview {
  outreach_mode: string | null;
  review: number;
  outbox_preview: number;
  inbox: number;
  campaigns_by_status: Record<string, number>;
  workers: { name: string | null; online: boolean; last_seen: string | null }[];
}

export interface SalesStatus {
  configured: boolean;
  enabled: boolean;
  connected: boolean;
  error: string | null;
  web_url: string | null;
  capabilities: { read: boolean; redraft: boolean; create_campaign: boolean; outbox_list: boolean };
  overview: SalesOverview | null;
}

export interface SalesStatusOption {
  id: string;
  label: string;
  stage: StageKey | null;
}

export interface SalesCampaign {
  id: string;
  name: string | null;
  status: string;
  status_label: string;
  active: boolean;
  region: string | null;
  industry: string | null;
  target_count: number | null;
  sources: string[];
  last_error: string | null;
  created_at: string | null;
  leads_by_status: Record<string, number>;
  lead_count: number;
  in_review: number;
}

export interface SalesSourceRun {
  source: string | null;
  status: string | null;
  hits: number | null;
  new_companies: number | null;
  merged: number | null;
  duration_ms: number | null;
  error: string | null;
  started_at: string | null;
}

export interface SalesCampaignDetail extends SalesCampaign {
  channel: string | null;
  top_n: number | null;
  budget_usd: number | null;
  cost_usd: number | null;
  source_runs: SalesSourceRun[];
}

export interface SalesLead {
  id: string;
  status: string;
  status_label: string;
  stage: StageKey | null;
  last_error: string | null;
  campaign_id: string | null;
  campaign_name: string | null;
  name: string | null;
  website: string | null;
  phone: string | null;
  email: string | null;
  address: string;
  score: number | null;
  priority: string | null;
}

export interface SalesLeadPage {
  total: number;
  offset: number;
  count: number;
  more: number;
  leads: SalesLead[];
}

export interface SalesDraft {
  subject: string | null;
  body: string | null;
  channel: string | null;
  iteration: number | null;
  evidence_ids: string[];
}

export interface SalesEvidence {
  id: string | null;
  kind: string | null;
  key: string | null;
  claim: string | null;
  source_url: string | null;
  confidence: number | null;
}

export interface SalesLeadDetail {
  id: string;
  campaign_name: string | null;
  status: string;
  status_label: string;
  stage: StageKey | null;
  last_error: string | null;
  company: {
    name: string | null;
    industry: string | null;
    website: string | null;
    phone: string | null;
    email: string | null;
    address: string;
  };
  score: { value: number | null; priority: string | null; version: string | null } | null;
  draft: SalesDraft | null;
  evidence: SalesEvidence[];
  history: { from: string | null; to: string | null; from_label: string | null; to_label: string; reason: string | null; at: string | null }[];
  can_redraft: boolean;
}

export interface SalesReviewItem {
  id: string;
  name: string | null;
  campaign_name: string | null;
  score: number | null;
  since: string | null;
}

export interface SalesOutbox {
  available: boolean;
  preview_count: number;
  outreach_mode: string | null;
  reason: string;
  items: unknown[];
}

export interface SalesAnalytics {
  basis: string;
  lead_total: number;
  by_status: { status: string; label: string; stage: StageKey | null; count: number }[];
  stages: { key: StageKey; label: string; count: number }[];
  off_track: { out: number; error: number };
  campaigns: { total: number; by_status: { status: string; label: string; count: number }[] };
  per_campaign: { id: string; name: string | null; status: string; status_label: string; leads: number; in_review: number; contacted: number; errors: number }[];
  tasks: { review: number; outbox_preview: number; inbox: number } | null;
}

export interface SalesIndustries {
  in_catalog: boolean;
  hint: string | null;
  items: { key: string; label: string }[];
}

export interface SalesActionResult {
  status: "ok" | "approval_required" | "duplicate" | "in_progress";
  tool: string;
  message?: string;
  approval_id?: string;
  summary?: string;
  risk?: string;
}

export interface CampaignInput {
  region: string;
  industry_key: string;
  target_count: number;
  select_top_n: number;
  channel: "email" | "phone" | "letter";
  budget_usd?: number;
}

/** Colour meaning as in the Lead-Tool: found/active = arc, needs a human = ember, failure = alarm. */
export function stageTone(stage: StageKey | null | undefined): Tone {
  switch (stage) {
    case "review":
      return "warn";
    case "error":
      return "bad";
    case "contact":
      return "ok";
    case "out":
    case null:
    case undefined:
      return "idle";
    default:
      return "info";
  }
}

export function campaignTone(c: Pick<SalesCampaign, "status" | "active">): Tone {
  if (c.status === "failed") return "bad";
  if (c.status === "done") return "ok";
  return c.active ? "info" : "idle";
}

/** A fresh id per submission: a retried request is not executed twice, a new submission is. */
export function newRequestId(): string {
  const c = globalThis.crypto;
  if (c && "randomUUID" in c) return `req-${c.randomUUID()}`;
  return `req-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

function isLoopback(url: string): boolean {
  try {
    const host = new URL(url).hostname;
    return host === "localhost" || host === "::1" || host === "[::1]" || host.startsWith("127.");
  } catch {
    return true;
  }
}

/**
 * Link into the Lead-Tool's own interface (approving, sending, the outbox). `null` when this client cannot reach it:
 * a loopback address only works on the computer the Core – and with it the Lead-Tool – runs on.
 */
export function leadToolLink(webUrl: string | null | undefined, remote: boolean, path = ""): string | null {
  if (!webUrl) return null;
  if (remote && isLoopback(webUrl)) return null;
  const base = webUrl.replace(/\/+$/, "");
  return `${base}${path.startsWith("/") || path === "" ? path : `/${path}`}`;
}

/** What happened after an action, as one toast line. */
export function actionMessage(r: SalesActionResult, done: string): { tone: "ok" | "info" | "warn"; title: string; body?: string } {
  switch (r.status) {
    case "approval_required":
      return { tone: "warn", title: "Freigabe nötig", body: `${r.summary ?? r.tool} wartet unter Freigaben auf deine Bestätigung.` };
    case "duplicate":
      return { tone: "info", title: "Schon erledigt", body: "Diese Anfrage wurde bereits ausgeführt." };
    case "in_progress":
      return { tone: "info", title: "Läuft bereits" };
    default:
      return { tone: "ok", title: done };
  }
}

export function formatScore(score: number | null | undefined, priority?: string | null): string {
  if (score === null || score === undefined) return "–";
  return priority ? `${score} · ${priority}` : String(score);
}

const CHANNEL_LABEL: Record<string, string> = { email: "E-Mail", phone: "Telefon", letter: "Brief" };
export function channelLabel(ch: string | null | undefined): string {
  return ch ? (CHANNEL_LABEL[ch] ?? ch) : "–";
}

const SOURCE_LABEL: Record<string, string> = { osm: "OpenStreetMap", google_places: "Google Places", gmaps_scraper: "Google Maps" };
export function sourceLabel(s: string | null | undefined): string {
  return s ? (SOURCE_LABEL[s] ?? s) : "–";
}

/** Lead counts of a campaign in pipeline order of the given status list (unknown statuses last). */
export function orderedCounts(byStatus: Record<string, number>, order: SalesStatusOption[]): { id: string; label: string; stage: StageKey | null; count: number }[] {
  const known = order.filter((o) => (byStatus[o.id] ?? 0) > 0).map((o) => ({ ...o, count: byStatus[o.id]! }));
  const rest = Object.entries(byStatus)
    .filter(([id, n]) => n > 0 && !order.some((o) => o.id === id))
    .map(([id, n]) => ({ id, label: id, stage: null, count: n }));
  return [...known, ...rest];
}
