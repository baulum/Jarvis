// Encryption at rest for the WhatsApp session (Signal keys, credentials).
//
// The Core hands over a random secret from the Jarvis key store (Keychain / Credential Manager / Secret
// Service, account "whatsapp-session-key") once, through the IPC pipe – never on the command line or in the
// environment. From it two keys are derived with HKDF-SHA256: one for AES-256-GCM, one for HMAC file names
// (so not even the names of the key files – which would contain phone numbers – are readable on disk).
import { createCipheriv, createDecipheriv, createHmac, hkdfSync, randomBytes } from "node:crypto";

const MAGIC = Buffer.from("JWS1");
const IV_BYTES = 12;
const TAG_BYTES = 16;

export interface SessionKeys {
  enc: Buffer;
  names: Buffer;
}

export function deriveKeys(secret: string): SessionKeys {
  if (!secret || secret.length < 32) throw new Error("session key too short");
  const ikm = Buffer.from(secret, "utf8");
  const salt = Buffer.from("jarvis-whatsapp-session");
  return {
    enc: Buffer.from(hkdfSync("sha256", ikm, salt, Buffer.from("aes-256-gcm v1"), 32)),
    names: Buffer.from(hkdfSync("sha256", ikm, salt, Buffer.from("file-names v1"), 32)),
  };
}

/** `aad` binds the ciphertext to its logical name: swapping two files on disk makes both unreadable. */
export function seal(key: Buffer, plaintext: Buffer, aad: string): Buffer {
  const iv = randomBytes(IV_BYTES);
  const cipher = createCipheriv("aes-256-gcm", key, iv);
  cipher.setAAD(Buffer.from(aad, "utf8"));
  const body = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  return Buffer.concat([MAGIC, iv, cipher.getAuthTag(), body]);
}

export function open(key: Buffer, sealed: Buffer, aad: string): Buffer {
  if (sealed.length < MAGIC.length + IV_BYTES + TAG_BYTES || !sealed.subarray(0, MAGIC.length).equals(MAGIC)) {
    throw new Error("not a Jarvis session file");
  }
  const iv = sealed.subarray(MAGIC.length, MAGIC.length + IV_BYTES);
  const tag = sealed.subarray(MAGIC.length + IV_BYTES, MAGIC.length + IV_BYTES + TAG_BYTES);
  const body = sealed.subarray(MAGIC.length + IV_BYTES + TAG_BYTES);
  const decipher = createDecipheriv("aes-256-gcm", key, iv);
  decipher.setAAD(Buffer.from(aad, "utf8"));
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(body), decipher.final()]);
}

export function fileNameFor(namesKey: Buffer, logical: string): string {
  return createHmac("sha256", namesKey).update(logical, "utf8").digest("hex").slice(0, 40) + ".enc";
}
