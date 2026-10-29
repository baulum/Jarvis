// UpTik connector (publishing over the platforms' web pages): types of /api/uptik and the pure helpers the view uses.
import type { Tone } from "./format";

export type UptikPlatform = "tiktok" | "instagram_reels" | "youtube_shorts";
export type PostStatus = "queued" | "running" | "retry_wait" | "published" | "failed" | "cancelled" | "blocked" | "needs_review";

export interface UptikAccount {
  id: string;
  platform: UptikPlatform;
  platform_label: string;
  label: string;
  handle: string;
  runner: "local" | "device";
  device: string;
  status: "not_connected" | "connecting" | "connected" | "login_failed";
  autopost: boolean;
  circuit_state: "ok" | "tripped";
  circuit_code: string | null;
  circuit_reason: string | null;
  circuit_at: string | null;
  last_error: string | null;
  connected_at: string | null;
  last_used_at: string | null;
  session_saved: boolean;
  session_saved_at: string | null;
  login_running: boolean;
}

export interface UptikSettings {
  runner: "local" | "device";
  device: string;
  device_staging_dir: string;
  headless: boolean;
  max_attempts: number;
  retry_base_s: number;
  max_parallel: number;
  handoff_enabled: boolean;
  handoff_publish_time: string;
  youtube_shorts_enabled: boolean;
  login_timeout_s: number;
  video_dirs: string[];
}

export interface UptikStatus {
  enabled: boolean;
  active: boolean;
  kill_switch: boolean;
  platforms: { id: UptikPlatform; label: string }[];
  settings: UptikSettings;
  local_runner: { ok: boolean; reason: string };
  accounts: UptikAccount[];
  tripped: string[];
  counts: Partial<Record<PostStatus, number>>;
}

export interface UptikPost {
  id: string;
  group_id: string;
  account_id: string;
  platform: UptikPlatform;
  video_name: string;
  caption: string;
  hashtags: string[];
  title: string;
  run_at: string;
  scheduled: boolean;
  status: PostStatus;
  stage: string;
  attempts: number;
  max_attempts: number;
  next_attempt_at: string | null;
  last_error: string | null;
  result_url: string | null;
  source: string;
  approval_id: string | null;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
}

export interface UptikInboxItem {
  id: string;
  source: string;
  source_ref: string;
  name: string;
  size: number;
  duration: number | null;
  title: string;
  captions: Partial<Record<UptikPlatform, { caption?: string; hashtags?: string[]; title?: string }>>;
  created_at: string;
}

export interface UptikPostBody {
  video: string;
  caption: string;
  hashtags: string[];
  title: string;
  accounts: string[];
  options?: Partial<Record<UptikPlatform, Record<string, string | boolean>>>;
  publish_at?: string;
}

export const PLATFORM_LABEL: Record<UptikPlatform, string> = {
  tiktok: "TikTok",
  instagram_reels: "Instagram Reels",
  youtube_shorts: "YouTube Shorts",
};

/** Caption limits (text + blank line + hashtags), the same numbers as the Core's validation. */
export const CAPTION_MAX: Record<UptikPlatform, number> = { tiktok: 2200, instagram_reels: 2200, youtube_shorts: 5000 };

const POST_LABEL: Record<PostStatus, string> = {
  queued: "wartet",
  running: "läuft",
  retry_wait: "neuer Versuch geplant",
  published: "veröffentlicht",
  failed: "fehlgeschlagen",
  cancelled: "abgebrochen",
  blocked: "angehalten",
  needs_review: "prüfen",
};

export function postStatusLabel(s: PostStatus): string {
  return POST_LABEL[s] ?? s;
}

export function postTone(s: PostStatus): Tone {
  if (s === "published") return "ok";
  if (s === "failed" || s === "blocked") return "bad";
  if (s === "needs_review" || s === "retry_wait") return "warn";
  if (s === "running") return "info";
  return "idle";
}

export function accountState(a: UptikAccount): { label: string; tone: Tone } {
  if (a.circuit_state === "tripped") return { label: `Angehalten: ${a.circuit_reason ?? "manuelle Prüfung nötig"}`, tone: "bad" };
  if (a.login_running || a.status === "connecting") return { label: "Anmeldung läuft im Browserfenster", tone: "info" };
  if (a.status === "connected") return { label: "Verbunden", tone: "ok" };
  if (a.status === "login_failed") return { label: `Anmeldung fehlgeschlagen${a.last_error ? `: ${a.last_error}` : ""}`, tone: "warn" };
  return { label: "Nicht angemeldet", tone: "idle" };
}

/** "#Web, kmu  #web" → ["#Web", "#kmu"] (the Core normalizes again and rejects invalid tags). */
export function parseHashtags(raw: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of raw.split(/[\s,;]+/)) {
    const tag = part.trim().replace(/^#+/, "");
    if (!tag || seen.has(tag.toLowerCase())) continue;
    seen.add(tag.toLowerCase());
    out.push(`#${tag}`);
  }
  return out;
}

export function postText(caption: string, hashtags: string[]): string {
  const body = caption.trim();
  return hashtags.length ? `${body}\n\n${hashtags.join(" ")}`.trim() : body;
}

/** Platforms whose limit the text exceeds (for the live hint in the form). */
export function overLimit(caption: string, hashtags: string[], platforms: UptikPlatform[]): UptikPlatform[] {
  const len = postText(caption, hashtags).length;
  return platforms.filter((p) => len > CAPTION_MAX[p]);
}

/** <input type="datetime-local"> value → the Core's Berlin wall-clock format ("" = publish now). */
export function publishAtValue(local: string): string | undefined {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(local.trim());
  return m ? `${m[1]} ${m[2]}` : undefined;
}

export function buildPostBody(f: {
  video: string;
  caption: string;
  hashtags: string;
  title: string;
  accounts: string[];
  when: string;
  options?: UptikPostBody["options"];
}): UptikPostBody {
  const body: UptikPostBody = {
    video: f.video,
    caption: f.caption.trim(),
    hashtags: parseHashtags(f.hashtags),
    title: f.title.trim(),
    accounts: [...f.accounts],
  };
  if (f.options && Object.keys(f.options).length) body.options = f.options;
  const at = publishAtValue(f.when);
  if (at) body.publish_at = at;
  return body;
}
