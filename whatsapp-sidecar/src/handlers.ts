// Request dispatch: validates the Core's parameters and calls the connection.
import { isAbsolute } from "node:path";
import { version as nodeVersion } from "node:process";
import type { Logger } from "pino";
import { WhatsAppConnection, type ConnectionOptions, type SendKind } from "./connection.js";
import { METHODS, PROTOCOL_VERSION, SidecarError } from "./protocol.js";
import { createLogger } from "./redact.js";

export const BAILEYS_VERSION = "6.7.24";

function str(p: Record<string, unknown>, key: string, opts: { required?: boolean; max?: number } = {}): string | undefined {
  const v = p[key];
  if (v === undefined || v === null || v === "") {
    if (opts.required ?? true) throw new SidecarError("invalid", `${key} fehlt`);
    return undefined;
  }
  if (typeof v !== "string") throw new SidecarError("invalid", `${key} muss Text sein`);
  if (v.length > (opts.max ?? 100_000)) throw new SidecarError("invalid", `${key} ist zu lang`);
  return v;
}

function num(p: Record<string, unknown>, key: string, fallback: number): number {
  const v = p[key];
  if (v === undefined || v === null) return fallback;
  if (typeof v !== "number" || !Number.isFinite(v)) throw new SidecarError("invalid", `${key} muss eine Zahl sein`);
  return v;
}

function absPath(p: Record<string, unknown>, key: string, required = true): string | undefined {
  const v = str(p, key, { required, max: 4096 });
  if (v === undefined) return undefined;
  if (!isAbsolute(v) || v.includes("\0")) throw new SidecarError("invalid", `${key} muss ein absoluter Pfad sein`);
  return v;
}

const SEND_KINDS = new Set<SendKind>(["text", "image", "video", "audio", "document"]);

export interface HandlerDeps {
  emit: (event: string, data: unknown) => void;
  exit: () => void;
  makeConnection?: (opts: ConnectionOptions) => WhatsAppConnection;
  logger?: Logger;
}

export function createHandler(deps: HandlerDeps) {
  let conn: WhatsAppConnection | undefined;
  let logger: Logger = deps.logger ?? createLogger("warn");
  const need = (): WhatsAppConnection => {
    if (!conn) throw new SidecarError("not_initialised", "init fehlt");
    return conn;
  };

  const handler = async (method: string, p: Record<string, unknown>): Promise<unknown> => {
    switch (method) {
      case "init": {
        if (conn) return { hasSession: await conn.hasSession(), alreadyInitialised: true };
        const secret = str(p, "sessionKey", { max: 512 })!;
        const sessionDir = absPath(p, "sessionDir")!;
        const level = str(p, "logLevel", { required: false, max: 10 }) ?? "warn";
        if (!deps.logger) logger = createLogger(["fatal", "error", "warn", "info", "debug"].includes(level) ? level : "warn");
        const opts: ConnectionOptions = {
          sessionDir,
          secret,
          syncFullHistory: p.syncFullHistory !== false,
          emit: deps.emit,
          logger,
        };
        conn = deps.makeConnection ? deps.makeConnection(opts) : new WhatsAppConnection(opts);
        const hasSession = await conn.hasSession();
        if (hasSession && p.autoConnect !== false) {
          void conn.resume().catch((e: unknown) => logger.error({ err: String(e) }, "resume failed"));
        }
        return { hasSession, protocol: PROTOCOL_VERSION, baileys: BAILEYS_VERSION, node: nodeVersion };
      }
      case "connect": {
        const mode = str(p, "method", { required: false, max: 10 }) ?? "qr";
        if (mode !== "qr" && mode !== "pairing") throw new SidecarError("invalid", "method: qr oder pairing");
        return need().connect(mode, str(p, "phoneNumber", { required: false, max: 32 }));
      }
      case "disconnect":
        await need().disconnect();
        return { ok: true };
      case "logout":
        await need().logout();
        return { ok: true };
      case "status":
        return conn ? conn.status() : { state: "idle", hasSession: false, error: "nicht initialisiert" };
      case "send": {
        const kind = str(p, "kind", { max: 10 }) as SendKind;
        if (!SEND_KINDS.has(kind)) throw new SidecarError("invalid", "kind: text, image, video, audio oder document");
        const quotedRaw = p.quoted;
        let quoted: { id: string; fromMe: boolean; participant?: string; text?: string } | undefined;
        if (quotedRaw && typeof quotedRaw === "object") {
          const q = quotedRaw as Record<string, unknown>;
          quoted = {
            id: str(q, "id", { max: 128 })!,
            fromMe: q.fromMe === true,
            ...(typeof q.participant === "string" ? { participant: q.participant } : {}),
            ...(typeof q.text === "string" ? { text: q.text.slice(0, 1000) } : {}),
          };
        }
        const text = str(p, "text", { required: false, max: 65_536 });
        const caption = str(p, "caption", { required: false, max: 4096 });
        const filePath = absPath(p, "filePath", false);
        const fileName = str(p, "fileName", { required: false, max: 255 });
        const mimetype = str(p, "mimetype", { required: false, max: 120 });
        return need().send({
          jid: str(p, "jid", { max: 200 })!,
          kind,
          idempotencyKey: str(p, "idempotencyKey", { max: 200 })!,
          ...(text !== undefined ? { text } : {}),
          ...(caption !== undefined ? { caption } : {}),
          ...(filePath !== undefined ? { filePath } : {}),
          ...(fileName !== undefined ? { fileName } : {}),
          ...(mimetype !== undefined ? { mimetype } : {}),
          ptt: p.ptt === true,
          maxBytes: num(p, "maxBytes", 100 * 1024 * 1024),
          ...(quoted ? { quoted } : {}),
        });
      }
      case "markRead": {
        const keys = Array.isArray(p.keys) ? p.keys : [];
        const parsed = keys.slice(0, 500).map((k) => {
          const o = (k ?? {}) as Record<string, unknown>;
          return {
            chatJid: str(o, "chatJid", { max: 200 })!,
            id: str(o, "id", { max: 128 })!,
            fromMe: o.fromMe === true,
            ...(typeof o.participant === "string" ? { participant: o.participant } : {}),
          };
        });
        return { marked: await need().markRead(parsed) };
      }
      case "download":
        return need().download(str(p, "raw", { max: 2_000_000 })!, absPath(p, "destPath")!, num(p, "maxBytes", 64 * 1024 * 1024));
      case "fetchHistory": {
        const oldest = (p.oldest ?? {}) as Record<string, unknown>;
        const requestId = await need().fetchHistory(str(p, "jid", { max: 200 })!, num(p, "count", 50), {
          id: str(oldest, "id", { max: 128 })!,
          fromMe: oldest.fromMe === true,
          timestamp: num(oldest, "timestamp", 0),
        });
        return { requestId };
      }
      case "groupMetadata":
        return need().groupMetadata(str(p, "jid", { max: 200 })!);
      case "checkNumber":
        return need().checkNumber(str(p, "phone", { max: 32 })!);
      case "shutdown":
        if (conn) await conn.disconnect().catch(() => undefined);
        setImmediate(deps.exit);
        return { ok: true };
      default:
        throw new SidecarError("invalid", `Unbekannte Methode ${method} (bekannt: ${METHODS.join(", ")})`);
    }
  };
  return { handler, connection: () => conn };
}
