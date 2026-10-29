import { describe, expect, it } from "vitest";
import { accountState, buildPostBody, overLimit, parseHashtags, postStatusLabel, postText, postTone, publishAtValue, type UptikAccount } from "./uptik";

const account = (over: Partial<UptikAccount> = {}): UptikAccount => ({
  id: "a1", platform: "tiktok", platform_label: "TikTok", label: "PJE", handle: "", runner: "local", device: "",
  status: "connected", autopost: false, circuit_state: "ok", circuit_code: null, circuit_reason: null, circuit_at: null,
  last_error: null, connected_at: null, last_used_at: null, session_saved: true, session_saved_at: null, login_running: false,
  ...over,
});

describe("uptik helpers", () => {
  it("normalizes hashtags and builds the caption text", () => {
    expect(parseHashtags("#Web, kmu  #web ##Wolnzach")).toEqual(["#Web", "#kmu", "#Wolnzach"]);
    expect(postText(" Hallo ", ["#a", "#b"])).toBe("Hallo\n\n#a #b");
    expect(postText("Nur Text", [])).toBe("Nur Text");
  });

  it("flags platforms over their caption limit", () => {
    expect(overLimit("x".repeat(2195), ["#abcdef"], ["tiktok", "youtube_shorts"])).toEqual(["tiktok"]);
    expect(overLimit("kurz", [], ["instagram_reels"])).toEqual([]);
  });

  it("turns the date field into Berlin wall-clock time or publish-now", () => {
    expect(publishAtValue("2026-11-02T18:30")).toBe("2026-11-02 18:30");
    expect(publishAtValue("")).toBeUndefined();
    const body = buildPostBody({ video: "inbox:1", caption: " Hi ", hashtags: "web", title: "", accounts: ["a1"], when: "" });
    expect(body).toEqual({ video: "inbox:1", caption: "Hi", hashtags: ["#web"], title: "", accounts: ["a1"] });
    expect(buildPostBody({ video: "v", caption: "", hashtags: "", title: "", accounts: [], when: "2026-11-02T08:05" }).publish_at).toBe("2026-11-02 08:05");
  });

  it("labels accounts and posts", () => {
    expect(accountState(account({ circuit_state: "tripped", circuit_reason: "Captcha erkannt" }))).toEqual({ label: "Angehalten: Captcha erkannt", tone: "bad" });
    expect(accountState(account({ status: "not_connected" })).tone).toBe("idle");
    expect(accountState(account({ login_running: true })).tone).toBe("info");
    expect(postStatusLabel("needs_review")).toBe("prüfen");
    expect(postTone("blocked")).toBe("bad");
    expect(postTone("published")).toBe("ok");
  });
});
