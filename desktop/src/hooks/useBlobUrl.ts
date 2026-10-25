// Authenticated files as object URLs, one fetch per key shared by every mount (WhatsApp thumbnails and files).
import { useEffect, useState } from "react";

const cache = new Map<string, Promise<string>>();
const MAX_CACHED = 80;

function load(key: string, loader: () => Promise<Blob>): Promise<string> {
  let hit = cache.get(key);
  if (!hit) {
    hit = loader().then((b) => URL.createObjectURL(b));
    hit.catch(() => cache.delete(key));
    cache.set(key, hit);
    while (cache.size > MAX_CACHED) {
      const [oldKey, old] = cache.entries().next().value as [string, Promise<string>];
      cache.delete(oldKey);
      old.then(URL.revokeObjectURL, () => undefined);
    }
  }
  return hit;
}

export function useBlobUrl(key: string | null, loader: () => Promise<Blob>): string | undefined {
  const [url, setUrl] = useState<{ key: string; url: string } | null>(null);
  useEffect(() => {
    if (!key) return;
    let alive = true;
    load(key, loader).then((u) => alive && setUrl({ key, url: u }), () => undefined);
    return () => {
      alive = false;
    };
    // the loader belongs to the key
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return url && url.key === key ? url.url : undefined;
}

