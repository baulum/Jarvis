// Progress of an agent task: goal, plan checklist, current activity, what the completion check still misses.
// The same data (Task.agent) the phone shows – the server keeps working whether or not this view is open.
import { PHASE_LABELS, PLAN_MARK, agentSummary, planProgress } from "../lib/agentTask";
import type { AgentTaskView } from "../lib/types";

export function AgentProgress({ view }: { view: AgentTaskView }) {
  return (
    <section aria-label="Fortschritt der Agent-Aufgabe" className="flex flex-col gap-3">
      <h3 className="type-title text-sm text-ink-dim">Ziel und Plan</h3>
      <p className="selectable whitespace-pre-wrap text-sm text-ink">{view.goal}</p>
      <div className="flex items-center gap-3 text-xs text-ink-dim">
        <span>{agentSummary(view)}</span>
        <div
          role="progressbar"
          aria-valuenow={planProgress(view)}
          aria-valuemin={0}
          aria-valuemax={100}
          className="h-1 flex-1 overflow-hidden rounded bg-seam"
        >
          <div className="h-full bg-arc-dim" style={{ width: `${planProgress(view)}%` }} />
        </div>
      </div>
      {view.activity && <p className="text-xs text-arc">{view.activity}</p>}
      <ol className="flex flex-col gap-1">
        {view.plan.map((p, i) => (
          <li key={i} className={`flex gap-2 text-sm ${p.status === "done" ? "text-ink-dim" : "text-ink"}`}>
            <span aria-label={p.status} className="w-4 shrink-0 text-center">{PLAN_MARK[p.status] ?? "○"}</span>
            <span>{p.title}{p.result ? <span className="text-ink-faint"> – {p.result}</span> : null}</span>
          </li>
        ))}
      </ol>
      {view.missing.length > 0 && (
        <p className="text-xs text-ember">Laut Prüfung fehlt noch: {view.missing.join("; ")}</p>
      )}
      {view.blocked && <p className="text-xs text-alarm">Blockiert: {view.blocked}</p>}
      {view.verification.passed && (
        <p className="text-xs text-arc">Abschlussprüfung bestanden{view.verification.evidence ? `: ${view.verification.evidence}` : ""}</p>
      )}
      <p className="sr-only">{PHASE_LABELS[view.phase] ?? view.phase}</p>
    </section>
  );
}
