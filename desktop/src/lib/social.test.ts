import { describe, expect, it } from "vitest";
import { captionOverLimit, captionText, publicationTone, safeFileName, validTime, videoStatusLabel } from "./social";

describe("social helpers", () => {
  it("builds the pasteable caption with hashtags", () => {
    expect(captionText("instagram", { text: " Hallo ", hashtags: ["#Webdesign", "#Wolnzach"] })).toBe("Hallo\n\n#Webdesign #Wolnzach");
    expect(captionText("youtube", { title: "T", description: "Beschreibung", hashtags: [] })).toBe("Beschreibung");
    expect(captionText("tiktok", undefined)).toBe("");
  });

  it("flags captions over the platform limit", () => {
    expect(captionOverLimit("tiktok", { text: "x".repeat(2201), hashtags: [] })).toBe(true);
    expect(captionOverLimit("youtube", { title: "T".repeat(101), description: "", hashtags: [] })).toBe(true);
    expect(captionOverLimit("instagram", { text: "kurz", hashtags: ["#a"] })).toBe(false);
  });

  it("labels states", () => {
    expect(videoStatusLabel({ status: "producing", stage: "qa" })).toBe("In Arbeit · Qualitätskontrolle");
    expect(videoStatusLabel({ status: "failed", stage: "render" })).toBe("Fehlgeschlagen (Rendern)");
    expect(publicationTone("waiting_approval")).toBe("warn");
  });

  it("validates times and file names", () => {
    expect(validTime("08:00")).toBe(true);
    expect(validTime("24:00")).toBe(false);
    expect(safeFileName({ file_name: "../x.mp4", title: "Captcha für Kunden!" })).toBe("Captcha-f-r-Kunden.mp4");
    expect(safeFileName({ file_name: "2026-10-28_a_b.mp4", title: "" })).toBe("2026-10-28_a_b.mp4");
  });
});
