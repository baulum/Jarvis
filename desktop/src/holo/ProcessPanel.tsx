// Live-activity view, left: what runs right now – each request with its tool calls as parallel lanes, agents,
// approvals – with real runtimes, plus per-tool statistics since the core started. All from activity steps.
import { useMemo } from "react";
import { formatDuration, type Process } from "./graph/live";
import { PHASE_ICON } from "./phaseIcons";
import { useBrain } from "./store";
import { useNow } from "./useNow";

const STATUS_DOT: Record<Process["status"], string> = {
  running: "bg-arc live-dot",
  waiting: "bg-ember",
  ok: "bg-[#7fdcb4]",
  error: "bg-alarm",
  cancelled: "bg-ink-faint",
};

/** Lane width: log scale up to 30 s, so a 200 ms lookup and a 20 s browser run are both readable. */
function laneWidth(ms: number): string {
  const f = Math.min(1, Math.log1p(ms / 100) / Math.log1p(300));
  return `${Math.max(4, f * 100).toFixed(1)}%`;
}

function nodeOf(p: Process): string | undefined {
  if (p.tool) return `tool:${p.tool}`;
  if (p.key.startsWith("turn:")) return `session:${p.key.slice(5)}`;
  if (p.phase === "agent") return p.key;
  return p.path[p.path.length - 1];
}

function Lane({ p, now, child }: { p: Process; now: number; child?: boolean }) {
  const select = useBrain((s) => s.select);
  const hover = useBrain((s) => s.hover);
  const running = p.until === undefined;
  const ms = running ? now - p.since : (p.durationMs ?? p.updated - p.since);
  const Icon = PHASE_ICON[p.phase];
  const node = nodeOf(p);
  return (
    <li>
      <button
        type="button"
        onClick={() => node && select(node)}
        onMouseEnter={() => node && hover(node)}
        onMouseLeave={() => hover(null)}
        className={`group flex w-full flex-col gap-1 rounded-md py-1.5 pr-2 text-left hover:bg-plate/70 ${child ? "pl-7" : "pl-2"}`}
      >
        <span className="flex items-center gap-2">
          <span className={`size-1.5 shrink-0 rounded-full ${STATUS_DOT[p.status]}`} aria-hidden />
          <Icon className="size-3.5 shrink-0 text-ink-faint" strokeWidth={1.6} />
          <span className="min-w-0 flex-1 truncate text-xs text-ink-dim group-hover:text-ink">{p.label}</span>
          <span className={`type-data shrink-0 text-[0.62rem] ${p.status === "error" ? "text-alarm" : running ? "text-arc-dim" : "text-ink-faint"}`}>{formatDuration(ms)}</span>
        </span>
        {p.tool && (
          <span className="flex items-center gap-2 pl-[1.375rem]">
            <span className="type-data truncate text-[0.6rem] text-ink-faint">{p.tool}</span>
            <span className="relative h-[3px] flex-1 overflow-hidden rounded-full bg-seam">
              <span
                className={`absolute inset-y-0 left-0 rounded-full ${p.status === "error" ? "bg-alarm" : running ? "bg-arc" : "bg-arc-dim"}`}
                style={{ width: laneWidth(ms) }}
              />
            </span>
          </span>
        )}
        {p.error && <span className="truncate pl-[1.375rem] text-2xs text-alarm">{p.error}</span>}
      </button>
    </li>
  );
}

export function ProcessPanel() {
  const processes = useBrain((s) => s.live.processes);
  const tools = useBrain((s) => s.live.tools);
  const anyRunning = Object.values(processes).some((p) => p.until === undefined);
  const now = useNow(anyRunning ? 150 : 1000);

  const { turns, orphans, agents, approvals } = useMemo(() => {
    const all = Object.values(processes).filter((p) => p.phase !== "memory.retrieve" && p.key !== "voice");
    const turnList = all.filter((p) => p.key.startsWith("turn:")).sort((a, b) => b.since - a.since);
    const turnKeys = new Set(turnList.map((t) => t.key));
    return {
      turns: turnList.map((t) => ({ turn: t, calls: all.filter((p) => p.phase === "tool" && p.parent === t.key).sort((a, b) => a.since - b.since) })),
      orphans: all.filter((p) => p.phase === "tool" && !(p.parent && turnKeys.has(p.parent))).sort((a, b) => b.since - a.since),
      agents: all.filter((p) => p.phase === "agent").sort((a, b) => b.since - a.since),
      approvals: all.filter((p) => p.phase === "approval"),
    };
  }, [processes]);

  const topTools = useMemo(
    () => Object.entries(tools).filter(([, s]) => s.calls > 0 || s.running > 0).sort((a, b) => b[1].calls - a[1].calls).slice(0, 6),
    [tools],
  );
  const empty = !turns.length && !orphans.length && !agents.length && !approvals.length;

  return (
    <section className="holo-panel pointer-events-auto flex min-h-0 flex-col" aria-label="Laufende Prozesse">
      <header className="flex items-baseline justify-between px-3.5 pt-3 pb-2">
        <h2 className="text-2xs tracking-[0.12em] text-ink-faint uppercase">Prozesse</h2>
        <span className="type-data text-[0.6rem] text-ink-faint">{Object.values(processes).filter((p) => p.until === undefined).length} aktiv</span>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-2">
        {empty && (
          <p className="px-2 pb-2 text-2xs leading-relaxed text-ink-faint">
            Gerade läuft nichts. Sobald Jarvis arbeitet, erscheinen hier Anfragen mit ihren Werkzeugaufrufen, Agenten und Freigaben – mit echten Laufzeiten.
          </p>
        )}
        {approvals.length > 0 && (
          <ul className="flex flex-col">
            {approvals.map((p) => (
              <Lane key={p.key} p={p} now={now} />
            ))}
          </ul>
        )}
        {turns.map(({ turn, calls }) => {
          const parallel = calls.filter((c) => c.until === undefined).length;
          return (
            <div key={turn.key} className="border-t border-seam/60 pt-1 first:border-t-0">
              <ul className="flex flex-col">
                <Lane p={turn} now={now} />
                {calls.map((c) => (
                  <Lane key={c.key} p={c} now={now} child />
                ))}
              </ul>
              {parallel > 1 && <p className="type-data pb-1 pl-7 text-[0.6rem] text-arc-dim">{parallel} Werkzeuge laufen parallel</p>}
            </div>
          );
        })}
        {orphans.length > 0 && (
          <ul className="flex flex-col border-t border-seam/60 pt-1">
            {orphans.map((p) => (
              <Lane key={p.key} p={p} now={now} />
            ))}
          </ul>
        )}
        {agents.length > 0 && (
          <>
            <p className="px-2 pt-2 pb-1 text-2xs tracking-[0.12em] text-ink-faint uppercase">Agenten & Aufgaben</p>
            <ul className="flex flex-col">
              {agents.map((p) => (
                <Lane key={p.key} p={p} now={now} />
              ))}
            </ul>
          </>
        )}
        {topTools.length > 0 && (
          <>
            <p className="px-2 pt-3 pb-1 text-2xs tracking-[0.12em] text-ink-faint uppercase">Werkzeuge seit Start</p>
            <table className="w-full text-left">
              <tbody>
                {topTools.map(([name, s]) => (
                  <tr key={name} className="text-2xs">
                    <td className="type-data max-w-0 truncate py-0.5 pl-2 text-[0.62rem] text-ink-dim">{name}</td>
                    <td className="type-data py-0.5 text-right text-[0.62rem] text-ink-faint">{s.calls}×</td>
                    <td className="type-data py-0.5 text-right text-[0.62rem] text-ink-faint">{s.avg_ms !== null ? `Ø ${formatDuration(s.avg_ms)}` : "läuft"}</td>
                    <td className={`type-data py-0.5 pr-2 text-right text-[0.62rem] ${s.errors ? "text-alarm" : "text-ink-faint"}`}>{s.errors ? `${s.errors} Fehler` : "ok"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </section>
  );
}
