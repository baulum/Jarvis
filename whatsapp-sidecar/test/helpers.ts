// Test doubles: a scriptable Baileys socket. No WhatsApp account, no network.
import { EventEmitter } from "node:events";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { vi } from "vitest";
import type { SocketConfig, SocketLike } from "../src/connection.js";
import { createLogger } from "../src/redact.js";

export class FakeSocket implements SocketLike {
  readonly emitter = new EventEmitter();
  readonly ev = {
    on: (event: string, listener: (arg: unknown) => void) => void this.emitter.on(event, listener),
    removeAllListeners: (event: string) => void this.emitter.removeAllListeners(event),
  } as unknown as SocketLike["ev"];
  user: { id: string; name?: string } | undefined = { id: "4915112345678:3@s.whatsapp.net", name: "Sir" };
  sendMessage = vi.fn(async (jid: string, content: Record<string, unknown>, options?: Record<string, unknown>) => ({
    key: { remoteJid: jid, id: String(options?.messageId ?? "X"), fromMe: true },
    message: "text" in content ? { conversation: String(content.text) } : { documentMessage: { fileName: "f.pdf", mimetype: "application/pdf" } },
    messageTimestamp: 1_760_000_000,
    status: 1,
  }));
  readMessages = vi.fn(async () => undefined);
  updateMediaMessage = vi.fn(async (m: unknown) => m as never);
  requestPairingCode = vi.fn(async () => "ABCD-EFGH");
  fetchMessageHistory = vi.fn(async () => "req-1");
  groupMetadata = vi.fn(async () => ({ subject: "Team", participants: [{ id: "491511111111@s.whatsapp.net", admin: "admin" }] }));
  onWhatsApp = vi.fn(async (jid: string) => [{ jid, exists: true }]);
  logout = vi.fn(async () => undefined);
  end = vi.fn();

  emit(event: string, data: unknown): void {
    this.emitter.emit(event, data);
  }
}

export interface Harness {
  sockets: FakeSocket[];
  configs: SocketConfig[];
  events: { event: string; data: Record<string, unknown> }[];
  factory: (cfg: SocketConfig) => SocketLike;
  last: () => FakeSocket;
  statuses: () => Record<string, unknown>[];
}

export function harness(): Harness {
  const h: Harness = {
    sockets: [],
    configs: [],
    events: [],
    factory: (cfg) => {
      const s = new FakeSocket();
      h.sockets.push(s);
      h.configs.push(cfg);
      return s;
    },
    last: () => {
      const s = h.sockets.at(-1);
      if (!s) throw new Error("no socket");
      return s;
    },
    statuses: () => h.events.filter((e) => e.event === "status").map((e) => e.data),
  };
  return h;
}

export async function tempDir(): Promise<string> {
  return mkdtemp(join(tmpdir(), "jarvis-wa-test-"));
}

export const SECRET = "x".repeat(16) + "test-secret-for-the-session-key-0123456789";
export const silentLogger = createLogger("silent", () => undefined);

export async function flush(times = 5): Promise<void> {
  for (let i = 0; i < times; i++) await new Promise((r) => setImmediate(r));
}

export async function waitFor(check: () => boolean | Promise<boolean>, ms = 3000): Promise<void> {
  const until = Date.now() + ms;
  while (!(await check())) {
    if (Date.now() > until) throw new Error("condition not met in time");
    await new Promise((r) => setImmediate(r));
  }
}

/** Fake only the reconnect timers; file I/O and setImmediate keep running for real. */
export function fakeTimers(): void {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
}

/** Waits until the last emitted status has the given state (status events pass through an async chain). */
export async function untilState(h: Harness, state: string): Promise<Record<string, unknown>> {
  await waitFor(() => h.statuses().at(-1)?.state === state);
  return h.statuses().at(-1)!;
}
