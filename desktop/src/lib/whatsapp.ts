// WhatsApp connector: types of /api/whatsapp/* and the small pure helpers the views use.
import type { Tone } from "./format";

export type WaState =
  | "stopped"
  | "starting"
  | "restarting"
  | "idle"
  | "connecting"
  | "qr"
  | "pairing"
  | "open"
  | "reconnecting"
  | "logged_out"
  | "replaced"
  | "error";

export interface WaStatus {
  enabled: boolean;
  state?: WaState;
  connected?: boolean;
  has_session?: boolean;
  error?: string | null;
  retry_in_ms?: number | null;
  attempt?: number | null;
  me?: { name: string; phone: string } | null;
  history?: { progress: number | null; latest: boolean; updated_at: string } | null;
  sidecar?: { running: boolean; restarts: number; error: string | null; baileys?: string | null };
  qr_svg?: string;
  pairing_code?: string;
  counts?: { chats: number; messages: number; files: number; unread_chats: number; pending_suggestions: number };
  download_dir?: string;
}

export interface WaLastMessage {
  kind: string;
  text: string;
  from_me: boolean;
  ts: string;
  deleted: boolean;
  file_name?: string | null;
}

export interface WaChat {
  jid: string;
  name: string;
  is_group: boolean;
  unread: number;
  last_message_at: string | null;
  archived: boolean;
  muted: boolean;
  last_message?: WaLastMessage | null;
}

export interface WaFile {
  file_id: string;
  chat_jid: string;
  message_id: string;
  sender_name: string;
  chat_name: string;
  name: string;
  location: string;
  path: string;
  mime: string;
  type: string;
  size: number;
  size_label?: string;
  sha256: string;
  duplicate_of: string | null;
  stored_at: string;
}

export interface WaMessage {
  id: string;
  chat_jid: string;
  from_me: boolean;
  sender_jid: string;
  sender_name: string;
  ts: string;
  kind: string;
  text: string;
  status: string;
  deleted: boolean;
  edited: boolean;
  forwarded: boolean;
  quoted_id: string | null;
  media?: { mimetype?: string; fileName?: string; fileLength?: number; seconds?: number; width?: number; height?: number };
  has_thumbnail?: boolean;
  location?: { latitude: number; longitude: number; name?: string };
  contact?: { displayName?: string };
  poll?: { name: string; options: string[] };
  reactions?: { sender: string; emoji: string }[];
  files?: WaFile[];
}

export interface WaConversation {
  chat: { jid: string; name: string; is_group: boolean; unread: number };
  messages: WaMessage[];
  has_more: boolean;
}

export interface WaSend {
  send_key: string;
  chat_jid: string;
  chat_name: string;
  kind: string;
  files: { name: string; type: string; size: number; sha256: string }[];
  text_len: number;
  status: string;
  message_ids: string[];
  error: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface WaSuggestion {
  id: string;
  chat_jid: string;
  chat_name: string;
  message_id: string | null;
  kind: string;
  title: string;
  detail: string;
  due_at: string | null;
  status: string;
  created_at: string;
}

export interface WaUpload {
  ref: string;
  name: string;
  size: number;
  size_label: string;
  type: string;
  category: string;
}

export interface WaSendResult {
  status: string;
  tool: string;
  approval_id?: string;
  summary?: string;
  message?: string;
  data?: unknown;
}

export interface WaLocations {
  download_dir: string;
  allowed: { path: string; exists: boolean }[];
  devices: { name: string; online: boolean; platform?: string }[];
}

const STATE_LABEL: Record<WaState, string> = {
  stopped: "Dienst gestoppt",
  starting: "Startet",
  restarting: "Dienst startet neu",
  idle: "Nicht verbunden",
  connecting: "Verbindet",
  qr: "Wartet auf QR-Scan",
  pairing: "Wartet auf Kopplungscode",
  open: "Verbunden",
  reconnecting: "Verbindung wird wiederhergestellt",
  logged_out: "Abgemeldet",
  replaced: "Von anderer Sitzung übernommen",
  error: "Fehler",
};

export function stateLabel(s: WaState | undefined): string {
  return s ? STATE_LABEL[s] ?? s : "Unbekannt";
}

export function stateTone(s: WaState | undefined): Tone {
  switch (s) {
    case "open":
      return "ok";
    case "qr":
    case "pairing":
    case "connecting":
    case "starting":
    case "restarting":
    case "reconnecting":
      return "warn";
    case "error":
    case "replaced":
      return "bad";
    default:
      return "idle";
  }
}

/** The login panel is shown while there is no usable session. */
export function needsLogin(st: WaStatus | undefined): boolean {
  if (!st?.enabled) return false;
  if (st.state === "qr" || st.state === "pairing") return true;
  return !st.has_session && st.state !== "open" && st.state !== "connecting";
}

const KIND_LABEL: Record<string, string> = {
  image: "Foto",
  video: "Video",
  audio: "Audio",
  voice: "Sprachnachricht",
  document: "Dokument",
  sticker: "Sticker",
  location: "Standort",
  contact: "Kontakt",
  poll: "Umfrage",
  system: "Hinweis",
};

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? "Nachricht";
}

export const MEDIA_KINDS = new Set(["image", "video", "audio", "voice", "document", "sticker"]);

/** One line for the chat list: the text, or what kind of message it was. */
export function previewText(m: WaLastMessage | null | undefined): string {
  if (!m) return "";
  if (m.deleted) return "Nachricht gelöscht";
  const prefix = m.from_me ? "Du: " : "";
  if (m.text) return prefix + m.text;
  if (m.file_name) return `${prefix}${kindLabel(m.kind)}: ${m.file_name}`;
  return prefix + kindLabel(m.kind);
}

const RECEIPT: Record<string, string> = {
  pending: "wird gesendet",
  server_ack: "gesendet",
  delivered: "zugestellt",
  read: "gelesen",
  played: "abgespielt",
  error: "nicht zugestellt",
};

export function receiptLabel(status: string): string {
  return RECEIPT[status] ?? "";
}

const SEND_STATUS: Record<string, string> = {
  pending: "vorbereitet",
  uploading: "lädt hoch",
  sent: "gesendet, Bestätigung ausstehend",
  server_ack: "vom Server angenommen",
  delivered: "zugestellt",
  read: "gelesen",
  played: "abgespielt",
  failed: "fehlgeschlagen",
  error: "nicht zugestellt",
};

export function sendStatusLabel(status: string): string {
  return SEND_STATUS[status] ?? status;
}

export function sendStatusTone(status: string): Tone {
  if (status === "failed" || status === "error") return "bad";
  if (["server_ack", "delivered", "read", "played"].includes(status)) return "ok";
  return "warn";
}

const SUGGESTION_LABEL: Record<string, string> = {
  task: "Aufgabe",
  appointment: "Termin",
  reminder: "Erinnerung",
  invoice: "Rechnung",
  change_request: "Änderungswunsch",
  project_info: "Projektinfo",
};

export function suggestionLabel(kind: string): string {
  return SUGGESTION_LABEL[kind] ?? kind;
}

/** Day separators in a conversation: "Heute", "Gestern" or the date. */
export function dayLabel(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  const start = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((start(now) - start(d)) / 86_400_000);
  if (diff === 0) return "Heute";
  if (diff === 1) return "Gestern";
  return d.toLocaleDateString("de-DE", { weekday: diff < 7 ? "long" : undefined, day: "2-digit", month: "2-digit", year: "numeric" });
}

export function timeLabel(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "";
  const d = new Date(iso);
  const sameDay = d.toDateString() === now.toDateString();
  return sameDay
    ? d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" });
}

/** Groups messages under day headings, preserving order. */
export function groupByDay(messages: WaMessage[], now: Date = new Date()): { day: string; messages: WaMessage[] }[] {
  const out: { day: string; messages: WaMessage[] }[] = [];
  for (const m of messages) {
    const day = dayLabel(m.ts, now);
    const last = out.at(-1);
    if (last && last.day === day) last.messages.push(m);
    else out.push({ day, messages: [m] });
  }
  return out;
}

/** Line breaks / comma separated folder list ↔ array (settings form). */
export function parseList(text: string): string[] {
  return [...new Set(text.split(/[\n,]/).map((s) => s.trim()).filter(Boolean))];
}

/**
 * Uploads a file for sending with progress (fetch has no upload progress, XMLHttpRequest does).
 * Resolves with the staged reference the send endpoint accepts.
 */
export function uploadWithProgress(
  url: string,
  token: string,
  file: Blob,
  onProgress: (fraction: number) => void,
  xhrFactory: () => XMLHttpRequest = () => new XMLHttpRequest(),
): Promise<WaUpload> {
  return new Promise((resolve, reject) => {
    const xhr = xhrFactory();
    xhr.open("POST", url);
    xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && e.total > 0) onProgress(Math.min(1, e.loaded / e.total));
    };
    xhr.onload = () => {
      let body: unknown;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        body = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress(1);
        resolve(body as WaUpload);
      } else {
        const detail = body && typeof body === "object" && "detail" in body ? String((body as { detail: unknown }).detail) : `HTTP ${xhr.status}`;
        reject(new Error(detail));
      }
    };
    xhr.onerror = () => reject(new Error("Core nicht erreichbar."));
    xhr.send(file);
  });
}
