// "/" or ⌘K: one field to find anything in the brain (labels instantly, memory semantically) and to reach
// every system view. Enter flies to the result.
import { AnimatePresence, motion } from "framer-motion";
import { Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { MemorySearchHit } from "../lib/types";
import { api } from "../store/app";
import { searchLocal } from "./graph/model";
import { KIND_LABEL } from "./graph/types";
import { SYSTEM_VIEWS } from "./SystemPanel";
import { useBrain } from "./store";

interface Item {
  key: string;
  label: string;
  hint: string;
  run: () => void;
}

export function CommandBar() {
  const overlay = useBrain((s) => s.overlay);
  const open = useBrain((s) => s.openOverlay);
  const visible = overlay === "search";
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA");
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !typing)) {
        e.preventDefault();
        open(visible ? null : "search");
      } else if (e.key === "Escape" && visible) open(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, visible]);
  return <AnimatePresence>{visible && <CommandPanel key="cmd" />}</AnimatePresence>;
}

function CommandPanel() {
  const open = useBrain((s) => s.openOverlay);
  const model = useBrain((s) => s.model);
  const select = useBrain((s) => s.select);
  const focusNodes = useBrain((s) => s.focusNodes);
  const [q, setQ] = useState("");
  const [result, setResult] = useState<{ q: string; hits: MemorySearchHit[] }>({ q: "", hits: [] });
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const t = setTimeout(() => input.current?.focus(), 30);
    return () => clearTimeout(t);
  }, []);

  // semantic memory search (debounced) – the same hybrid search Jarvis uses
  useEffect(() => {
    const query = q.trim();
    if (query.length < 3) return;
    const t = setTimeout(() => {
      api
        .memorySearch(query, 6)
        .then((hits) => setResult({ q: query, hits }))
        .catch(() => setResult({ q: query, hits: [] }));
    }, 220);
    return () => clearTimeout(t);
  }, [q]);
  const hits = q.trim().length >= 3 && result.q === q.trim() ? result.hits : [];

  const items: Item[] = useMemo(() => {
    const out: Item[] = [];
    if (model && q.trim()) {
      for (const n of searchLocal(model, q, 6)) {
        out.push({ key: n.id, label: n.label, hint: KIND_LABEL[n.kind] ?? n.kind, run: () => select(n.id) });
      }
      for (const h of hits) {
        const id = `memory:${h.id}`;
        if (out.some((o) => o.key === id)) continue;
        out.push({ key: id, label: h.title, hint: `${KIND_LABEL[h.kind] ?? h.kind} · Gedächtnis`, run: () => select(id) });
      }
      if (hits.length > 1) {
        out.push({
          key: "all-hits",
          label: `Alle ${hits.length} Treffer zeigen`,
          hint: "Cluster",
          run: () => focusNodes(hits.map((h) => `memory:${h.id}`)),
        });
      }
    }
    const ql = q.trim().toLowerCase();
    for (const v of SYSTEM_VIEWS) {
      if (!ql || v.label.toLowerCase().includes(ql) || v.keywords.some((k) => k.includes(ql))) {
        out.push({ key: `view:${v.id}`, label: v.label, hint: "Systemansicht", run: () => open("system", v.id) });
      }
    }
    return out.slice(0, 14);
  }, [model, q, hits, select, focusNodes, open]);

  function run(i: number) {
    const it = items[i];
    if (!it) return;
    if (!it.key.startsWith("view:")) open(null);
    it.run();
  }

  return (
    <>
      {(
        <motion.div
          key="cmd"
          className="pointer-events-auto absolute inset-0 z-30 flex justify-center bg-void/40 pt-[14vh]"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onMouseDown={(e) => e.target === e.currentTarget && open(null)}
        >
          <motion.div
            initial={{ y: -8, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: -8, opacity: 0 }}
            className="holo-panel h-fit w-[min(36rem,calc(100vw-2rem))]"
            role="dialog"
            aria-label="Suche"
          >
            <div className="flex items-center gap-3 border-b border-seam px-4">
              <Search className="size-4 text-ink-faint" />
              <input
                ref={input}
                value={q}
                onChange={(e) => {
                  setQ(e.target.value);
                  setActive(0);
                }}
                onKeyDown={(e) => {
                  if (e.key === "ArrowDown") {
                    e.preventDefault();
                    setActive((a) => Math.min(items.length - 1, a + 1));
                  } else if (e.key === "ArrowUp") {
                    e.preventDefault();
                    setActive((a) => Math.max(0, a - 1));
                  } else if (e.key === "Enter") {
                    e.preventDefault();
                    run(active);
                  }
                }}
                placeholder="Erinnerung, Person, Projekt, Werkzeug – oder eine Ansicht"
                className="h-12 flex-1 bg-transparent text-[0.9375rem] text-ink placeholder:text-ink-faint focus:outline-none"
                aria-label="Suchen"
              />
            </div>
            <ul className="max-h-[50vh] overflow-y-auto py-1.5" role="listbox">
              {items.map((it, i) => (
                <li key={it.key} role="option" aria-selected={i === active}>
                  <button
                    type="button"
                    onMouseEnter={() => setActive(i)}
                    onClick={() => run(i)}
                    className={`flex w-full items-baseline gap-3 px-4 py-2 text-left ${i === active ? "bg-plate-hi" : ""}`}
                  >
                    <span className="truncate text-[0.875rem] text-ink">{it.label}</span>
                    <span className="ml-auto shrink-0 text-2xs text-ink-faint">{it.hint}</span>
                  </button>
                </li>
              ))}
              {items.length === 0 && <li className="px-4 py-3 text-xs text-ink-faint">Nichts gefunden.</li>}
            </ul>
          </motion.div>
        </motion.div>
      )}
    </>
  );
}
