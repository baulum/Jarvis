import { describe, expect, it } from "vitest";
import { actionMessage, campaignTone, formatScore, leadToolLink, newRequestId, orderedCounts, stageTone } from "./sales";

describe("leadToolLink", () => {
  it("links into the Lead-Tool on the Core's own computer", () => {
    expect(leadToolLink("http://127.0.0.1:3100/", false, "/review?lead=x")).toBe("http://127.0.0.1:3100/review?lead=x");
    expect(leadToolLink("http://127.0.0.1:3100", false)).toBe("http://127.0.0.1:3100");
    expect(leadToolLink("http://127.0.0.1:3100", false, "outbox")).toBe("http://127.0.0.1:3100/outbox");
  });
  it("hides a loopback address on a remote client, keeps a reachable one", () => {
    expect(leadToolLink("http://127.0.0.1:3100", true, "/outbox")).toBeNull();
    expect(leadToolLink("http://localhost:3100", true)).toBeNull();
    expect(leadToolLink("https://leads.pje.example", true, "/outbox")).toBe("https://leads.pje.example/outbox");
    expect(leadToolLink(null, false)).toBeNull();
    expect(leadToolLink("kein url", true)).toBeNull();
  });
});

describe("actionMessage", () => {
  it("says when an approval is pending instead of claiming success", () => {
    const m = actionMessage({ status: "approval_required", tool: "ext_pje_sales_ai_request_redraft", summary: "Neu formulieren" }, "Erledigt");
    expect(m.tone).toBe("warn");
    expect(m.title).toBe("Freigabe nötig");
    expect(m.body).toContain("Neu formulieren");
    expect(actionMessage({ status: "ok", tool: "t" }, "Erledigt")).toEqual({ tone: "ok", title: "Erledigt" });
    expect(actionMessage({ status: "duplicate", tool: "t" }, "Erledigt").title).toBe("Schon erledigt");
  });
});

describe("helpers", () => {
  it("tones follow the Lead-Tool's colour meaning", () => {
    expect(stageTone("review")).toBe("warn");
    expect(stageTone("error")).toBe("bad");
    expect(stageTone("contact")).toBe("ok");
    expect(stageTone(null)).toBe("idle");
    expect(campaignTone({ status: "failed", active: false })).toBe("bad");
    expect(campaignTone({ status: "processing", active: true })).toBe("info");
  });
  it("formats scores and orders counts in pipeline order", () => {
    expect(formatScore(null)).toBe("–");
    expect(formatScore(78, "A")).toBe("78 · A");
    const order = [
      { id: "DISCOVERED", label: "Gefunden", stage: "found" as const },
      { id: "AWAITING_APPROVAL", label: "Wartet auf Freigabe", stage: "review" as const },
    ];
    expect(orderedCounts({ NEW_X: 1, AWAITING_APPROVAL: 2, DISCOVERED: 3, DRAFTING: 0 }, order).map((c) => [c.id, c.count])).toEqual([
      ["DISCOVERED", 3],
      ["AWAITING_APPROVAL", 2],
      ["NEW_X", 1],
    ]);
  });
  it("request ids are unique and valid for the Core", () => {
    const a = newRequestId();
    expect(a).toMatch(/^[A-Za-z0-9_-]{8,64}$/);
    expect(newRequestId()).not.toBe(a);
  });
});
