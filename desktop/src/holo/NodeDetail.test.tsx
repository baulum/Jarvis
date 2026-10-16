// @vitest-environment jsdom
// Node detail overlay: real content from the core, related memories, edit and two-step delete.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { memoryItem, memoryEdit, memoryDelete } = vi.hoisted(() => ({
  memoryItem: vi.fn(),
  memoryEdit: vi.fn(),
  memoryDelete: vi.fn(),
}));
vi.mock("../store/app", async () => {
  const { create } = await import("zustand");
  const useApp = create(() => ({ versions: { memory: 0 }, pushToast: vi.fn(), setActiveSession: vi.fn() }));
  return { api: { memoryItem, memoryEdit, memoryDelete }, useApp };
});

import { buildModel } from "./graph/model";
import { NodeDetail } from "./NodeDetail";
import { useBrain } from "./store";

const ITEM = {
  id: "a1",
  title: "Westside Taxi UG",
  kind: "customer",
  content: "Taxiunternehmen aus Ingolstadt. Geschäftsführer ist Albin Shatri.",
  path: "Kunden/Westside Taxi UG.md",
  status: "active",
  sensitivity: "normal",
  entities: [],
  source: { type: "conversation", channel: "voice" },
  backlinks: [],
  superseded_by: null,
  created_at: "2026-10-15T13:00:00+00:00",
  updated_at: "2026-10-16T10:00:00+00:00",
  layer: "customer",
  aliases: ["Westside Taxi UG – Kunde und Geschäftsführung"],
  confidence: 0.9,
  related: [{ id: null, node: "entity:albin shatri", title: "Albin Shatri", kind: "person", relation: "Geschäftsführer", direction: "out" }],
};

beforeEach(() => {
  memoryItem.mockResolvedValue(ITEM);
  memoryEdit.mockResolvedValue({ status: "updated" });
  memoryDelete.mockResolvedValue({ status: "deleted" });
  useBrain.setState({
    model: buildModel({
      version: 1,
      counts: {},
      truncated: {},
      nodes: [
        { id: "memory:a1", kind: "customer", label: "Westside Taxi UG", group: "customer", weight: 0.5, meta: {} },
        { id: "entity:albin shatri", kind: "person", label: "Albin Shatri", group: "entities", weight: 0.2, meta: {} },
      ],
      edges: [{ source: "memory:a1", target: "entity:albin shatri", relation: "Geschäftsführer", weight: 1 }],
    }),
    selected: null,
    overlay: null,
  });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("NodeDetail", () => {
  it("shows the memory with category, source, aliases and related people; focuses a related node", async () => {
    render(<NodeDetail />);
    act(() => useBrain.getState().select("memory:a1"));
    expect(await screen.findByText(/Taxiunternehmen aus Ingolstadt/)).toBeTruthy();
    expect(memoryItem).toHaveBeenCalledWith("a1");
    expect(screen.getByText("Kunde")).toBeTruthy();
    expect(screen.getByText(/aus einem Gespräch \(voice\)/)).toBeTruthy();
    expect(screen.getByText(/Kunde und Geschäftsführung/)).toBeTruthy();
    expect(screen.getByText("90 %")).toBeTruthy();
    fireEvent.click(screen.getByText("Albin Shatri"));
    expect(useBrain.getState().selected).toBe("entity:albin shatri");
    expect(useBrain.getState().focus).toMatchObject({ kind: "node", id: "entity:albin shatri" });
  });

  it("edits in place and deletes only after confirmation", async () => {
    render(<NodeDetail />);
    act(() => useBrain.getState().select("memory:a1"));
    await screen.findByText(/Taxiunternehmen/);
    fireEvent.click(screen.getByText("Bearbeiten"));
    fireEvent.change(screen.getByLabelText("Inhalt"), { target: { value: "Neuer Inhalt." } });
    fireEvent.click(screen.getByText("Speichern"));
    await waitFor(() => expect(memoryEdit).toHaveBeenCalledWith("a1", { title: "Westside Taxi UG", content: "Neuer Inhalt." }));
    fireEvent.click(screen.getByText("Löschen"));
    expect(memoryDelete).not.toHaveBeenCalled(); // first click only asks
    expect(screen.getByText("Endgültig löschen?")).toBeTruthy();
    fireEvent.click(screen.getAllByText("Löschen").at(-1)!);
    await waitFor(() => expect(memoryDelete).toHaveBeenCalledWith("a1"));
    await waitFor(() => expect(useBrain.getState().selected).toBeNull());
  });

  it("shows an error when the memory cannot be loaded", async () => {
    memoryItem.mockRejectedValueOnce(new Error("404 Erinnerung nicht gefunden"));
    render(<NodeDetail />);
    act(() => useBrain.getState().select("memory:a1"));
    expect(await screen.findByText(/404 Erinnerung nicht gefunden/)).toBeTruthy();
  });
});
