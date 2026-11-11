// @vitest-environment jsdom
// Gedächtnis → Verhaltensregeln: display, filter, edit, (de)activate, create.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { BehaviourRule, RulesResponse } from "../lib/types";

const rule = (o: Partial<BehaviourRule>): BehaviourRule => ({
  id: "1", title: "Regel", text: "Text", scope: "global", project: "", active: true, topic: "", path: "Regeln/x.md",
  created_at: "2026-01-01", updated_at: "2026-01-01", supersedes: null, superseded: false, in_force: true,
  shadowed_by: null, migrated_from: null, note: null, ...o,
});
const data = (): RulesResponse => ({
  in_force: 2,
  budget: 6000,
  rules: [
    rule({ id: "a", title: "Anrede", text: "Sprich mich immer mit Sir an." }),
    rule({ id: "b", title: "Lead-Tool Tests", text: "Immer npm test.", scope: "project", project: "Lead-Tool" }),
    rule({ id: "c", title: "Altes Modell", text: "Immer Sonnet.", active: false, in_force: false }),
  ],
});

const { api } = vi.hoisted(() => ({
  api: {
    memoryRules: vi.fn(),
    memoryRuleUpdate: vi.fn(async () => ({ status: "updated" })),
    memoryRuleCreate: vi.fn(async () => ({ status: "created", id: "n" })),
    memoryRulesMigrate: vi.fn(async () => ({ dry_run: false, migrated: 0, candidates: [] })),
  },
}));
vi.mock("../store/app", async () => {
  const { create } = await import("zustand");
  const useApp = create(() => ({ pushToast: vi.fn(), token: "t", versions: { memory: 0 } as Record<string, number> }));
  return { useApp, api };
});

import { RulesPanel } from "./RulesPanel";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("RulesPanel", () => {
  it("lists the rules in force with their scope and hides deactivated ones by default", async () => {
    api.memoryRules.mockResolvedValue(data());
    render(<RulesPanel />);
    expect(await screen.findByText("Anrede")).toBeTruthy();
    expect(screen.getByText("Projekt: Lead-Tool")).toBeTruthy();
    expect(screen.queryByText("Altes Modell")).toBeNull();
    expect(screen.getByText("2 gelten bei jedem Lauf")).toBeTruthy();
  });

  it("filters to inactive rules and shows why they do not apply", async () => {
    api.memoryRules.mockResolvedValue(data());
    render(<RulesPanel />);
    await screen.findByText("Anrede");
    fireEvent.click(screen.getByRole("tab", { name: "Inaktiv" }));
    expect(screen.getByText("Altes Modell")).toBeTruthy();
    expect(screen.getByText("deaktiviert")).toBeTruthy();
    expect(screen.queryByText("Anrede")).toBeNull();
  });

  it("switches a rule off and on", async () => {
    api.memoryRules.mockResolvedValue(data());
    render(<RulesPanel />);
    await screen.findByText("Anrede");
    fireEvent.click(screen.getByRole("switch", { name: /Anrede.*deaktivieren/ }));
    await waitFor(() => expect(api.memoryRuleUpdate).toHaveBeenCalledWith("a", { active: false }));
  });

  it("edits a rule", async () => {
    api.memoryRules.mockResolvedValue(data());
    render(<RulesPanel />);
    await screen.findByText("Anrede");
    fireEvent.click(screen.getByRole("button", { name: "Regel bearbeiten: Anrede" }));
    const form = screen.getByRole("form", { name: "Regel bearbeiten" });
    fireEvent.change(within(form).getByLabelText("Regel (als Anweisung an Jarvis)"), { target: { value: "Sprich mich mit Chef an." } });
    fireEvent.click(within(form).getByRole("button", { name: "Speichern" }));
    await waitFor(() =>
      expect(api.memoryRuleUpdate).toHaveBeenCalledWith("a", { title: "Anrede", text: "Sprich mich mit Chef an.", scope: "global", project: "" }),
    );
  });

  it("creates a project rule only with a project name", async () => {
    api.memoryRules.mockResolvedValue(data());
    render(<RulesPanel />);
    await screen.findByText("Anrede");
    fireEvent.click(screen.getByRole("button", { name: /Neue Regel/ }));
    const form = screen.getByRole("form", { name: "Neue Regel" });
    fireEvent.change(within(form).getByLabelText("Titel"), { target: { value: "Ruff" } });
    fireEvent.change(within(form).getByLabelText("Regel (als Anweisung an Jarvis)"), { target: { value: "Vor dem Commit ruff ausführen." } });
    fireEvent.change(within(form).getByLabelText("Geltungsbereich"), { target: { value: "project" } });
    const save = within(form).getByRole("button", { name: "Speichern" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true); // project missing
    fireEvent.change(within(form).getByLabelText(/Projekt \(Name/), { target: { value: "Jarvis" } });
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() =>
      expect(api.memoryRuleCreate).toHaveBeenCalledWith({ title: "Ruff", text: "Vor dem Commit ruff ausführen.", scope: "project", project: "Jarvis" }),
    );
  });
});
