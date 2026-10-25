// WhatsApp media in the app: thumbnails and stored files come from the Core as authenticated Blobs and are
// shown through object URLs – the webview never gets a file path or an unauthenticated URL.
import { FileText, Film, Image as ImageIcon, Mic, Music } from "lucide-react";
import { useBlobUrl } from "../../hooks/useBlobUrl";
import { api } from "../../store/app";

export function KindIcon({ kind, className = "size-4" }: { kind: string; className?: string }) {
  if (kind === "image" || kind === "sticker") return <ImageIcon className={className} aria-hidden />;
  if (kind === "video") return <Film className={className} aria-hidden />;
  if (kind === "voice") return <Mic className={className} aria-hidden />;
  if (kind === "audio") return <Music className={className} aria-hidden />;
  return <FileText className={className} aria-hidden />;
}

/** Preview of a received photo/video/document: the stored file if Jarvis has it, else WhatsApp's thumbnail. */
export function MediaPreview({ chatJid, msgId, hasThumb, fileId, category, mime, alt }: {
  chatJid: string;
  msgId: string;
  hasThumb: boolean;
  fileId?: string;
  category?: string;
  mime?: string;
  alt: string;
}) {
  const full = fileId && (category === "image" || category === "video" || category === "audio") ? fileId : null;
  const fullUrl = useBlobUrl(full ? `wa-file:${full}` : null, () => api.whatsapp.fileContent(full!));
  const thumbUrl = useBlobUrl(hasThumb && !fullUrl ? `wa-thumb:${chatJid}/${msgId}` : null, () => api.whatsapp.thumbnail(chatJid, msgId));
  if (fullUrl && category === "image") {
    return <img src={fullUrl} alt={alt} className="max-h-72 max-w-full rounded-md object-contain" />;
  }
  if (fullUrl && category === "video") {
    return <video src={fullUrl} controls className="max-h-72 max-w-full rounded-md" aria-label={alt} />;
  }
  if (fullUrl && category === "audio") {
    return <audio src={fullUrl} controls className="w-64" aria-label={alt} />;
  }
  if (thumbUrl) {
    return <img src={thumbUrl} alt={alt} className="max-h-48 max-w-full rounded-md object-contain opacity-80" />;
  }
  void mime;
  return null;
}
