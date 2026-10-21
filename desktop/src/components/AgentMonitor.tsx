// Agent monitor building blocks: live view of one coding job, controls (pause / resume / cancel / message)
// shared by jobs and tasks, and the chronological timeline also used for tool calls in conversations.
// Pause and cancel always ask first. Every state has an icon and a label, never only a color.
import {
  ArrowDown,
  ChevronRight,
  CircleCheck,
  CircleMinus,
  CirclePause,
  CircleX,
  Clock,
  Hand,
  ListTree,
  LoaderCircle,
  MessageSquare,
  Pause,
  Pin,
  PinOff,
  Play,
  RotateCw,
  SendHorizontal,
  Square,
  type LucideIcon,
} from "lucide-react";
import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useAction, useQuery } from "../hooks/useQuery";
import { useNow } from "../hooks/useNow";
import { formatClock, formatDuration, formatTokens, formatUsd, type Tone } from "../lib/format";
import {
  ENTRY_STATUS,
  NO_FILTER,
  filterEntries,
  groupEntries,
  jobEntries,
  jobPhase,
  statusCounts,
  toolOptions,
  type EntryGroup,
  type EntryStatus,
  type MonitorEntry,
  type MonitorFilter,
  type StatusFilter,
  type TimelineItem,
} from "../lib/monitor";
import type { JobMonitor } from "../lib/types";
import { api } from "../store/app";
import { ConfirmDialog } from "./ConfirmDialog";
import { Button, CodeBlock, ErrorState, RiskBadge, Skeleton, Tabs } from "./ui";

export interface ControlState {
  canPause: boolean;
  canResume: boolean;
  canCancel: boolean;
  canMessage: boolean;
}

export function AgentControls({
  subject,
  state,
  pending = 0,
  onPause,
  onResume,
  onCancel,
  onMessage,
  compact = false,
}: {
  /** what is being controlled, for the dialogs ("Job", "Aufgabe") */
  subject: string;
  state: ControlState;
  pending?: number;
  onPause: () => Promise<unknown>;
  onResume: () => Promise<unknown>;
  onCancel: () => Promise<unknown>;
  onMessage: (text: string, immediate: boolean) => Promise<unknown>;
  compact?: boolean;
}) {
  const [confirm, setConfirm] = useState<"pause" | "cancel" | null>(null);
  const [text, setText] = useState("");
  const [immediate, setImmediate] = useState(false);
  const act = useAction();

  const run = (kind: "pause" | "cancel") =>
    act
      .run(kind, kind === "pause" ? onPause : onCancel, kind === "pause" ? `${subject} pausiert` : `${subject} abgebrochen`)
      .finally(() => setConfirm(null));

  async function sendMessage() {
    const t = text.trim();
    if (!t) return;
    const ok = await act.run(
      "message",
      async () => {
        await onMessage(t, immediate);
        return true;
      },
      immediate ? "Nachricht gesendet – Schritt wird unterbrochen" : "Nachricht vorgemerkt",
    );
    if (ok) setText("");
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        {state.canPause && (
          <Button size="sm" icon={<Pause className="size-3" />} onClick={() => setConfirm("pause")}>
            Pausieren
          </Button>
        )}
        {state.canResume && (
          <Button size="sm" variant="primary" icon={<Play className="size-3" />} busy={act.busy === "resume"} onClick={() => void act.run("resume", onResume, `${subject} läuft weiter`)}>
            Fortsetzen
          </Button>
        )}
        {state.canCancel && (
          <Button size="sm" variant="danger" icon={<Square className="size-3" />} onClick={() => setConfirm("cancel")}>
            Abbrechen
          </Button>
        )}
      </div>
      {state.canMessage && (
        <form
          className="flex flex-col gap-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            void sendMessage();
          }}
        >
          <div className="flex items-end gap-2">
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  void sendMessage();
                }
              }}
              rows={compact ? 1 : 2}
              placeholder={`Nachricht an den ${subject === "Job" ? "Agenten" : "laufenden Agenten"} …`}
              aria-label="Nachricht an den Agenten"
              className="field min-h-[2.2rem] flex-1 resize-none py-1.5 text-[0.8125rem]"
            />
            <Button type="submit" variant="primary" size="sm" busy={act.busy === "message"} disabled={!text.trim()} icon={<SendHorizontal className="size-3.5" />}>
              Senden
            </Button>
          </div>
          <label className="flex items-center gap-2 text-2xs text-ink-faint">
            <input type="checkbox" checked={immediate} onChange={(e) => setImmediate(e.target.checked)} />
            Sofort zustellen (unterbricht den aktuellen Schritt) – sonst nach dem aktuellen Lauf
            {pending > 0 && <span className="text-ember">· {pending} wartet</span>}
          </label>
        </form>
      )}

      <ConfirmDialog
        open={confirm === "pause"}
        title={`${subject} pausieren?`}
        confirmLabel="Pausieren"
        busy={act.busy === "pause"}
        onCancel={() => setConfirm(null)}
        onConfirm={() => void run("pause")}
      >
        {subject === "Job"
          ? "Der Agent-Prozess wird eingefroren und läuft mit „Fortsetzen“ genau an dieser Stelle weiter."
          : "Der Schritt in Arbeit wird noch beendet, danach hält die Aufgabe an und läuft mit „Fortsetzen“ weiter."}
      </ConfirmDialog>
      <ConfirmDialog
        open={confirm === "cancel"}
        title={`${subject} abbrechen?`}
        tone="danger"
        confirmLabel="Abbrechen"
        busy={act.busy === "cancel"}
        onCancel={() => setConfirm(null)}
        onConfirm={() => void run("cancel")}
      >
        Die Arbeit wird beendet und kann nicht fortgesetzt werden. Bereits geschriebene Dateien bleiben im Worktree erhalten.
      </ConfirmDialog>
    </div>
  );
}

// ------------------------------------------------------------------ status

const STATUS_ICON: Record<EntryStatus, LucideIcon> = {
  running: LoaderCircle,
  waiting: Hand,
  paused: CirclePause,
  queued: Clock,
  ok: CircleCheck,
  error: CircleX,
  aborted: CircleMinus,
  note: MessageSquare,
};

const TONE_TEXT: Record<Tone, string> = {
  ok: "text-arc",
  info: "text-arc-dim",
  warn: "text-ember",
  bad: "text-alarm",
  idle: "text-ink-faint",
};

/** State as icon: shape and color both carry it; the label is the accessible name. */
export function StatusIcon({ status, className = "size-3.5" }: { status: EntryStatus; className?: string }) {
  const Icon = STATUS_ICON[status];
  const meta = ENTRY_STATUS[status];
  return (
    <Icon
      role="img"
      aria-label={meta.label}
      className={`shrink-0 ${TONE_TEXT[meta.tone]} ${status === "running" ? "spin-slow" : ""} ${className}`}
    >
      <title>{meta.label}</title>
    </Icon>
  );
}

/** Icon plus label, for places where the state is the main information (task list, job list). */
export function StatusBadge({ status, label }: { status: EntryStatus; label?: string }) {
  return (
    <span className={`inline-flex shrink-0 items-center gap-1.5 text-xs ${TONE_TEXT[ENTRY_STATUS[status].tone]}`}>
      <StatusIcon status={status} className="size-3.5" />
      {label ?? ENTRY_STATUS[status].label}
    </span>
  );
}

// ------------------------------------------------------------------ timeline

const ROW_TINT: Partial<Record<EntryStatus, string>> = {
  error: "bg-alarm-deep/35",
  waiting: "bg-ember-deep/35",
};

/** Time | state | tool + summary | duration | chevron. The same columns in every row so the eye can scan. */
const ROW_GRID = "grid grid-cols-[4.25rem_0.875rem_minmax(0,1fr)_auto_0.75rem] items-center gap-x-2.5";

function liveDuration(e: { status: EntryStatus; at: string | null; durationS: number | null }, now: number): string {
  if (e.durationS != null) return formatDuration(e.durationS);
  if (e.status === "running" && e.at) return formatDuration(Math.max(0, (now - Date.parse(e.at)) / 1000));
  return "";
}

function EntryDetails({ entry, action }: { entry: MonitorEntry; action?: ReactNode }) {
  const isError = entry.status === "error";
  const facts: [string, ReactNode][] = [
    ["Status", ENTRY_STATUS[entry.status].label],
    ["Start", formatClock(entry.at)],
  ];
  if (entry.durationS != null) facts.push(["Dauer", formatDuration(entry.durationS)]);
  if (entry.scope) facts.push(["Von", entry.scope]);
  if (entry.risk) facts.push(["Risiko", <RiskBadge key="r" risk={entry.risk} />]);
  return (
    <div className="mt-2 mb-1 flex flex-col gap-2.5 pl-[calc(4.25rem+0.875rem+1.25rem)]">
      <dl className="flex flex-wrap gap-x-5 gap-y-1 text-xs">
        {facts.map(([k, v]) => (
          <div key={k} className="flex items-center gap-1.5">
            <dt className="text-ink-faint">{k}</dt>
            <dd className="text-ink-dim">{v}</dd>
          </div>
        ))}
      </dl>
      {entry.kind === "message" ? (
        <p className="selectable text-[0.8125rem] whitespace-pre-wrap text-ink">{entry.summary}</p>
      ) : (
        <>
          {entry.params && (
            <div>
              <p className="mb-1 text-2xs text-ink-faint">Parameter</p>
              <CodeBlock value={entry.params} className="max-h-48" />
            </div>
          )}
          {entry.result && (
            <div>
              <p className="mb-1 text-2xs text-ink-faint">{isError ? "Fehlermeldung" : entry.kind === "approval" ? "Begründung" : "Ergebnis (gekürzt)"}</p>
              <CodeBlock value={entry.result} className={`max-h-48 ${isError ? "border-alarm/30 text-alarm" : ""}`} />
            </div>
          )}
          {!entry.params && !entry.result && <p className="text-xs text-ink-faint">Keine weiteren Details.</p>}
        </>
      )}
      {action && <div>{action}</div>}
    </div>
  );
}

function EntryRow({ entry, now, action }: { entry: MonitorEntry; now: number; action?: (e: MonitorEntry) => ReactNode }) {
  const [open, setOpen] = useState(false);
  // a parameter (command, file, pattern) is code: monospace; messages and error texts are prose
  const mono = entry.kind === "tool" && !!entry.params && entry.summary === entry.params;
  return (
    <li className={`px-4 py-1.5 ${ROW_TINT[entry.status] ?? ""}`}>
      <button type="button" onClick={() => setOpen(!open)} className={`${ROW_GRID} w-full py-0.5 text-left`} aria-expanded={open}>
        <span className="type-data text-ink-faint">{formatClock(entry.at)}</span>
        <StatusIcon status={entry.status} />
        <span className="flex min-w-0 items-baseline gap-2">
          <span className={`shrink-0 text-[0.8125rem] font-medium ${entry.kind === "message" ? "text-ember" : "text-ink"}`}>{entry.tool}</span>
          {entry.risk && entry.risk !== "read" && <span className="shrink-0 self-center"><RiskBadge risk={entry.risk} /></span>}
          {entry.summary && (
            <span className={`min-w-0 truncate ${mono ? "type-data text-ink-dim" : "text-xs text-ink-dim"} ${entry.status === "error" && !mono ? "text-alarm" : ""}`}>
              {entry.summary}
            </span>
          )}
        </span>
        <span className="type-data text-ink-faint">{liveDuration(entry, now)}</span>
        <ChevronRight aria-hidden className={`size-3 text-ink-faint transition-transform duration-150 ${open ? "rotate-90" : ""}`} />
      </button>
      {open && <EntryDetails entry={entry} action={action?.(entry)} />}
    </li>
  );
}

function GroupRow({ group, now, action }: { group: EntryGroup; now: number; action?: (e: MonitorEntry) => ReactNode }) {
  // open by default while something in it needs attention; a click overrides that
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const auto = group.status === "error" || group.status === "waiting" || group.status === "running";
  const open = userOpen ?? auto;
  const errors = group.entries.filter((e) => e.status === "error").length;
  const running = group.entries.filter((e) => e.status === "running").length;
  return (
    <li>
      <button type="button" onClick={() => setUserOpen(!open)} className={`${ROW_GRID} w-full px-4 py-2 text-left hover:bg-plate-hi`} aria-expanded={open}>
        <span className="type-data text-ink-faint">{formatClock(group.at)}</span>
        <StatusIcon status={group.status} />
        <span className="flex min-w-0 items-baseline gap-2">
          <span className="shrink-0 text-[0.8125rem] font-medium text-ink">{group.tool}</span>
          <span className="type-data shrink-0 text-ink-faint">× {group.entries.length}</span>
          <span className="min-w-0 truncate text-xs text-ink-dim">
            {[errors ? `${errors} Fehler` : null, running ? `${running} läuft` : null].filter(Boolean).join(" · ") || "zusammengefasst"}
          </span>
        </span>
        <span className="type-data text-ink-faint">{group.durationS != null ? formatDuration(group.durationS) : ""}</span>
        <ChevronRight aria-hidden className={`size-3 text-ink-faint transition-transform duration-150 ${open ? "rotate-90" : ""}`} />
      </button>
      {open && (
        <ul className="ml-[calc(4.25rem+1rem)] border-l border-seam-hi pb-1">
          {group.entries.map((e) => (
            <EntryRow key={e.id} entry={e} now={now} action={action} />
          ))}
        </ul>
      )}
    </li>
  );
}

const FILTER_LABEL: Record<StatusFilter, string> = { all: "Alle", active: "Aktiv", error: "Fehler" };

/**
 * Chronological timeline (oldest at the top, newest at the bottom) with status/tool filter, grouping of
 * repeated calls and auto-scroll to the newest entry. Scrolling up or "Mitlaufen" off holds the position.
 */
export function MonitorTimeline({
  entries,
  live,
  empty,
  note,
  action,
  maxHeight = "max-h-[28rem]",
}: {
  entries: MonitorEntry[];
  /** something is still running: durations of running entries count up */
  live: boolean;
  empty: ReactNode;
  /** small hint in the toolbar (e.g. "letzte 200 von 340") */
  note?: string;
  /** extra content in the details of an entry (e.g. a link to the approvals) */
  action?: (e: MonitorEntry) => ReactNode;
  maxHeight?: string;
}) {
  const [filter, setFilter] = useState<MonitorFilter>(NO_FILTER);
  const [follow, setFollow] = useState(true);
  const [atBottom, setAtBottom] = useState(true);
  const bottomRef = useRef(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  const now = useNow(live);

  const tools = toolOptions(entries);
  const tool = filter.tool && tools.some((t) => t.tool === filter.tool) ? filter.tool : null;
  const counts = statusCounts(entries, tool);
  const visible = filterEntries(entries, { status: filter.status, tool });
  const filtered = filter.status !== "all" || tool != null;
  // grouping only in the full view: a filtered list is short and every hit should be visible
  const items: TimelineItem[] = filtered ? visible.map((entry) => ({ type: "entry", entry })) : groupEntries(visible);
  const last = visible[visible.length - 1];
  const signal = `${visible.length}:${last?.id ?? ""}:${last?.status ?? ""}:${filter.status}:${tool ?? ""}`;

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el && follow && bottomRef.current) el.scrollTop = el.scrollHeight;
  }, [signal, follow]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const bottom = el.scrollHeight - el.scrollTop - el.clientHeight < 32;
    bottomRef.current = bottom;
    setAtBottom(bottom);
  };
  const toNewest = () => {
    bottomRef.current = true;
    setAtBottom(true);
    setFollow(true);
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  };

  return (
    <div className="flex min-w-0 flex-col">
      {entries.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-seam px-4 py-2">
          <Tabs<StatusFilter>
            value={filter.status}
            onChange={(status) => setFilter({ ...filter, status })}
            items={(["all", "active", "error"] as const).map((id) => ({ id, label: FILTER_LABEL[id], count: counts[id] }))}
          />
          {tools.length > 1 && (
            <select
              aria-label="Nach Werkzeug filtern"
              className="field h-7 w-auto max-w-[12rem] py-0 text-xs"
              value={tool ?? ""}
              onChange={(e) => setFilter({ ...filter, tool: e.target.value || null })}
            >
              <option value="">Alle Werkzeuge</option>
              {tools.map((t) => (
                <option key={t.tool} value={t.tool}>
                  {t.tool} ({t.count})
                </option>
              ))}
            </select>
          )}
          {note && <span className="text-2xs text-ink-faint">{note}</span>}
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto"
            aria-pressed={follow}
            title={follow ? "Springt automatisch zum neuesten Eintrag – klicken zum Anhalten" : "Auto-Scroll angehalten – klicken zum Mitlaufen"}
            icon={follow ? <Pin className="size-3" /> : <PinOff className="size-3" />}
            onClick={() => (follow ? setFollow(false) : toNewest())}
          >
            {follow ? "Mitlaufen" : "Angehalten"}
          </Button>
        </div>
      )}
      <div className="relative">
        <div ref={scrollRef} onScroll={onScroll} className={`${maxHeight} overflow-y-auto`}>
          {entries.length === 0 ? (
            <div className="px-4 py-6 text-sm text-ink-faint">{empty}</div>
          ) : visible.length === 0 ? (
            <div className="flex flex-col items-start gap-2 px-4 py-6 text-sm text-ink-faint">
              <p>{filter.status === "error" ? "Keine Fehler – alles im grünen Bereich." : "Keine Einträge für diesen Filter."}</p>
              <Button size="sm" variant="ghost" onClick={() => setFilter(NO_FILTER)}>
                Filter zurücksetzen
              </Button>
            </div>
          ) : (
            <ul className="divide-y divide-seam/70 py-1">
              {items.map((it) =>
                it.type === "group" ? (
                  <GroupRow key={it.group.id} group={it.group} now={now} action={action} />
                ) : (
                  <EntryRow key={it.entry.id} entry={it.entry} now={now} action={action} />
                ),
              )}
            </ul>
          )}
        </div>
        {!atBottom && visible.length > 0 && (
          <Button size="sm" variant="outline" className="absolute right-4 bottom-3 bg-plate" icon={<ArrowDown className="size-3" />} onClick={toNewest}>
            Zum neuesten
          </Button>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ job monitor

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex min-w-0 items-baseline gap-1.5" title={hint}>
      <dt className="text-2xs text-ink-faint">{label}</dt>
      <dd className="type-data truncate text-ink">{value}</dd>
    </div>
  );
}

function MonitorSkeleton() {
  return (
    <div className="flex flex-col gap-4 p-4" aria-busy="true" aria-label="Monitor lädt">
      <div className="flex items-center gap-3">
        <div className="size-5 animate-pulse rounded-full bg-plate-hi" />
        <div className="h-4 w-48 animate-pulse rounded bg-plate-hi" />
      </div>
      <Skeleton rows={5} />
    </div>
  );
}

/** Live monitor of one coding job: current state on top, then numbers, controls and the step timeline. */
export function JobMonitorPanel({ jobId }: { jobId: string }) {
  const q = useQuery(() => api.codingMonitor(jobId), `monitor-${jobId}`, { topics: ["coding"], pollMs: 5000 });
  const m = q.data;
  const active = m ? m.status === "running" : false;
  const now = useNow(active);

  if (q.loading) return <MonitorSkeleton />;
  if (q.error && !m) return <div className="p-4"><ErrorState message={q.error} onRetry={q.refetch} /></div>;
  if (!m) return null;

  const elapsed = m.elapsed_s + (active ? Math.max(0, (now - Date.parse(m.snapshot_at)) / 1000) : 0);
  const u = m.usage;
  const act = (fn: () => Promise<unknown>) => async () => {
    await fn();
    await q.refetch();
  };
  const entries = jobEntries(m);

  return (
    <div className="flex min-w-0 flex-col">
      <CurrentState m={m} now={now} />
      {q.error && (
        <div role="status" className="flex items-center gap-2 border-b border-seam bg-ember-deep/30 px-4 py-2 text-xs text-ember">
          <span className="min-w-0 flex-1">Live-Aktualisierung unterbrochen – Stand {formatClock(m.snapshot_at)}.</span>
          <Button size="sm" variant="ghost" icon={<RotateCw className="size-3" />} onClick={() => void q.refetch()}>
            Erneut laden
          </Button>
        </div>
      )}
      <dl className="flex flex-wrap gap-x-6 gap-y-1.5 border-b border-seam px-4 py-2.5">
        <Stat label="Laufzeit" value={formatDuration(elapsed)} hint="ohne Pausen" />
        <Stat label="Schritte" value={String(m.step_count)} />
        <Stat
          label="Tokens"
          value={formatTokens(u.input_tokens + u.output_tokens)}
          hint={`Ein ${u.input_tokens} · Aus ${u.output_tokens} · Cache gelesen ${u.cache_read_tokens} · Cache geschrieben ${u.cache_creation_tokens}`}
        />
        <Stat
          label="Kosten"
          value={u.cost_usd == null ? "–" : `${u.cost_final ? "" : "≥ "}${formatUsd(u.cost_usd)}`}
          hint="API-Gegenwert; das Abo berechnet nichts. Die CLI meldet Kosten erst am Ende eines Laufs."
        />
        {m.model && <Stat label="Modell" value={`${m.model}${m.effort ? ` · ${m.effort}` : ""}`} />}
      </dl>
      {(m.can_pause || m.can_resume || m.can_cancel || m.can_message) && (
        <div className="border-b border-seam px-4 py-3">
          <AgentControls
            subject="Job"
            state={{ canPause: m.can_pause, canResume: m.can_resume, canCancel: m.can_cancel, canMessage: m.can_message }}
            pending={m.pending_messages}
            onPause={act(() => api.codingPause(jobId))}
            onResume={act(() => api.codingResume(jobId))}
            onCancel={act(() => api.codingCancel(jobId))}
            onMessage={(text, immediate) => api.codingMessage(jobId, text, immediate).then(() => q.refetch())}
          />
        </div>
      )}
      <div className="flex items-center gap-2 px-4 pt-3 pb-1">
        <ListTree aria-hidden className="size-3.5 text-ink-faint" />
        <h3 className="type-title text-[0.8125rem] text-ink">Verlauf</h3>
        <span className="text-2xs text-ink-faint">Werkzeugaufrufe und Nachrichten, älteste oben</span>
      </div>
      <MonitorTimeline
        entries={entries}
        live={active}
        note={m.step_count > m.steps.length ? `letzte ${m.steps.length} von ${m.step_count} Schritten` : undefined}
        empty={m.status === "queued" ? "Der Job wartet auf seinen Start." : "Noch keine Werkzeugaufrufe."}
      />
    </div>
  );
}

const PHASE_FRAME: Partial<Record<EntryStatus, string>> = {
  error: "bg-alarm-deep/40",
  paused: "bg-ember-deep/30",
  waiting: "bg-ember-deep/30",
};

/** "What is the agent doing right now?" – the biggest element of the monitor. */
function CurrentState({ m, now }: { m: JobMonitor; now: number }) {
  const phase = jobPhase(m);
  const since = phase.since ? Math.max(0, (now - Date.parse(phase.since)) / 1000) : null;
  const done = m.current.kind === "done";
  return (
    <div className={`border-b border-seam px-4 py-3.5 ${PHASE_FRAME[phase.status] ?? ""}`} aria-live="polite">
      <p className="text-2xs text-ink-faint">{done ? "Ergebnis" : "Gerade"}</p>
      <div className="mt-1 flex min-w-0 items-center gap-2.5">
        <StatusIcon status={phase.status} className="size-5" />
        <p className={`type-title text-[1rem] ${TONE_TEXT[ENTRY_STATUS[phase.status].tone]}`}>{phase.title}</p>
        {phase.tool && <span className="truncate text-[0.9375rem] font-medium text-ink">{phase.tool}</span>}
        {since != null && Number.isFinite(since) && (
          <span className="type-data ml-auto shrink-0 text-ink-faint" title="Dauer des aktuellen Schritts">
            seit {formatDuration(since)}
          </span>
        )}
      </div>
      {phase.detail && <p className="type-data selectable mt-1.5 line-clamp-2 break-all text-ink-dim" title={phase.detail}>{phase.detail}</p>}
      {m.running_tools.length > 1 && (
        <p className="mt-1 text-xs text-ink-faint">{m.running_tools.length} Werkzeugaufrufe laufen parallel</p>
      )}
      {m.last_text && !done && (
        <p className="mt-2 line-clamp-2 text-[0.8125rem] text-ink-dim" title={m.last_text}>
          {m.last_text}
        </p>
      )}
      {m.error && (
        <div role="alert" className="mt-2.5 flex items-start gap-2 rounded-md border border-alarm/30 bg-alarm-deep/50 px-3 py-2">
          <CircleX aria-hidden className="mt-0.5 size-3.5 shrink-0 text-alarm" />
          <p className="selectable min-w-0 text-[0.8125rem] break-words whitespace-pre-wrap text-ink">{m.error}</p>
        </div>
      )}
    </div>
  );
}
