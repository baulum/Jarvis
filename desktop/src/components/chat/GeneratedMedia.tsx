// A generated image or video inside Jarvis' answer (image_generate / video_generate). The file comes from the
// core as an authenticated Blob (useChatFile). Images are capped in size and open enlarged on click; videos play
// inline. Under each file: its name and "Im Finder zeigen". A file that cannot be loaded says why, with a retry.
import { FolderOpen, ImageOff, Loader2, Maximize2, RotateCw } from "lucide-react";
import { useCallback, useState } from "react";
import { useChatFile, useRevealInFinder } from "../../hooks/useChatFile";
import type { MediaDisplay } from "../../lib/timeline";
import { IconButton } from "../ui";
import { Lightbox } from "./Lightbox";

const ratio = (aspect?: string) => {
  const m = /^(\d+):(\d+)$/.exec(aspect ?? "");
  return m ? `${m[1]} / ${m[2]}` : "1 / 1";
};

export function GeneratedMedia({ display, compact = false }: { display: MediaDisplay; compact?: boolean }) {
  const { url, error, retry } = useChatFile(display.media, display.file);
  const [broken, setBroken] = useState(false); // the file arrived but could not be decoded/played
  const [zoom, setZoom] = useState(false);
  const close = useCallback(() => setZoom(false), []);
  const reveal = useRevealInFinder();
  const image = display.kind === "image";
  const what = image ? "Bild" : "Video";
  const maxH = compact ? "max-h-48" : "max-h-[26rem]";

  if (error || broken)
    return (
      <div
        role="img"
        aria-label={`${what} konnte nicht geladen werden`}
        className={`flex w-full max-w-sm flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed border-alarm/40 px-4 text-center text-xs text-ink-dim ${compact ? "min-h-24 py-3" : "min-h-36 py-4"}`}
      >
        <ImageOff className="size-5 text-alarm/80" aria-hidden />
        <span className="text-ink">{what} konnte nicht geladen werden</span>
        <span>{broken ? "Die Datei ist beschädigt oder hat ein unbekanntes Format." : error}</span>
        <span className="type-data max-w-full truncate text-ink-faint">{display.file}</span>
        {!broken && (
          <button type="button" onClick={retry} className="mt-1 inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-ink-dim hover:bg-plate-hi hover:text-ink">
            <RotateCw className="size-3.5" aria-hidden />
            Erneut laden
          </button>
        )}
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

  const actions = (
    <div className="flex max-w-full items-center gap-1 pl-1">
      <span className="type-data min-w-0 flex-1 truncate text-ink-faint" title={display.file}>
        {display.file}
      </span>
      <IconButton label="Im Finder zeigen" onClick={() => reveal(display.media, display.file)} className="size-6">
        <FolderOpen className="size-3.5" />
      </IconButton>
    </div>
  );

  if (!image)
    return (
      <figure className="flex w-fit max-w-full flex-col gap-1">
        <video
          src={url}
          controls
          preload="metadata"
          playsInline
          aria-label={display.prompt ? `Erzeugtes Video: ${display.prompt}` : "Erzeugtes Video"}
          onError={() => setBroken(true)}
          className={`block w-auto max-w-full ${maxH} rounded-lg border border-seam bg-void`}
        />
        {actions}
      </figure>
    );

  const alt = display.prompt ? `Erzeugtes Bild: ${display.prompt}` : "Erzeugtes Bild";
  return (
    <figure className="flex w-fit max-w-full flex-col gap-1">
      <button
        type="button"
        onClick={() => setZoom(true)}
        aria-label="Bild vergrößern"
        className="group relative block w-fit max-w-full cursor-zoom-in overflow-hidden rounded-lg border border-seam bg-void hover:border-seam-hi"
      >
        <img src={url} alt={alt} draggable={false} onError={() => setBroken(true)} className={`block h-auto max-w-full ${maxH} object-contain`} />
        <Maximize2 className="absolute top-2 right-2 size-3.5 text-ink opacity-0 drop-shadow transition-opacity group-hover:opacity-80" aria-hidden />
      </button>
      {actions}
      {zoom && <Lightbox url={url} alt={alt} caption={display.prompt} onClose={close} onReveal={() => reveal(display.media, display.file)} />}
    </figure>
  );
}
