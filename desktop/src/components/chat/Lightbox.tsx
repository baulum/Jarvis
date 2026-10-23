// An image of the chat enlarged over the whole window (generated images and image attachments).
import { FolderOpen, X } from "lucide-react";
import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { IconButton } from "../ui";

export function Lightbox({ url, alt, caption, onClose, onReveal, revealLabel = "Im Finder zeigen" }: { url: string; alt: string; caption?: string; onClose: () => void; onReveal?: () => void; revealLabel?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("button")?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      prev?.focus();
    };
  }, [onClose]);
  // portal: transformed chat containers would otherwise trap the fixed overlay
  return createPortal(
    <div
      ref={ref}
      role="dialog"
      aria-modal="true"
      aria-label="Bild vergrößert"
      className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-3 bg-void/85 p-6 backdrop-blur-[2px]"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="absolute top-4 right-4 flex items-center gap-1 rounded-md bg-plate/80">
        {onReveal && (
          <IconButton label={revealLabel} onClick={onReveal}>
            <FolderOpen className="size-4" />
          </IconButton>
        )}
        <IconButton label="Schließen" onClick={onClose}>
          <X className="size-4" />
        </IconButton>
      </div>
      <img src={url} alt={alt} className="max-h-[calc(100vh-6rem)] max-w-full rounded-lg border border-seam-hi object-contain shadow-2xl shadow-black/60" />
      {caption && <p className="type-data max-w-[80ch] truncate text-ink-faint">{caption}</p>}
    </div>,
    document.body,
  );
}
