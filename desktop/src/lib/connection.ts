// Where the client finds the Jarvis Core and with which device token.
//
// - Desktop app, local Core (always on the Mac): 127.0.0.1:8765 + the owner token from disk – as before.
// - Desktop app as client of a remote Core (Windows/Linux PC, client.json written by the installer or the
//   connection form): the Core's Tailscale address (https://<mac>.<tailnet>.ts.net) + that device's token.
// - Browser client served by the Core at /app: same origin, token entered once (sign-in screen).
import { invoke } from "@tauri-apps/api/core";
import { CORE_URL } from "./api";
import { inTauri } from "./native";

export interface CoreConnection {
  baseUrl: string;
  token: string;
  /** true when the Core runs on another computer (no local services, Finder actions hidden). */
  remote: boolean;
}

/** The browser client has no token yet: show the sign-in screen. */
export class NeedsSignIn extends Error {
  constructor() {
    super("Anmeldung mit einem Geräte-Token nötig.");
    this.name = "NeedsSignIn";
  }
}

export const WEB_TOKEN_KEY = "jarvis.deviceToken";

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

/** null when the address is usable, else a German reason. Remote Cores must use HTTPS (Tailscale Serve). */
export function validateCoreUrl(raw: string): string | null {
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    return "Keine gültige Adresse (Beispiel: https://mac.tail1234.ts.net)";
  }
  const loopback = url.hostname === "127.0.0.1" || url.hostname === "localhost";
  if (url.protocol !== "https:" && !(url.protocol === "http:" && loopback)) {
    return "Nur https:// – den Core über Tailscale Serve freigeben (gültiges Zertifikat)";
  }
  if (url.pathname !== "/" && url.pathname !== "") return "Nur die Adresse ohne Pfad angeben";
  return null;
}

export function normalizeCoreUrl(raw: string): string {
  return new URL(raw.trim()).origin;
}

/** Device tokens of this system start with jdv_ (desktop/device) – anything else is a typo or a foreign secret. */
export function validateToken(raw: string): string | null {
  const t = raw.trim();
  if (!/^jdv_[A-Za-z0-9_-]{20,}$/.test(t)) return "Geräte-Token beginnen mit „jdv_“ (jarvisctl pair … auf dem Core-Rechner)";
  return null;
}

/** The browser client lives under /app of the Core; the API is on the same origin. */
export function servedByCore(loc: Pick<Location, "pathname">): boolean {
  return loc.pathname === "/app" || loc.pathname.startsWith("/app/");
}

export function readWebToken(session: StorageLike, local: StorageLike): string | null {
  return session.getItem(WEB_TOKEN_KEY) ?? local.getItem(WEB_TOKEN_KEY);
}

export function saveWebToken(token: string, remember: boolean, session: StorageLike = sessionStorage, local: StorageLike = localStorage): void {
  session.setItem(WEB_TOKEN_KEY, token.trim());
  if (remember) local.setItem(WEB_TOKEN_KEY, token.trim());
  else local.removeItem(WEB_TOKEN_KEY);
}

export function clearWebToken(session: StorageLike = sessionStorage, local: StorageLike = localStorage): void {
  session.removeItem(WEB_TOKEN_KEY);
  local.removeItem(WEB_TOKEN_KEY);
}

interface NativeConnection {
  core_url: string | null;
  token: string;
  remote: boolean;
}

export async function loadConnection(): Promise<CoreConnection> {
  if (inTauri()) {
    const c = await invoke<NativeConnection>("get_core_connection");
    return { baseUrl: c.core_url ?? CORE_URL, token: c.token, remote: Boolean(c.core_url) };
  }
  if (import.meta.env.DEV) {
    // Development only (stripped from production builds): `pnpm dev` + ?devtoken=<viewer token> for UI review.
    const t = new URLSearchParams(window.location.search).get("devtoken");
    if (t) return { baseUrl: CORE_URL, token: t, remote: false };
  }
  if (servedByCore(window.location)) {
    const token = readWebToken(sessionStorage, localStorage);
    if (!token) throw new NeedsSignIn();
    return { baseUrl: window.location.origin, token, remote: true };
  }
  throw new Error("Das Geräte-Token kann nur in der Desktop-App gelesen werden (pnpm tauri dev).");
}

/** Desktop app: connect to a Core on another computer (stored in client.json, read again on reload). */
export async function saveRemoteCore(url: string, token: string): Promise<void> {
  await invoke("set_core_connection", { coreUrl: normalizeCoreUrl(url), token: token.trim() });
}

/** Desktop app: back to the Core on this computer. */
export async function clearRemoteCore(): Promise<void> {
  await invoke("clear_core_connection");
}
