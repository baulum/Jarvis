// Formatting helpers (German locale) shared by all views.
import type { HealthStatus, Risk } from "./types";

const nf1 = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 });
const nf0 = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });

export function formatBytes(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "–";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${i === 0 ? nf0.format(v) : nf1.format(v)} ${units[i]}`;
}

export function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "–";
  return nf0.format(n);
}

export function formatPercent(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "–";
  return `${nf1.format(n)} %`;
}

/** 3725 -> "1 h 2 min", 42 -> "42 s". */
export function formatDuration(totalSeconds: number | null | undefined): string {
  if (totalSeconds === null || totalSeconds === undefined || !Number.isFinite(totalSeconds) || totalSeconds < 0) return "–";
  const s = Math.floor(totalSeconds);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d} d ${h} h`;
  if (h > 0) return `${h} h ${m} min`;
  if (m > 0) return `${m} min ${s % 60} s`;
  return `${s} s`;
}

export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "–";
  return ms >= 1000 ? `${nf1.format(ms / 1000)} s` : `${nf0.format(ms)} ms`;
}

/** Relative time in German ("vor 3 min", "in 2 h"). */
export function formatRelative(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "–";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "–";
  const diff = Math.round((t - now) / 1000);
  const abs = Math.abs(diff);
  const future = diff > 0;
  let text: string;
  if (abs < 45) return future ? "gleich" : "gerade eben";
  if (abs < 3600) text = `${Math.round(abs / 60)} min`;
  else if (abs < 86400) text = `${Math.round(abs / 3600)} h`;
  else text = `${Math.round(abs / 86400)} d`;
  return future ? `in ${text}` : `vor ${text}`;
}

export function formatClock(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "–";
  return d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "–";
  return d.toLocaleString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

const RISK_LABELS: Record<Risk, string> = {
  read: "Lesen",
  low: "Niedrig",
  medium: "Mittel",
  high: "Hoch",
  critical: "Kritisch",
};

export function riskLabel(r: string): string {
  return RISK_LABELS[r as Risk] ?? r;
}

export const RISK_ORDER: Record<Risk, number> = { read: 0, low: 1, medium: 2, high: 3, critical: 4 };

export type Tone = "ok" | "warn" | "bad" | "idle" | "info";

export function healthTone(status: HealthStatus | null | undefined): Tone {
  switch (status) {
    case "ok":
    case "connected":
      return "ok";
    case "degraded":
    case "warning":
      return "warn";
    case "error":
    case "down":
    case "failed":
      return "bad";
    default:
      return "idle";
  }
}

const HEALTH_LABELS: Record<string, string> = {
  ok: "Bereit",
  degraded: "Eingeschränkt",
  error: "Fehler",
  down: "Ausgefallen",
  offline: "Offline",
  not_configured: "Nicht eingerichtet",
  disabled: "Deaktiviert",
  unknown: "Unbekannt",
};

export function healthLabel(status: string | null | undefined): string {
  if (!status) return "Unbekannt";
  return HEALTH_LABELS[status] ?? status;
}

const STATUS_LABELS: Record<string, string> = {
  queued: "Wartet",
  pending: "Ausstehend",
  running: "Läuft",
  waiting_approval: "Wartet auf Freigabe",
  waiting_input: "Wartet auf Eingabe",
  waiting_external: "Wartet extern",
  completed: "Abgeschlossen",
  failed: "Fehlgeschlagen",
  cancelled: "Abgebrochen",
  skipped: "Übersprungen",
  approved: "Freigegeben",
  rejected: "Abgelehnt",
  executed: "Ausgeführt",
  expired: "Abgelaufen",
  ok: "OK",
  error: "Fehler",
};

export function statusLabel(s: string | null | undefined): string {
  if (!s) return "–";
  return STATUS_LABELS[s] ?? s;
}

export function statusTone(s: string | null | undefined): Tone {
  switch (s) {
    case "completed":
    case "executed":
    case "approved":
    case "ok":
      return "ok";
    case "running":
    case "queued":
      return "info";
    case "waiting_approval":
    case "waiting_input":
    case "waiting_external":
    case "pending":
      return "warn";
    case "failed":
    case "rejected":
    case "error":
      return "bad";
    default:
      return "idle";
  }
}

const COMPONENT_LABELS: Record<string, string> = {
  ollama: "Ollama (lokale Modelle)",
  hermes: "Hermes-Agent",
  database: "Datenbank",
  stt: "Spracherkennung",
  voice_daemon: "Sprach-Daemon",
};

export function componentLabel(key: string): string {
  if (key.startsWith("connector:")) return key.slice("connector:".length);
  return COMPONENT_LABELS[key] ?? key;
}

/** Stringify an unknown value compactly for display (args, results). */
export function compactJson(v: unknown, max = 600): string {
  let s: string;
  try {
    s = typeof v === "string" ? v : JSON.stringify(v, null, 2);
  } catch {
    s = String(v);
  }
  if (s === undefined) s = "";
  return s.length > max ? `${s.slice(0, max)} …` : s;
}
