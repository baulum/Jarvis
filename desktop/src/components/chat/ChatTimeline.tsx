// The conversation as it happened: Sir's messages, Jarvis' answers with text and tool calls in event order,
// the "Gedankenprozess" of every answer and system notes – all from the ordered timeline (lib/timeline.ts).
import {
  Brain,
  Check,
  ChevronRight,
  CircleSlash,
  ClipboardList,
  Cpu,
  Database,
  Lightbulb,
  ListChecks,
  Loader2,
  Search,
  ShieldAlert,
  TriangleAlert,
  Wrench,
  X,
} from "lucide-react";
import { lazy, memo, Suspense, useCallback, useMemo, useState, type ReactNode } from "react";
import { useNow } from "../../hooks/useNow";
import { formatMs } from "../../lib/format";
import { routeSummary } from "../../lib/route";
import {
  buildRows,
  currentStep,
  formatClockMs,
  hasModelReasoning,
  isRouteDisplay,
  isTool,
  SOURCE_LABEL,
  toolSpans,
  type AnswerRow,
  type ChatThread,
  type MessageData,
  type ProcessData,
  type TimelineEvent,
  type ToolData,
  type ToolSpan,
} from "../../lib/timeline";
import { useApp } from "../../store/app";
import { Markdown } from "../Markdown";

const RouteMap = lazy(() => import("./RouteMap"));

interface Props {
  thread: ChatThread;
  compact?: boolean;
  /** show only the newest rows (chat dock) */
  lastRows?: number;
  onLoadOlder?: () => void;
  loadingOlder?: boolean;
}

export function ChatTimeline({ thread, compact = false, lastRows, onLoadOlder, loadingOlder }: Props) {
  const rows = useMemo(() => buildRows(thread.events), [thread.events]);
  const shown = lastRows ? rows.slice(-lastRows) : rows;
  const toggleCheck = useApp((s) => s.toggleCheck);
  const onToggle = useCallback(
    (eventId: string, index: number, checked: boolean) => void toggleCheck(thread.sessionId, eventId, index, checked),
    [toggleCheck, thread.sessionId],
  );
  return (
    <>
      {!lastRows && thread.hasMore && onLoadOlder && (
        <button
          type="button"
          onClick={onLoadOlder}
          disabled={loadingOlder}
          className="mx-auto flex items-center gap-2 rounded-full border border-seam px-3 py-1 text-xs text-ink-dim hover:border-seam-hi hover:text-ink disabled:opacity-50"
        >
          {loadingOlder && <Loader2 className="spin-slow size-3" />}
          Ältere Nachrichten laden
        </button>
      )}
      {shown.map((row) => (
        <div key={row.key} className={compact ? undefined : "timeline-row"}>
          {row.type === "user" ? (
            <UserBubble event={row.event} compact={compact} />
          ) : row.type === "system" ? (
            <SystemNote event={row.event} />
          ) : (
            <AnswerBlock row={row} compact={compact} onToggle={onToggle} />
          )}
        </div>
      ))}
    </>
  );
}

// ------------------------------------------------------------------ message types

const UserBubble = memo(function UserBubble({ event, compact }: { event: TimelineEvent<MessageData>; compact: boolean }) {
  if (compact) return <p className="selectable text-right text-[0.8125rem] whitespace-pre-wrap text-ink-dim">{event.data.text}</p>;
  return (
    <div className="flex flex-col items-end gap-1">
      <span className="type-data px-1 text-ink-faint">Sie</span>
      <div
        className={`selectable max-w-[80%] rounded-2xl rounded-br-sm border border-arc-dim/40 bg-arc-deep/60 px-4 py-2.5 text-[0.9375rem] leading-relaxed whitespace-pre-wrap text-white ${event.local ? "opacity-70" : ""}`}
      >
        {event.data.text}
      </div>
    </div>
  );
});

const SystemNote = memo(function SystemNote({ event }: { event: TimelineEvent<MessageData> }) {
  const error = event.data.level === "error";
  return (
    <div role="status" className="flex items-center gap-3 py-0.5">
      <span className="h-px flex-1 bg-seam" />
      <span className={`flex max-w-[80%] items-center gap-1.5 text-xs ${error ? "text-alarm" : "text-ember"}`}>
        {error ? <TriangleAlert className="size-3.5 shrink-0" /> : <CircleSlash className="size-3.5 shrink-0" />}
        <span className="selectable">{event.data.text}</span>
      </span>
      <span className="h-px flex-1 bg-seam" />
    </div>
  );
});

// ------------------------------------------------------------------ Jarvis' answer

const sameList = <T,>(a: T[], b: T[]) => a.length === b.length && a.every((x, i) => x === b[i]);

const AnswerBlock = memo(
  function AnswerBlock({
    row,
    compact,
    onToggle,
  }: {
    row: AnswerRow;
    compact: boolean;
    onToggle: (eventId: string, index: number, checked: boolean) => void;
  }) {
    const running = row.turn?.data.status === "running";
    const now = useNow(running && row.tools.some((t) => t.data.status === "running"), 250);
    const spans = useMemo(() => toolSpans(row.tools, now), [row.tools, now]);
    const showTrack = row.tools.length > 1;
    // "Denkt nach" only while nothing else shows progress (no text yet, no running tool)
    const empty =
      row.items.every((i) => isTool(i) || !(i.data as MessageData).text) && !row.tools.some((t) => t.data.status === "running");
    const body: ReactNode[] = row.items.map((item) =>
      isTool(item) ? (
        <ToolCall key={item.key ?? item.id} event={item} span={showTrack ? spans.get(item.id) : undefined} compact={compact} now={now} />
      ) : (item.data as MessageData).text ? (
        <div key={item.key ?? item.id} className={(item.data as MessageData).status === "streaming" ? "caret" : undefined}>
          <Markdown
            text={(item.data as MessageData).text}
            compact={compact}
            checks={(item.data as MessageData).checks}
            onToggleTask={item.local ? undefined : (index, checked) => onToggle(item.id, index, checked)}
          />
        </div>
      ) : null,
    );
    const process = row.process.length > 0 || running;
    if (compact) {
      return (
        <div className="selectable flex flex-col gap-1.5 text-[0.875rem] leading-relaxed text-ink">
          {process && <ThoughtProcess process={row.process} running={running} compact />}
          {body}
          {running && empty && <Pending />}
        </div>
      );
    }
    const meta = row.turn?.data;
    return (
      <div className="flex flex-col items-start gap-1">
        <span className="type-data flex items-center gap-2 px-1 text-arc-dim">
          Jarvis
          {meta?.status === "done" && meta.duration_ms !== undefined && <span className="text-ink-faint">{formatMs(meta.duration_ms)}</span>}
        </span>
        <div className="selectable flex w-full max-w-[min(100%,46rem)] flex-col gap-3 rounded-2xl rounded-tl-sm border border-seam bg-plate/70 px-5 py-4 max-[700px]:px-3.5">
          {process && <ThoughtProcess process={row.process} running={running} />}
          {body}
          {running && empty && <Pending />}
        </div>
      </div>
    );
  },
  (a, b) =>
    a.compact === b.compact &&
    a.onToggle === b.onToggle &&
    a.row.turn === b.row.turn &&
    sameList(a.row.items, b.row.items) &&
    sameList(a.row.process, b.row.process),
);

function Pending() {
  return <span className="flex h-6 items-center text-sm text-ink-faint">Denkt nach</span>;
}

// ------------------------------------------------------------------ tool call

const STATUS_LABEL = { running: "läuft", ok: "erfolgreich", failed: "fehlgeschlagen" } as const;

function ToolCall({ event, span, compact, now }: { event: TimelineEvent<ToolData>; span?: ToolSpan; compact: boolean; now: number }) {
  const [open, setOpen] = useState(false);
  const d = event.data;
  const duration = d.duration_ms ?? (d.status === "running" && d.started_ts ? now - d.started_ts : undefined);
  const route = isRouteDisplay(d.display) ? d.display : null;
  const detail = !d.legacy && (d.input || d.result || d.error || d.started_ts);
  const icon =
    d.status === "running" ? (
      <Loader2 className="spin-slow size-3.5 text-arc" />
    ) : d.status === "failed" ? (
      <X className="size-3.5 text-alarm" />
    ) : (
      <Check className="size-3.5 text-arc-dim" />
    );
  return (
    <div className="flex flex-col gap-2">
      <div className={`tool-call rounded-md border ${d.status === "failed" ? "border-alarm/35" : d.status === "running" ? "border-arc-dim/50" : "border-seam"}`}>
        <button
          type="button"
          disabled={!detail}
          aria-expanded={detail ? open : undefined}
          onClick={() => setOpen((o) => !o)}
          title={`${d.name}: ${STATUS_LABEL[d.status]}`}
          className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left enabled:hover:bg-plate-hi/60"
        >
          {icon}
          <Wrench className="size-3 shrink-0 text-ink-faint" aria-hidden />
          <span className="type-data min-w-0 truncate text-ink">{d.name}</span>
          <span className="sr-only">{STATUS_LABEL[d.status]}</span>
          {span?.parallel && <span className="text-2xs text-ink-faint">parallel</span>}
          <span className="ml-auto flex items-center gap-2">
            {span && !compact && (
              <span className="tool-track" aria-hidden>
                <span
                  className={`tool-track__bar ${d.status === "failed" ? "bg-alarm/70" : d.status === "running" ? "bg-arc" : "bg-arc-dim"}`}
                  style={{ left: `${span.offset * 100}%`, width: `${span.width * 100}%` }}
                />
              </span>
            )}
            {duration !== undefined && <span className="type-data w-14 text-right text-ink-faint tabular-nums">{formatMs(duration)}</span>}
            {detail && <ChevronRight className={`size-3.5 text-ink-faint transition-transform ${open ? "rotate-90" : ""}`} />}
          </span>
        </button>
        {d.status === "failed" && d.error && !open && <p className="selectable border-t border-alarm/20 px-2.5 py-1.5 text-xs text-alarm">{d.error}</p>}
        {open && detail && (
          <div className="selectable flex flex-col gap-2 border-t border-seam px-2.5 py-2 text-xs">
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-ink-dim">
              <dt className="text-ink-faint">Start</dt>
              <dd className="type-data">{formatClockMs(d.started_ts)}</dd>
              <dt className="text-ink-faint">Ende</dt>
              <dd className="type-data">{d.ended_ts ? formatClockMs(d.ended_ts) : "läuft"}</dd>
              {d.tool_use_id && (
                <>
                  <dt className="text-ink-faint">Aufruf-ID</dt>
                  <dd className="type-data truncate">{d.tool_use_id}</dd>
                </>
              )}
            </dl>
            {d.input && <Payload label="Eingabe" text={d.input} />}
            {d.error && <Payload label="Fehler" text={d.error} tone="bad" />}
            {d.result && <Payload label="Ergebnis" text={d.result} />}
            {d.redacted && <p className="text-ink-faint">Ergebnis auf diesem Gerät ausgeblendet.</p>}
          </div>
        )}
      </div>
      {route && d.status === "ok" && (
        <Suspense
          fallback={
            <p className={`flex items-center rounded-lg border border-seam bg-void px-3 text-xs text-ink-dim ${compact ? "h-44" : "h-64"}`}>
              {routeSummary(route)}
            </p>
          }
        >
          <RouteMap display={route} compact={compact} />
        </Suspense>
      )}
    </div>
  );
}

function Payload({ label, text, tone }: { label: string; text: string; tone?: "bad" }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-ink-faint">{label}</span>
      <pre className={`max-h-56 overflow-auto rounded border border-seam bg-void px-2.5 py-2 font-mono text-[0.75rem] leading-[1.5] whitespace-pre-wrap ${tone === "bad" ? "text-alarm" : "text-ink-dim"}`}>
        {text}
      </pre>
    </div>
  );
}

// ------------------------------------------------------------------ Gedankenprozess

const STEP_ICON: Record<string, typeof Brain> = {
  reasoning: Lightbulb,
  model: Cpu,
  memory: Database,
  search: Search,
  tool: Wrench,
  plan: ListChecks,
  action: ShieldAlert,
  summary: ClipboardList,
};

function ThoughtProcess({ process, running, compact = false }: { process: TimelineEvent<ProcessData>[]; running: boolean; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const live = running ? currentStep(process) : undefined;
  const reasoning = hasModelReasoning(process);
  const steps = process.filter((p) => p.data.step !== "summary").length;
  return (
    <div className={`thought rounded-md ${open ? "border border-seam bg-void/40" : ""}`}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full min-w-0 items-center gap-2 rounded-md px-1 py-0.5 text-left text-xs text-ink-dim hover:text-ink"
      >
        <ChevronRight className={`size-3.5 shrink-0 transition-transform ${open ? "rotate-90" : ""}`} />
        <Brain className={`size-3.5 shrink-0 ${running ? "text-arc" : "text-ink-faint"}`} />
        <span className="shrink-0">Gedankenprozess</span>
        {steps > 0 && <span className="shrink-0 text-ink-faint">{steps} Schritt{steps === 1 ? "" : "e"}</span>}
        {live && (
          <span className="flex min-w-0 items-center gap-1.5 text-ink-faint">
            <span className="live-dot" aria-hidden />
            <span className="truncate">{live.data.title}</span>
          </span>
        )}
      </button>
      {open && (
        <div className={`flex flex-col gap-2 px-2.5 pt-1 pb-2.5 ${compact ? "max-h-64 overflow-y-auto" : ""}`}>
          {!reasoning && (
            <p className="rounded border border-seam px-2.5 py-1.5 text-xs text-ink-faint">
              Für diese Antwort hat das Modell keine Gedanken übermittelt. Zu sehen sind nur protokollierte Schritte.
            </p>
          )}
          {process.length === 0 && <p className="text-xs text-ink-faint">Noch keine Schritte.</p>}
          <ol className="flex flex-col gap-2">
            {process.map((p) => (
              <ProcessEntry key={p.id} p={p} />
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}

function ProcessEntry({ p }: { p: TimelineEvent<ProcessData> }) {
  const d = p.data;
  const Icon = STEP_ICON[d.step] ?? Wrench;
  const failed = d.status === "failed";
  return (
    <li className="flex gap-2.5 text-xs">
      <Icon className={`mt-0.5 size-3.5 shrink-0 ${d.source === "model" ? "text-arc" : failed ? "text-alarm" : "text-ink-faint"}`} />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
          <span className={failed ? "text-alarm" : "text-ink"}>{d.title}</span>
          <span className={`source-tag source-tag--${d.source}`}>{SOURCE_LABEL[d.source] ?? d.source}</span>
          {d.status === "running" && <Loader2 className="spin-slow size-3 self-center text-arc" />}
          {d.status === "waiting" && <span className="text-ember">wartet</span>}
        </div>
        {d.text && (
          <p className={`selectable whitespace-pre-wrap ${d.step === "reasoning" ? "max-h-60 overflow-y-auto leading-relaxed text-ink-dim italic" : "text-ink-dim"}`}>
            {d.text}
          </p>
        )}
        {d.items && d.items.length > 0 && (
          <ul className="flex flex-col gap-0.5 pl-3 text-ink-dim">
            {d.items.map((item, i) => (
              <li key={i} className="list-disc">
                {item}
              </li>
            ))}
          </ul>
        )}
        {d.result && <p className="selectable truncate text-ink-faint" title={d.result}>Zwischenergebnis: {d.result}</p>}
        {d.redacted && <p className="text-ink-faint">Details auf diesem Gerät ausgeblendet.</p>}
      </div>
    </li>
  );
}
