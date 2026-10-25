import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { Readable } from "node:stream";
import { proto, type WAMessage } from "@whiskeysockets/baileys";
import { afterEach, describe, expect, it, vi } from "vitest";
import { messageIdFor, WhatsAppConnection, type Downloader } from "../src/connection.js";
import { serializeRaw } from "../src/normalize.js";
import { SidecarError } from "../src/protocol.js";
import { fakeTimers, flush, harness, SECRET, silentLogger, tempDir, untilState, waitFor, type Harness } from "./helpers.js";

const boom = (statusCode: number) => Object.assign(new Error(`closed ${statusCode}`), { output: { statusCode } });

async function setup(extra: { downloader?: Downloader } = {}) {
  const h = harness();
  const dir = await tempDir();
  const conn = new WhatsAppConnection({
    sessionDir: dir,
    secret: SECRET,
    syncFullHistory: true,
    emit: (event, data) => h.events.push({ event, data: data as Record<string, unknown> }),
    logger: silentLogger,
    factory: h.factory,
    resolveVersion: async () => undefined,
    random: () => 0.5,
    ...extra,
  });
  return { h, dir, conn };
}

async function open(h: Harness, conn: WhatsAppConnection) {
  await conn.connect("qr");
  h.last().emit("connection.update", { connection: "open" });
  await flush();
}

afterEach(() => {
  vi.useRealTimers();
});

describe("login", () => {
  it("QR flow: shows the QR, then reports the connected account", async () => {
    const { h, conn } = await setup();
    await conn.connect("qr");
    h.last().emit("connection.update", { qr: "2@abc,def" });
    expect(await untilState(h, "qr")).toMatchObject({ qr: "2@abc,def" });
    h.last().emit("connection.update", { connection: "open" });
    expect(await untilState(h, "open")).toMatchObject({ me: { jid: "4915112345678@s.whatsapp.net", name: "Sir" } });
    expect(conn.state).toBe("open");
  });

  it("pairing code flow: requests the code for the given number", async () => {
    const { h, conn } = await setup();
    const pending = conn.connect("pairing", "+49 151 1234-5678");
    await flush();
    h.last().emit("connection.update", { qr: "ignored-when-pairing" });
    const res = await pending;
    await waitFor(() => h.statuses().at(-1)?.state === "pairing");
    expect(res.pairingCode).toBe("ABCD-EFGH");
    expect(h.last().requestPairingCode).toHaveBeenCalledWith("4915112345678");
    expect(h.statuses().some((s) => s.state === "qr")).toBe(false);
    expect(h.statuses().at(-1)).toMatchObject({ state: "pairing", pairingCode: "ABCD-EFGH" });
  });

  it("rejects an invalid pairing number", async () => {
    const { conn } = await setup();
    await expect(conn.connect("pairing", "12")).rejects.toBeInstanceOf(SidecarError);
  });

  it("does not retry forever when the QR code is never scanned", async () => {
    fakeTimers();
    const { h, conn } = await setup();
    await conn.connect("qr");
    h.last().emit("connection.update", { connection: "close", lastDisconnect: { error: boom(408), date: new Date() } });
    const st = await untilState(h, "idle");
    expect(String(st.error)).toContain("QR");
    await vi.advanceTimersByTimeAsync(120_000);
    expect(h.sockets.length).toBe(1);
  });
});

describe("reconnect", () => {
  it("reconnects with exponential backoff after a dropped connection", async () => {
    fakeTimers();
    const { h, conn } = await setup();
    await open(h, conn);
    h.last().emit("connection.update", { connection: "close", lastDisconnect: { error: boom(428), date: new Date() } });
    expect(await untilState(h, "reconnecting")).toMatchObject({ retryInMs: 1000, attempt: 1 });
    await vi.advanceTimersByTimeAsync(1000);
    await waitFor(() => h.sockets.length === 2);
    h.last().emit("connection.update", { connection: "close", lastDisconnect: { error: boom(500), date: new Date() } });
    await waitFor(() => h.statuses().at(-1)?.attempt === 2);
    expect(h.statuses().at(-1)).toMatchObject({ state: "reconnecting", retryInMs: 2000 });
    await vi.advanceTimersByTimeAsync(1999);
    expect(h.sockets.length).toBe(2);
    await vi.advanceTimersByTimeAsync(1);
    await waitFor(() => h.sockets.length === 3);
    h.last().emit("connection.update", { connection: "open" });
    await untilState(h, "open");
    expect(conn.state).toBe("open");
  });

  it("reconnects at once when WhatsApp asks for a restart (515)", async () => {
    const { h, conn } = await setup();
    await open(h, conn);
    h.last().emit("connection.update", { connection: "close", lastDisconnect: { error: boom(515), date: new Date() } });
    await flush(10);
    expect(h.sockets.length).toBe(2);
  });

  it("never retries a logout and removes the session", async () => {
    fakeTimers();
    const { h, conn } = await setup();
    await conn.connect("qr");
    h.configs.at(-1)!.auth.creds.registered = true;
    h.last().emit("creds.update", {});
    await waitFor(() => conn.hasSession());
    h.last().emit("connection.update", { connection: "close", lastDisconnect: { error: boom(401), date: new Date() } });
    await untilState(h, "logged_out");
    await vi.advanceTimersByTimeAsync(120_000);
    expect(h.sockets.length).toBe(1);
    expect(await conn.hasSession()).toBe(false);
  });

  it("does not fight a ban or a takeover", async () => {
    fakeTimers();
    for (const [code, state] of [
      [403, "error"],
      [440, "replaced"],
    ] as const) {
      const { h, conn } = await setup();
      await open(h, conn);
      h.last().emit("connection.update", { connection: "close", lastDisconnect: { error: boom(code), date: new Date() } });
      await untilState(h, state);
      await vi.advanceTimersByTimeAsync(300_000);
      expect(h.sockets.length).toBe(1);
    }
  });

  it("restores the session after a restart without a new scan", async () => {
    const { h, dir, conn } = await setup();
    await conn.connect("qr");
    h.configs.at(-1)!.auth.creds.registered = true;
    h.last().emit("creds.update", {});
    await waitFor(() => conn.hasSession());
    await conn.disconnect();

    const h2 = harness();
    const restarted = new WhatsAppConnection({
      sessionDir: dir,
      secret: SECRET,
      syncFullHistory: true,
      emit: (event, data) => h2.events.push({ event, data: data as Record<string, unknown> }),
      logger: silentLogger,
      factory: h2.factory,
      resolveVersion: async () => undefined,
    });
    expect(await restarted.resume()).toBe(true);
    expect(h2.configs[0]!.auth.creds.registered).toBe(true);
    h2.last().emit("connection.update", { connection: "open" });
    await flush();
    expect(h2.statuses().some((s) => s.state === "qr")).toBe(false);
    expect(restarted.state).toBe("open");
  });
});

describe("messages", () => {
  it("forwards incoming messages and edits/deletions as updates", async () => {
    const { h, conn } = await setup();
    await open(h, conn);
    const incoming: WAMessage = {
      key: { remoteJid: "4917000000001@s.whatsapp.net", id: "IN1", fromMe: false },
      message: { conversation: "Hallo" },
      messageTimestamp: 1_760_000_000,
      pushName: "Jonathan",
    };
    h.last().emit("messages.upsert", { type: "notify", messages: [incoming] });
    h.last().emit("messages.update", [
      { key: { remoteJid: "4917000000001@s.whatsapp.net", id: "OUT1", fromMe: true }, update: { status: 3 } },
      { key: { remoteJid: "4917000000001@s.whatsapp.net", id: "IN0", fromMe: false }, update: { message: null, messageStubType: proto.WebMessageInfo.StubType.REVOKE } },
      {
        key: { remoteJid: "4917000000001@s.whatsapp.net", id: "IN1", fromMe: false },
        update: { message: { editedMessage: { message: { conversation: "Hallo Sir" } } }, messageTimestamp: 1_760_000_100 },
      },
    ]);
    await flush();
    const msgs = h.events.find((e) => e.event === "messages")?.data;
    expect(msgs).toMatchObject({ type: "notify", messages: [{ id: "IN1", text: "Hallo", senderName: "Jonathan" }] });
    const updates = h.events.filter((e) => e.event === "message_updates").flatMap((e) => e.data.updates as Record<string, unknown>[]);
    expect(updates).toContainEqual({ chatJid: "4917000000001@s.whatsapp.net", id: "OUT1", fromMe: true, status: 3 });
    expect(updates).toContainEqual({ chatJid: "4917000000001@s.whatsapp.net", id: "IN0", fromMe: false, deleted: true });
    expect(updates).toContainEqual({ chatJid: "4917000000001@s.whatsapp.net", id: "IN1", fromMe: false, edit: { text: "Hallo Sir", timestamp: 1_760_000_100 } });
  });

  it("forwards history sync with chats and contacts", async () => {
    const { h, conn } = await setup();
    await open(h, conn);
    h.last().emit("messaging-history.set", {
      chats: [{ id: "4917000000001@s.whatsapp.net", name: "Jonathan", unreadCount: 2, conversationTimestamp: 1_760_000_000 }, { id: "status@broadcast" }],
      contacts: [{ id: "4917000000001@s.whatsapp.net", name: "Jonathan Weber", notify: "Jo" }],
      messages: [{ key: { remoteJid: "4917000000001@s.whatsapp.net", id: "H1", fromMe: true }, message: { conversation: "alt" }, messageTimestamp: 1 }],
      isLatest: true,
      progress: 100,
    });
    await flush();
    expect(h.events.find((e) => e.event === "chats")?.data).toEqual({
      chats: [{ jid: "4917000000001@s.whatsapp.net", name: "Jonathan", unread: 2, timestamp: 1_760_000_000, isGroup: false }],
    });
    expect(h.events.find((e) => e.event === "contacts")?.data).toEqual({
      contacts: [{ jid: "4917000000001@s.whatsapp.net", name: "Jonathan Weber", notify: "Jo" }],
    });
    expect(h.events.find((e) => e.event === "messages")?.data).toMatchObject({ type: "history", messages: [{ id: "H1", fromMe: true }] });
    expect(h.events.find((e) => e.event === "history_progress")?.data).toMatchObject({ isLatest: true, progress: 100 });
  });
});

describe("send", () => {
  it("derives the WhatsApp id like the Core does (core/tests/unit/whatsapp/test_wa_sidecar.py)", () => {
    expect(messageIdFor("send:abc")).toBe("3EB0DF85D5526DC4905C02");
  });

  it("is idempotent per key and derives the WhatsApp id from it", async () => {
    const { h, conn } = await setup();
    await open(h, conn);
    const params = { jid: "4917000000001@s.whatsapp.net", kind: "text" as const, text: "Hallo", idempotencyKey: "send:abc" };
    const [a, b] = await Promise.all([conn.send(params), conn.send(params)]);
    const c = await conn.send(params);
    expect(h.last().sendMessage).toHaveBeenCalledTimes(1);
    expect(a.messageId).toBe(messageIdFor("send:abc"));
    expect(b.messageId).toBe(a.messageId);
    expect(c.messageId).toBe(a.messageId);
    expect(h.last().sendMessage.mock.calls[0]![2]).toMatchObject({ messageId: messageIdFor("send:abc") });
    expect(a.message).toMatchObject({ fromMe: true, text: "Hallo" });
  });

  it("allows a retry with the same key after a failure", async () => {
    const { h, conn } = await setup();
    await open(h, conn);
    h.last().sendMessage.mockRejectedValueOnce(new Error("socket hang up"));
    const params = { jid: "4917000000001@s.whatsapp.net", kind: "text" as const, text: "Hallo", idempotencyKey: "send:retry" };
    await expect(conn.send(params)).rejects.toMatchObject({ code: "send_failed" });
    await expect(conn.send(params)).resolves.toMatchObject({ messageId: messageIdFor("send:retry") });
    expect(h.last().sendMessage).toHaveBeenCalledTimes(2);
  });

  it("refuses to send while disconnected", async () => {
    const { conn } = await setup();
    await expect(conn.send({ jid: "1@s.whatsapp.net", kind: "text", text: "x", idempotencyKey: "k" })).rejects.toMatchObject({ code: "not_connected" });
  });

  it("sends a document from a file path with name, type and caption, quoting a message", async () => {
    const { h, dir, conn } = await setup();
    await open(h, conn);
    const file = join(dir, "Angebot.pdf");
    await writeFile(file, "%PDF-1.7 test");
    await conn.send({
      jid: "4917000000001@s.whatsapp.net",
      kind: "document",
      filePath: file,
      fileName: "Angebot.pdf",
      mimetype: "application/pdf",
      caption: "Hier ist das Angebot",
      idempotencyKey: "send:doc",
      quoted: { id: "Q1", fromMe: false, text: "Schickst du mir das Angebot?" },
    });
    const [jid, content, options] = h.last().sendMessage.mock.calls[0]!;
    expect(jid).toBe("4917000000001@s.whatsapp.net");
    expect(content).toEqual({ document: { url: file }, mimetype: "application/pdf", fileName: "Angebot.pdf", caption: "Hier ist das Angebot" });
    expect((options as { quoted: WAMessage }).quoted.key.id).toBe("Q1");
    expect(h.events.filter((e) => e.event === "send_progress").map((e) => e.data.stage)).toEqual(["uploading", "sent"]);
  });

  it("checks size limits and missing files", async () => {
    const { h, dir, conn } = await setup();
    await open(h, conn);
    const file = join(dir, "big.bin");
    await writeFile(file, Buffer.alloc(2048));
    await expect(conn.send({ jid: "1@s.whatsapp.net", kind: "document", filePath: file, maxBytes: 1024, idempotencyKey: "big" })).rejects.toMatchObject({
      code: "too_large",
    });
    await expect(conn.send({ jid: "1@s.whatsapp.net", kind: "image", filePath: join(dir, "nope.png"), idempotencyKey: "nope" })).rejects.toMatchObject({
      code: "not_found",
    });
  });
});

describe("download", () => {
  const media: WAMessage = {
    key: { remoteJid: "4917000000001@s.whatsapp.net", id: "IMG1", fromMe: false },
    message: { imageMessage: { url: "https://mmg.whatsapp.net/x", mimetype: "image/png", mediaKey: new Uint8Array([1]) } },
    messageTimestamp: 1,
  };

  it("streams the file to disk and returns size and SHA-256", async () => {
    const data = Buffer.from("\x89PNG\r\n\x1a\nfake-image-bytes");
    const { h, dir, conn } = await setup({ downloader: async () => Readable.from([data]) });
    await open(h, conn);
    const dest = join(dir, "dl", "file.png");
    const res = await conn.download(serializeRaw(media), dest, 1024 * 1024);
    expect(res.size).toBe(data.length);
    expect(res.sha256).toBe(createHash("sha256").update(data).digest("hex"));
    expect((await readFile(dest)).equals(data)).toBe(true);
  });

  it("aborts above the size limit and leaves no partial file", async () => {
    const { h, dir, conn } = await setup({ downloader: async () => Readable.from([Buffer.alloc(4096)]) });
    await open(h, conn);
    const dest = join(dir, "big.bin");
    await expect(conn.download(serializeRaw(media), dest, 1024)).rejects.toMatchObject({ code: "too_large" });
    await expect(readFile(dest + ".part")).rejects.toThrow();
  });

  it("asks the sender's phone to re-upload expired media and reports media_expired when that fails", async () => {
    const downloader: Downloader = async (m, _t, _o, ctx) => {
      if (ctx) await ctx.reuploadRequest(m);
      throw Object.assign(new Error("Request failed with status code 410"), { response: { status: 410 } });
    };
    const { h, dir, conn } = await setup({ downloader });
    await open(h, conn);
    await expect(conn.download(serializeRaw(media), join(dir, "x.png"), 1024)).rejects.toMatchObject({ code: "media_expired" });
    expect(h.last().updateMediaMessage).toHaveBeenCalledTimes(1);
  });

  it("returns the refreshed message after a successful re-upload", async () => {
    const downloader: Downloader = async (m, _t, _o, ctx) => {
      if (ctx) await ctx.reuploadRequest(m);
      return Readable.from([Buffer.from("ok")]);
    };
    const { h, dir, conn } = await setup({ downloader });
    await open(h, conn);
    const res = await conn.download(serializeRaw(media), join(dir, "y.png"), 1024);
    expect(res.updatedRaw).toBeTruthy();
  });
});

describe("other actions", () => {
  it("marks only incoming messages as read", async () => {
    const { h, conn } = await setup();
    await open(h, conn);
    const n = await conn.markRead([
      { chatJid: "1@s.whatsapp.net", id: "A", fromMe: false },
      { chatJid: "1@s.whatsapp.net", id: "B", fromMe: true },
    ]);
    expect(n).toBe(1);
    expect(h.last().readMessages).toHaveBeenCalledWith([{ remoteJid: "1@s.whatsapp.net", id: "A", fromMe: false }]);
  });

  it("logout tells WhatsApp, removes the session and stays offline", async () => {
    const { h, conn } = await setup();
    await conn.connect("qr");
    h.configs.at(-1)!.auth.creds.registered = true;
    h.last().emit("creds.update", {});
    h.last().emit("creds.update", {});
    h.last().emit("connection.update", { connection: "open" });
    await flush(2);
    const sock = h.last();
    await conn.logout();
    expect(sock.logout).toHaveBeenCalled();
    expect(await conn.hasSession()).toBe(false);
    expect(conn.state).toBe("logged_out");
  });

  it("group metadata, number check and history request", async () => {
    const { h, conn } = await setup();
    await open(h, conn);
    expect(await conn.groupMetadata("120363000000000001@g.us")).toEqual({
      subject: "Team",
      participants: [{ jid: "491511111111@s.whatsapp.net", admin: true }],
    });
    expect(await conn.checkNumber("+49 170 0000001")).toEqual({ exists: true, jid: "491700000001@s.whatsapp.net" });
    expect(await conn.fetchHistory("4917000000001@s.whatsapp.net", 500, { id: "H1", fromMe: false, timestamp: 5 })).toBe("req-1");
    expect(h.last().fetchMessageHistory).toHaveBeenCalledWith(50, { remoteJid: "4917000000001@s.whatsapp.net", id: "H1", fromMe: false }, 5);
  });
});
