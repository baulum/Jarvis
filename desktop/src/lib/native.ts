// Thin wrappers around Tauri APIs. Each degrades gracefully when the UI runs in a plain browser.
import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export const inTauri = (): boolean => {
  try {
    return isTauri();
  } catch {
    return false;
  }
};

export async function loadCoreToken(): Promise<string> {
  if (!inTauri()) {
    // Development only (stripped from production builds): `pnpm dev` + ?devtoken=<viewer token> for UI review.
    if (import.meta.env.DEV) {
      const t = new URLSearchParams(window.location.search).get("devtoken");
      if (t) return t;
    }
    throw new Error("Das Geräte-Token kann nur in der Desktop-App gelesen werden (pnpm tauri dev).");
  }
  return invoke<string>("get_core_token");
}

/** Diagnostic line on the Rust side's stdout. Never pass secrets. */
export function nativeLog(message: string): void {
  if (!inTauri()) return;
  void invoke("frontend_log", { message }).catch(() => undefined);
}

export async function openExternal(url: string): Promise<void> {
  if (inTauri()) {
    const { openUrl } = await import("@tauri-apps/plugin-opener");
    await openUrl(url);
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

let notifyPermission: boolean | null = null;

export async function nativeNotify(title: string, body: string): Promise<void> {
  if (!inTauri()) return;
  const n = await import("@tauri-apps/plugin-notification");
  if (notifyPermission === null) {
    notifyPermission = await n.isPermissionGranted();
    if (!notifyPermission) notifyPermission = (await n.requestPermission()) === "granted";
  }
  if (notifyPermission) n.sendNotification({ title, body });
}

/** Asks for a destination and writes the bytes there. Returns the path or null when cancelled. */
export async function saveBytes(
  defaultName: string,
  bytes: Uint8Array,
  filter: { name: string; extensions: string[]; mime?: string } = { name: "ZIP-Archiv", extensions: ["zip"], mime: "application/zip" },
): Promise<string | null> {
  if (!inTauri()) {
    const blob = new Blob([bytes as BlobPart], { type: filter.mime ?? "application/octet-stream" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = defaultName;
    a.click();
    URL.revokeObjectURL(a.href);
    return defaultName;
  }
  const { save } = await import("@tauri-apps/plugin-dialog");
  const path = await save({ defaultPath: defaultName, filters: filter.extensions.length ? [{ name: filter.name, extensions: filter.extensions }] : [] });
  if (!path) return null;
  const { writeFile } = await import("@tauri-apps/plugin-fs");
  await writeFile(path, bytes);
  return path;
}

/** True when the app runs on macOS (features that drive the Mac itself are hidden elsewhere). */
export function isMac(): boolean {
  return typeof navigator !== "undefined" && /Mac/i.test(navigator.userAgent);
}

/**
 * Asks for an existing file – on macOS an .app bundle counts as a file. Returns the absolute path, or null when
 * cancelled or outside the desktop app (a browser cannot reveal real paths).
 */
export async function pickPath(opts: { title: string; filterName: string; extensions: string[]; defaultPath?: string }): Promise<string | null> {
  if (!inTauri()) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const picked = await open({
    title: opts.title,
    multiple: false,
    directory: false,
    defaultPath: opts.defaultPath,
    filters: [{ name: opts.filterName, extensions: opts.extensions }],
  });
  return typeof picked === "string" ? picked : null;
}

export async function autostartEnabled(): Promise<boolean | null> {
  if (!inTauri()) return null;
  const a = await import("@tauri-apps/plugin-autostart");
  return a.isEnabled();
}

export async function setAutostart(enabled: boolean): Promise<void> {
  const a = await import("@tauri-apps/plugin-autostart");
  if (enabled) await a.enable();
  else await a.disable();
}

export interface TrayAction {
  action: string;
  ok: boolean;
  detail: string;
}

export async function onTrayAction(cb: (a: TrayAction) => void): Promise<UnlistenFn> {
  if (!inTauri()) return () => undefined;
  return listen<TrayAction>("tray-action", (e) => cb(e.payload));
}

/** Tray action while the app is a client of a remote Core: the webview sends it over HTTPS. */
export interface TrayRequest {
  action: string;
  path: string;
  body: Record<string, unknown> | null;
}

export async function onTrayRequest(cb: (r: TrayRequest) => void): Promise<UnlistenFn> {
  if (!inTauri()) return () => undefined;
  return listen<TrayRequest>("tray-request", (e) => cb(e.payload));
}

export interface ServicesEvent {
  state: "starting" | "running" | "error";
  detail?: string;
}

/** Background services (core, voice, whisper, bridge) are started by the app itself. */
export async function onServices(cb: (e: ServicesEvent) => void): Promise<UnlistenFn> {
  if (!inTauri()) return () => undefined;
  return listen<ServicesEvent>("services", (e) => cb(e.payload));
}
