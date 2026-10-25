import { describe, expect, it } from "vitest";
import {
  groupByDay,
  needsLogin,
  parseList,
  previewText,
  sendStatusTone,
  stateLabel,
  stateTone,
  uploadWithProgress,
  type WaMessage,
} from "./whatsapp";

const msg = (id: string, ts: string): WaMessage => ({
  id, chat_jid: "x@s.whatsapp.net", from_me: false, sender_jid: "x", sender_name: "X", ts, kind: "text", text: id,
  status: "", deleted: false, edited: false, forwarded: false, quoted_id: null,
});

class FakeXhr {
  status = 0;
  responseText = "";
  headers: Record<string, string> = {};
  sent: unknown = null;
  upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  open(method: string, url: string) {
    this.headers[":request"] = `${method} ${url}`;
  }
  setRequestHeader(k: string, v: string) {
    this.headers[k] = v;
  }
  send(body: unknown) {
    this.sent = body;
  }
  finish(status: number, body: unknown) {
    this.status = status;
    this.responseText = JSON.stringify(body);
    this.onload?.();
  }
}

describe("whatsapp helpers", () => {
  it("labels connection states and shows the login only without a session", () => {
    expect(stateLabel("open")).toBe("Verbunden");
    expect(stateLabel(undefined)).toBe("Unbekannt");
    expect(stateTone("qr")).toBe("warn");
    expect(stateTone("replaced")).toBe("bad");
    expect(needsLogin({ enabled: false })).toBe(false);
    expect(needsLogin({ enabled: true, state: "qr", has_session: true })).toBe(true);
    expect(needsLogin({ enabled: true, state: "idle", has_session: false })).toBe(true);
    expect(needsLogin({ enabled: true, state: "reconnecting", has_session: true })).toBe(false);
  });

  it("previews media by kind and file name", () => {
    expect(previewText({ kind: "document", text: "", from_me: true, ts: "", deleted: false, file_name: "Angebot.pdf" })).toBe("Du: Dokument: Angebot.pdf");
    expect(previewText({ kind: "voice", text: "", from_me: false, ts: "", deleted: false })).toBe("Sprachnachricht");
    expect(previewText({ kind: "image", text: "x", from_me: false, ts: "", deleted: true })).toBe("Nachricht gelöscht");
    expect(sendStatusTone("delivered")).toBe("ok");
    expect(sendStatusTone("failed")).toBe("bad");
  });

  it("groups messages by day and parses folder lists", () => {
    const now = new Date(2026, 9, 25, 12);
    const groups = groupByDay([msg("a", new Date(2026, 9, 24, 9).toISOString()), msg("b", new Date(2026, 9, 25, 8).toISOString()), msg("c", new Date(2026, 9, 25, 9).toISOString())], now);
    expect(groups.map((g) => [g.day, g.messages.map((m) => m.id)])).toEqual([["Gestern", ["a"]], ["Heute", ["b", "c"]]]);
    expect(parseList("~/Desktop\n ~/Documents, ~/Desktop\n\n")).toEqual(["~/Desktop", "~/Documents"]);
  });
});

describe("uploadWithProgress", () => {
  it("sends the raw file with the token and reports progress", async () => {
    const xhr = new FakeXhr();
    const seen: number[] = [];
    const file = new Blob(["hello"]);
    const p = uploadWithProgress("http://core/api/whatsapp/uploads?name=a.png", "tok", file, (f) => seen.push(f), () => xhr as unknown as XMLHttpRequest);
    expect(xhr.headers.Authorization).toBe("Bearer tok");
    expect(xhr.headers["Content-Type"]).toBe("application/octet-stream");
    expect(xhr.sent).toBe(file);
    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 2, total: 4 });
    xhr.finish(201, { ref: "staged:abc", name: "a.png", size: 5, size_label: "5 B", type: "PNG", category: "image" });
    await expect(p).resolves.toMatchObject({ ref: "staged:abc" });
    expect(seen).toEqual([0.5, 1]);
  });

  it("rejects with the server's reason", async () => {
    const xhr = new FakeXhr();
    const p = uploadWithProgress("u", "t", new Blob(["x"]), () => {}, () => xhr as unknown as XMLHttpRequest);
    xhr.finish(415, { detail: "Programmdateien werden nicht versendet." });
    await expect(p).rejects.toThrow("Programmdateien");
    const offline = new FakeXhr();
    const q = uploadWithProgress("u", "t", new Blob(["x"]), () => {}, () => offline as unknown as XMLHttpRequest);
    offline.onerror?.();
    await expect(q).rejects.toThrow("Core nicht erreichbar");
  });
});
