import { PassThrough } from "node:stream";
import { describe, expect, it } from "vitest";
import { backoffDelay } from "../src/backoff.js";
import { LineIpc } from "../src/ipc.js";
import { SidecarError } from "../src/protocol.js";
import { maskDigits, redactLine } from "../src/redact.js";

describe("backoff", () => {
  it("doubles up to the cap", () => {
    const fixed = { random: () => 0.5 }; // no jitter
    expect([0, 1, 2, 3, 4, 5, 6, 7].map((a) => backoffDelay(a, fixed))).toEqual([1000, 2000, 4000, 8000, 16000, 32000, 60000, 60000]);
  });
  it("adds at most ±20 % jitter", () => {
    expect(backoffDelay(3, { random: () => 0 })).toBe(6400);
    expect(backoffDelay(3, { random: () => 1 })).toBe(9600);
  });
});

describe("log redaction", () => {
  it("masks phone numbers and jids", () => {
    expect(maskDigits("from 4915112345678@s.whatsapp.net")).toBe("from …78@s.whatsapp.net");
    expect(maskDigits("+49 151 1234 5678")).toBe("…78");
    expect(maskDigits("code 515")).toBe("code 515");
  });
  it("drops message content fields from Baileys log lines", () => {
    const line = JSON.stringify({ level: 30, msg: "recv", message: { conversation: "geheim" }, pushName: "Jonathan", jid: "4915112345678@s.whatsapp.net" });
    const out = redactLine(line);
    expect(out).not.toContain("geheim");
    expect(out).not.toContain("Jonathan");
    expect(out).not.toContain("4915112345678");
    expect(JSON.parse(out).msg).toBe("recv");
  });
});

describe("ipc", () => {
  async function roundTrip(lines: string[], handler: (m: string, p: Record<string, unknown>) => Promise<unknown>) {
    const input = new PassThrough();
    const output = new PassThrough();
    const ipc = new LineIpc(input, output, handler);
    ipc.start();
    const received: Record<string, unknown>[] = [];
    let buf = "";
    output.on("data", (c: Buffer) => {
      buf += c.toString();
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        received.push(JSON.parse(buf.slice(0, i)) as Record<string, unknown>);
        buf = buf.slice(i + 1);
      }
    });
    for (const l of lines) input.write(l);
    await new Promise((r) => setTimeout(r, 20));
    await ipc.settle();
    await new Promise((r) => setTimeout(r, 5));
    return received;
  }

  it("answers requests (also split across chunks) and maps errors to codes", async () => {
    const res = await roundTrip(
      ['{"type":"req","id":"1","method":"ok","par', 'ams":{"a":1}}\n', '{"type":"req","id":"2","method":"bad","params":{}}\n', "not json\n"],
      async (m, p) => {
        if (m === "bad") throw new SidecarError("not_connected", "WhatsApp ist nicht verbunden.");
        return { echo: p.a };
      },
    );
    expect(res).toContainEqual({ type: "res", id: "1", ok: true, result: { echo: 1 } });
    expect(res).toContainEqual({ type: "res", id: "2", ok: false, error: { code: "not_connected", message: "WhatsApp ist nicht verbunden." } });
    expect(res.some((r) => r.type === "event" && r.event === "protocol_error")).toBe(true);
  });
});
