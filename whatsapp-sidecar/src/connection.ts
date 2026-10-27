// One WhatsApp multi-device connection (Baileys), owned by the sidecar process.
//
// Life cycle: init → connect (QR or pairing code) → open. A dropped connection is re-established with
// exponential backoff (backoff.ts); a logout (401), a ban/forbidden (403) or a takeover by another session (440)
// is never retried – the state says why and Sir decides. The session lives encrypted on disk (authState.ts), so
// a restart reconnects without a new scan.
//
// Sending is idempotent: the Core passes an idempotency key per send; the same key returns the first result
// (also while it is still running) and the WhatsApp message id is derived from it, so even a retried send can
// never produce a second message.
import { createHash } from "node:crypto";
import { createWriteStream } from "node:fs";
import { mkdir, rename, rm, stat } from "node:fs/promises";
import { dirname } from "node:path";
import { Transform, type TransformCallback } from "node:stream";
import { pipeline } from "node:stream/promises";
import makeWASocket, {
  ALL_WA_PATCH_NAMES,
  Browsers,
  DisconnectReason,
  downloadMediaMessage,
  fetchLatestWaWebVersion,
  isJidGroup,
  isJidStatusBroadcast,
  jidNormalizedUser,
  makeCacheableSignalKeyStore,
  normalizeMessageContent,
  proto,
  type AnyMessageContent,
  type AuthenticationState,
  type BaileysEventMap,
  type Chat,
  type Contact,
  type WAMessage,
  type WAPatchName,
  type WAMessageKey,
} from "@whiskeysockets/baileys";
import type { Logger } from "pino";
import { EncryptedFileStore, useEncryptedAuthState } from "./authState.js";
import { backoffDelay } from "./backoff.js";
import { classify, parseRaw, serializeRaw, textOf } from "./normalize.js";
import {
  SidecarError,
  type ChatInfo,
  type ConnectionStateName,
  type ContactInfo,
  type MessageUpdate,
  type NormalizedMessage,
  type StatusEvent,
} from "./protocol.js";

export type WAVersion = [number, number, number];

/** The part of a Baileys socket this connection uses (lets the tests drive it without WhatsApp). */
export interface SocketLike {
  ev: {
    on<T extends keyof BaileysEventMap>(event: T, listener: (arg: BaileysEventMap[T]) => void): void;
    removeAllListeners<T extends keyof BaileysEventMap>(event: T): void;
  };
  user?: { id: string; name?: string } | undefined;
  sendMessage(jid: string, content: AnyMessageContent, options?: Record<string, unknown>): Promise<WAMessage | undefined>;
  readMessages(keys: WAMessageKey[]): Promise<void>;
  updateMediaMessage(message: WAMessage): Promise<WAMessage>;
  requestPairingCode(phoneNumber: string): Promise<string>;
  fetchMessageHistory(count: number, oldestMsgKey: WAMessageKey, oldestMsgTimestamp: number): Promise<string>;
  groupMetadata(jid: string): Promise<{ subject: string; participants: { id: string; admin?: string | null }[] }>;
  onWhatsApp(...jids: string[]): Promise<{ jid: string; exists: unknown }[] | undefined>;
  groupFetchAllParticipating?(): Promise<Record<string, { id: string; subject?: string }>>;
  resyncAppState?(collections: readonly WAPatchName[], isInitialSync: boolean): Promise<void>;
  logout(msg?: string): Promise<void>;
  end(error: Error | undefined): void;
}

export interface SocketConfig {
  auth: AuthenticationState;
  logger: Logger;
  version?: WAVersion;
  syncFullHistory: boolean;
  getMessage: (key: proto.IMessageKey) => Promise<proto.IMessage | undefined>;
}

export type SocketFactory = (cfg: SocketConfig) => SocketLike;

export const realSocketFactory: SocketFactory = (cfg) =>
  makeWASocket({
    auth: { creds: cfg.auth.creds, keys: makeCacheableSignalKeyStore(cfg.auth.keys, cfg.logger) },
    logger: cfg.logger,
    ...(cfg.version ? { version: cfg.version } : {}),
    browser: Browsers.appropriate("Desktop"),
    // Sir's phone keeps ringing for new messages: Jarvis does not mark itself "online"
    markOnlineOnConnect: false,
    syncFullHistory: cfg.syncFullHistory,
    generateHighQualityLinkPreview: false,
    getMessage: cfg.getMessage,
    shouldIgnoreJid: (jid?: string) => !jid || isJidStatusBroadcast(jid) || jid.endsWith("@newsletter"),
  }) as unknown as SocketLike;

export async function resolveWaVersion(): Promise<WAVersion | undefined> {
  try {
    const res = await fetchLatestWaWebVersion({ timeout: 5000 });
    return res.isLatest || !res.error ? (res.version as WAVersion) : undefined;
  } catch {
    return undefined; // Baileys' bundled version is used
  }
}

export type SendKind = "text" | "image" | "video" | "audio" | "document";

export interface SendParams {
  jid: string;
  kind: SendKind;
  idempotencyKey: string;
  text?: string;
  caption?: string;
  filePath?: string;
  fileName?: string;
  mimetype?: string;
  ptt?: boolean;
  maxBytes?: number;
  quoted?: { id: string; fromMe: boolean; participant?: string; text?: string };
}

export interface SendResult {
  messageId: string;
  chatJid: string;
  timestamp: number;
  status: number;
  message?: NormalizedMessage;
}

export interface DownloadResult {
  path: string;
  size: number;
  sha256: string;
  updatedRaw?: string;
}

export interface ConnectionOptions {
  sessionDir: string;
  secret: string;
  syncFullHistory: boolean;
  emit: (event: string, data: unknown) => void;
  logger: Logger;
  factory?: SocketFactory;
  resolveVersion?: () => Promise<WAVersion | undefined>;
  random?: () => number;
  downloader?: Downloader;
}

export type Downloader = (
  message: WAMessage,
  type: "stream",
  options: Record<string, never>,
  ctx?: { logger: Logger; reuploadRequest: (m: WAMessage) => Promise<WAMessage> },
) => Promise<NodeJS.ReadableStream>;

const defaultDownloader: Downloader = (message, type, options, ctx) =>
  downloadMediaMessage(message, type, options, ctx) as Promise<NodeJS.ReadableStream>;

const SEND_TIMEOUT_MS = 180_000;
const PAIRING_TIMEOUT_MS = 45_000;
const SENDS_KEPT = 2000;
const SENT_CACHE = 500;
const EVENT_BATCH = 200;
const REUPLOAD_STATUS = new Set([404, 410]);

/** WhatsApp message id derived from the idempotency key: a repeated send reuses the same id. */
export function messageIdFor(idempotencyKey: string): string {
  return "3EB0" + createHash("sha256").update(idempotencyKey, "utf8").digest("hex").slice(0, 18).toUpperCase();
}

export function statusCodeOf(error: unknown): number | undefined {
  if (!error || typeof error !== "object") return undefined;
  const out = (error as { output?: { statusCode?: unknown } }).output;
  if (out && typeof out.statusCode === "number") return out.statusCode;
  const direct = (error as { statusCode?: unknown }).statusCode;
  return typeof direct === "number" ? direct : undefined;
}

function httpStatusOf(error: unknown): number | undefined {
  const resp = (error as { response?: { status?: unknown } } | null)?.response;
  if (resp && typeof resp.status === "number") return resp.status;
  return statusCodeOf(error);
}

function withTimeout<T>(p: Promise<T>, ms: number, what: string): Promise<T> {
  let timer: NodeJS.Timeout | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new SidecarError("timeout", `${what}: Zeitüberschreitung`)), ms);
  });
  return Promise.race([p, timeout]).finally(() => clearTimeout(timer));
}

function chunks<T>(items: T[], size = EVENT_BATCH): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

export function chatInfo(c: Partial<Chat> & { id?: string | null }): ChatInfo | null {
  if (!c.id) return null;
  const jid = jidNormalizedUser(c.id);
  if (!jid || isJidStatusBroadcast(jid) || jid.endsWith("@newsletter")) return null;
  const info: ChatInfo = { jid, isGroup: Boolean(isJidGroup(jid)) };
  if (c.name) info.name = c.name;
  if (typeof c.unreadCount === "number") info.unread = Math.max(0, c.unreadCount);
  const ts = c.conversationTimestamp as unknown;
  if (typeof ts === "number") info.timestamp = ts;
  else if (ts && typeof ts === "object" && "toNumber" in ts) info.timestamp = (ts as { toNumber(): number }).toNumber();
  if (typeof c.archived === "boolean") info.archived = c.archived;
  const mute = c.muteEndTime as unknown;
  if (typeof mute === "number") info.mutedUntil = mute;
  return info;
}

export function contactInfo(c: Partial<Contact>): ContactInfo | null {
  const raw = c.jid || c.id;
  if (!raw) return null;
  const jid = jidNormalizedUser(raw);
  if (!jid || isJidGroup(jid)) return null;
  const info: ContactInfo = { jid };
  if (c.lid) info.lid = c.lid;
  if (c.name) info.name = c.name.slice(0, 120);
  if (c.notify) info.notify = c.notify.slice(0, 120);
  if (c.verifiedName) info.verifiedName = c.verifiedName.slice(0, 120);
  return info;
}

export class WhatsAppConnection {
  state: ConnectionStateName = "idle";
  private sock: SocketLike | undefined;
  private readonly store: EncryptedFileStore;
  private readonly factory: SocketFactory;
  private readonly resolveVersion: () => Promise<WAVersion | undefined>;
  private readonly downloader: Downloader;
  private wantConnected = false;
  private attempt = 0;
  private retryTimer: NodeJS.Timeout | undefined;
  private generation = 0;
  private me: { jid: string; name?: string } | undefined;
  private lastError: string | undefined;
  private lastExtra: Partial<StatusEvent> = {};
  private statusChain: Promise<void> = Promise.resolve();
  private pairing:
    | { phone: string; requested: boolean; resolve: (code: string) => void; reject: (e: Error) => void }
    | undefined;
  private readonly sends = new Map<string, { at: number; promise: Promise<SendResult> }>();
  private readonly sent = new Map<string, proto.IMessage>();

  constructor(private readonly opts: ConnectionOptions) {
    this.store = new EncryptedFileStore(opts.sessionDir, opts.secret);
    this.factory = opts.factory ?? realSocketFactory;
    this.resolveVersion = opts.resolveVersion ?? resolveWaVersion;
    this.downloader = opts.downloader ?? defaultDownloader;
  }

  hasSession(): Promise<boolean> {
    return this.store.hasCreds();
  }

  async status(): Promise<StatusEvent> {
    return this.snapshot(await this.hasSession());
  }

  private snapshot(hasSession: boolean): StatusEvent {
    const out: StatusEvent = { state: this.state, hasSession, ...this.lastExtra };
    if (this.me) out.me = this.me;
    if (this.lastError && !out.error) out.error = this.lastError;
    return out;
  }

  private setState(state: ConnectionStateName, extra: Partial<StatusEvent> = {}): void {
    this.state = state;
    this.lastExtra = extra;
    if (extra.error) this.lastError = extra.error;
    if (state === "open") this.lastError = undefined;
    // the snapshot is taken now; only `hasSession` needs the disk – emitted in order through one chain
    const pending = this.snapshot(false);
    this.statusChain = this.statusChain
      .then(() => this.hasSession().catch(() => false))
      .then((has) => this.opts.emit("status", { ...pending, hasSession: has }));
  }

  // ------------------------------------------------------------------ connect / disconnect
  async connect(method: "qr" | "pairing", phone?: string): Promise<{ started: boolean; pairingCode?: string }> {
    if (this.state === "open" && this.sock) return { started: false };
    let codePromise: Promise<string> | undefined;
    if (method === "pairing") {
      const digits = (phone ?? "").replace(/\D/g, "");
      if (digits.length < 7 || digits.length > 15) throw new SidecarError("invalid", "Telefonnummer mit Ländervorwahl angeben (nur Ziffern).");
      if (await this.hasSession()) throw new SidecarError("invalid", "Es besteht bereits eine Sitzung – erst abmelden.");
      codePromise = new Promise<string>((resolve, reject) => {
        this.pairing = { phone: digits, requested: false, resolve, reject };
      });
    } else {
      this.pairing = undefined;
    }
    this.wantConnected = true;
    this.attempt = 0;
    this.clearRetry();
    await this.openSocket();
    if (!codePromise) return { started: true };
    const pairingCode = await withTimeout(codePromise, PAIRING_TIMEOUT_MS, "Kopplungscode");
    return { started: true, pairingCode };
  }

  async resume(): Promise<boolean> {
    if (!(await this.hasSession())) return false;
    this.wantConnected = true;
    await this.openSocket();
    return true;
  }

  async disconnect(): Promise<void> {
    this.wantConnected = false;
    this.clearRetry();
    this.closeSocket();
    this.setState("idle");
  }

  async logout(): Promise<void> {
    this.wantConnected = false;
    this.clearRetry();
    const sock = this.sock;
    if (sock && this.state === "open") {
      try {
        await withTimeout(sock.logout("Jarvis: abgemeldet"), 15_000, "Abmelden");
      } catch (e) {
        this.opts.logger.warn({ err: String(e) }, "logout request failed – local session is removed anyway");
      }
    }
    this.closeSocket();
    await this.store.clear();
    this.me = undefined;
    this.lastError = undefined;
    this.setState("logged_out");
  }

  private clearRetry(): void {
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.retryTimer = undefined;
  }

  private closeSocket(): void {
    const sock = this.sock;
    this.sock = undefined;
    this.generation++;
    if (!sock) return;
    for (const ev of [
      "connection.update",
      "creds.update",
      "messages.upsert",
      "messages.update",
      "messages.reaction",
      "messages.delete",
      "messaging-history.set",
      "chats.upsert",
      "chats.update",
      "contacts.upsert",
      "contacts.update",
      "groups.upsert",
      "groups.update",
    ] as const) {
      sock.ev.removeAllListeners(ev);
    }
    try {
      sock.end(undefined);
    } catch {
      // already closed
    }
  }

  private async openSocket(): Promise<void> {
    this.closeSocket();
    const gen = this.generation;
    this.setState("connecting");
    const { state, saveCreds } = await useEncryptedAuthState(this.store);
    const version = await this.resolveVersion();
    if (gen !== this.generation) return; // disconnected meanwhile
    // WhatsApp currently answers a socket that asks for the full history with close code 428 (no QR, no login), so
    // the option stays off whatever the setting says; the recent history WhatsApp sends on its own is still stored
    const syncFullHistory = false;
    const sock = this.factory({
      auth: state,
      logger: this.opts.logger,
      ...(version ? { version } : {}),
      syncFullHistory,
      getMessage: async (key) => (key.id ? this.sent.get(key.id) : undefined),
    });
    this.sock = sock;
    const live = () => gen === this.generation && this.sock === sock;
    sock.ev.on("creds.update", () => {
      saveCreds().catch((e: unknown) => this.opts.logger.error({ err: String(e) }, "saving credentials failed"));
    });
    sock.ev.on("connection.update", (u) => {
      if (live()) void this.onConnectionUpdate(u, sock);
    });
    sock.ev.on("messages.upsert", ({ messages, type }) => {
      if (live()) this.onMessages(messages, type === "notify" ? "notify" : "append");
    });
    sock.ev.on("messages.update", (updates) => {
      if (live()) this.onMessageUpdates(updates);
    });
    sock.ev.on("messages.reaction", (items) => {
      if (!live()) return;
      const updates: MessageUpdate[] = [];
      for (const { key, reaction } of items) {
        if (!key.id || !key.remoteJid) continue;
        const sender = reaction.key?.fromMe ? (this.me?.jid ?? "me") : jidNormalizedUser(reaction.key?.participant || reaction.key?.remoteJid || "");
        updates.push({
          chatJid: jidNormalizedUser(key.remoteJid),
          id: key.id,
          fromMe: Boolean(key.fromMe),
          reaction: { senderJid: sender, emoji: reaction.text || "" },
        });
      }
      this.emitUpdates(updates);
    });
    sock.ev.on("messages.delete", (item) => {
      if (!live() || !("keys" in item)) return;
      this.emitUpdates(
        item.keys
          .filter((k) => k.id && k.remoteJid)
          .map((k) => ({ chatJid: jidNormalizedUser(k.remoteJid!), id: k.id!, fromMe: Boolean(k.fromMe), deleted: true })),
      );
    });
    sock.ev.on("messaging-history.set", ({ chats, contacts, messages, isLatest, progress, syncType }) => {
      if (!live()) return;
      this.emitChats(chats);
      this.emitContacts(contacts);
      this.onMessages(messages, "history");
      this.opts.emit("history_progress", {
        isLatest: Boolean(isLatest),
        progress: typeof progress === "number" ? progress : null,
        syncType: syncType ?? null,
        messages: messages.length,
      });
    });
    sock.ev.on("chats.upsert", (chats) => {
      if (live()) this.emitChats(chats);
    });
    sock.ev.on("chats.update", (chats) => {
      if (live()) this.emitChats(chats as Partial<Chat>[]);
    });
    sock.ev.on("groups.upsert", (groups) => {
      if (live()) this.emitGroupNames(groups);
    });
    sock.ev.on("groups.update", (groups) => {
      if (live()) this.emitGroupNames(groups);
    });
    sock.ev.on("contacts.upsert", (contacts) => {
      if (live()) this.emitContacts(contacts);
    });
    sock.ev.on("contacts.update", (contacts) => {
      if (live()) this.emitContacts(contacts);
    });
  }

  private async onConnectionUpdate(u: BaileysEventMap["connection.update"], sock: SocketLike): Promise<void> {
    if (u.qr) {
      const pairing = this.pairing;
      if (pairing && !pairing.requested) {
        pairing.requested = true;
        try {
          const code = await sock.requestPairingCode(pairing.phone);
          this.setState("pairing", { pairingCode: code });
          pairing.resolve(code);
        } catch (e) {
          const err = new SidecarError("failed", `Kopplungscode konnte nicht angefordert werden: ${(e as Error).message}`);
          pairing.reject(err);
          this.setState("error", { error: err.message });
        }
      } else if (!pairing) {
        this.setState("qr", { qr: u.qr });
      }
    }
    if (u.connection === "open") {
      this.attempt = 0;
      this.pairing = undefined;
      const user = sock.user;
      if (user?.id) this.me = { jid: jidNormalizedUser(user.id), ...(user.name ? { name: user.name } : {}) };
      this.setState("open");
      void this.refreshDirectory(sock);
    } else if (u.connection === "close") {
      await this.onClose(u.lastDisconnect?.error);
    }
  }

  private async onClose(error: unknown): Promise<void> {
    const code = statusCodeOf(error);
    const registered = await this.hasSession();
    this.closeSocket();
    const failPairing = (msg: string) => {
      this.pairing?.reject(new SidecarError("failed", msg));
      this.pairing = undefined;
    };
    if (code === DisconnectReason.loggedOut) {
      await this.store.clear();
      this.wantConnected = false;
      this.me = undefined;
      failPairing("Abgemeldet");
      this.setState("logged_out", { error: "WhatsApp hat die Verknüpfung beendet (Gerät entfernt oder abgemeldet). Zum erneuten Verbinden den QR-Code scannen." });
      return;
    }
    if (code === DisconnectReason.connectionReplaced) {
      this.wantConnected = false;
      this.setState("replaced", { error: "Eine andere Sitzung mit derselben Verknüpfung hat die Verbindung übernommen. Jarvis verbindet sich erst wieder, wenn Sie es auslösen." });
      return;
    }
    if (code === DisconnectReason.forbidden) {
      this.wantConnected = false;
      failPairing("Zugriff verweigert");
      this.setState("error", { error: "WhatsApp verweigert den Zugang (403). Jarvis versucht es nicht automatisch erneut – bitte den Status in der WhatsApp-App prüfen." });
      return;
    }
    if (code === DisconnectReason.restartRequired && this.wantConnected) {
      await this.openSocket(); // normal after a fresh login: WhatsApp asks for one reconnect
      return;
    }
    if (!this.wantConnected) {
      this.setState("idle");
      return;
    }
    if (!registered && code === DisconnectReason.timedOut) {
      this.wantConnected = false;
      failPairing("Zeitüberschreitung");
      this.setState("idle", { error: "Der QR-Code wurde nicht rechtzeitig gescannt. Bitte erneut verbinden." });
      return;
    }
    const delay = backoffDelay(this.attempt, this.opts.random ? { random: this.opts.random } : {});
    this.attempt += 1;
    this.setState("reconnecting", {
      retryInMs: delay,
      attempt: this.attempt,
      error: `Verbindung unterbrochen${code ? ` (Code ${code})` : ""} – neuer Versuch in ${Math.round(delay / 1000)} s`,
    });
    this.clearRetry();
    this.retryTimer = setTimeout(() => {
      this.retryTimer = undefined;
      if (!this.wantConnected) return;
      this.openSocket().catch((e: unknown) => {
        this.opts.logger.error({ err: String(e) }, "reconnect failed");
        void this.onClose(e);
      });
    }, delay);
  }

  /** Group subjects and address book names are not part of the chat list: fetch them once the link is open. */
  private async refreshDirectory(sock: SocketLike): Promise<void> {
    const live = () => this.sock === sock;
    try {
      const groups = await sock.groupFetchAllParticipating?.();
      if (groups && live()) this.emitGroupNames(Object.values(groups));
    } catch (e) {
      this.opts.logger.warn({ err: String(e) }, "could not fetch group names");
    }
    try {
      if (live()) await sock.resyncAppState?.(ALL_WA_PATCH_NAMES, true);
    } catch (e) {
      this.opts.logger.warn({ err: String(e) }, "could not sync the address book");
    }
  }

  private emitGroupNames(groups: { id?: string | null; subject?: string | null }[]): void {
    this.emitChats(groups.filter((g) => g.id && g.subject).map((g) => ({ id: g.id!, name: g.subject! })));
  }

  // ------------------------------------------------------------------ incoming
  private onMessages(messages: WAMessage[], type: "notify" | "append" | "history"): void {
    const normalized: NormalizedMessage[] = [];
    const updates: MessageUpdate[] = [];
    for (const m of messages) {
      try {
        const res = classify(m, this.me?.jid);
        if (res.message) normalized.push(res.message);
        if (res.update) updates.push(res.update);
      } catch (e) {
        this.opts.logger.warn({ err: String(e) }, "could not read a message");
      }
    }
    for (const batch of chunks(normalized)) this.opts.emit("messages", { type, messages: batch });
    this.emitUpdates(updates);
  }

  private onMessageUpdates(items: BaileysEventMap["messages.update"]): void {
    const updates: MessageUpdate[] = [];
    for (const { key, update } of items) {
      if (!key.id || !key.remoteJid) continue;
      const target = { chatJid: jidNormalizedUser(key.remoteJid), id: key.id, fromMe: Boolean(key.fromMe) };
      if (typeof update.status === "number") updates.push({ ...target, status: update.status });
      if (update.message === null || update.messageStubType === proto.WebMessageInfo.StubType.REVOKE) {
        updates.push({ ...target, deleted: true });
        continue;
      }
      if (update.message) {
        const inner = normalizeMessageContent(update.message);
        const text = textOf(inner);
        if (text && update.message.editedMessage) {
          const ts = Number(update.messageTimestamp ?? 0) || Math.floor(Date.now() / 1000);
          updates.push({ ...target, edit: { text, timestamp: ts } });
        }
      }
    }
    this.emitUpdates(updates);
  }

  private emitUpdates(updates: MessageUpdate[]): void {
    for (const batch of chunks(updates)) this.opts.emit("message_updates", { updates: batch });
  }

  private emitChats(chats: (Partial<Chat> & { id?: string | null })[]): void {
    const out = chats.map(chatInfo).filter((c): c is ChatInfo => c !== null);
    for (const batch of chunks(out)) this.opts.emit("chats", { chats: batch });
  }

  private emitContacts(contacts: Partial<Contact>[]): void {
    const out = contacts.map(contactInfo).filter((c): c is ContactInfo => c !== null);
    for (const batch of chunks(out)) this.opts.emit("contacts", { contacts: batch });
  }

  // ------------------------------------------------------------------ actions
  private requireOpen(): SocketLike {
    if (!this.sock || this.state !== "open") {
      throw new SidecarError("not_connected", "WhatsApp ist nicht verbunden.");
    }
    return this.sock;
  }

  send(params: SendParams): Promise<SendResult> {
    const key = params.idempotencyKey;
    if (!key || key.length > 200) throw new SidecarError("invalid", "idempotencyKey fehlt oder ist zu lang");
    const known = this.sends.get(key);
    if (known) return known.promise;
    const promise = this.doSend(params).catch((e: unknown) => {
      this.sends.delete(key); // a failed send may be retried with the same key
      throw e;
    });
    this.sends.set(key, { at: Date.now(), promise });
    if (this.sends.size > SENDS_KEPT) {
      const oldest = [...this.sends.entries()].sort((a, b) => a[1].at - b[1].at).slice(0, this.sends.size - SENDS_KEPT);
      for (const [k] of oldest) this.sends.delete(k);
    }
    return promise;
  }

  private async content(p: SendParams): Promise<AnyMessageContent> {
    if (p.kind === "text") {
      const text = (p.text ?? "").trim();
      if (!text) throw new SidecarError("invalid", "Leere Nachricht");
      return { text };
    }
    if (!p.filePath) throw new SidecarError("invalid", "filePath fehlt");
    const info = await stat(p.filePath).catch(() => null);
    if (!info?.isFile()) throw new SidecarError("not_found", "Datei nicht gefunden");
    if (p.maxBytes && info.size > p.maxBytes) throw new SidecarError("too_large", "Datei ist größer als erlaubt");
    const url = { url: p.filePath };
    const caption = p.caption?.trim() || undefined;
    switch (p.kind) {
      case "image":
        return { image: url, ...(caption ? { caption } : {}), ...(p.mimetype ? { mimetype: p.mimetype } : {}) };
      case "video":
        return { video: url, ...(caption ? { caption } : {}), ...(p.mimetype ? { mimetype: p.mimetype } : {}) };
      case "audio":
        return { audio: url, ptt: Boolean(p.ptt), mimetype: p.mimetype || (p.ptt ? "audio/ogg; codecs=opus" : "audio/mpeg") };
      case "document":
        return {
          document: url,
          mimetype: p.mimetype || "application/octet-stream",
          ...(p.fileName ? { fileName: p.fileName } : {}),
          ...(caption ? { caption } : {}),
        };
      default:
        throw new SidecarError("invalid", `Unbekannte Art ${String(p.kind)}`);
    }
  }

  private async doSend(p: SendParams): Promise<SendResult> {
    const sock = this.requireOpen();
    const jid = jidNormalizedUser(p.jid);
    if (!jid.includes("@")) throw new SidecarError("invalid", "Ungültige Chat-ID");
    const content = await this.content(p);
    const options: Record<string, unknown> = { messageId: messageIdFor(p.idempotencyKey) };
    if (p.quoted?.id) {
      const quoted: WAMessage = {
        key: { remoteJid: jid, id: p.quoted.id, fromMe: p.quoted.fromMe, ...(p.quoted.participant ? { participant: p.quoted.participant } : {}) },
        message: { conversation: p.quoted.text ?? "" },
      };
      options.quoted = quoted;
    }
    if (p.kind !== "text") this.opts.emit("send_progress", { idempotencyKey: p.idempotencyKey, stage: "uploading" });
    let sent: WAMessage | undefined;
    try {
      sent = await withTimeout(sock.sendMessage(jid, content, options), SEND_TIMEOUT_MS, "Senden");
    } catch (e) {
      if (e instanceof SidecarError) throw e;
      throw new SidecarError("send_failed", `Senden fehlgeschlagen: ${(e as Error).message ?? String(e)}`);
    }
    if (!sent?.key?.id) throw new SidecarError("send_failed", "WhatsApp hat den Versand nicht bestätigt");
    if (sent.message) {
      this.sent.set(sent.key.id, sent.message);
      if (this.sent.size > SENT_CACHE) this.sent.delete(this.sent.keys().next().value as string);
    }
    this.opts.emit("send_progress", { idempotencyKey: p.idempotencyKey, stage: "sent", messageId: sent.key.id });
    const normalized = classify(sent, this.me?.jid).message;
    return {
      messageId: sent.key.id,
      chatJid: jid,
      timestamp: Number(sent.messageTimestamp ?? 0) || Math.floor(Date.now() / 1000),
      status: typeof sent.status === "number" ? sent.status : proto.WebMessageInfo.Status.PENDING,
      ...(normalized ? { message: normalized } : {}),
    };
  }

  async markRead(keys: { chatJid: string; id: string; fromMe: boolean; participant?: string }[]): Promise<number> {
    const sock = this.requireOpen();
    const waKeys: WAMessageKey[] = keys
      .filter((k) => k.id && k.chatJid && !k.fromMe)
      .map((k) => ({ remoteJid: k.chatJid, id: k.id, fromMe: false, ...(k.participant ? { participant: k.participant } : {}) }));
    if (waKeys.length) await withTimeout(sock.readMessages(waKeys), 30_000, "Gelesen markieren");
    return waKeys.length;
  }

  async download(raw: string, destPath: string, maxBytes: number): Promise<DownloadResult> {
    let msg: WAMessage;
    try {
      msg = parseRaw(raw);
    } catch {
      throw new SidecarError("invalid", "Nachricht für den Download unlesbar");
    }
    const sock = this.state === "open" ? this.sock : undefined;
    let updated: WAMessage | undefined;
    let reuploadTried = false;
    const ctx = sock
      ? {
          logger: this.opts.logger,
          reuploadRequest: async (m: WAMessage) => {
            reuploadTried = true;
            updated = await withTimeout(sock.updateMediaMessage(m), 60_000, "Erneutes Hochladen anfordern");
            return updated;
          },
        }
      : undefined;
    await mkdir(dirname(destPath), { recursive: true, mode: 0o700 });
    const part = `${destPath}.part`;
    const hash = createHash("sha256");
    let size = 0;
    const counter = new Transform({
      transform(chunk: Buffer, _enc: BufferEncoding, cb: TransformCallback) {
        size += chunk.length;
        if (size > maxBytes) {
          cb(new SidecarError("too_large", "Die Datei überschreitet das Download-Limit"));
          return;
        }
        hash.update(chunk);
        cb(null, chunk);
      },
    });
    try {
      const stream = await this.downloader(msg, "stream", {}, ctx);
      await pipeline(stream, counter, createWriteStream(part, { mode: 0o600 }));
      await rename(part, destPath);
    } catch (e) {
      await rm(part, { force: true });
      if (e instanceof SidecarError) throw e;
      const status = httpStatusOf(e);
      if ((status !== undefined && REUPLOAD_STATUS.has(status)) || reuploadTried) {
        throw new SidecarError(
          "media_expired",
          sock
            ? "Die Datei ist auf den WhatsApp-Servern nicht mehr verfügbar; auch die Anfrage an das Absendergerät, sie erneut hochzuladen, war erfolglos."
            : "Die Datei ist auf den WhatsApp-Servern abgelaufen. Für eine Anfrage zum erneuten Hochladen muss WhatsApp verbunden sein.",
        );
      }
      throw new SidecarError("failed", `Download fehlgeschlagen: ${(e as Error).message ?? String(e)}`);
    }
    const result: DownloadResult = { path: destPath, size, sha256: hash.digest("hex") };
    if (updated) result.updatedRaw = serializeRaw(updated);
    return result;
  }

  async fetchHistory(jid: string, count: number, oldest: { id: string; fromMe: boolean; timestamp: number }): Promise<string> {
    const sock = this.requireOpen();
    const n = Math.max(1, Math.min(50, Math.floor(count)));
    return withTimeout(
      sock.fetchMessageHistory(n, { remoteJid: jidNormalizedUser(jid), id: oldest.id, fromMe: oldest.fromMe }, oldest.timestamp),
      30_000,
      "Verlauf anfordern",
    );
  }

  async groupMetadata(jid: string): Promise<{ subject: string; participants: { jid: string; admin: boolean }[] }> {
    const sock = this.requireOpen();
    const meta = await withTimeout(sock.groupMetadata(jidNormalizedUser(jid)), 30_000, "Gruppeninfo");
    return {
      subject: meta.subject,
      participants: meta.participants.map((p) => ({ jid: jidNormalizedUser(p.id), admin: Boolean(p.admin) })),
    };
  }

  async checkNumber(phone: string): Promise<{ exists: boolean; jid?: string }> {
    const sock = this.requireOpen();
    const digits = phone.replace(/\D/g, "");
    if (digits.length < 7 || digits.length > 15) throw new SidecarError("invalid", "Ungültige Telefonnummer");
    const res = await withTimeout(sock.onWhatsApp(`${digits}@s.whatsapp.net`), 30_000, "Nummer prüfen");
    const hit = res?.find((r) => Boolean(r.exists));
    return hit ? { exists: true, jid: jidNormalizedUser(hit.jid) } : { exists: false };
  }
}
