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
export async function saveBytes(defaultName: string, bytes: Uint8Array): Promise<string | null> {
  if (!inTauri()) {
    const blob = new Blob([bytes as BlobPart], { type: "application/zip" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = defaultName;
    a.click();
    URL.revokeObjectURL(a.href);
    return defaultName;
  }
  const { save } = await import("@tauri-apps/plugin-dialog");
  const path = await save({ defaultPath: defaultName, filters: [{ name: "ZIP-Archiv", extensions: ["zip"] }] });
  if (!path) return null;
  const { writeFile } = await import("@tauri-apps/plugin-fs");
  await writeFile(path, bytes);
  return path;
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
