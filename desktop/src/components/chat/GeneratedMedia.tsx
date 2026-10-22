// A generated image or video inside Jarvis' answer (image_generate / video_generate). The file comes from the
// core as an authenticated Blob (GET /api/media/{images|videos}/{file} – file name only, never a path) and is
// shown via an object URL. Images are capped in size and open enlarged on click; videos play inline.
import { ImageOff, Loader2, Maximize2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { MediaDisplay } from "../../lib/timeline";
import { api } from "../../store/app";
import { IconButton } from "../ui";

// One fetch per file, shared by every mount (re-renders, the chat dock, re-opening a chat).
const cache = new Map<string, Promise<string>>();
const MAX_CACHED = 60;

function loadMedia(media: MediaDisplay["media"], file: string): Promise<string> {
  const key = `${media}/${file}`;
  let hit = cache.get(key);
  if (!hit) {
    hit = api.media(media, file).then((blob) => URL.createObjectURL(blob));
    hit.catch(() => cache.delete(key)); // a failed load is retried on the next mount
    cache.set(key, hit);
    while (cache.size > MAX_CACHED) {
      const [oldKey, old] = cache.entries().next().value as [string, Promise<string>];
      cache.delete(oldKey);
      old.then(URL.revokeObjectURL, () => undefined);
    }
  }
  return hit;
}

function useMediaUrl({ media, file }: MediaDisplay): { url?: string; failed: boolean } {
  const key = `${media}/${file}`;
  const [state, setState] = useState<{ key: string; url?: string; failed: boolean }>({ key, failed: false });
  useEffect(() => {
    let alive = true;
    loadMedia(media, file).then(
      (url) => alive && setState({ key: `${media}/${file}`, url, failed: false }),
      () => alive && setState({ key: `${media}/${file}`, failed: true }),
    );
    return () => {
      alive = false;
    };
  }, [media, file]);
  return state.key === key ? state : { failed: false }; // another file than last time: loading
}

const ratio = (aspect?: string) => {
  const m = /^(\d+):(\d+)$/.exec(aspect ?? "");
  return m ? `${m[1]} / ${m[2]}` : "1 / 1";
};

export function GeneratedMedia({ display, compact = false }: { display: MediaDisplay; compact?: boolean }) {
  const { url, failed } = useMediaUrl(display);
  const [broken, setBroken] = useState(false); // the file arrived but could not be decoded/played
  const [zoom, setZoom] = useState(false);
  const close = useCallback(() => setZoom(false), []);
  const image = display.kind === "image";
  const what = image ? "Bild" : "Video";
  const maxH = compact ? "max-h-48" : "max-h-[26rem]";

  if (failed || broken)
    return (
      <div
        role="img"
        aria-label={`${what} konnte nicht geladen werden`}
        className={`flex w-full max-w-sm flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed border-seam px-4 text-center text-xs text-ink-faint ${compact ? "h-24" : "h-36"}`}
      >
        <ImageOff className="size-5" aria-hidden />
        <span>{what} konnte nicht geladen werden</span>
        <span className="type-data max-w-full truncate">{display.file}</span>
      </div>
    );

  if (!url)
    return (
      <div
        role="status"
        aria-label={`${what} wird geladen`}
        style={{ aspectRatio: ratio(display.aspect_ratio) }}
        className={`flex w-full ${compact ? "max-w-48" : "max-w-xs"} items-center justify-center rounded-lg border border-seam bg-void`}
      >
        <Loader2 className="spin-slow size-4 text-ink-faint" />
      </div>
    );

  if (!image)
    return (
      <video
        src={url}
        controls
        preload="metadata"
        playsInline
        aria-label={display.prompt ? `Erzeugtes Video: ${display.prompt}` : "Erzeugtes Video"}
        onError={() => setBroken(true)}
        className={`block w-auto max-w-full ${maxH} rounded-lg border border-seam bg-void`}
      />
    );

  const alt = display.prompt ? `Erzeugtes Bild: ${display.prompt}` : "Erzeugtes Bild";
  return (
    <>
      <button
        type="button"
        onClick={() => setZoom(true)}
        aria-label="Bild vergrößern"
        title="Bild vergrößern"
        className="group relative block w-fit max-w-full cursor-zoom-in overflow-hidden rounded-lg border border-seam bg-void hover:border-seam-hi"
      >
        <img src={url} alt={alt} draggable={false} onError={() => setBroken(true)} className={`block h-auto max-w-full ${maxH} object-contain`} />
        <Maximize2 className="absolute top-2 right-2 size-3.5 text-ink opacity-0 drop-shadow transition-opacity group-hover:opacity-80" aria-hidden />
      </button>
      {zoom && <Lightbox url={url} alt={alt} onClose={close} />}
    </>
  );
}

function Lightbox({ url, alt, onClose }: { url: string; alt: string; onClose: () => void }) {
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
      className="fixed inset-0 z-50 flex items-center justify-center bg-void/85 p-6 backdrop-blur-[2px]"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <IconButton label="Schließen" onClick={onClose} className="absolute top-4 right-4 bg-plate/80">
        <X className="size-4" />
      </IconButton>
      <img src={url} alt={alt} className="max-h-full max-w-full rounded-lg border border-seam-hi object-contain shadow-2xl shadow-black/60" />
    </div>,
    document.body,
  );
}
