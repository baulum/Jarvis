// Agent monitor building blocks: live view of one coding job, controls (pause / resume / cancel / message)
// shared by jobs and tasks. Pause and cancel always ask first.
import { Pause, Play, SendHorizontal, Square } from "lucide-react";
import { useState } from "react";
import { useAction, useQuery } from "../hooks/useQuery";
import { useNow } from "../hooks/useNow";
import { formatClock, formatDuration, formatTokens, formatUsd, statusLabel, statusTone } from "../lib/format";
import type { JobMonitor, MonitorStep } from "../lib/types";
import { api } from "../store/app";
import { ConfirmDialog } from "./ConfirmDialog";
import { Button, Dot, ErrorState, Skeleton, Tag } from "./ui";

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

const STEP_TONE = { running: "info", paused: "warn", ok: "ok", error: "bad", aborted: "idle" } as const;

function StepRow({ step }: { step: MonitorStep }) {
  const [open, setOpen] = useState(false);
  const live = step.status === "running";
  return (
    <li className="px-4 py-2 text-sm">
      <button type="button" onClick={() => setOpen(!open)} className="flex w-full items-center gap-3 text-left" aria-expanded={open}>
        <Dot tone={STEP_TONE[step.status]} pulse={live} />
        <span className="type-data shrink-0 text-arc">{step.tool}</span>
        <span className="type-data min-w-0 flex-1 truncate text-ink-dim">{step.detail}</span>
        <span className="type-data shrink-0 text-ink-faint">
          {step.duration_s != null ? formatDuration(step.duration_s) : formatClock(step.started_at)}
        </span>
      </button>
      {step.result && (
        <p className={`type-data mt-1 pl-[1.125rem] break-words text-ink-faint ${open ? "" : "line-clamp-1"} ${step.status === "error" ? "text-alarm" : ""}`}>
          {step.result}
        </p>
      )}
      {open && step.detail && <p className="type-data selectable mt-1 pl-[1.125rem] break-all whitespace-pre-wrap text-ink-dim">{step.detail}</p>}
    </li>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="min-w-0" title={hint}>
      <dt className="text-2xs text-ink-faint">{label}</dt>
      <dd className="type-data truncate text-ink">{value}</dd>
    </div>
  );
}

/** Live monitor of one coding job: current step, tool calls with results, runtime, tokens, cost, controls. */
export function JobMonitorPanel({ jobId }: { jobId: string }) {
  const q = useQuery(() => api.codingMonitor(jobId), `monitor-${jobId}`, { topics: ["coding"], pollMs: 5000 });
  const m = q.data;
  const active = m ? m.status === "running" : false;
  const now = useNow(active);

  if (q.loading) return <div className="p-4"><Skeleton rows={5} /></div>;
  if (q.error && !m) return <div className="p-4"><ErrorState message={q.error} onRetry={q.refetch} /></div>;
  if (!m) return null;

  const elapsed = m.elapsed_s + (active ? Math.max(0, (now - Date.parse(m.snapshot_at)) / 1000) : 0);
  const history = [...m.steps].reverse();
  const u = m.usage;
  const act = (fn: () => Promise<unknown>) => async () => {
    await fn();
    await q.refetch();
  };

  return (
    <div className="flex min-w-0 flex-col">
      <CurrentStep m={m} />
      <dl className="grid grid-cols-2 gap-x-6 gap-y-2 border-b border-seam px-4 py-3 sm:grid-cols-4">
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
      {m.messages.length > 0 && (
        <div className="border-b border-seam px-4 py-2">
          <p className="text-2xs text-ink-faint">Nachrichten an den Agenten</p>
          <ul className="mt-1 flex flex-col gap-1">
            {m.messages.map((x, i) => (
              <li key={i} className="text-[0.8125rem] text-ink-dim">
                <span className="type-data text-ink-faint">{formatClock(x.ts)}</span> {x.text}
                {x.immediate && <span className="ml-1.5 text-2xs text-ember">sofort</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="flex items-center justify-between px-4 pt-3 pb-1">
        <p className="text-2xs text-ink-faint">Schritte (neueste zuerst){m.step_count > m.steps.length ? ` · letzte ${m.steps.length} von ${m.step_count}` : ""}</p>
      </div>
      {history.length === 0 ? (
        <p className="px-4 py-3 text-sm text-ink-faint">Noch keine Werkzeugaufrufe.</p>
      ) : (
        <ul className="max-h-[26rem] divide-y divide-seam overflow-y-auto">
          {history.map((s) => (
            <StepRow key={s.id} step={s} />
          ))}
        </ul>
      )}
      {m.error && <p className="border-t border-seam px-4 py-3 text-sm text-alarm">{m.error}</p>}
    </div>
  );
}

function CurrentStep({ m }: { m: JobMonitor }) {
  const tone = m.current.kind === "paused" ? "warn" : m.current.kind === "done" ? statusTone(m.status) : "info";
  const live = m.current.kind === "tool" || m.current.kind === "thinking";
  return (
    <div className="border-b border-seam px-4 py-3">
      <div className="flex items-center gap-2 text-2xs text-ink-faint">
        <Dot tone={tone} pulse={live} />
        <span>{m.current.kind === "done" ? "Ergebnis" : "Aktueller Schritt"}</span>
        <Tag tone={statusTone(m.status)}>{statusLabel(m.status)}</Tag>
        {m.model && <span className="type-data ml-auto">{m.model}{m.effort ? ` · ${m.effort}` : ""}</span>}
      </div>
      <p className="type-data selectable mt-1.5 break-words text-[0.8125rem] text-ink">{m.current.label}</p>
      {m.running_tools.length > 1 && (
        <p className="mt-1 text-2xs text-ink-faint">{m.running_tools.length} Werkzeugaufrufe laufen parallel</p>
      )}
      {m.last_text && m.current.kind !== "done" && <p className="mt-1.5 line-clamp-2 text-xs text-ink-dim">{m.last_text}</p>}
    </div>
  );
}
