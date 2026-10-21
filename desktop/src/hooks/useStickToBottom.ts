import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

const THRESHOLD_PX = 48;

/**
 * Keeps a scroll container pinned to its end while new content arrives – but only while Sir is at the end.
 * Scrolling up releases it; `toBottom` (or sending a message) pins it again. Content that grows on its own
 * (a map loading, a code block expanding) is followed via ResizeObserver. Older content inserted above keeps
 * the visible position (`keepPosition` before the insert).
 */
export function useStickToBottom<T extends HTMLElement>(signal: unknown) {
  const ref = useRef<T>(null);
  const stick = useRef(true);
  const anchor = useRef<number | null>(null);
  const [atBottom, setAtBottom] = useState(true);

  const onScroll = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const bottom = el.scrollHeight - el.scrollTop - el.clientHeight < THRESHOLD_PX;
    stick.current = bottom;
    setAtBottom(bottom);
  }, []);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (anchor.current !== null) {
      el.scrollTop += el.scrollHeight - anchor.current;
      anchor.current = null;
    } else if (stick.current) el.scrollTop = el.scrollHeight;
  }, [signal]);

  // (re)attach whenever the container or its content element changes – e.g. a log that only mounts with the
  // first message
  const observed = useRef<{ content: Element | null; ro: ResizeObserver | null }>({ content: null, ro: null });
  useEffect(() => {
    const el = ref.current;
    const content = el?.firstElementChild ?? null;
    const o = observed.current;
    if (o.content === content) return;
    o.ro?.disconnect();
    o.content = content;
    o.ro = null;
    if (!el || !content || typeof ResizeObserver === "undefined") return;
    o.ro = new ResizeObserver(() => {
      if (stick.current) el.scrollTop = el.scrollHeight;
    });
    o.ro.observe(content);
  });
  useEffect(() => {
    const o = observed.current;
    return () => o.ro?.disconnect();
  }, []);

  const toBottom = useCallback((smooth = true) => {
    stick.current = true;
    setAtBottom(true);
    const el = ref.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  }, []);

  const keepPosition = useCallback(() => {
    if (ref.current) anchor.current = ref.current.scrollHeight;
  }, []);

  return { ref, onScroll, atBottom, toBottom, keepPosition };
}
