// Log hygiene: no message content and no phone numbers in any log line.
//
// Baileys logs through pino. Its JSON lines pass through `redactLine` before they reach stderr (which the Core
// writes to whatsapp-sidecar.log): content-carrying fields are dropped and digit runs that look like phone
// numbers (also inside JIDs) are masked.
import pino, { type Logger } from "pino";

const CONTENT_KEYS = new Set([
  "message",
  "messages",
  "text",
  "caption",
  "conversation",
  "body",
  "content",
  "pushName",
  "notify",
  "verifiedName",
  "name",
  "subject",
  "qr",
  "pairingCode",
  "fileName",
  "jpegThumbnail",
  "thumbnail",
  "mediaKey",
  "raw",
  "creds",
  "keys",
]);

const PHONE = /\+?\d[\d\s-]{5,}\d/g;

export function maskDigits(text: string): string {
  return text.replace(PHONE, (m) => {
    const digits = m.replace(/\D/g, "");
    return digits.length >= 6 ? `…${digits.slice(-2)}` : m;
  });
}

function scrub(value: unknown, depth = 0): unknown {
  if (depth > 6) return "[…]";
  if (typeof value === "string") return maskDigits(value).slice(0, 2000);
  if (Array.isArray(value)) return value.slice(0, 20).map((v) => scrub(v, depth + 1));
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value)) {
      out[k] = CONTENT_KEYS.has(k) ? "[redacted]" : scrub(v, depth + 1);
    }
    return out;
  }
  return value;
}

export function redactLine(line: string): string {
  const trimmed = line.trim();
  if (!trimmed) return "";
  try {
    return JSON.stringify(scrub(JSON.parse(trimmed)));
  } catch {
    return maskDigits(trimmed).slice(0, 2000);
  }
}

export function createLogger(level: string, write: (line: string) => void = (l) => process.stderr.write(l + "\n")): Logger {
  return pino(
    { level, base: undefined, timestamp: pino.stdTimeFunctions.isoTime },
    {
      write(chunk: string) {
        for (const line of chunk.split("\n")) {
          const clean = redactLine(line);
          if (clean) write(clean);
        }
      },
    },
  );
}
