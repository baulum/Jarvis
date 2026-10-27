import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { EncryptedFileStore, isLinked, useEncryptedAuthState } from "../src/authState.js";
import { deriveKeys, fileNameFor, open, seal } from "../src/crypto.js";
import { SECRET, tempDir } from "./helpers.js";

describe("crypto", () => {
  it("round-trips and binds the ciphertext to its name", () => {
    const { enc } = deriveKeys(SECRET);
    const sealed = seal(enc, Buffer.from("geheim"), "creds.json");
    expect(open(enc, sealed, "creds.json").toString()).toBe("geheim");
    expect(() => open(enc, sealed, "other.json")).toThrow();
  });

  it("rejects a wrong key and tampering", () => {
    const a = deriveKeys(SECRET);
    const b = deriveKeys(SECRET + "-other");
    const sealed = seal(a.enc, Buffer.from("geheim"), "n");
    expect(() => open(b.enc, sealed, "n")).toThrow();
    const tampered = Buffer.from(sealed);
    tampered[tampered.length - 1] = (tampered.at(-1) ?? 0) ^ 1;
    expect(() => open(a.enc, tampered, "n")).toThrow();
  });

  it("refuses short secrets", () => {
    expect(() => deriveKeys("short")).toThrow();
  });

  it("hides logical names (no phone numbers in file names)", () => {
    const { names } = deriveKeys(SECRET);
    const name = fileNameFor(names, "session-4915112345678.0.json");
    expect(name).toMatch(/^[0-9a-f]{40}\.enc$/);
    expect(name).not.toContain("4915112345678");
  });
});

describe("encrypted auth state", () => {
  it("persists credentials encrypted and restores them after a restart", async () => {
    const dir = await tempDir();
    const store = new EncryptedFileStore(dir, SECRET);
    const first = await useEncryptedAuthState(store);
    expect(await store.hasCreds()).toBe(false);
    first.state.creds.registered = true;
    first.state.creds.me = { id: "4915112345678@s.whatsapp.net", name: "Sir" };
    await first.saveCreds();
    await first.state.keys.set({ "pre-key": { "1": { private: new Uint8Array([1, 2]), public: new Uint8Array([3, 4]) } } });

    const files = await readdir(dir);
    expect(files.length).toBe(2);
    for (const f of files) {
      const content = await readFile(join(dir, f));
      expect(content.subarray(0, 4).toString()).toBe("JWS1");
      expect(content.includes(Buffer.from("4915112345678"))).toBe(false);
    }

    // "restart": a new store over the same folder
    const again = await useEncryptedAuthState(new EncryptedFileStore(dir, SECRET));
    expect(again.state.creds.registered).toBe(true);
    expect(again.state.creds.me?.name).toBe("Sir");
    const keys = await again.state.keys.get("pre-key", ["1", "2"]);
    expect(Buffer.from(keys["1"]!.public).equals(Buffer.from([3, 4]))).toBe(true);
    expect(keys["2"]).toBeUndefined();
  });

  it("treats a session encrypted with another key as absent", async () => {
    const dir = await tempDir();
    const store = new EncryptedFileStore(dir, SECRET);
    const st = await useEncryptedAuthState(store);
    st.state.creds.registered = true;
    await st.saveCreds();
    expect(await new EncryptedFileStore(dir, SECRET + "-wrong").hasCreds()).toBe(false);
  });

  it("clear() removes the session", async () => {
    const dir = await tempDir();
    const store = new EncryptedFileStore(dir, SECRET);
    const st = await useEncryptedAuthState(store);
    st.state.creds.registered = true;
    await st.saveCreds();
    await store.clear();
    expect(await readdir(dir)).toEqual([]);
    expect(await store.hasCreds()).toBe(false);
  });

  it("deleting a key (null) removes its file", async () => {
    const dir = await tempDir();
    const store = new EncryptedFileStore(dir, SECRET);
    const st = await useEncryptedAuthState(store);
    await st.state.keys.set({ session: { a: new Uint8Array([1]) } });
    expect((await readdir(dir)).length).toBe(1);
    await st.state.keys.set({ session: { a: null } });
    expect((await readdir(dir)).length).toBe(0);
  });
});
