import { describe, expect, it } from "vitest";
import { agentSummary, planProgress } from "./agentTask";
import { filterRules, ruleState } from "./rules";
import type { AgentTaskView, BehaviourRule } from "./types";

const rule = (o: Partial<BehaviourRule>): BehaviourRule => ({
  id: "1", title: "Regel", text: "x", scope: "global", project: "", active: true, topic: "", path: "Regeln/Regel.md",
  created_at: "", updated_at: "", supersedes: null, superseded: false, in_force: true, shadowed_by: null,
  migrated_from: null, note: null, ...o,
});

describe("filterRules", () => {
  const [a, b, c, d] = [
    rule({ id: "a", title: "Anrede" }),
    rule({ id: "b", title: "Tests", scope: "project", project: "Lead-Tool" }),
    rule({ id: "c", title: "Aus", active: false, in_force: false }),
    rule({ id: "d", title: "Alt", superseded: true, in_force: false }),
  ];
  const rules = [a, b, c, d];
  it("shows what applies by default and the rest on request", () => {
    expect(filterRules(rules, "in_force").map((r) => r.id)).toEqual(["a", "b"]);
    expect(filterRules(rules, "global").map((r) => r.id)).toEqual(["a", "c"]);
    expect(filterRules(rules, "project").map((r) => r.id)).toEqual(["b"]);
    expect(filterRules(rules, "inactive").map((r) => r.id)).toEqual(["c", "d"]);
    expect(filterRules(rules, "all")).toHaveLength(4);
    expect(filterRules(rules, "all", "lead").map((r) => r.id)).toEqual(["b"]);
  });
  it("names the reason a rule does not apply", () => {
    expect(ruleState(a)).toBe("");
    expect(ruleState(c)).toBe("deaktiviert");
    expect(ruleState(d)).toBe("ersetzt");
    expect(ruleState(rule({ shadowed_by: "x" }))).toContain("überholt");
  });
});

describe("agent task summary", () => {
  const v = { phase: "verifying", plan_done: 3, plan_total: 5, tool_calls: 12 } as AgentTaskView;
  it("summarises phase, plan and tool calls", () => {
    expect(agentSummary(v)).toBe("Prüft das Ergebnis · 3/5 Schritte · 12 Werkzeugaufrufe");
    expect(planProgress(v)).toBe(60);
    expect(planProgress({ plan_done: 0, plan_total: 0 })).toBe(0);
  });
});
