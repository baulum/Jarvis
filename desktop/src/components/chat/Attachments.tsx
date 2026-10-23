// Attachments in the chat: the paper clip and the chips of the message being written, the drop overlay, and the
// files of a sent message (image thumbnails, document chips) in the conversation.
import { FolderOpen, ImageOff, Loader2, Paperclip, RotateCw, X } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { useChatFile, useRevealInFinder } from "../../hooks/useChatFile";
import type { DraftAttachment } from "../../hooks/useAttachmentDraft";
import { ACCEPT, formatBytes, MAX_ATTACHMENT_BYTES, MAX_ATTACHMENTS, typeLabel, type AttachmentInfo } from "../../lib/attachments";
import { IconButton } from "../ui";
import { Lightbox } from "./Lightbox";

// ------------------------------------------------------------------ composer

export function AttachButton({ onFiles, disabled, className = "" }: { onFiles: (files: File[]) => void; disabled?: boolean; className?: string }) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <IconButton
        label="Datei anhängen"
        disabled={disabled}
        onClick={() => input.current?.click()}
        className={className}
      >
        <Paperclip className="size-4" strokeWidth={1.7} />
      </IconButton>
      <input
        ref={input}
        type="file"
        multiple
        accept={ACCEPT}
        hidden
        aria-hidden
        tabIndex={-1}
        data-testid="attach-input"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = ""; // the same file can be picked again after removing it
          if (files.length) onFiles(files);
        }}
      />
    </>
  );
}

/** Small square that says what a file is: the image itself, or its type in the data voice. */
function Thumb({ kind, name, url, size = "size-9" }: { kind: string; name: string; url?: string; size?: string }) {
  if (kind === "image" && url) return <img src={url} alt="" draggable={false} className={`${size} shrink-0 rounded-[5px] object-cover`} />;
  return (
    <span className={`${size} type-data flex shrink-0 items-center justify-center rounded-[5px] border border-seam-hi text-[0.6rem] text-ink-dim`}>
      {typeLabel(name)}
    </span>
  );
}

export function AttachmentTray({ items, onRemove, disabled }: { items: DraftAttachment[]; onRemove: (id: string) => void; disabled?: boolean }) {
  if (!items.length) return null;
  return (
    <ul aria-label="Anhänge dieser Nachricht" className="flex flex-wrap gap-1.5">
      {items.map((a) => {
        const failed = a.status === "failed";
        return (
          <li
            key={a.id}
            className={`flex h-11 max-w-[15rem] items-center gap-2 rounded-md border bg-plate/70 pr-0.5 pl-1 ${failed ? "border-alarm/50" : "border-seam"}`}
            title={failed ? a.error : a.name}
          >
            <span className="relative">
              <Thumb kind={a.kind} name={a.name} url={a.previewUrl} />
              {a.status === "uploading" && (
                <span className="absolute inset-0 flex items-center justify-center rounded-[5px] bg-void/70" role="status" aria-label={`${a.name} wird hochgeladen`}>
                  <Loader2 className="spin-slow size-3.5 text-arc" />
                </span>
              )}
            </span>
            <span className="flex min-w-0 flex-col leading-tight">
              <span className="truncate text-xs text-ink">{a.name}</span>
              {failed ? (
                <span className="truncate text-2xs text-alarm">{a.error}</span>
              ) : (
                <span className="type-data text-ink-faint">{formatBytes(a.size)}</span>
              )}
            </span>
            <IconButton label={`Anhang entfernen: ${a.name}`} onClick={() => onRemove(a.id)} disabled={disabled} className="size-6 shrink-0">
              <X className="size-3.5" />
            </IconButton>
          </li>
        );
      })}
    </ul>
  );
}

/** Shown while files are dragged over the chat. */
export function DropOverlay({ active, rounded = "rounded-[inherit]" }: { active: boolean; rounded?: string }) {
  if (!active) return null;
  return (
    <div
      aria-hidden
      className={`pointer-events-none absolute inset-1 z-20 flex flex-col items-center justify-center gap-1 border border-dashed border-arc-dim/80 bg-void/85 ${rounded}`}
    >
      <Paperclip className="size-4 text-arc" strokeWidth={1.7} />
      <span className="text-sm text-ink">Loslassen zum Anhängen</span>
      <span className="type-data text-ink-faint">
        bis {MAX_ATTACHMENTS} Dateien · je {formatBytes(MAX_ATTACHMENT_BYTES)}
      </span>
    </div>
  );
}

// ------------------------------------------------------------------ sent with a message

function SentImage({ info, compact }: { info: AttachmentInfo; compact: boolean }) {
  const { url, error, retry } = useChatFile("attachments", info.file);
  const [broken, setBroken] = useState(false); // the file arrived but could not be decoded
  const [zoom, setZoom] = useState(false);
  const close = useCallback(() => setZoom(false), []);
  const reveal = useRevealInFinder();
  const box = compact ? "h-14 max-w-24" : "h-24 max-w-40";
  if (error || broken) {
    const why = broken ? "Die Datei ist beschädigt oder hat ein unbekanntes Format." : error;
    return (
      <div
        role="img"
        aria-label={`Bild ${info.name} konnte nicht geladen werden`}
        title={`${info.name}: ${why}`}
        className={`flex ${compact ? "min-h-14 w-32" : "min-h-24 w-40"} flex-col items-center justify-center gap-0.5 rounded-lg border border-dashed border-alarm/40 px-2 py-1.5 text-center text-2xs text-ink-dim`}
      >
        <ImageOff className="size-4 text-alarm/80" aria-hidden />
        <span className="w-full truncate text-ink">{info.name}</span>
        <span className="line-clamp-2">{why}</span>
        {!broken && (
          <button type="button" onClick={retry} aria-label={`Erneut laden: ${info.name}`} className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-plate-hi hover:text-ink">
            <RotateCw className="size-3" aria-hidden />
            Erneut laden
          </button>
        )}
      </div>
    );
  }
  if (!url)
    return (
      <span role="status" aria-label={`${info.name} wird geladen`} className={`flex ${compact ? "size-14" : "size-24"} items-center justify-center rounded-lg border border-seam bg-void`}>
        <Loader2 className="spin-slow size-3.5 text-ink-faint" />
      </span>
    );
  return (
    <figure className="flex w-fit max-w-full flex-col gap-0.5">
      <button
        type="button"
        onClick={() => setZoom(true)}
        aria-label={`Anhang vergrößern: ${info.name}`}
        title={info.name}
        className="block cursor-zoom-in overflow-hidden rounded-lg border border-seam bg-void hover:border-seam-hi"
      >
        <img src={url} alt={info.name} draggable={false} onError={() => setBroken(true)} className={`block w-auto object-cover ${box}`} />
      </button>
      {/* w-0 min-w-full: the row is as wide as the image, a long name does not widen it */}
      <div className="flex w-0 min-w-full items-center gap-1 pl-1">
        {!compact && (
          <span className="type-data min-w-0 flex-1 truncate text-ink-faint" title={info.name}>
            {info.name}
          </span>
        )}
        <IconButton label={`Im Finder zeigen: ${info.name}`} onClick={() => reveal("attachments", info.file)} className="ml-auto size-6 shrink-0">
          <FolderOpen className="size-3.5" />
        </IconButton>
      </div>
      {zoom && <Lightbox url={url} alt={info.name} caption={info.name} onClose={close} onReveal={() => reveal("attachments", info.file)} />}
    </figure>
  );
}

function SentDocument({ info }: { info: AttachmentInfo }) {
  const reveal = useRevealInFinder();
  return (
    <span className="flex h-10 max-w-[16rem] items-center gap-2 rounded-md border border-seam bg-void/60 pr-0.5 pl-1">
      <Thumb kind="document" name={info.name} size="size-8" />
      <span className="flex min-w-0 flex-col leading-tight">
        <span className="truncate text-xs text-ink" title={info.name}>
          {info.name}
        </span>
        <span className="type-data text-ink-faint">{formatBytes(info.size)}</span>
      </span>
      <IconButton label={`Im Finder zeigen: ${info.name}`} onClick={() => reveal("attachments", info.file)} className="size-6 shrink-0">
        <FolderOpen className="size-3.5" />
      </IconButton>
    </span>
  );
}

export function SentAttachments({ items, compact = false, align = "end" }: { items: AttachmentInfo[]; compact?: boolean; align?: "start" | "end" }) {
  return (
    <ul aria-label="Anhänge" className={`flex flex-wrap gap-1.5 ${align === "end" ? "justify-end" : ""}`}>
      {items.map((a) => (
        <li key={a.file}>{a.kind === "image" ? <SentImage info={a} compact={compact} /> : <SentDocument info={a} />}</li>
      ))}
    </ul>
  );
}
