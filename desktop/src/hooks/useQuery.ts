import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../lib/api";
import { useApp, type Topic } from "../store/app";

export interface QueryState<T> {
  data: T | undefined;
  error: string | null;
  /** True only until the first response (keeps previous data visible on refetch). */
  loading: boolean;
  refreshing: boolean;
  refetch: () => Promise<void>;
  setData: (fn: (prev: T | undefined) => T | undefined) => void;
}

function message(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}

/**
 * Minimal data hook: fetches on mount and whenever `key` changes, refetches when one of the
 * given bus topics bumps, optionally polls. No caching layer on purpose.
 */
export function useQuery<T>(
  fetcher: () => Promise<T>,
  key: string,
  opts: { topics?: Topic[]; pollMs?: number; enabled?: boolean } = {},
): QueryState<T> {
  const { topics = [], pollMs, enabled = true } = opts;
  const [data, setDataState] = useState<T | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const seq = useRef(0);
  const token = useApp((s) => s.token);
  const topicVersion = useApp((s) => topics.map((t) => s.versions[t]).join(","));

  const run = useCallback(async () => {
    const my = (seq.current += 1);
    setRefreshing(true);
    try {
      const res = await fetcherRef.current();
      if (my !== seq.current) return;
      setDataState(res);
      setError(null);
    } catch (e) {
      if (my !== seq.current) return;
      setError(message(e));
    } finally {
      if (my === seq.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => {
    if (!enabled || !token) return;
    void run();
  }, [run, key, topicVersion, token, enabled]);

  useEffect(() => {
    if (!pollMs || !enabled || !token) return;
    const t = setInterval(() => void run(), pollMs);
    return () => clearInterval(t);
  }, [run, pollMs, enabled, token]);

  const setData = useCallback((fn: (prev: T | undefined) => T | undefined) => setDataState((p) => fn(p)), []);

  return { data, error, loading: loading && data === undefined, refreshing, refetch: run, setData };
}

/** Wraps an async action with busy/error state and a toast on failure. */
export function useAction() {
  const [busy, setBusy] = useState<string | null>(null);
  const pushToast = useApp((s) => s.pushToast);
  const run = useCallback(
    async <R,>(label: string, fn: () => Promise<R>, success?: string): Promise<R | undefined> => {
      setBusy(label);
      try {
        const r = await fn();
        if (success) pushToast({ tone: "ok", title: success });
        return r;
      } catch (e) {
        pushToast({ tone: "bad", title: "Aktion fehlgeschlagen", body: message(e) });
        return undefined;
      } finally {
        setBusy(null);
      }
    },
    [pushToast],
  );
  return { busy, run };
}
