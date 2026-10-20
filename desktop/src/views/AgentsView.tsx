import { TerminalSquare } from "lucide-react";
import { useState } from "react";
import { AgentControls, JobMonitorPanel } from "../components/AgentMonitor";
import { Dot, EmptyState, ErrorState, KeyValue, Section, Skeleton, StatusText, Tag } from "../components/ui";
import { useQuery } from "../hooks/useQuery";
import { formatClock, formatMs, formatRelative, statusLabel, statusTone } from "../lib/format";
import type { Task } from "../lib/types";
import { api, useApp } from "../store/app";

const TASK_ACTIVE = ["queued", "running", "paused", "waiting_approval", "waiting_input", "waiting_external"];

export function AgentsView() {
  const live = useApp((s) => s.liveTools);
  const status = useApp((s) => s.status);
  const jobs = useQuery(() => api.codingJobs(), "coding-jobs", { topics: ["coding"] });
  const tasks = useQuery(() => api.tasks(), "agent-tasks", { topics: ["task"] });
  const [selected, setSelected] = useState<string | null>(null);
  const job = jobs.data?.find((j) => j.id === selected) ?? jobs.data?.[0];
  const activeTasks = (tasks.data ?? []).filter((t) => TASK_ACTIVE.includes(t.status));

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto grid max-w-[80rem] gap-6 px-8 py-6 lg:grid-cols-[1fr_1.5fr]">
        <div className="flex flex-col gap-6">
          <Section title="Agent" meta="Claude Code · Subscription">
            <div className="p-4">
              <KeyValue rows={[
                ["Status", <span key="h" className="flex items-center gap-2"><Dot tone={status?.components.agent === "ok" ? "ok" : "bad"} />{status?.components.agent ?? "–"}</span>],
                ["Aktive Gespräche", String(status?.agent?.active_sessions ?? 0)],
                ["Bereitschaftsprozess", status?.agent?.standby_ready ? "bereit" : "startet"],
                ["Modellwahl", status?.agent?.routing ? (status.agent.routing.default === "sonnet" && status.agent.routing.auto_downgrade ? "Auto (Sonnet, einfach → Haiku)" : status.agent.routing.default) : "–"],
                ["Letzter Fehler", status?.agent?.last_error ?? "–"],
                ["Offene Aufgaben", String(status?.open_tasks ?? 0)],
              ]} />
            </div>
          </Section>

          <Section title="Aufgaben" meta={activeTasks.length ? `${activeTasks.length} aktiv` : "Task-Engine"}>
            {tasks.loading && <div className="p-4"><Skeleton rows={3} /></div>}
            {tasks.error && <div className="p-4"><ErrorState message={tasks.error} onRetry={tasks.refetch} /></div>}
            {tasks.data && activeTasks.length === 0 && (
              <div className="px-4"><EmptyState title="Keine aktiven Aufgaben." /></div>
            )}
            <ul className="divide-y divide-seam">
              {activeTasks.map((t) => (
                <TaskRow key={t.id} task={t} onChanged={tasks.refetch} />
              ))}
            </ul>
          </Section>

          <Section title="Werkzeugaufrufe" meta="live, Gespräche">
            {live.length === 0 ? (
              <div className="px-4"><EmptyState title="Noch keine Werkzeugaufrufe in dieser Sitzung." /></div>
            ) : (
              <ul className="max-h-[22rem] divide-y divide-seam overflow-y-auto">
                {[...live].reverse().map((t) => (
                  <li key={t.key} className="flex items-center gap-3 px-4 py-2 text-sm">
                    <Dot tone={t.status === "running" ? "info" : t.status === "ok" ? "ok" : "bad"} pulse={t.status === "running"} />
                    <span className="type-data flex-1 truncate text-ink">{t.tool}</span>
                    {t.risk && t.risk !== "read" && <Tag tone="warn">{t.risk}</Tag>}
                    <span className="text-xs text-ink-faint">{t.durationMs != null ? formatMs(t.durationMs) : formatClock(t.startedAt)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </div>

        <Section title="Coding-Jobs" meta="Claude Code, isolierte Worktrees">
          {jobs.loading && <div className="p-4"><Skeleton rows={4} /></div>}
          {jobs.error && <div className="p-4"><ErrorState message={jobs.error} onRetry={jobs.refetch} /></div>}
          {jobs.data && jobs.data.length === 0 && (
            <div className="px-4"><EmptyState icon={<TerminalSquare className="size-6" />} title="Noch keine Coding-Jobs.">
              Bitte J.A.R.V.I.S. um eine Entwicklungsaufgabe – sie läuft dann hier in einem eigenen Git-Worktree.
            </EmptyState></div>
          )}
          {jobs.data && jobs.data.length > 0 && (
            <div className="grid min-h-[28rem] grid-cols-[13rem_1fr] divide-x divide-seam">
              <ul className="divide-y divide-seam overflow-y-auto">
                {jobs.data.map((j) => (
                  <li key={j.id}>
                    <button className={`w-full px-3 py-2.5 text-left text-sm hover:bg-plate-hi ${job?.id === j.id ? "bg-plate-hi" : ""}`} onClick={() => setSelected(j.id)}>
                      <div className="flex items-center gap-2"><Dot tone={statusTone(j.status)} pulse={j.status === "running"} /><span className="truncate text-ink">{j.title}</span></div>
                      <div className="mt-0.5 pl-4 text-xs text-ink-faint">{statusLabel(j.status)} · {formatRelative(j.created_at)}</div>
                    </button>
                  </li>
                ))}
              </ul>
              {job && (
                <div className="min-w-0">
                  <div className="border-b border-seam px-4 py-2.5">
                    <p className="truncate text-[0.875rem] font-semibold text-ink">{job.title}</p>
                    <p className="type-data truncate text-ink-faint">{job.branch ?? "–"}</p>
                  </div>
                  <JobMonitorPanel key={job.id} jobId={job.id} />
                  <RawLog jobId={job.id} />
                </div>
              )}
            </div>
          )}
        </Section>
      </div>
    </div>
  );
}

function TaskRow({ task, onChanged }: { task: Task; onChanged: () => Promise<void> }) {
  const done = task.steps.filter((s) => s.status === "completed" || s.status === "skipped").length;
  const current = task.steps.find((s) => !["completed", "skipped"].includes(s.status));
  const controllable = task.status === "queued" || task.status === "running" || task.status === "paused";
  const after = (p: Promise<unknown>) => p.then(onChanged);
  return (
    <li className="flex flex-col gap-2 px-4 py-3">
      <div className="flex items-center gap-2">
        <StatusText tone={statusTone(task.status)} pulse={task.status === "running"}>{statusLabel(task.status)}</StatusText>
        <span className="min-w-0 flex-1 truncate text-sm text-ink">{task.title}</span>
        <span className="type-data shrink-0 text-ink-faint">{done}/{task.steps.length}</span>
      </div>
      {current && <p className="type-data truncate text-ink-dim">Schritt {current.idx + 1}: {current.name}</p>}
      {task.question && <p className="text-xs text-ember">{task.question}</p>}
      <AgentControls
        compact
        subject="Aufgabe"
        state={{
          canPause: task.status === "queued" || task.status === "running",
          canResume: task.status === "paused",
          canCancel: !["completed", "failed", "cancelled"].includes(task.status),
          canMessage: controllable || task.status === "waiting_external",
        }}
        onPause={() => after(api.taskPause(task.id))}
        onResume={() => after(api.taskResume(task.id))}
        onCancel={() => after(api.taskCancel(task.id))}
        onMessage={(text, immediate) => api.taskMessage(task.id, text, immediate)}
      />
    </li>
  );
}

/** Original raw stream (text, tool lines, result) – kept for debugging next to the structured monitor. */
function RawLog({ jobId }: { jobId: string }) {
  const [open, setOpen] = useState(false);
  const codingLive = useApp((s) => s.codingLive[jobId]);
  const log = useQuery(() => api.codingLog(jobId), `coding-log-${jobId}`, { topics: ["coding"], enabled: open });
  const lines = [...(log.data ?? []), ...(codingLive ?? [])].filter((l) => l.type !== "tool_result" && l.type !== "control");
  return (
    <details className="border-t border-seam" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary className="cursor-default px-4 py-2 text-xs text-ink-faint hover:text-ink-dim">Rohprotokoll</summary>
      <div className="selectable max-h-72 overflow-y-auto px-4 pb-3 font-mono text-[0.72rem] leading-relaxed">
        {lines.length === 0 && <span className="text-ink-faint">Noch keine Ausgabe.</span>}
        {lines.map((l, i) => (
          <div key={i} className="break-words whitespace-pre-wrap">
            {l.tool ? <span className="text-arc">▸ {l.tool} <span className="text-ink-dim">{l.detail}</span></span>
              : l.type === "result" ? <span className={l.is_error ? "text-alarm" : "text-ember"}>■ {l.text}</span>
              : l.type === "user_message" ? <span className="text-ember">» {l.text}</span>
              : <span className="text-ink">{l.text}</span>}
          </div>
        ))}
      </div>
    </details>
  );
}
