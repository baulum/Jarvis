// Files the chat shows (generated images/videos, image attachments) come from the core as authenticated Blobs
// (GET /api/media/{kind}/{file} – file name only, never a path) and are shown via object URLs. The webview never
// touches the file system: no file://, no asset protocol, nothing outside the folders the core allows.
import { useCallback, useEffect, useState } from "react";
import { ApiError, type ChatFileKind } from "../lib/api";
import { api, useApp } from "../store/app";

// One fetch per file, shared by every mount (re-renders, the chat dock, re-opening a chat).
const cache = new Map<string, Promise<string>>();
const MAX_CACHED = 60;

function loadChatFile(kind: ChatFileKind, file: string): Promise<string> {
  const key = `${kind}/${file}`;
  let hit = cache.get(key);
  if (!hit) {
    hit = api.media(kind, file).then((blob) => URL.createObjectURL(blob));
    hit.catch(() => cache.delete(key)); // a failed load is retried on the next mount or "Erneut laden"
    cache.set(key, hit);
    while (cache.size > MAX_CACHED) {
      const [oldKey, old] = cache.entries().next().value as [string, Promise<string>];
      cache.delete(oldKey);
      old.then(URL.revokeObjectURL, () => undefined);
    }
  }
  return hit;
}

/** Why a file did not load, in words Sir can act on. */
export function loadError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 404) return "Die Datei ist nicht mehr vorhanden.";
    return e.message;
  }
  return "Die Datei konnte nicht gelesen werden.";
}

export function useChatFile(kind: ChatFileKind, file: string): { url?: string; error?: string; retry: () => void } {
  const key = `${kind}/${file}`;
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ key: string; url?: string; error?: string }>({ key });
  useEffect(() => {
    let alive = true;
    loadChatFile(kind, file).then(
      (url) => alive && setState({ key: `${kind}/${file}`, url }),
      (e: unknown) => alive && setState({ key: `${kind}/${file}`, error: loadError(e) }),
    );
    return () => {
      alive = false;
    };
  }, [kind, file, attempt]);
  const retry = useCallback(() => {
    setState({ key: `${kind}/${file}` });
    setAttempt((n) => n + 1);
  }, [kind, file]);
  return state.key === key ? { url: state.url, error: state.error, retry } : { retry }; // another file: loading
}

/**
 * File manager of the Core host ("Finder" on the Mac, "Explorer", "Dateimanager"), or null when the file lies on
 * another computer (desktop app/browser as client of a remote Core): then "… zeigen" is not offered.
 */
export function useFileManager(): string | null {
  const remote = useApp((s) => s.remote);
  const name = useApp((s) => s.status?.platform?.file_manager);
  return remote ? null : (name ?? "Finder");
}

/** "Im Finder zeigen" – the core opens the Finder after checking the name; failures become a toast. */
export function useRevealInFinder(): (kind: ChatFileKind, file: string) => void {
  const pushToast = useApp((s) => s.pushToast);
  const manager = useFileManager() ?? "Finder";
  return useCallback(
    (kind, file) => {
      api.revealMedia(kind, file).catch((e: unknown) =>
        pushToast({ tone: "bad", title: `Im ${manager} nicht gefunden`, body: e instanceof Error ? e.message : String(e) }),
      );
    },
    [pushToast, manager],
  );
}
