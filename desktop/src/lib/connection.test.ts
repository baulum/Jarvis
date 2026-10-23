import { describe, expect, it } from "vitest";
import { clearWebToken, normalizeCoreUrl, readWebToken, saveWebToken, servedByCore, validateCoreUrl, validateToken, WEB_TOKEN_KEY } from "./connection";

class MemoryStorage {
  data = new Map<string, string>();
  getItem(k: string) {
    return this.data.get(k) ?? null;
  }
  setItem(k: string, v: string) {
    this.data.set(k, v);
  }
  removeItem(k: string) {
    this.data.delete(k);
  }
}

describe("core address", () => {
  it("accepts https and loopback http only", () => {
    expect(validateCoreUrl("https://mac.tail1234.ts.net")).toBeNull();
    expect(validateCoreUrl("https://mac.tail1234.ts.net/")).toBeNull();
    expect(validateCoreUrl("http://127.0.0.1:8765")).toBeNull();
    expect(validateCoreUrl("http://100.101.102.103:8767")).toMatch(/https/);
    expect(validateCoreUrl("mac.local")).toMatch(/gültige/);
    expect(validateCoreUrl("https://mac.ts.net/app")).toMatch(/ohne Pfad/);
    expect(normalizeCoreUrl(" https://Mac.Tail1234.ts.net/ ")).toBe("https://mac.tail1234.ts.net");
  });

  it("recognises device tokens", () => {
    expect(validateToken("jdv_" + "a".repeat(43))).toBeNull();
    expect(validateToken("sk-ant-123")).toMatch(/jdv_/);
  });

  it("knows when it is the browser client served by the core", () => {
    expect(servedByCore({ pathname: "/app/" })).toBe(true);
    expect(servedByCore({ pathname: "/app" })).toBe(true);
    expect(servedByCore({ pathname: "/" })).toBe(false);
  });
});

describe("browser token storage", () => {
  it("keeps the token for the tab, remembers it only on request", () => {
    const session = new MemoryStorage();
    const local = new MemoryStorage();
    saveWebToken(" jdv_abc ", false, session, local);
    expect(readWebToken(session, local)).toBe("jdv_abc");
    expect(local.getItem(WEB_TOKEN_KEY)).toBeNull();
    saveWebToken("jdv_abc", true, session, local);
    expect(readWebToken(new MemoryStorage(), local)).toBe("jdv_abc");
    clearWebToken(session, local);
    expect(readWebToken(session, local)).toBeNull();
  });
});
