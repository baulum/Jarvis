// Turns Baileys' protobuf messages into the flat records the Core stores (see protocol.ts).
//
// Edits and deletions arrive as protocol messages that point at an older message; they become MessageUpdates
// instead of new messages. Media keep a BufferJSON copy of the original WAMessage (`raw`): the Core keeps it
// encrypted and hands it back for a download or a re-upload request when the CDN link has expired.
import {
  BufferJSON,
  isJidGroup,
  jidNormalizedUser,
  normalizeMessageContent,
  proto,
  toNumber,
  type WAMessage,
} from "@whiskeysockets/baileys";
import type { MediaInfo, MessageKind, MessageUpdate, NormalizedMessage } from "./protocol.js";

const THUMB_MAX_BYTES = 16 * 1024;
const TEXT_MAX = 64 * 1024;

type Long = { toNumber(): number } | number | null | undefined;

function num(v: Long): number | undefined {
  if (v === null || v === undefined) return undefined;
  const n = toNumber(v as Parameters<typeof toNumber>[0]);
  return Number.isFinite(n) ? n : undefined;
}

function b64(v: Uint8Array | string | null | undefined, max = Infinity): string | undefined {
  if (!v) return undefined;
  if (typeof v === "string") return v.length <= (max * 4) / 3 ? v : undefined;
  if (v.length === 0 || v.length > max) return undefined;
  return Buffer.from(v).toString("base64");
}

function clip(text: string | null | undefined): string | undefined {
  if (!text) return undefined;
  return text.length > TEXT_MAX ? text.slice(0, TEXT_MAX) : text;
}

export function chatJidOf(msg: WAMessage): string {
  return jidNormalizedUser(msg.key.remoteJid ?? "");
}

export function senderOf(msg: WAMessage, meJid?: string): string {
  if (msg.key.fromMe) return meJid ? jidNormalizedUser(meJid) : "me";
  const participant = msg.key.participant || msg.participant;
  return jidNormalizedUser(participant || msg.key.remoteJid || "");
}

/** Text content of a message (conversation, extended text or a media caption). */
export function textOf(content: proto.IMessage | null | undefined): string | undefined {
  if (!content) return undefined;
  return clip(
    content.conversation ||
      content.extendedTextMessage?.text ||
      content.imageMessage?.caption ||
      content.videoMessage?.caption ||
      content.documentMessage?.caption ||
      undefined,
  );
}

function contextOf(content: proto.IMessage): proto.IContextInfo | null | undefined {
  return (
    content.extendedTextMessage?.contextInfo ||
    content.imageMessage?.contextInfo ||
    content.videoMessage?.contextInfo ||
    content.audioMessage?.contextInfo ||
    content.documentMessage?.contextInfo ||
    content.stickerMessage?.contextInfo ||
    content.locationMessage?.contextInfo ||
    content.contactMessage?.contextInfo ||
    undefined
  );
}

function mediaInfo(m: {
  mimetype?: string | null;
  fileLength?: Long;
  fileSha256?: Uint8Array | null;
  jpegThumbnail?: Uint8Array | null;
  width?: number | null;
  height?: number | null;
}): MediaInfo {
  const info: MediaInfo = {};
  if (m.mimetype) info.mimetype = m.mimetype;
  const len = num(m.fileLength);
  if (len !== undefined) info.fileLength = len;
  const sha = b64(m.fileSha256);
  if (sha) info.fileSha256 = sha;
  const thumb = b64(m.jpegThumbnail, THUMB_MAX_BYTES);
  if (thumb) info.thumbnail = thumb;
  if (m.width) info.width = m.width;
  if (m.height) info.height = m.height;
  return info;
}

export function serializeRaw(msg: WAMessage): string {
  return JSON.stringify({ key: msg.key, message: msg.message, messageTimestamp: msg.messageTimestamp }, BufferJSON.replacer);
}

export function parseRaw(raw: string): WAMessage {
  const parsed = JSON.parse(raw, BufferJSON.reviver) as WAMessage;
  if (!parsed || typeof parsed !== "object" || !parsed.key || !parsed.message) throw new Error("invalid raw message");
  return parsed;
}

export interface Classified {
  message?: NormalizedMessage;
  update?: MessageUpdate;
}

/** One WAMessage → a new message, an update of an older one (edit, deletion, reaction) – or nothing. */
export function classify(msg: WAMessage, meJid?: string): Classified {
  const chatJid = chatJidOf(msg);
  if (!chatJid || !msg.key.id) return {};
  if (chatJid === "status@broadcast" || chatJid.endsWith("@newsletter")) return {};
  const content = normalizeMessageContent(msg.message);
  const timestamp = num(msg.messageTimestamp as Long) ?? Math.floor(Date.now() / 1000);
  const fromMe = Boolean(msg.key.fromMe);

  const protocol = content?.protocolMessage;
  if (protocol?.key?.id) {
    const target = {
      chatJid: jidNormalizedUser(protocol.key.remoteJid || chatJid),
      id: protocol.key.id,
      fromMe: Boolean(protocol.key.fromMe ?? fromMe),
    };
    if (protocol.type === proto.Message.ProtocolMessage.Type.REVOKE) return { update: { ...target, deleted: true } };
    if (protocol.type === proto.Message.ProtocolMessage.Type.MESSAGE_EDIT) {
      const text = textOf(normalizeMessageContent(protocol.editedMessage));
      return text ? { update: { ...target, edit: { text, timestamp } } } : {};
    }
    return {};
  }
  if (content?.reactionMessage?.key?.id) {
    const r = content.reactionMessage;
    const key = r.key!;
    return {
      update: {
        chatJid: jidNormalizedUser(key.remoteJid || chatJid),
        id: key.id!,
        fromMe: Boolean(key.fromMe),
        reaction: { senderJid: senderOf(msg, meJid), emoji: r.text || "" },
      },
    };
  }

  const out: NormalizedMessage = {
    id: msg.key.id,
    chatJid,
    fromMe,
    senderJid: senderOf(msg, meJid),
    timestamp,
    kind: "unsupported",
    isGroup: Boolean(isJidGroup(chatJid)),
  };
  if (msg.pushName && !fromMe) out.senderName = msg.pushName.slice(0, 120);
  if (typeof msg.status === "number") out.status = msg.status;

  if (!content) {
    if (msg.messageStubType) {
      out.kind = "system";
      out.system = proto.WebMessageInfo.StubType[msg.messageStubType] ?? String(msg.messageStubType);
      return { message: out };
    }
    return {};
  }

  const ctx = contextOf(content);
  if (ctx?.stanzaId) out.quotedId = ctx.stanzaId;
  if (ctx?.isForwarded) out.forwarded = true;
  const text = textOf(content);
  if (text) out.text = text;

  let kind: MessageKind = "unsupported";
  if (content.conversation || content.extendedTextMessage) kind = "text";
  else if (content.imageMessage) {
    kind = "image";
    out.media = mediaInfo(content.imageMessage);
  } else if (content.videoMessage) {
    kind = "video";
    out.media = { ...mediaInfo(content.videoMessage), ...(content.videoMessage.seconds ? { seconds: content.videoMessage.seconds } : {}) };
  } else if (content.audioMessage) {
    const a = content.audioMessage;
    kind = a.ptt ? "voice" : "audio";
    out.media = { ...mediaInfo(a), ptt: Boolean(a.ptt), ...(a.seconds ? { seconds: a.seconds } : {}) };
  } else if (content.documentMessage) {
    const d = content.documentMessage;
    kind = "document";
    out.media = mediaInfo(d);
    const name = d.fileName || d.title;
    if (name) out.media.fileName = name.slice(0, 255);
  } else if (content.stickerMessage) {
    kind = "sticker";
    out.media = mediaInfo(content.stickerMessage);
  } else if (content.locationMessage || content.liveLocationMessage) {
    const l = content.locationMessage || content.liveLocationMessage!;
    kind = "location";
    out.location = {
      latitude: l.degreesLatitude ?? 0,
      longitude: l.degreesLongitude ?? 0,
      ...("name" in l && l.name ? { name: l.name } : {}),
      ...("address" in l && l.address ? { address: l.address } : {}),
    };
  } else if (content.contactMessage || content.contactsArrayMessage) {
    kind = "contact";
    out.contact = { displayName: (content.contactMessage?.displayName || content.contactsArrayMessage?.displayName) ?? undefined };
  } else if (content.pollCreationMessage || content.pollCreationMessageV2 || content.pollCreationMessageV3) {
    const p = content.pollCreationMessage || content.pollCreationMessageV2 || content.pollCreationMessageV3!;
    kind = "poll";
    out.poll = { name: p.name ?? "", options: (p.options ?? []).map((o) => o.optionName ?? "").filter(Boolean) };
  }
  out.kind = kind;
  if (kind === "image" || kind === "video" || kind === "audio" || kind === "voice" || kind === "document" || kind === "sticker") {
    out.raw = serializeRaw(msg);
  }
  return { message: out };
}

/** Delivery status from a messages.update entry, if it carries one. */
export function statusUpdate(chatJid: string, id: string, fromMe: boolean, status: number | null | undefined): MessageUpdate | null {
  if (typeof status !== "number") return null;
  return { chatJid: jidNormalizedUser(chatJid), id, fromMe, status };
}
