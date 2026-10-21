// @vitest-environment jsdom
// Agent monitor timeline: rows with state icons, collapsed details, status/tool filter, grouping, empty states.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../store/app", async () => {
  const { create } = await import("zustand");
  const useApp = create(() => ({ pushToast: () => undefined }));
  return { useApp, api: {} };
});

import type { MonitorEntry } from "../lib/monitor";
import { MonitorTimeline } from "./AgentMonitor";

afterEach(cleanup);

const e = (id: string, tool: string, status: MonitorEntry["status"], params = `${tool} ${id}`): MonitorEntry => ({
  id,
  kind: "tool",
  at: "2026-01-01T12:00:00Z",
  tool,
  summary: params,
  status,
  durationS: 2,
  params,
  result: status === "error" ? "Exit 1: kaputt" : "ok",
});

describe("MonitorTimeline", () => {
  it("shows an empty state without a toolbar", () => {
    render(<MonitorTimeline entries={[]} live={false} empty="Noch keine Werkzeugaufrufe." />);
    expect(screen.getByText("Noch keine Werkzeugaufrufe.")).toBeTruthy();
    expect(screen.queryByRole("tablist")).toBeNull();
  });

  it("labels states with icons and keeps details collapsed until clicked", () => {
    render(<MonitorTimeline entries={[e("1", "Read", "ok"), e("2", "Bash", "error")]} live={false} empty="" />);
    expect(screen.getByRole("img", { name: "Fehler" })).toBeTruthy();
    expect(screen.getByRole("img", { name: "Erledigt" })).toBeTruthy();
    expect(screen.queryByText("Exit 1: kaputt")).toBeNull();
    fireEvent.click(screen.getByText("Bash 2"));
    expect(screen.getByText("Fehlermeldung")).toBeTruthy();
    expect(screen.getByText("Exit 1: kaputt")).toBeTruthy();
  });

  it("filters errors and by tool, and resets an empty filter", () => {
    render(<MonitorTimeline entries={[e("1", "Read", "ok"), e("2", "Bash", "error"), e("3", "Bash", "ok")]} live={false} empty="" />);
    fireEvent.click(screen.getByRole("tab", { name: /Fehler/ }));
    expect(screen.queryByText("Read 1")).toBeNull();
    expect(screen.getByText("Bash 2")).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: /Alle/ }));
    fireEvent.change(screen.getByLabelText("Nach Werkzeug filtern"), { target: { value: "Read" } });
    expect(screen.getByText("Read 1")).toBeTruthy();
    expect(screen.queryByText("Bash 3")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: /Fehler/ }));
    expect(screen.getByText("Keine Fehler – alles im grünen Bereich.")).toBeTruthy();
    fireEvent.click(screen.getByText("Filter zurücksetzen"));
    expect(screen.getByText("Bash 3")).toBeTruthy();
  });

  it("folds repeated calls into a group that opens on click (and by itself on errors)", () => {
    const { rerender } = render(<MonitorTimeline entries={[e("1", "Read", "ok"), e("2", "Read", "ok"), e("3", "Read", "ok")]} live={false} empty="" />);
    expect(screen.getByText("× 3")).toBeTruthy();
    expect(screen.queryByText("Read 2")).toBeNull();
    fireEvent.click(screen.getByText("× 3"));
    expect(screen.getByText("Read 2")).toBeTruthy();

    rerender(<MonitorTimeline entries={[e("a", "Grep", "ok"), e("b", "Grep", "error"), e("c", "Grep", "ok")]} live={false} empty="" />);
    expect(screen.getByText("1 Fehler")).toBeTruthy();
    expect(screen.getByText("Grep b")).toBeTruthy();
  });

  it("lets Sir stop and resume following the newest entry", () => {
    render(<MonitorTimeline entries={[e("1", "Read", "ok")]} live={false} empty="" />);
    const pin = screen.getByRole("button", { name: "Mitlaufen" });
    expect(pin.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(pin);
    expect(screen.getByRole("button", { name: "Angehalten" }).getAttribute("aria-pressed")).toBe("false");
  });
});
