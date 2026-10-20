import { Square, TerminalSquare } from "lucide-react";
import { useState } from "react";
import { Button, Dot, EmptyState, ErrorState, KeyValue, Section, Skeleton, Tag } from "../components/ui";
import { useAction, useQuery } from "../hooks/useQuery";
import { formatClock, formatMs, formatRelative, statusLabel, statusTone } from "../lib/format";
import { api, useApp } from "../store/app";

export function AgentsView() {
  const live = useApp((s) => s.liveTools);
  const status = useApp((s) => s.status);
  const codingLive = useApp((s) => s.codingLive);
  const jobs = useQuery(() => api.codingJobs(), "coding-jobs", { topics: ["coding"] });
  const [selected, setSelected] = useState<string | null>(null);
  const job = jobs.data?.find((j) => j.id === selected) ?? jobs.data?.[0];
  const log = useQuery(() => (job ? api.codingLog(job.id) : Promise.resolve([])), `coding-log-${job?.id ?? "none"}`, {
    topics: ["coding"],
    enabled: !!job,
  });
  const lines = [...(log.data ?? []), ...(job ? codingLive[job.id] ?? [] : [])];
  const act = useAction();

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto grid max-w-[76rem] gap-6 px-8 py-6 lg:grid-cols-[1fr_1.3fr]">
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
          <Section title="Werkzeugaufrufe" meta="live">
            {live.length === 0 ? (
              <div className="px-4"><EmptyState title="Noch keine Werkzeugaufrufe in dieser Sitzung." /></div>
            ) : (
              <ul className="max-h-[28rem] divide-y divide-seam overflow-y-auto">
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
            <div className="grid min-h-[28rem] grid-cols-[14rem_1fr] divide-x divide-seam">
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
                <div className="flex min-w-0 flex-col">
                  <div className="flex items-center gap-2 border-b border-seam px-4 py-2 text-xs text-ink-dim">
                    <span className="type-data truncate">{job.branch ?? "–"}</span>
                    {job.model && <span className="type-data shrink-0 text-ink-faint">{job.model}{job.effort ? ` · ${job.effort}` : ""}</span>}
                    {job.status === "running" && (
                      <Button size="sm" variant="danger" className="ml-auto" icon={<Square className="size-3" />}
                              onClick={() => act.run("cancel", () => api.codingCancel(job.id), "Job abgebrochen")}>Abbrechen</Button>
                    )}
                  </div>
                  <div className="selectable min-h-0 flex-1 overflow-y-auto p-4 font-mono text-[0.72rem] leading-relaxed">
                    {lines.length === 0 && <span className="text-ink-faint">Noch keine Ausgabe.</span>}
                    {lines.map((l, i) => (
                      <div key={i} className="whitespace-pre-wrap break-words">
                        {l.tool ? <span className="text-arc">▸ {l.tool} <span className="text-ink-dim">{l.detail}</span></span>
                          : l.type === "result" ? <span className={l.is_error ? "text-alarm" : "text-ember"}>■ {l.text}</span>
                          : <span className="text-ink">{l.text}</span>}
                      </div>
                    ))}
                    {job.result && typeof job.result === "object" && "tests" in job.result && job.result.tests != null && (
                      <div className="mt-3 border-t border-seam pt-3 text-ink-dim">
                        Tests: {(job.result.tests as { passed?: boolean }).passed ? "bestanden" : "fehlgeschlagen"}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </Section>
      </div>
    </div>
  );
}
