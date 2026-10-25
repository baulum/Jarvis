import { proto, type WAMessage } from "@whiskeysockets/baileys";
import { describe, expect, it } from "vitest";
import { classify, parseRaw } from "../src/normalize.js";

const CHAT = "4917000000001@s.whatsapp.net";
const GROUP = "120363000000000001@g.us";

function msg(message: proto.IMessage, extra: Partial<WAMessage> = {}): WAMessage {
  return {
    key: { remoteJid: CHAT, id: "MSG1", fromMe: false },
    message,
    messageTimestamp: 1_760_000_000,
    pushName: "Jonathan",
    ...extra,
  } as WAMessage;
}

describe("classify", () => {
  it("normalises a text message", () => {
    const { message } = classify(msg({ conversation: "Hallo Sir" }));
    expect(message).toMatchObject({ id: "MSG1", chatJid: CHAT, kind: "text", text: "Hallo Sir", fromMe: false, senderName: "Jonathan", isGroup: false });
    expect(message?.raw).toBeUndefined();
  });

  it("keeps quote and forward info", () => {
    const { message } = classify(
      msg({ extendedTextMessage: { text: "Antwort", contextInfo: { stanzaId: "OLD", isForwarded: true } } }),
    );
    expect(message).toMatchObject({ kind: "text", quotedId: "OLD", forwarded: true });
  });

  it("normalises an image with caption, thumbnail and a raw copy for later download", () => {
    const { message } = classify(
      msg({
        imageMessage: {
          caption: "Entwurf",
          mimetype: "image/jpeg",
          fileLength: 2048,
          width: 800,
          height: 600,
          jpegThumbnail: new Uint8Array([0xff, 0xd8, 0xff]),
          fileSha256: new Uint8Array([1, 2, 3]),
          url: "https://mmg.whatsapp.net/x",
          mediaKey: new Uint8Array([9, 9]),
        },
      }),
    );
    expect(message).toMatchObject({ kind: "image", text: "Entwurf", media: { mimetype: "image/jpeg", fileLength: 2048, width: 800, height: 600 } });
    expect(message?.media?.thumbnail).toBe(Buffer.from([0xff, 0xd8, 0xff]).toString("base64"));
    const raw = parseRaw(message!.raw!);
    expect(raw.key.id).toBe("MSG1");
    expect(raw.message?.imageMessage?.url).toBe("https://mmg.whatsapp.net/x");
  });

  it("recognises voice notes, documents and stickers", () => {
    expect(classify(msg({ audioMessage: { ptt: true, seconds: 12, mimetype: "audio/ogg; codecs=opus" } })).message).toMatchObject({
      kind: "voice",
      media: { ptt: true, seconds: 12 },
    });
    expect(classify(msg({ audioMessage: { ptt: false, mimetype: "audio/mpeg" } })).message?.kind).toBe("audio");
    expect(
      classify(msg({ documentWithCaptionMessage: { message: { documentMessage: { fileName: "Rechnung.pdf", mimetype: "application/pdf", caption: "Bitte prüfen" } } } }))
        .message,
    ).toMatchObject({ kind: "document", text: "Bitte prüfen", media: { fileName: "Rechnung.pdf", mimetype: "application/pdf" } });
    expect(classify(msg({ stickerMessage: { mimetype: "image/webp" } })).message?.kind).toBe("sticker");
  });

  it("uses the participant as sender in groups", () => {
    const { message } = classify(
      msg({ conversation: "Hi" }, { key: { remoteJid: GROUP, id: "G1", fromMe: false, participant: "4917000000002@s.whatsapp.net" } }),
    );
    expect(message).toMatchObject({ isGroup: true, senderJid: "4917000000002@s.whatsapp.net", chatJid: GROUP });
  });

  it("turns revoke and edit protocol messages into updates of the original", () => {
    const revoke = classify(
      msg({ protocolMessage: { type: proto.Message.ProtocolMessage.Type.REVOKE, key: { remoteJid: CHAT, id: "ORIG", fromMe: false } } }),
    );
    expect(revoke.message).toBeUndefined();
    expect(revoke.update).toEqual({ chatJid: CHAT, id: "ORIG", fromMe: false, deleted: true });

    const edit = classify(
      msg({
        editedMessage: {
          message: {
            protocolMessage: {
              type: proto.Message.ProtocolMessage.Type.MESSAGE_EDIT,
              key: { remoteJid: CHAT, id: "ORIG", fromMe: false },
              editedMessage: { conversation: "korrigiert" },
            },
          },
        },
      }),
    );
    expect(edit.update).toMatchObject({ id: "ORIG", edit: { text: "korrigiert" } });
  });

  it("turns reactions into updates", () => {
    const r = classify(msg({ reactionMessage: { key: { remoteJid: CHAT, id: "ORIG", fromMe: true }, text: "👍" } }));
    expect(r.update).toMatchObject({ id: "ORIG", fromMe: true, reaction: { emoji: "👍", senderJid: CHAT } });
  });

  it("ignores status broadcasts and newsletters", () => {
    expect(classify(msg({ conversation: "x" }, { key: { remoteJid: "status@broadcast", id: "S", fromMe: false } }))).toEqual({});
    expect(classify(msg({ conversation: "x" }, { key: { remoteJid: "1@newsletter", id: "N", fromMe: false } }))).toEqual({});
  });

  it("reports system stubs", () => {
    const { message } = classify({
      key: { remoteJid: GROUP, id: "SYS", fromMe: false },
      messageStubType: proto.WebMessageInfo.StubType.GROUP_CREATE,
      messageTimestamp: 1,
    } as WAMessage);
    expect(message).toMatchObject({ kind: "system", system: "GROUP_CREATE" });
  });
});
