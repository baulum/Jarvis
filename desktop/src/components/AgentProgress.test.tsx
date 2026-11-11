// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { AgentTaskView } from "../lib/types";
import { AgentProgress } from "./AgentProgress";

afterEach(cleanup);

const view = (o: Partial<AgentTaskView> = {}): AgentTaskView => ({
  goal: "Rechnungen prüfen und Übersicht senden",
  phase: "executing",
  activity: "Werkzeug lexware_list_invoices",
  plan: [
    { title: "Rechnungen laden", status: "done", result: "12 gefunden" },
    { title: "Übersicht senden", status: "open", result: "" },
  ],
  plan_done: 1,
  plan_total: 2,
  session_id: "s",
  turns: 2,
  continuations: 1,
  tool_calls: 5,
  retries: 0,
  notes: [],
  missing: ["Übersicht noch nicht gesendet"],
  verification: {},
  waiting: {},
  blocked: "",
  active_s: 30,
  updated: "",
  ...o,
});

describe("AgentProgress", () => {
  it("shows goal, plan checklist, activity and what the check still misses", () => {
    render(<AgentProgress view={view()} />);
    expect(screen.getByText("Rechnungen prüfen und Übersicht senden")).toBeTruthy();
    expect(screen.getByText("Rechnungen laden")).toBeTruthy();
    expect(screen.getByText("Werkzeug lexware_list_invoices")).toBeTruthy();
    expect(screen.getByText(/Laut Prüfung fehlt noch: Übersicht noch nicht gesendet/)).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("50");
  });
  it("shows a blocker and a passed completion check", () => {
    render(<AgentProgress view={view({ blocked: "Zeitbudget erreicht", missing: [], verification: { passed: true, evidence: "PDF liegt vor" } })} />);
    expect(screen.getByText(/Blockiert: Zeitbudget erreicht/)).toBeTruthy();
    expect(screen.getByText(/Abschlussprüfung bestanden: PDF liegt vor/)).toBeTruthy();
  });
});
