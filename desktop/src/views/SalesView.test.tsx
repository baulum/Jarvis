// @vitest-environment jsdom
// Kundensuche: smoke test that status and the campaign list load without errors, plus the lead file and the redraft
// action. The API is a mock – nothing reaches the Lead-Tool, nothing is approved or sent.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SalesAnalytics, SalesCampaign, SalesLeadDetail, SalesStatus } from "../lib/sales";

const status: SalesStatus = {
  configured: true, enabled: true, connected: true, error: null, web_url: "http://127.0.0.1:3100",
  capabilities: { read: true, redraft: true, create_campaign: true, outbox_list: false },
  overview: {
    outreach_mode: "draft_only", review: 1, outbox_preview: 2, inbox: 0, campaigns_by_status: { processing: 1 },
    workers: [{ name: "worker", online: true, last_seen: "2026-11-13T10:00:00Z" }],
  },
};

const campaign: SalesCampaign = {
  id: "c1", name: "Elektriker München", status: "processing", status_label: "verarbeitet", active: true, region: "München",
  industry: "Elektriker", target_count: 50, sources: ["osm"], last_error: null, created_at: "2026-11-12T09:00:00Z",
  leads_by_status: { DISCOVERED: 3, AWAITING_APPROVAL: 1 }, lead_count: 4, in_review: 1,
};

const analytics: SalesAnalytics = {
  basis: "Aktueller Stand aus 1 Kampagnen.", lead_total: 4,
  by_status: [], stages: [{ key: "found", label: "Gefunden", count: 3 }, { key: "review", label: "Freigabe", count: 1 }],
  off_track: { out: 0, error: 0 }, campaigns: { total: 1, by_status: [{ status: "processing", label: "verarbeitet", count: 1 }] },
  per_campaign: [{ id: "c1", name: "Elektriker München", status: "processing", status_label: "verarbeitet", leads: 4, in_review: 1, contacted: 0, errors: 0 }],
  tasks: { review: 1, outbox_preview: 2, inbox: 0 },
};

const lead: SalesLeadDetail = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", campaign_name: "Elektriker München", status: "AWAITING_APPROVAL",
  status_label: "Wartet auf Freigabe", stage: "review", last_error: null,
  company: { name: "Elektro Huber GmbH", industry: "Elektriker", website: "https://huber.example", phone: null, email: null, address: "Hauptstraße 5, 80331 München" },
  score: { value: 78, priority: "A", version: "v3" },
  draft: { subject: "Ihre Website", body: "Guten Tag Herr Huber", channel: "email", iteration: 1, evidence_ids: ["e1"] },
  evidence: [{ id: "e1", kind: "fact", key: "website.https", claim: "Kein HTTPS.", source_url: null, confidence: 0.9 }],
  history: [], can_redraft: true,
};

const { api, openExternal } = vi.hoisted(() => ({
  api: {
    sales: {
      status: vi.fn(), statuses: vi.fn(), campaigns: vi.fn(), campaign: vi.fn(), leads: vi.fn(), lead: vi.fn(),
      review: vi.fn(), outbox: vi.fn(), analytics: vi.fn(), industries: vi.fn(), redraft: vi.fn(), createCampaign: vi.fn(),
    },
  },
  openExternal: vi.fn(),
}));
vi.mock("../store/app", async () => {
  const { create } = await import("zustand");
  const useApp = create(() => ({ pushToast: vi.fn(), token: "t", remote: false, versions: {} as Record<string, number> }));
  return { useApp, api };
});
vi.mock("../lib/native", () => ({ openExternal }));

import { SalesView } from "./SalesView";

function mockAll() {
  api.sales.status.mockResolvedValue(status);
  api.sales.analytics.mockResolvedValue(analytics);
  api.sales.campaigns.mockResolvedValue([campaign]);
  api.sales.statuses.mockResolvedValue([
    { id: "DISCOVERED", label: "Gefunden", stage: "found" },
    { id: "AWAITING_APPROVAL", label: "Wartet auf Freigabe", stage: "review" },
  ]);
  api.sales.review.mockResolvedValue([{ id: lead.id, name: "Elektro Huber GmbH", campaign_name: "Elektriker München", score: 78, since: "2026-11-12T10:00:00Z" }]);
  api.sales.lead.mockResolvedValue(lead);
  api.sales.campaign.mockResolvedValue({ ...campaign, channel: "email", top_n: 10, budget_usd: null, cost_usd: 0.4, source_runs: [] });
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SalesView", () => {
  it("smoke: status and campaign list load without errors", async () => {
    mockAll();
    render(<SalesView />);
    expect(await screen.findByText("Stand je Station")).toBeTruthy();
    expect(screen.getByText("Worker läuft")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: /Kampagnen/ }));
    expect(await screen.findByText("Elektriker München")).toBeTruthy();
    expect(screen.getByText("4 Leads")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(api.sales.status).toHaveBeenCalledTimes(1);
    expect(api.sales.campaigns).toHaveBeenCalled();
  });

  it("explains why the area is empty when the Lead-Tool is not reachable", async () => {
    api.sales.status.mockResolvedValue({ ...status, connected: false, overview: null, error: "Das Lead-Tool ist nicht erreichbar: der MCP-Server läuft nicht." });
    render(<SalesView />);
    expect(await screen.findByText("Das Lead-Tool ist gerade nicht erreichbar.")).toBeTruthy();
    expect(screen.getByText(/der MCP-Server läuft nicht/)).toBeTruthy();
    expect(api.sales.campaigns).not.toHaveBeenCalled();
  });

  it("review queue: shows the draft, approving only links to the Lead-Tool, redraft goes to the Core", async () => {
    mockAll();
    api.sales.redraft.mockResolvedValue({ status: "approval_required", tool: "ext_pje_sales_ai_request_redraft", summary: "Neu formulieren" });
    render(<SalesView />);
    fireEvent.click(await screen.findByRole("tab", { name: /Freigabe/ }));
    const file = await screen.findByRole("article", { name: /Elektro Huber GmbH/ });
    expect(within(file).getByText("Guten Tag Herr Huber")).toBeTruthy();
    expect(within(file).getByText("im Entwurf")).toBeTruthy();

    fireEvent.click(within(file).getByRole("button", { name: "Im Lead-Tool freigeben" }));
    expect(openExternal).toHaveBeenCalledWith(`http://127.0.0.1:3100/review?lead=${lead.id}`);
    expect(api.sales.redraft).not.toHaveBeenCalled();

    fireEvent.click(within(file).getByRole("button", { name: "Neu formulieren" }));
    const dialog = await screen.findByRole("dialog", { name: "Entwurf neu formulieren" });
    const send = within(dialog).getByRole("button", { name: "Neu formulieren lassen" }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText("Anweisung"), { target: { value: "Bitte kürzer" } });
    fireEvent.click(send);
    await waitFor(() => expect(api.sales.redraft).toHaveBeenCalledTimes(1));
    const [id, note, requestId] = api.sales.redraft.mock.calls[0]!;
    expect([id, note]).toEqual([lead.id, "Bitte kürzer"]);
    expect(requestId).toMatch(/^req-/);
  });

  it("leads: filters by status and campaign", async () => {
    mockAll();
    api.sales.leads.mockResolvedValue({ total: 0, offset: 0, count: 0, more: 0, leads: [] });
    render(<SalesView />);
    fireEvent.click(await screen.findByRole("tab", { name: /Leads/ }));
    await screen.findByText("Keine Leads für diesen Filter.");
    await screen.findByRole("option", { name: "Wartet auf Freigabe" });
    fireEvent.change(screen.getByLabelText("Nach Status filtern"), { target: { value: "AWAITING_APPROVAL" } });
    await screen.findByRole("option", { name: "Elektriker München" });
    fireEvent.change(screen.getByLabelText("Nach Kampagne filtern"), { target: { value: "c1" } });
    await waitFor(() =>
      expect(api.sales.leads).toHaveBeenLastCalledWith({ campaign_id: "c1", status: "AWAITING_APPROVAL", limit: 50 }),
    );
  });
});
