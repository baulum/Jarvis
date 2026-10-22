// @vitest-environment jsdom
// Composing a message with attachments: paper clip, drag and drop, pasted screenshots, chips, limits, upload.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const { uploadAttachment } = vi.hoisted(() => ({
  uploadAttachment: vi.fn(async (file: Blob, name: string) => ({
    file: `20261022-141500-1a2b3c4d-${name.replace(/[^A-Za-z0-9]+/g, "-")}`,
    name,
    kind: "image",
    mime: "image/png",
    size: file.size,
  })),
}));
vi.mock("../../store/app", async () => {
  const { create } = await import("zustand");
  return { useApp: create(() => ({ pushToast: vi.fn() })), api: { uploadAttachment } };
});
URL.createObjectURL = vi.fn((b: Blob) => `blob:test/${b.size}`);
URL.revokeObjectURL = vi.fn();

import { useAttachmentDraft } from "../../hooks/useAttachmentDraft";
import { MAX_ATTACHMENT_BYTES, type AttachmentInfo } from "../../lib/attachments";
import { AttachButton, AttachmentTray, DropOverlay } from "./Attachments";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const file = (name: string, size = 64, type = "image/png") => {
  const f = new File(["x".repeat(Math.min(size, 64))], name, { type });
  Object.defineProperty(f, "size", { value: size });
  return f;
};

function Composer({ onErrors, scope = "element", onSent }: { onErrors: (e: string[]) => void; scope?: "element" | "window"; onSent?: (a: AttachmentInfo[]) => void }) {
  const a = useAttachmentDraft({ onErrors, scope });
  return (
    <div data-testid="chat" className="relative" {...a.dropProps}>
      <DropOverlay active={a.dragging} />
      <AttachmentTray items={a.items} onRemove={a.remove} />
      <AttachButton onFiles={a.add} />
      <textarea aria-label="Nachricht" onPaste={a.onPaste} />
      <button type="button" onClick={() => void a.upload().then((r) => onSent?.(r), () => undefined)}>
        Senden
      </button>
    </div>
  );
}

const pick = (files: File[]) => fireEvent.change(screen.getByTestId("attach-input"), { target: { files } });
const chips = () => within(screen.getByRole("list", { name: "Anhänge dieser Nachricht" })).getAllByRole("listitem");

describe("composing with attachments", () => {
  it("adds picked files as chips with preview and size, and removes them again", () => {
    const onErrors = vi.fn();
    render(<Composer onErrors={onErrors} />);
    expect(screen.queryByRole("list")).toBeNull();
    pick([file("foto.png", 2048), file("vertrag.pdf", 3 * 1024 * 1024, "application/pdf")]);
    expect(chips()).toHaveLength(2);
    expect(chips()[0]?.querySelector("img")?.getAttribute("src")).toMatch(/^blob:/);
    expect(chips()[0]?.textContent).toContain("2 KB");
    expect(chips()[1]?.textContent).toContain("PDF"); // documents show their type instead of a preview
    expect(chips()[1]?.textContent).toContain("3,0 MB");
    fireEvent.click(screen.getByRole("button", { name: "Anhang entfernen: foto.png" }));
    expect(chips()).toHaveLength(1);
    expect(URL.revokeObjectURL).toHaveBeenCalled();
    expect(onErrors).not.toHaveBeenCalled();
  });

  it("refuses unsupported and oversized files with a clear message", () => {
    const onErrors = vi.fn();
    render(<Composer onErrors={onErrors} />);
    pick([file("setup.exe", 10, "application/octet-stream"), file("riesig.png", MAX_ATTACHMENT_BYTES + 5 * 1024 * 1024)]);
    expect(screen.queryByRole("list")).toBeNull();
    const [errors] = onErrors.mock.calls[0] as [string[]];
    expect(errors[0]).toContain("„setup.exe“: Dieser Dateityp wird nicht unterstützt");
    expect(errors[1]).toBe("„riesig.png“ ist 25,0 MB groß – erlaubt sind höchstens 20,0 MB je Datei.");
  });

  it("takes files dropped onto the chat and shows the drop hint while dragging", () => {
    render(<Composer onErrors={vi.fn()} />);
    const chat = screen.getByTestId("chat");
    const dataTransfer = { types: ["Files"], files: [file("drop.png")], dropEffect: "none" };
    fireEvent.dragEnter(chat, { dataTransfer });
    expect(screen.getByText("Loslassen zum Anhängen")).toBeTruthy();
    fireEvent.drop(chat, { dataTransfer });
    expect(screen.queryByText("Loslassen zum Anhängen")).toBeNull();
    expect(chips()[0]?.textContent).toContain("drop.png");
    // dragging text (not files) is left alone
    fireEvent.dragEnter(chat, { dataTransfer: { types: ["text/plain"], files: [] } });
    expect(screen.queryByText("Loslassen zum Anhängen")).toBeNull();
  });

  it("the dock attaches files dropped anywhere on the window", () => {
    render(<Composer onErrors={vi.fn()} scope="window" />);
    const dataTransfer = { types: ["Files"], files: [file("irgendwo.png")] };
    act(() => {
      const enter = new Event("dragenter", { bubbles: true, cancelable: true });
      Object.assign(enter, { dataTransfer });
      window.dispatchEvent(enter);
    });
    expect(screen.getByText("Loslassen zum Anhängen")).toBeTruthy();
    const drop = new Event("drop", { bubbles: true, cancelable: true });
    Object.assign(drop, { dataTransfer });
    act(() => {
      window.dispatchEvent(drop);
    });
    expect(drop.defaultPrevented).toBe(true); // the webview does not navigate to the file
    expect(chips()[0]?.textContent).toContain("irgendwo.png");
  });

  it("a pasted screenshot gets a readable name; pasting text stays text", () => {
    render(<Composer onErrors={vi.fn()} />);
    const box = screen.getByRole("textbox", { name: "Nachricht" });
    fireEvent.paste(box, { clipboardData: { files: [file("image.png")] } });
    expect(chips()[0]?.textContent).toMatch(/Bildschirmfoto \d{4}-\d{2}-\d{2} um \d{2}\.\d{2}\.\d{2}\.png/);
    const text = fireEvent.paste(box, { clipboardData: { files: [] } });
    expect(text).toBe(true); // not prevented: the text lands in the input
    expect(chips()).toHaveLength(1);
  });

  it("uploads once on send, and keeps what went through when another file fails", async () => {
    const sent = vi.fn();
    render(<Composer onErrors={vi.fn()} onSent={sent} />);
    pick([file("a.png"), file("b.png")]);
    uploadAttachment.mockRejectedValueOnce(new Error("Core nicht erreichbar.")).mockImplementationOnce(async (f: Blob, name: string) => ({
      file: `20261022-141500-1a2b3c4d-${name}`.replace(".png", "-png") + ".png",
      name,
      kind: "image",
      mime: "image/png",
      size: f.size,
    }));
    fireEvent.click(screen.getByRole("button", { name: "Senden" }));
    await vi.waitFor(() => expect(chips()[0]?.textContent).toContain("Core nicht erreichbar."));
    expect(chips()[0]?.getAttribute("title")).toBe("Core nicht erreichbar.");
    expect(sent).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Senden" }));
    await vi.waitFor(() => expect(sent).toHaveBeenCalled());
    const infos = sent.mock.calls[0]?.[0] as AttachmentInfo[];
    expect(infos.map((i) => i.name)).toEqual(["a.png", "b.png"]);
    // a.png failed once and was retried; b.png went up exactly once
    expect(uploadAttachment.mock.calls.map((c) => c[1])).toEqual(["a.png", "a.png", "b.png"]);
  });
});
