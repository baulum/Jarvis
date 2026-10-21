import { TerminalSquare } from "lucide-react";
import { useState } from "react";
import { AgentControls, JobMonitorPanel, MonitorTimeline, StatusBadge, StatusIcon } from "../components/AgentMonitor";
import { Button, Dot, EmptyState, ErrorState, KeyValue, Section, Skeleton } from "../components/ui";
import { useFocusedSelection } from "../hooks/useFocusedSelection";
import { useNow } from "../hooks/useNow";
import { useQuery } from "../hooks/useQuery";
import { formatRelative, statusLabel } from "../lib/format";
import { entryStatus, liveEntries, type EntryStatus } from "../lib/monitor";
import type { CodingJob, Task } from "../lib/types";
import { api, useApp } from "../store/app";

const TASK_ACTIVE = ["queued", "running", "paused", "waiting_approval", "waiting_input", "waiting_external"];
const JOB_ACTIVE = ["queued", "running", "paused"];
const DAY_MS = 86_400_000;

export function AgentsView() {
  const live = useApp((s) => s.liveTools);
  const approvals = useApp((s) => s.pendingApprovals);
  const setView = useApp((s) => s.setView);
  const status = useApp((s) => s.status);
  const jobs = useQuery(() => api.codingJobs(), "coding-jobs", { topics: ["coding"] });
  const tasks = useQuery(() => api.tasks(), "agent-tasks", { topics: ["task"] });
  const [selected, setSelected] = useFocusedSelection("job");
  const activeJobs = (jobs.data ?? []).filter((j) => JOB_ACTIVE.includes(j.status));
  const pastJobs = (jobs.data ?? []).filter((j) => !JOB_ACTIVE.includes(j.status));
  // without a click the monitor shows what is running right now, otherwise the newest job
  const job = jobs.data?.find((j) => j.id === selected) ?? activeJobs[0] ?? jobs.data?.[0];
  const activeTasks = (tasks.data ?? []).filter((t) => TASK_ACTIVE.includes(t.status));
  const toolEntries = liveEntries(live, approvals);
  const now = useNow(true, 60_000);
  const recentFailures = (jobs.data ?? []).filter(
    (j) => j.status === "failed" && now - Date.parse(j.finished_at ?? j.created_at) < DAY_MS,
  );
  const latestFailure = recentFailures[0];

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-[80rem] flex-col gap-6 px-8 py-6">
        <Overview
          items={[
            { status: "running", count: activeJobs.length, label: activeJobs.length === 1 ? "Coding-Job aktiv" : "Coding-Jobs aktiv" },
            { status: "running", count: activeTasks.length, label: activeTasks.length === 1 ? "Aufgabe aktiv" : "Aufgaben aktiv" },
            {
              status: "waiting",
              count: approvals.length,
              label: approvals.length === 1 ? "Freigabe offen" : "Freigaben offen",
              action: approvals.length > 0 ? { label: "Prüfen", run: () => setView("approvals") } : undefined,
            },
            {
              status: "error",
              count: recentFailures.length,
              label: "Fehlgeschlagen (24 h)",
              action: latestFailure ? { label: "Ansehen", run: () => setSelected(latestFailure.id) } : undefined,
            },
          ]}
        />

        <Section title="Coding-Jobs" meta="Claude Code, isolierte Worktrees">
          {jobs.loading && <div className="p-4"><Skeleton rows={4} /></div>}
          {jobs.error && <div className="p-4"><ErrorState message={jobs.error} onRetry={jobs.refetch} /></div>}
          {jobs.data && jobs.data.length === 0 && (
            <div className="px-4"><EmptyState icon={<TerminalSquare className="size-6" />} title="Noch keine Coding-Jobs.">
              Bitte J.A.R.V.I.S. um eine Entwicklungsaufgabe – sie läuft dann hier in einem eigenen Git-Worktree.
            </EmptyState></div>
          )}
          {jobs.data && jobs.data.length > 0 && (
            <div className="grid min-h-[28rem] grid-cols-[15rem_minmax(0,1fr)] divide-x divide-seam">
              <nav className="max-h-[48rem] overflow-y-auto" aria-label="Coding-Jobs">
                <JobGroup title="Läuft" jobs={activeJobs} selected={job?.id} onSelect={setSelected} empty="Gerade läuft kein Job." />
                <JobGroup title="Verlauf" jobs={pastJobs} selected={job?.id} onSelect={setSelected} />
              </nav>
              {job && (
                <div className="min-w-0">
                  <div className="border-b border-seam px-4 py-2.5">
                    <p className="truncate text-[0.875rem] font-semibold text-ink" title={job.title}>{job.title}</p>
                    <p className="type-data truncate text-ink-faint">{job.branch ?? "–"}</p>
                  </div>
                  <JobMonitorPanel key={job.id} jobId={job.id} />
                  <RawLog jobId={job.id} />
                </div>
              )}
            </div>
          )}
        </Section>

        <div className="grid gap-6 lg:grid-cols-2">
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

          <Section title="Werkzeugaufrufe" meta="live, Gespräche · seit App-Start">
            <MonitorTimeline
              entries={toolEntries}
              live={toolEntries.some((e) => e.status === "running")}
              maxHeight="max-h-[24rem]"
              empty="Noch keine Werkzeugaufrufe in dieser Sitzung."
              action={(e) =>
                e.kind === "approval" ? (
                  <Button size="sm" variant="primary" onClick={() => setView("approvals")}>
                    Zu den Freigaben
                  </Button>
                ) : null
              }
            />
          </Section>
        </div>

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
      </div>
    </div>
  );
}

/** Lagebild: four numbers that answer "is anything running, waiting for me or broken?". */
function Overview({
  items,
}: {
  items: { status: EntryStatus; count: number; label: string; action?: { label: string; run: () => void } }[];
}) {
  return (
    <div className="plate grid grid-cols-2 divide-seam sm:grid-cols-4 sm:divide-x">
      {items.map((it) => {
        const quiet = it.count === 0;
        return (
          <div key={it.label} className="flex min-w-0 items-center gap-3 px-4 py-3">
            {quiet ? <StatusIcon status="ok" className="size-4 opacity-40" /> : <StatusIcon status={it.status} className="size-4" />}
            <div className="min-w-0 flex-1">
              <p className={`type-data-lg text-[1.25rem] leading-none ${quiet ? "text-ink-faint" : "text-ink"}`}>{it.count}</p>
              <p className="mt-1 truncate text-xs text-ink-dim">{it.label}</p>
            </div>
            {it.action && (
              <Button size="sm" variant="ghost" onClick={it.action.run}>
                {it.action.label}
              </Button>
            )}
          </div>
        );
      })}
    </div>
  );
}

function JobGroup({
  title,
  jobs,
  selected,
  onSelect,
  empty,
}: {
  title: string;
  jobs: CodingJob[];
  selected: string | undefined;
  onSelect: (id: string) => void;
  empty?: string;
}) {
  if (jobs.length === 0 && !empty) return null;
  return (
    <div>
      <p className="sticky top-0 z-[1] border-b border-seam bg-plate px-3 py-1.5 text-2xs text-ink-faint">
        {title} <span className="type-data">{jobs.length}</span>
      </p>
      {jobs.length === 0 && <p className="px-3 py-2.5 text-xs text-ink-faint">{empty}</p>}
      <ul className="divide-y divide-seam">
        {jobs.map((j) => {
          const st = entryStatus(j.status);
          return (
            <li key={j.id}>
              <button
                type="button"
                aria-current={selected === j.id ? "true" : undefined}
                className={`w-full border-l-2 px-3 py-2.5 text-left text-sm hover:bg-plate-hi ${selected === j.id ? "border-arc-dim bg-plate-hi" : "border-transparent"}`}
                onClick={() => onSelect(j.id)}
              >
                <span className="line-clamp-2 text-ink" title={j.title}>{j.title}</span>
                <span className="mt-1 flex items-center gap-2 text-xs text-ink-faint">
                  <StatusBadge status={st} label={statusLabel(j.status)} />
                  <span>· {formatRelative(j.created_at)}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function TaskRow({ task, onChanged }: { task: Task; onChanged: () => Promise<void> }) {
  const done = task.steps.filter((s) => s.status === "completed" || s.status === "skipped").length;
  const current = task.steps.find((s) => !["completed", "skipped"].includes(s.status));
  const controllable = task.status === "queued" || task.status === "running" || task.status === "paused";
  const after = (p: Promise<unknown>) => p.then(onChanged);
  const needsSir = task.status === "waiting_approval" || task.status === "waiting_input";
  return (
    <li className={`flex flex-col gap-2 px-4 py-3 ${needsSir ? "bg-ember-deep/25" : ""}`}>
      <div className="flex items-center gap-2">
        <StatusBadge status={entryStatus(task.status)} label={statusLabel(task.status)} />
        <span className="min-w-0 flex-1 truncate text-sm text-ink" title={task.title}>{task.title}</span>
        <span className="type-data shrink-0 text-ink-faint" title="erledigte Schritte">{done}/{task.steps.length}</span>
      </div>
      {current && <p className="truncate text-xs text-ink-dim">Schritt {current.idx + 1}: {current.name}</p>}
      {task.error && <p className="line-clamp-2 text-xs text-alarm" title={task.error}>{task.error}</p>}
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
      <summary className="cursor-default px-4 py-2 text-xs text-ink-faint hover:text-ink-dim">Rohprotokoll (für die Fehlersuche)</summary>
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
