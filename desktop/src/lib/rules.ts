// Behaviour rules (memory kind "rule"): grouping, filtering and the label of a rule's state. Pure functions – the
// panel only renders what these return.
import type { BehaviourRule } from "./types";

export type RuleFilter = "in_force" | "global" | "project" | "inactive" | "all";

export const RULE_FILTERS: { id: RuleFilter; label: string }[] = [
  { id: "in_force", label: "Aktiv" },
  { id: "global", label: "Global" },
  { id: "project", label: "Projekt" },
  { id: "inactive", label: "Inaktiv" },
  { id: "all", label: "Alle" },
];

export function filterRules(rules: BehaviourRule[], filter: RuleFilter, project = ""): BehaviourRule[] {
  const q = project.trim().toLowerCase();
  return rules.filter((r) => {
    if (q && !r.project.toLowerCase().includes(q) && !r.title.toLowerCase().includes(q)) return false;
    switch (filter) {
      case "in_force":
        return r.in_force;
      case "global":
        return r.scope === "global" && !r.superseded;
      case "project":
        return r.scope === "project" && !r.superseded;
      case "inactive":
        return !r.in_force;
      default:
        return true;
    }
  });
}

/** Why a rule does not reach the agent (empty when it does). */
export function ruleState(r: BehaviourRule): string {
  if (r.superseded) return "ersetzt";
  if (!r.active) return "deaktiviert";
  if (r.shadowed_by) return "überholt (neuere Regel zum selben Thema)";
  return "";
}

export function scopeLabel(r: BehaviourRule): string {
  return r.scope === "project" ? `Projekt: ${r.project}` : "Global";
}
