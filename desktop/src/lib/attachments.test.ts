// Chat attachments: type and size checks, admission of picked/dropped/pasted files, how they travel to the core.
import { describe, expect, it, vi } from "vitest";
import { createApi } from "./api";
import {
  admitFiles,
  attachmentKey,
  checkFile,
  formatBytes,
  MAX_ATTACHMENT_BYTES,
  MAX_ATTACHMENTS,
  messageAttachments,
  pastedName,
  typeLabel,
  type AttachmentInfo,
} from "./attachments";
import { addOptimisticUser, emptyThread, upsertEvent, type MessageData, type TimelineEvent } from "./timeline";

const f = (name: string, size = 1000) => ({ name, size, type: "" });
const info = (file: string, name = "foto.png"): AttachmentInfo => ({ file, name, kind: "image", mime: "image/png", size: 12 });

describe("checkFile", () => {
  it("accepts every supported type, case-insensitively", () => {
    for (const name of ["a.png", "b.JPG", "c.jpeg", "d.webp", "e.gif", "f.pdf", "g.txt", "h.md", "i.csv", "j.json", "k.docx"]) {
      expect(checkFile(f(name)).ok, name).toBe(true);
    }
    expect(checkFile(f("a.png"))).toEqual({ ok: true, kind: "image" });
    expect(checkFile(f("a.pdf"))).toEqual({ ok: true, kind: "document" });
  });

  it("rejects unknown types, empty and oversized files with a message that names the file and the limit", () => {
    const exe = checkFile(f("setup.exe"));
    expect(exe.ok).toBe(false);
    expect(!exe.ok && exe.error).toMatch(/„setup.exe“.*nicht unterstützt.*PNG, JPG/);
    expect(checkFile(f("ohne-endung")).ok).toBe(false);
    expect(checkFile(f(".png")).ok).toBe(false); // a dot file is not a png
    const empty = checkFile(f("leer.txt", 0));
    expect(!empty.ok && empty.error).toBe("„leer.txt“ ist leer.");
    expect(checkFile(f("max.pdf", MAX_ATTACHMENT_BYTES)).ok).toBe(true);
    const big = checkFile(f("video.png", MAX_ATTACHMENT_BYTES + 1));
    expect(!big.ok && big.error).toBe("„video.png“ ist 20,0 MB groß – erlaubt sind höchstens 20,0 MB je Datei.");
  });
});

describe("admitFiles", () => {
  it("keeps good files, reports each bad one, ignores duplicates and caps the count", () => {
    const current = [f("schon.png", 10)];
    const { accepted, errors } = admitFiles(current, [f("schon.png", 10), f("neu.pdf"), f("x.exe"), f("neu.pdf")]);
    expect(accepted.map((a) => [a.file.name, a.kind])).toEqual([["neu.pdf", "document"]]);
    expect(errors).toHaveLength(1);
    expect(errors[0]).toContain("x.exe");

    const many = Array.from({ length: MAX_ATTACHMENTS + 2 }, (_, i) => f(`bild-${i}.png`));
    const capped = admitFiles([], many);
    expect(capped.accepted).toHaveLength(MAX_ATTACHMENTS);
    expect(capped.errors).toEqual([`Höchstens ${MAX_ATTACHMENTS} Anhänge pro Nachricht – 2 Dateien wurden nicht angehängt.`]);
  });
});

describe("helpers", () => {
  it("formats sizes the German way and labels types", () => {
    expect(formatBytes(12)).toBe("12 B");
    expect(formatBytes(340 * 1024)).toBe("340 KB");
    expect(formatBytes(1.25 * 1024 * 1024)).toBe("1,3 MB");
    expect(typeLabel("Vertrag.final.docx")).toBe("DOCX");
    expect(typeLabel("README")).toBe("DATEI");
  });

  it("names pasted screenshots by time", () => {
    expect(pastedName("image/png", new Date(2026, 9, 22, 9, 5, 7))).toBe("Bildschirmfoto 2026-10-22 um 09.05.07.png");
    expect(pastedName("image/jpeg", new Date(2026, 0, 1, 0, 0, 0))).toMatch(/\.jpg$/);
  });

  it("accepts only well-formed attachments from the server", () => {
    const ok = info("20261022-141500-1a2b3c4d-foto.png");
    expect(messageAttachments([ok, { ...ok, file: "../../desktop.token" }, { ...ok, kind: "exe" }, null, "x"])).toEqual([ok]);
    expect(messageAttachments(undefined)).toEqual([]);
  });
});

describe("optimistic message with attachments", () => {
  it("is replaced by the server's copy with the same text and files, not by another one", () => {
    const a = info("20261022-141500-1a2b3c4d-a.png");
    const b = info("20261022-141500-0000aaaa-b.png");
    const t = addOptimisticUser(emptyThread("s"), "", [a]);
    const local = t.events[0] as TimelineEvent<MessageData>;
    expect(local.data.attachments).toEqual([a]);
    const server = (id: string, files: AttachmentInfo[]): TimelineEvent =>
      ({ id, session_id: "s", turn_id: "t", seq: 1, ts: 1, kind: "message", data: { role: "user", text: "", attachments: files } }) as TimelineEvent;
    const other = upsertEvent(t.events, server("srv-b", [b]));
    expect(other).toHaveLength(2); // different files: not the same message
    const same = upsertEvent(t.events, server("srv-a", [a]));
    expect(same).toHaveLength(1);
    expect(same[0]?.key).toBe(local.key);
    expect(attachmentKey([a, b])).toBe(`${a.file}|${b.file}`);
  });
});

describe("api", () => {
  const setup = () => {
    const fetchImpl = vi.fn(async (_url: string | URL | Request, _init?: RequestInit) => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    return { fetchImpl, api: createApi({ baseUrl: "http://core", getToken: () => "tok", fetchImpl: fetchImpl as unknown as typeof fetch }) };
  };

  it("uploads raw bytes with the file name in the query", async () => {
    const { api, fetchImpl } = setup();
    const file = new Blob(["png"], { type: "image/png" });
    await api.uploadAttachment(file, "Bildschirmfoto 1.png");
    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://core/api/attachments?name=Bildschirmfoto+1.png");
    expect(init.method).toBe("POST");
    expect(init.body).toBe(file);
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/octet-stream");
  });

  it("sends attachments by stored name only and stays compatible without them", async () => {
    const { api, fetchImpl } = setup();
    await api.sendChat("Hallo", null);
    expect(JSON.parse(fetchImpl.mock.calls[0]?.[1]?.body as string)).toEqual({ message: "Hallo" });
    await api.sendChat("", "sid", [info("20261022-141500-1a2b3c4d-a.png", "a.png")]);
    expect(JSON.parse(fetchImpl.mock.calls[1]?.[1]?.body as string)).toEqual({
      message: "",
      session_id: "sid",
      attachments: [{ file: "20261022-141500-1a2b3c4d-a.png", name: "a.png" }],
    });
    await api.revealMedia("attachments", "a b.pdf");
    expect(fetchImpl.mock.calls[2]?.[0]).toBe("http://core/api/media/attachments/a%20b.pdf/reveal");
  });
});
