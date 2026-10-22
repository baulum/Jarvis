// Attachments of the message being written: picked (paper clip), dropped onto the chat or pasted (screenshots).
// Files are checked right away (type, size, count) and uploaded when the message is sent; an upload that went
// through is kept, so sending again after an error does not upload twice.
import { useCallback, useEffect, useRef, useState, type ClipboardEvent, type DragEvent } from "react";
import { admitFiles, pastedName, type AttachmentInfo, type AttachmentKind } from "../lib/attachments";
import { api } from "../store/app";

export interface DraftAttachment {
  id: string;
  file: File;
  name: string;
  kind: AttachmentKind;
  size: number;
  /** object URL of the local file for the chip thumbnail (images only) */
  previewUrl?: string;
  status: "ready" | "uploading" | "failed";
  error?: string;
  info?: AttachmentInfo;
}

const hasFiles = (dt: DataTransfer | null) => Boolean(dt && Array.from(dt.types).includes("Files"));
// clipboard screenshots are called "image.png" (or have no name at all)
const unnamed = (f: File) => !f.name || /^image\.(png|jpe?g|gif|webp)$/i.test(f.name);

let nextId = 0;

/**
 * `scope: "window"` (the chat dock): files dropped anywhere on the window are attached, except where another drop
 * zone already took them. `scope: "element"`: spread `dropProps` on the chat container.
 */
export function useAttachmentDraft({ onErrors, scope = "element" }: { onErrors: (errors: string[]) => void; scope?: "element" | "window" }) {
  const [items, setItems] = useState<DraftAttachment[]>([]);
  const [dragging, setDragging] = useState(false);
  const latest = useRef(items);
  const depth = useRef(0);

  useEffect(() => {
    latest.current = items;
  }, [items]);
  useEffect(() => () => latest.current.forEach((i) => i.previewUrl && URL.revokeObjectURL(i.previewUrl)), []);

  const add = useCallback(
    (files: File[]) => {
      const { accepted, errors } = admitFiles(latest.current, files);
      if (accepted.length) {
        const fresh = accepted.map(({ file, kind }): DraftAttachment => {
          nextId += 1;
          return {
            id: `att-${nextId}`,
            file,
            name: file.name,
            kind,
            size: file.size,
            previewUrl: kind === "image" ? URL.createObjectURL(file) : undefined,
            status: "ready",
          };
        });
        latest.current = [...latest.current, ...fresh];
        setItems(latest.current);
      }
      if (errors.length) onErrors(errors);
    },
    [onErrors],
  );

  const remove = useCallback((id: string) => {
    const gone = latest.current.find((i) => i.id === id);
    if (gone?.previewUrl) URL.revokeObjectURL(gone.previewUrl);
    latest.current = latest.current.filter((i) => i.id !== id);
    setItems(latest.current);
  }, []);

  const clear = useCallback(() => {
    latest.current.forEach((i) => i.previewUrl && URL.revokeObjectURL(i.previewUrl));
    latest.current = [];
    setItems([]);
  }, []);

  /** Uploads what is not uploaded yet; returns all stored attachments or throws with the first failure. */
  const upload = useCallback(async (): Promise<AttachmentInfo[]> => {
    const patch = (id: string, change: Partial<DraftAttachment>) => {
      latest.current = latest.current.map((i) => (i.id === id ? { ...i, ...change } : i));
      setItems(latest.current);
    };
    const out: AttachmentInfo[] = [];
    for (const item of latest.current) {
      if (item.info) {
        out.push(item.info);
        continue;
      }
      patch(item.id, { status: "uploading", error: undefined });
      try {
        const info = await api.uploadAttachment(item.file, item.name);
        patch(item.id, { status: "ready", info });
        out.push(info);
      } catch (e) {
        const error = e instanceof Error ? e.message : String(e);
        patch(item.id, { status: "failed", error });
        throw new Error(error.includes(item.name) ? error : `„${item.name}“: ${error}`, { cause: e });
      }
    }
    return out;
  }, []);

  /** Pasting a screenshot (or a file copied in the Finder) attaches it; plain text pastes as usual. */
  const onPaste = useCallback(
    (e: ClipboardEvent<HTMLElement>) => {
      const files = Array.from(e.clipboardData.files);
      if (!files.length) return;
      e.preventDefault();
      add(files.map((f) => (unnamed(f) ? new File([f], pastedName(f.type), { type: f.type, lastModified: f.lastModified }) : f)));
    },
    [add],
  );

  const enter = useCallback((dt: DataTransfer | null) => {
    if (!hasFiles(dt)) return false;
    depth.current += 1;
    setDragging(true);
    return true;
  }, []);
  const leave = useCallback((dt: DataTransfer | null) => {
    if (!hasFiles(dt)) return;
    depth.current = Math.max(0, depth.current - 1);
    if (depth.current === 0) setDragging(false);
  }, []);
  const drop = useCallback(
    (dt: DataTransfer | null) => {
      depth.current = 0;
      setDragging(false);
      if (hasFiles(dt) && dt) add(Array.from(dt.files));
    },
    [add],
  );

  const dropProps = {
    onDragEnter: (e: DragEvent<HTMLElement>) => enter(e.dataTransfer) && e.preventDefault(),
    onDragOver: (e: DragEvent<HTMLElement>) => {
      if (!hasFiles(e.dataTransfer)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    },
    onDragLeave: (e: DragEvent<HTMLElement>) => leave(e.dataTransfer),
    onDrop: (e: DragEvent<HTMLElement>) => {
      if (!hasFiles(e.dataTransfer)) return;
      e.preventDefault();
      drop(e.dataTransfer);
    },
  };

  useEffect(() => {
    if (scope !== "window") return;
    // without this a file dropped on the window would replace the app with the file
    const onEnter = (e: globalThis.DragEvent) => !e.defaultPrevented && enter(e.dataTransfer) && e.preventDefault();
    const onOver = (e: globalThis.DragEvent) => {
      if (!hasFiles(e.dataTransfer)) return;
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
    };
    const onLeave = (e: globalThis.DragEvent) => leave(e.dataTransfer);
    const onDrop = (e: globalThis.DragEvent) => {
      if (!hasFiles(e.dataTransfer)) return;
      const taken = e.defaultPrevented; // another chat (the conversations view) attached them already
      e.preventDefault();
      if (taken) {
        depth.current = 0;
        setDragging(false);
      } else drop(e.dataTransfer);
    };
    window.addEventListener("dragenter", onEnter);
    window.addEventListener("dragover", onOver);
    window.addEventListener("dragleave", onLeave);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragenter", onEnter);
      window.removeEventListener("dragover", onOver);
      window.removeEventListener("dragleave", onLeave);
      window.removeEventListener("drop", onDrop);
    };
  }, [scope, enter, leave, drop]);

  return { items, add, remove, clear, upload, onPaste, dragging, dropProps: scope === "element" ? dropProps : undefined };
}
