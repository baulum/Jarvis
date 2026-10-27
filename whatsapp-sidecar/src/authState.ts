// Encrypted, file-based Baileys auth state (drop-in for useMultiFileAuthState).
//
// Every credential/key file is AES-256-GCM encrypted (see crypto.ts) and written atomically (temp file +
// rename, mode 0600, folder 0700). A restart of the Core therefore restores the session without a new QR scan
// as long as WhatsApp still accepts it.
import { mkdir, readFile, readdir, rename, rm, stat, unlink, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { BufferJSON, initAuthCreds, proto } from "@whiskeysockets/baileys";
import type { AuthenticationCreds, AuthenticationState, SignalDataSet, SignalDataTypeMap } from "@whiskeysockets/baileys";
import { deriveKeys, fileNameFor, open, seal, type SessionKeys } from "./crypto.js";

const CREDS = "creds.json";

class KeyedLock {
  private tails = new Map<string, Promise<void>>();

  async run<T>(key: string, fn: () => Promise<T>): Promise<T> {
    const prev = this.tails.get(key) ?? Promise.resolve();
    let release!: () => void;
    const next = new Promise<void>((r) => (release = r));
    const tail = prev.then(() => next);
    this.tails.set(key, tail);
    await prev;
    try {
      return await fn();
    } finally {
      release();
      if (this.tails.get(key) === tail) this.tails.delete(key);
    }
  }
}

export interface EncryptedAuthState {
  state: AuthenticationState;
  saveCreds: () => Promise<void>;
}

/** Baileys never sets `registered` when a device is linked by QR or pairing code – the account in `me` is what counts. */
export function isLinked(creds: Pick<AuthenticationCreds, "registered" | "me"> | null | undefined): boolean {
  return Boolean(creds?.registered || creds?.me?.id);
}

export class EncryptedFileStore {
  private readonly keys: SessionKeys;
  private readonly lock = new KeyedLock();
  // clear() bumps the epoch: a write that started before (e.g. a late creds.update) is discarded, so a logout
  // can never be undone by a key file landing afterwards
  private epoch = 0;
  private readonly pending = new Set<Promise<void>>();

  constructor(
    readonly folder: string,
    secret: string,
  ) {
    this.keys = deriveKeys(secret);
  }

  private path(logical: string): string {
    return join(this.folder, fileNameFor(this.keys.names, logical));
  }

  async ensureFolder(): Promise<void> {
    const info = await stat(this.folder).catch(() => null);
    if (info && !info.isDirectory()) throw new Error("session path is not a directory");
    if (!info) await mkdir(this.folder, { recursive: true, mode: 0o700 });
  }

  async write(logical: string, value: unknown): Promise<void> {
    const file = this.path(logical);
    const epoch = this.epoch;
    const job = this.lock.run(file, async () => {
      if (epoch !== this.epoch) return;
      const sealed = seal(this.keys.enc, Buffer.from(JSON.stringify(value, BufferJSON.replacer), "utf8"), logical);
      const tmp = `${file}.${process.pid}.tmp`;
      await writeFile(tmp, sealed, { mode: 0o600 });
      if (epoch !== this.epoch) {
        await rm(tmp, { force: true });
        return;
      }
      await rename(tmp, file);
    });
    this.pending.add(job);
    try {
      await job;
    } finally {
      this.pending.delete(job);
    }
  }

  async read<T = unknown>(logical: string): Promise<T | null> {
    const file = this.path(logical);
    return this.lock.run(file, async () => {
      let raw: Buffer;
      try {
        raw = await readFile(file);
      } catch {
        return null;
      }
      // A file that cannot be decrypted (other key, tampering) is treated as absent: WhatsApp then asks for a
      // new login instead of the sidecar crashing in a loop.
      try {
        return JSON.parse(open(this.keys.enc, raw, logical).toString("utf8"), BufferJSON.reviver) as T;
      } catch {
        return null;
      }
    });
  }

  async remove(logical: string): Promise<void> {
    const file = this.path(logical);
    await this.lock.run(file, async () => {
      await unlink(file).catch(() => undefined);
    });
  }

  async hasCreds(): Promise<boolean> {
    const creds = await this.read<AuthenticationCreds>(CREDS);
    return isLinked(creds);
  }

  async clear(): Promise<void> {
    this.epoch++;
    await Promise.allSettled([...this.pending]);
    const entries = await readdir(this.folder).catch(() => [] as string[]);
    await Promise.all(
      entries.filter((e) => e.endsWith(".enc") || e.endsWith(".tmp")).map((e) => rm(join(this.folder, e), { force: true })),
    );
  }
}

export async function useEncryptedAuthState(store: EncryptedFileStore): Promise<EncryptedAuthState> {
  await store.ensureFolder();
  const creds = (await store.read<AuthenticationCreds>(CREDS)) ?? initAuthCreds();
  return {
    state: {
      creds,
      keys: {
        get: async <T extends keyof SignalDataTypeMap>(type: T, ids: string[]) => {
          const out: { [id: string]: SignalDataTypeMap[T] } = {};
          await Promise.all(
            ids.map(async (id) => {
              let value = await store.read<SignalDataTypeMap[T]>(`${type}-${id}.json`);
              if (type === "app-state-sync-key" && value) {
                value = proto.Message.AppStateSyncKeyData.fromObject(value) as unknown as SignalDataTypeMap[T];
              }
              if (value !== null) out[id] = value;
            }),
          );
          return out;
        },
        set: async (data: SignalDataSet) => {
          const tasks: Promise<void>[] = [];
          for (const category of Object.keys(data) as (keyof SignalDataTypeMap)[]) {
            const entries = data[category] ?? {};
            for (const id of Object.keys(entries)) {
              const value = entries[id];
              const name = `${category}-${id}.json`;
              tasks.push(value ? store.write(name, value) : store.remove(name));
            }
          }
          await Promise.all(tasks);
        },
      },
    },
    saveCreds: () => store.write(CREDS, creds),
  };
}
