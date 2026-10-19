import { describe, expect, it } from "vitest";
import { currentChoice, resetLabel } from "./model";

describe("model choice", () => {
  it("shows Auto for the untouched default and the fixed model otherwise", () => {
    expect(currentChoice({ default: "sonnet", auto_downgrade: true, session_override: null })).toBe("auto");
    expect(currentChoice({ default: "sonnet", auto_downgrade: false, session_override: null })).toBe("sonnet");
    expect(currentChoice({ default: "opus", auto_downgrade: false, session_override: null })).toBe("opus");
    expect(currentChoice({ default: "sonnet", auto_downgrade: true, session_override: "haiku" })).toBe("haiku");
    expect(currentChoice(undefined)).toBe("auto");
  });

  it("formats the quota reset", () => {
    expect(resetLabel(3700)).toBe("Reset in 1 h 1 min");
    expect(resetLabel(600)).toBe("Reset in 10 min");
    expect(resetLabel(null)).toBe("");
  });
});
