import { Pause, Play, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Button, EmptyState, Tabs } from "../components/ui";
import { formatClock } from "../lib/format";
import type { LogEntry } from "../lib/types";
import { api, useApp } from "../store/app";

const LEVELS = ["ALL", "INFO", "WARNING", "ERROR"] as const;
type Level = (typeof LEVELS)[number];
const RANK: Record<string, number> = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3, CRITICAL: 4 };
const TONE: Record<string, string> = { WARNING: "text-ember", ERROR: "text-alarm", CRITICAL: "text-alarm" };

export function LogsView() {
  const live = useApp((s) => s.logs);
  const paused = useApp((s) => s.logsPaused);
  const setPaused = useApp((s) => s.setLogsPaused);
  const clear = useApp((s) => s.clearLiveLogs);
  const [history, setHistory] = useState<LogEntry[]>([]);
  const [level, setLevel] = useState<Level>("ALL");
  const [q, setQ] = useState("");
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.logs(500).then(setHistory).catch(() => setHistory([]));
  }, []);

  const rows = useMemo(() => {
    const seen = new Set(live.map((l) => l.ts + l.msg));
    const merged = [...history.filter((h) => !seen.has(h.ts + h.msg)), ...live];
    const needle = q.trim().toLowerCase();
    return merged.filter(
      (l) => (level === "ALL" || (RANK[l.level] ?? 0) >= (RANK[level] ?? 0)) &&
        (!needle || l.msg.toLowerCase().includes(needle) || l.logger.toLowerCase().includes(needle)),
    );
  }, [history, live, level, q]);

  useEffect(() => {
    if (!paused) bottom.current?.scrollIntoView({ block: "end" });
  }, [rows.length, paused]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b border-seam px-8 py-2.5">
        <Tabs value={level} onChange={setLevel} items={LEVELS.map((l) => ({ id: l, label: l === "ALL" ? "Alle" : l }))} />
        <input className="field ml-auto w-64" placeholder="Filtern …" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Logs filtern" />
        <Button size="sm" variant="ghost" icon={paused ? <Play className="size-3.5" /> : <Pause className="size-3.5" />} onClick={() => setPaused(!paused)}>
          {paused ? "Fortsetzen" : "Pausieren"}
        </Button>
        <Button size="sm" variant="ghost" icon={<Trash2 className="size-3.5" />} onClick={() => { clear(); setHistory([]); }}>
          Leeren
        </Button>
      </div>
      <div className="selectable min-h-0 flex-1 overflow-y-auto px-8 py-3 font-mono text-[0.75rem] leading-relaxed">
        {rows.length === 0 ? (
          <EmptyState title="Keine Logeinträge." >Neue Einträge des Cores erscheinen hier in Echtzeit.</EmptyState>
        ) : (
          rows.map((l, i) => (
            <div key={`${l.ts}-${i}`} className="flex gap-3 whitespace-pre-wrap break-all hover:bg-plate-hi">
              <span className="shrink-0 text-ink-faint">{formatClock(l.ts)}</span>
              <span className={`w-16 shrink-0 ${TONE[l.level] ?? "text-ink-dim"}`}>{l.level}</span>
              <span className="w-56 shrink-0 truncate text-arc-dim" title={l.logger}>{l.logger}</span>
              <span className="text-ink">{l.msg}</span>
            </div>
          ))
        )}
        <div ref={bottom} />
      </div>
    </div>
  );
}
