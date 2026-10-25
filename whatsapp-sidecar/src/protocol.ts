// Wire protocol between the Jarvis Core (Python) and this sidecar: one JSON object per line over stdio.
//
//   core → sidecar   {"type":"req","id":"7","method":"send","params":{...}}
//   sidecar → core   {"type":"res","id":"7","ok":true,"result":{...}}
//   sidecar → core   {"type":"res","id":"7","ok":false,"error":{"code":"not_connected","message":"…"}}
//   sidecar → core   {"type":"event","event":"status","data":{...}}
//
// The pipe is the only channel: the sidecar opens no port, so nothing but its parent process can talk to it.
// core/src/jarvis/whatsapp/sidecar.py implements the other side; tests on both sides pin the method names.

export const PROTOCOL_VERSION = 1;
export const MAX_LINE_BYTES = 32 * 1024 * 1024;

export const METHODS = [
  "init",
  "connect",
  "disconnect",
  "logout",
  "status",
  "send",
  "markRead",
  "download",
  "fetchHistory",
  "groupMetadata",
  "checkNumber",
  "shutdown",
] as const;
export type Method = (typeof METHODS)[number];

export const ERROR_CODES = [
  "invalid",
  "not_initialised",
  "not_connected",
  "not_found",
  "media_expired",
  "too_large",
  "send_failed",
  "timeout",
  "logged_out",
  "failed",
] as const;
export type ErrorCode = (typeof ERROR_CODES)[number];

export class SidecarError extends Error {
  constructor(
    readonly code: ErrorCode,
    message: string,
  ) {
    super(message);
  }
}

export type ConnectionStateName =
  | "idle"
  | "connecting"
  | "qr"
  | "pairing"
  | "open"
  | "reconnecting"
  | "logged_out"
  | "replaced"
  | "error";

export interface StatusEvent {
  state: ConnectionStateName;
  qr?: string;
  pairingCode?: string;
  me?: { jid: string; name?: string };
  error?: string;
  retryInMs?: number;
  attempt?: number;
  hasSession: boolean;
}

export type MessageKind =
  | "text"
  | "image"
  | "video"
  | "audio"
  | "voice"
  | "document"
  | "sticker"
  | "location"
  | "contact"
  | "poll"
  | "system"
  | "unsupported";

export interface MediaInfo {
  mimetype?: string;
  fileName?: string;
  fileLength?: number;
  seconds?: number;
  ptt?: boolean;
  width?: number;
  height?: number;
  /** base64 SHA-256 of the plaintext file as declared by the sender (checked after download) */
  fileSha256?: string;
  /** small JPEG preview (base64) that WhatsApp embeds in image/video/document messages */
  thumbnail?: string;
}

export interface NormalizedMessage {
  id: string;
  chatJid: string;
  fromMe: boolean;
  senderJid: string;
  senderName?: string;
  timestamp: number;
  kind: MessageKind;
  text?: string;
  quotedId?: string;
  isGroup: boolean;
  media?: MediaInfo;
  location?: { latitude: number; longitude: number; name?: string; address?: string };
  contact?: { displayName?: string };
  poll?: { name: string; options: string[] };
  system?: string;
  forwarded?: boolean;
  /** BufferJSON-serialised WAMessage – only for media messages (needed to download/re-request the file later) */
  raw?: string;
  status?: number;
}

export interface MessageUpdate {
  chatJid: string;
  id: string;
  fromMe: boolean;
  status?: number;
  edit?: { text: string; timestamp: number };
  deleted?: boolean;
  reaction?: { senderJid: string; emoji: string };
}

export interface ChatInfo {
  jid: string;
  name?: string;
  unread?: number;
  timestamp?: number;
  archived?: boolean;
  mutedUntil?: number;
  isGroup: boolean;
}

export interface ContactInfo {
  jid: string;
  lid?: string;
  name?: string;
  notify?: string;
  verifiedName?: string;
}
