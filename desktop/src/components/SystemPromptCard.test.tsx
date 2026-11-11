// @vitest-environment jsdom
// Einstellungen → System-Prompt: edit, save, reset to the default; the fixed core is shown read-only.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SystemPromptView } from "../lib/types";

const view = (o: Partial<SystemPromptView> = {}): SystemPromptView => ({
  persona: "jarvis",
  persona_label: "J.A.R.V.I.S.",
  text: "# Jarvis\nDu bist Jarvis.",
  default: "# Jarvis\nDu bist Jarvis.",
  customized: false,
  updated_at: null,
  max_chars: 20000,
  core: "# Kernanweisungen (fest)\nFREIGABE ERFORDERLICH …",
  rules: "### Globale Regeln\n- Anrede: Sir",
  rules_warnings: [],
  ...o,
});

const { api } = vi.hoisted(() => ({
  api: { systemPrompt: vi.fn(), setSystemPrompt: vi.fn(), resetSystemPrompt: vi.fn() },
}));
vi.mock("../store/app", async () => {
  const { create } = await import("zustand");
  const useApp = create(() => ({ pushToast: vi.fn(), token: "t", versions: { system: 0 } as Record<string, number> }));
  return { useApp, api };
});

import { SystemPromptCard } from "./SystemPromptCard";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SystemPromptCard", () => {
  it("shows the editable text of the active persona and saves an edit", async () => {
    api.systemPrompt.mockResolvedValue(view());
    api.setSystemPrompt.mockResolvedValue(view({ text: "Neuer Ton", customized: true, updated_at: "2026-02-01T10:00:00+00:00" }));
    render(<SystemPromptCard />);
    const box = (await screen.findByLabelText("System-Prompt")) as HTMLTextAreaElement;
    expect(box.value).toContain("Du bist Jarvis.");
    const save = screen.getByRole("button", { name: "Speichern" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true); // nothing changed yet
    fireEvent.change(box, { target: { value: "Neuer Ton" } });
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await waitFor(() => expect(api.setSystemPrompt).toHaveBeenCalledWith("Neuer Ton"));
    await waitFor(() => expect(screen.getByText(/angepasst/)).toBeTruthy());
  });

  it("restores the default", async () => {
    api.systemPrompt.mockResolvedValue(view({ text: "Eigener Text", customized: true }));
    api.resetSystemPrompt.mockResolvedValue(view());
    render(<SystemPromptCard />);
    await screen.findByLabelText("System-Prompt");
    fireEvent.click(screen.getByRole("button", { name: /Standard wiederherstellen/ }));
    await waitFor(() => expect(api.resetSystemPrompt).toHaveBeenCalled());
    await waitFor(() => expect((screen.getByLabelText("System-Prompt") as HTMLTextAreaElement).value).toContain("Du bist Jarvis."));
  });

  it("does not allow saving an empty or oversized prompt", async () => {
    api.systemPrompt.mockResolvedValue(view({ max_chars: 20 }));
    render(<SystemPromptCard />);
    const box = await screen.findByLabelText("System-Prompt");
    const save = screen.getByRole("button", { name: "Speichern" }) as HTMLButtonElement;
    fireEvent.change(box, { target: { value: "   " } });
    expect(save.disabled).toBe(true);
    fireEvent.change(box, { target: { value: "x".repeat(21) } });
    expect(save.disabled).toBe(true);
  });

  it("shows the fixed core and the loaded rules read-only", async () => {
    api.systemPrompt.mockResolvedValue(view());
    render(<SystemPromptCard />);
    await screen.findByLabelText("System-Prompt");
    expect(screen.queryByLabelText("Kernanweisungen")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Feste Anteile anzeigen/ }));
    expect(screen.getByLabelText("Kernanweisungen").textContent).toContain("FREIGABE ERFORDERLICH");
    expect(screen.getByLabelText("Geladene Verhaltensregeln").textContent).toContain("Anrede: Sir");
    expect(screen.getByLabelText("Kernanweisungen").tagName).toBe("PRE"); // not an input: cannot be edited
  });
});
