// Agent tasks (kind "agent_task"): what the progress card shows. Pure helpers, shared by the tests.
import type { AgentTaskView } from "./types";

export const PHASE_LABELS: Record<string, string> = {
  planning: "Plant",
  executing: "Arbeitet",
  verifying: "Prüft das Ergebnis",
  waiting: "Wartet",
  done: "Fertig",
};

export function planProgress(v: Pick<AgentTaskView, "plan_done" | "plan_total">): number {
  return v.plan_total > 0 ? Math.round((v.plan_done / v.plan_total) * 100) : 0;
}

/** One line for lists: "Prüft das Ergebnis · 3/5 Schritte · 12 Werkzeugaufrufe". */
export function agentSummary(v: AgentTaskView): string {
  const parts = [PHASE_LABELS[v.phase] ?? v.phase];
  if (v.plan_total > 0) parts.push(`${v.plan_done}/${v.plan_total} Schritte`);
  if (v.tool_calls > 0) parts.push(`${v.tool_calls} Werkzeugaufrufe`);
  return parts.join(" · ");
}

export const PLAN_MARK: Record<string, string> = { done: "✓", skipped: "–", failed: "!", open: "○" };
