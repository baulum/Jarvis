// Chat attachments: which files Sir may send and how a picked, dropped or pasted file becomes a draft chip.
// Same rules as the core (core/src/jarvis/agent/attachments.py) – the core checks everything again.

export type AttachmentKind = "image" | "document";

/** A stored attachment as the core returns it (and as the user message in the timeline carries it). */
export interface AttachmentInfo {
  file: string;
  name: string;
  kind: AttachmentKind;
  mime: string;
  size: number;
}

export const MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024;
export const MAX_ATTACHMENTS = 8;
export const SUPPORTED_LABEL = "PNG, JPG, WEBP, GIF, PDF, TXT, MD, CSV, JSON, DOCX";

const TYPES: Record<string, AttachmentKind> = {
  png: "image",
  jpg: "image",
  jpeg: "image",
  webp: "image",
  gif: "image",
  pdf: "document",
  txt: "document",
  md: "document",
  csv: "document",
  json: "document",
  docx: "document",
};
/** `accept` of the file picker */
export const ACCEPT = Object.keys(TYPES)
  .map((e) => `.${e}`)
  .join(",");

const PASTE_EXT: Record<string, string> = { "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif" };
// same file name rule as the core: a bare stored name, never a path
const STORED_NAME = /^[A-Za-z0-9][A-Za-z0-9_-]{0,100}\.[a-z0-9]{2,5}$/;

export function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
}

/** Short type label for chips: "PDF", "DOCX" … */
export const typeLabel = (name: string): string => extensionOf(name).toUpperCase() || "DATEI";

/** 1,2 MB · 340 KB · 12 B */
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1).replace(".", ",")} MB`;
}

export interface FileLike {
  name: string;
  size: number;
  type?: string;
}

export type FileCheck = { ok: true; kind: AttachmentKind } | { ok: false; error: string };

export function checkFile(f: FileLike): FileCheck {
  const kind = TYPES[extensionOf(f.name)];
  if (!kind) return { ok: false, error: `„${f.name}“: Dieser Dateityp wird nicht unterstützt. Erlaubt: ${SUPPORTED_LABEL}.` };
  if (f.size === 0) return { ok: false, error: `„${f.name}“ ist leer.` };
  if (f.size > MAX_ATTACHMENT_BYTES)
    return { ok: false, error: `„${f.name}“ ist ${formatBytes(f.size)} groß – erlaubt sind höchstens ${formatBytes(MAX_ATTACHMENT_BYTES)} je Datei.` };
  return { ok: true, kind };
}

/** Pasted screenshots arrive as "image.png": give them a name Sir recognises later. */
export function pastedName(type: string, now: Date = new Date()): string {
  const p = (n: number) => String(n).padStart(2, "0");
  const ext = PASTE_EXT[type] ?? "png";
  return `Bildschirmfoto ${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())} um ${p(now.getHours())}.${p(now.getMinutes())}.${p(now.getSeconds())}.${ext}`;
}

/**
 * Which of the incoming files join the draft: supported, within size, not already attached, at most
 * MAX_ATTACHMENTS in total. Every rejected file gets its own readable reason.
 */
export function admitFiles<T extends FileLike>(
  current: FileLike[],
  incoming: T[],
): { accepted: { file: T; kind: AttachmentKind }[]; errors: string[] } {
  const accepted: { file: T; kind: AttachmentKind }[] = [];
  const errors: string[] = [];
  const key = (f: FileLike) => `${f.name}\u0000${f.size}`;
  const seen = new Set(current.map(key));
  let skipped = 0;
  for (const f of incoming) {
    if (seen.has(key(f))) continue; // the same file twice: quietly once
    const res = checkFile(f);
    if (!res.ok) {
      errors.push(res.error);
      continue;
    }
    if (current.length + accepted.length >= MAX_ATTACHMENTS) {
      skipped += 1;
      continue;
    }
    seen.add(key(f));
    accepted.push({ file: f, kind: res.kind });
  }
  if (skipped) errors.push(`Höchstens ${MAX_ATTACHMENTS} Anhänge pro Nachricht – ${skipped} ${skipped === 1 ? "Datei wurde" : "Dateien wurden"} nicht angehängt.`);
  return { accepted, errors };
}

/** Attachments of a timeline message, only when well-formed (the data comes from the server). */
export function messageAttachments(raw: unknown): AttachmentInfo[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (a): a is AttachmentInfo =>
      Boolean(a) &&
      typeof a === "object" &&
      typeof (a as AttachmentInfo).file === "string" &&
      STORED_NAME.test((a as AttachmentInfo).file) &&
      typeof (a as AttachmentInfo).name === "string" &&
      ((a as AttachmentInfo).kind === "image" || (a as AttachmentInfo).kind === "document") &&
      typeof (a as AttachmentInfo).size === "number",
  );
}

/** Stable identity of a message's attachments (to match the server's copy of an optimistic message). */
export const attachmentKey = (list: AttachmentInfo[] | undefined): string => (list ?? []).map((a) => a.file).join("|");
