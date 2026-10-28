// The live event stream: one headline for "what is happening right now" and the latest real activity steps
// ("Analysiere Anfrage", "Durchsuche Erinnerungen · 3 Treffer", "Starte Browser" …). Every line comes from a
// filtered core event; hovering a line highlights its nodes in the brain, a click flies there.
import { AnimatePresence, motion } from "framer-motion";
import { ChevronDown } from "lucide-react";
import { useMemo } from "react";
import { formatDuration, headline, type ActivityLine } from "./graph/live";
import { PHASE_ICON } from "./phaseIcons";
import { useBrain } from "./store";
import { useNow } from "./useNow";

const TONE_TEXT: Record<ActivityLine["tone"], string> = {
  info: "text-ink-dim",
  ok: "text-ink-dim",
  bad: "text-alarm",
  warn: "text-ember",
};

const ICON_TONE: Record<ActivityLine["tone"], string> = {
  info: "text-arc-dim",
  ok: "text-[#7fdcb4]",
  bad: "text-alarm",
  warn: "text-ember",
};

function clock(ms: number): string {
  return new Date(ms).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** Titles of the memories a line refers to (resolved from the graph – the stream itself carries only ids). */
function useTitles(ids: string[] | undefined): string {
  const model = useBrain((s) => s.model);
  return useMemo(() => {
    if (!ids?.length || !model) return "";
    const names = ids.map((id) => model.nodes[model.byId.get(id) ?? -1]?.label).filter(Boolean) as string[];
    if (!names.length) return "";
    return names.slice(0, 2).join(", ") + (ids.length > 2 ? ` +${ids.length - 2}` : "");
  }, [ids, model]);
}

function Line({ line }: { line: ActivityLine }) {
  const select = useBrain((s) => s.select);
  const focusNodes = useBrain((s) => s.focusNodes);
  const hover = useBrain((s) => s.hover);
  const titles = useTitles(line.phase === "memory.retrieve" || line.phase === "memory.store" ? (line.nodes ?? (line.node ? [line.node] : undefined)) : undefined);
  const Icon = PHASE_ICON[line.phase];
  const target = line.nodes && line.nodes.length > 1 ? line.nodes : line.node ? [line.node] : [];
  return (
    <li>
      <button
        type="button"
        disabled={!target.length}
        onMouseEnter={() => target[0] && hover(target[0])}
        onMouseLeave={() => hover(null)}
        onClick={() => (target.length > 1 ? focusNodes(target) : target[0] && select(target[0]))}
        className="group flex w-full items-start gap-2.5 rounded-md px-2 py-1 text-left enabled:hover:bg-plate/70"
      >
        <Icon className={`mt-[3px] size-3.5 shrink-0 ${ICON_TONE[line.tone]} ${line.replayed ? "opacity-50" : ""}`} strokeWidth={1.6} />
        <span className="min-w-0 flex-1">
          <span className={`block truncate text-xs ${TONE_TEXT[line.tone]} group-enabled:group-hover:text-ink`}>{line.text}</span>
          {(line.detail || titles) && (
            <span className="block truncate text-2xs text-ink-faint">
              {[titles, line.detail].filter(Boolean).join(" · ")}
            </span>
          )}
        </span>
        <time className="type-data mt-[2px] shrink-0 text-[0.6rem] text-ink-faint">{clock(line.at)}</time>
      </button>
    </li>
  );
}

export function Headline() {
  const live = useBrain((s) => s.live);
  const busy = Object.values(live.processes).some((p) => p.until === undefined);
  const now = useNow(busy ? 200 : 1000);
  const h = headline(live, now);
  const dot = h.tone === "bad" ? "bg-alarm" : h.tone === "warn" ? "bg-ember" : h.tone === "busy" ? "bg-arc live-dot" : "bg-ink-faint";
  return (
    <div className="flex min-w-0 items-center gap-2.5" role="status" aria-live="polite">
      <span className={`size-1.5 shrink-0 rounded-full ${dot}`} aria-hidden />
      <div className="min-w-0 flex-1">
        <p className={`truncate text-[0.8125rem] ${h.tone === "bad" ? "text-alarm" : h.tone === "warn" ? "text-ember" : "text-ink"}`}>{h.text}</p>
        {h.detail && <p className="type-data truncate text-[0.6rem] text-ink-faint">{h.detail}</p>}
      </div>
      {h.since !== undefined && <span className="type-data shrink-0 text-[0.62rem] text-arc-dim">{formatDuration(now - h.since)}</span>}
    </div>
  );
}

/** ``compact``: headline and the last few lines (brain/network view). ``full``: the whole recent stream. */
export function ActivityStream({ mode }: { mode: "compact" | "full" }) {
  const lines = useBrain((s) => s.live.lines);
  const open = useBrain((s) => s.streamOpen);
  const setOpen = useBrain((s) => s.setStreamOpen);
  const shown = mode === "full" ? [...lines].reverse() : lines.slice(-6).reverse();
  return (
    <section className="holo-panel pointer-events-auto flex min-h-0 flex-col" aria-label="Live-Aktivität">
      <header className="flex items-center gap-2 px-3 py-2.5">
        <div className="min-w-0 flex-1">
          <Headline />
        </div>
        {mode === "compact" && (
          <button
            type="button"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
            aria-label={open ? "Ereignisse einklappen" : "Ereignisse ausklappen"}
            className="rounded p-1 text-ink-faint hover:text-ink"
          >
            <ChevronDown className={`size-3.5 transition-transform duration-200 ${open ? "" : "-rotate-90"}`} />
          </button>
        )}
      </header>
      <AnimatePresence initial={false}>
        {(open || mode === "full") && (
          <motion.div
            key="lines"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
            className="min-h-0 overflow-hidden border-t border-seam"
          >
            {shown.length ? (
              <ul className={`flex flex-col gap-px p-1.5 ${mode === "full" ? "max-h-full overflow-y-auto" : ""}`}>
                {shown.map((l) => (
                  <Line key={l.id} line={l} />
                ))}
              </ul>
            ) : (
              <p className="px-3.5 py-3 text-2xs leading-relaxed text-ink-faint">
                Noch keine Aktivität. Anfragen, Werkzeugaufrufe, Gedächtniszugriffe und Agenten erscheinen hier, sobald sie passieren.
              </p>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}
