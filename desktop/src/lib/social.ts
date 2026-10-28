// Social Media (daily PJE Systems video): types of /api/social and the small pure helpers the view uses.
import type { Tone } from "./format";

export type Platform = "instagram" | "facebook" | "tiktok" | "youtube";
export const PLATFORMS: Platform[] = ["instagram", "facebook", "tiktok", "youtube"];

export interface PostCaption {
  text?: string;
  title?: string;
  description?: string;
  hashtags: string[];
}

export type Captions = Partial<Record<Platform, PostCaption>>;

export interface Publication {
  id: string;
  video_id: string;
  platform: Platform;
  status: "scheduled" | "waiting_approval" | "publishing" | "published" | "failed" | "manual";
  scheduled_at: string | null;
  published_at: string | null;
  post_id: string | null;
  post_url: string | null;
  error: string | null;
}

export interface SocialVideo {
  id: string;
  status: "producing" | "ready" | "failed";
  stage: string;
  trigger: "schedule" | "manual" | "regenerate";
  auto_date: string | null;
  parent_id: string | null;
  title: string;
  category: string;
  concept_key: string;
  tone: string;
  palette: string;
  hook: string;
  summary: string;
  file_name: string | null;
  size: number;
  duration_s: number;
  attempts: number;
  error: string | null;
  email: { status?: string; to?: string; sent_at?: string; attached?: boolean; subject?: string };
  qa_passed: boolean | null;
  created_at: string;
  captions?: Captions;
  publications?: Publication[];
}

export interface QaCheck {
  id: string;
  label: string;
  ok: boolean;
  detail: string;
}

export interface SocialVideoDetail extends SocialVideo {
  concept: Record<string, unknown> & { summary?: string; idea?: string; why?: string; inspiration?: string[] };
  script: string;
  storyboard: { scenes?: { id: string; component: string; duration: number; transition?: string }[]; music?: string };
  components: string[];
  transitions: string[];
  sounds: string[];
  captions: Captions;
  qa: { passed?: boolean; checks?: QaCheck[]; probe?: Record<string, unknown> };
  log: { ts: string; level: string; msg: string }[];
  metrics: Record<string, Record<string, unknown>>;
  publications: Publication[];
  email_preview: { subject: string; text: string } | null;
}

export interface SocialSchedule {
  exists: boolean;
  id?: string;
  enabled: boolean;
  time: string;
  timezone: string;
  next_run_at?: string | null;
  last_run_at?: string | null;
  last_status?: string | null;
}

export interface SocialSettings {
  email_enabled: boolean;
  email_recipient: string;
  publish_time: string | null;
  autopost_enabled: boolean;
  platforms: Record<Platform, { autopost: boolean }>;
  voiceover: boolean;
  hint: string;
  schedule: SocialSchedule;
  owner_email: string;
}

export interface PlatformStatus {
  platform: Platform;
  connector?: string;
  enabled: boolean;
  app_configured?: boolean;
  connected: boolean;
  account?: string;
  autopost: boolean;
  will_autopost: boolean;
  health: { status: string; message: string } | null;
}

export interface SocialStatus {
  enabled: boolean;
  dependencies: { ok: boolean | null; problems: string[]; checked_at?: string | null; browser?: string };
  schedule: SocialSchedule;
  settings: Omit<SocialSettings, "schedule" | "owner_email">;
  owner_email: string;
  producing: SocialVideo[];
  last_video: SocialVideo | null;
  recent_errors: { id: string; title: string; error: string | null; at: string }[];
  recent_publications: Publication[];
  counts: Record<string, number>;
  progress: { video_id?: string; stage?: string; percent?: number };
  platforms: PlatformStatus[];
}

export const PLATFORM_LABEL: Record<Platform, string> = {
  instagram: "Instagram Reels",
  facebook: "Facebook Reels",
  tiktok: "TikTok",
  youtube: "YouTube Shorts",
};

/** Where Sir posts by hand when autopost is off (upload pages of the official apps/sites). */
export const MANUAL_UPLOAD: Record<Platform, string> = {
  instagram: "https://www.instagram.com/",
  facebook: "https://www.facebook.com/reels/create",
  tiktok: "https://www.tiktok.com/upload",
  youtube: "https://studio.youtube.com/",
};

export const CATEGORY_LABEL: Record<string, string> = {
  A: "Humor & Hooks",
  B: "Pattern Interrupt",
  C: "Premium Motion",
  D: "Kundenprobleme",
  E: "Angebote & Projekte",
  F: "Experimentell",
};

export const STAGE_LABEL: Record<string, string> = {
  concept: "Idee",
  render: "Rendern",
  qa: "Qualitätskontrolle",
  captions: "Captions",
  library: "Bibliothek",
  email: "E-Mail",
  done: "Fertig",
};

export function videoTone(v: Pick<SocialVideo, "status">): Tone {
  return v.status === "ready" ? "ok" : v.status === "failed" ? "bad" : "info";
}

export function videoStatusLabel(v: Pick<SocialVideo, "status" | "stage">): string {
  if (v.status === "ready") return "Fertig";
  if (v.status === "failed") return `Fehlgeschlagen (${STAGE_LABEL[v.stage] ?? v.stage})`;
  return `In Arbeit · ${STAGE_LABEL[v.stage] ?? v.stage}`;
}

export const PUBLICATION_LABEL: Record<Publication["status"], string> = {
  scheduled: "geplant",
  waiting_approval: "wartet auf Freigabe",
  publishing: "wird veröffentlicht",
  published: "veröffentlicht",
  failed: "fehlgeschlagen",
  manual: "manuell",
};

export function publicationTone(s: Publication["status"]): Tone {
  return s === "published" ? "ok" : s === "failed" ? "bad" : s === "waiting_approval" ? "warn" : "info";
}

/** The text as it is pasted into the app: caption (YouTube: description), blank line, hashtags. */
export function captionText(platform: Platform, c: PostCaption | undefined): string {
  if (!c) return "";
  const body = ((platform === "youtube" ? c.description : c.text) ?? "").trim();
  const tags = (c.hashtags ?? []).join(" ");
  return tags ? `${body}\n\n${tags}`.trim() : body;
}

/** Instagram/TikTok/Facebook count UTF-16 code units like JavaScript's `length`. */
export const CAPTION_LIMIT: Record<Platform, number> = { instagram: 2200, tiktok: 2200, facebook: 2200, youtube: 5000 };

export function captionOverLimit(platform: Platform, c: PostCaption | undefined): boolean {
  return captionText(platform, c).length > CAPTION_LIMIT[platform] || (platform === "youtube" && (c?.title ?? "").length > 100);
}

export function dependencyTone(d: SocialStatus["dependencies"]): Tone {
  return d.ok === null ? "idle" : d.ok ? "ok" : "bad";
}

export function validTime(t: string): boolean {
  return /^([01]\d|2[0-3]):[0-5]\d$/.test(t);
}

export function safeFileName(v: Pick<SocialVideo, "file_name" | "title">): string {
  if (v.file_name && /^[\w.-]+\.mp4$/.test(v.file_name)) return v.file_name;
  return `${(v.title || "pje-video").replace(/[^\w-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60) || "pje-video"}.mp4`;
}
