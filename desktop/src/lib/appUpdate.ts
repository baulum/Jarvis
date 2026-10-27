// Labels for the OTA updates of the Android app (core: jarvis.mobile.ota, phone: update/AppUpdater.kt).
import type { Tone } from "./format";
import type { AppUpdateBuild, AppUpdateDevice, AppUpdateStatus } from "./types";

export const ACTIVE_BUILD = ["preparing", "building", "verifying", "publishing"];

const BUILD_LABELS: Record<string, string> = {
  idle: "Noch kein Build",
  preparing: "Bereite vor",
  building: "Baue",
  verifying: "Prüfe Signatur",
  publishing: "Veröffentliche",
  ready: "Gebaut",
  distributed: "Verteilt",
  failed: "Fehlgeschlagen",
};

const DEVICE_LABELS: Record<string, string> = {
  idle: "wartet auf Prüfung",
  checking: "prüft",
  up_to_date: "aktuell",
  downloading: "lädt",
  paused: "Download pausiert",
  verifying: "prüft Download",
  ready: "bereit zur Installation",
  waiting_user: "wartet auf Bestätigung",
  installing: "installiert",
  installed: "aktualisiert",
  failed: "fehlgeschlagen",
  unsupported: "keine Updates möglich",
};

export function buildActive(b: AppUpdateBuild | undefined): boolean {
  return !!b && ACTIVE_BUILD.includes(b.state);
}

export function buildLabel(b: AppUpdateBuild): string {
  return BUILD_LABELS[b.state] ?? b.state;
}

export function buildTone(b: AppUpdateBuild): Tone {
  if (b.state === "failed") return "bad";
  if (buildActive(b)) return "info";
  if (b.state === "ready" || b.state === "distributed") return "ok";
  return "idle";
}

/** Status line of one phone, e.g. "lädt 1.0.0+3002 · 42 %". */
export function deviceLine(d: AppUpdateDevice, latestCode: number | null): string {
  const label = DEVICE_LABELS[d.state] ?? d.state;
  const target = d.target_code && d.target_code !== d.version_code ? ` ${d.target_code}` : "";
  const pct = d.state === "downloading" && d.progress != null ? ` · ${Math.round(d.progress * 100)} %` : "";
  const behind = latestCode != null && d.version_code != null && d.version_code < latestCode && d.state === "up_to_date";
  return `${label}${target}${pct}${behind ? " (Core hat neuere Version)" : ""}`;
}

export function deviceTone(d: AppUpdateDevice, latestCode: number | null): Tone {
  if (d.state === "failed" || d.state === "unsupported") return "bad";
  if (d.state === "waiting_user" || d.can_install === false) return "warn";
  if (latestCode != null && d.version_code != null && d.version_code >= latestCode) return "ok";
  return "info";
}

/** What Sir has to do on the phone, if anything (shown under the phone's row). */
export function deviceHint(d: AppUpdateDevice): string | null {
  if (d.error && (d.state === "failed" || d.state === "unsupported" || d.state === "waiting_user")) return d.error;
  if (d.can_install === false) return "Auf dem Handy einmalig „Unbekannte Apps installieren“ für J.A.R.V.I.S. erlauben (App › Einstellungen › App-Updates).";
  if (d.unattended === false) return "Android < 12: jedes Update einmal auf dem Handy bestätigen.";
  return null;
}

/** Headline of the card: what is going on right now. */
export function summary(s: AppUpdateStatus): string {
  if (!s.enabled) return "App-Updates sind ausgeschaltet (mobile.app_update.enabled).";
  const b = s.build;
  if (buildActive(b)) return `${buildLabel(b)} ${b.version_name ?? (b.commit ?? "").slice(0, 7)} – ${b.message ?? ""}`.trim();
  if (b.state === "failed") return `Build fehlgeschlagen: ${b.message ?? "unbekannt"}`;
  if (s.queued) return `Prüfung geplant (${s.queued_reason ?? "automatisch"})`;
  if (s.latest) return `Aktuell: ${s.latest.version_name}`;
  return s.last_check?.reason ?? "Noch kein Release gebaut.";
}

/** "AB:CD:…" like Android and the install page show it, shortened for the card. */
export function shortFingerprint(hex: string | null | undefined, groups = 8): string {
  if (!hex) return "–";
  const pairs = hex.toUpperCase().match(/.{2}/g) ?? [];
  return pairs.slice(0, groups).join(":") + (pairs.length > groups ? ":…" : "");
}
