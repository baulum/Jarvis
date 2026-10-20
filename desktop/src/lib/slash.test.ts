import { describe, expect, it } from "vitest";
import { parseSlash, promptFor, slashItems, takesArgs, type SlashCommand } from "./slash";
import { formatTokens, formatUsd } from "./format";

const cmds: SlashCommand[] = [
  { name: "neu", description: "Neues Gespräch", usage: "/neu", action: "new" },
  { name: "modell", description: "Modell wählen", usage: "/modell auto|haiku|sonnet|opus", action: "model", choices: ["auto", "haiku", "sonnet", "opus"] },
  { name: "merke", description: "Etwas merken", usage: "/merke <Text>", action: "prompt", prompt: "Merke dir: {args}" },
  { name: "status", description: "Systemstatus", usage: "/status", action: "prompt", prompt: "Status?" },
];

describe("slash suggestions", () => {
  it("lists every command for a bare slash, none for normal text", () => {
    expect(slashItems("/", cmds).map((i) => i.command.name)).toEqual(["neu", "modell", "merke", "status"]);
    expect(slashItems("hallo", cmds)).toEqual([]);
    expect(slashItems("", cmds)).toEqual([]);
    expect(slashItems("/neu\nzweite Zeile", cmds)).toEqual([]);
  });
  it("filters by prefix first, then by description", () => {
    expect(slashItems("/m", cmds).map((i) => i.command.name)).toEqual(["modell", "merke"]);
    expect(slashItems("/gespr", cmds).map((i) => i.command.name)).toEqual(["neu"]);
    expect(slashItems("/xyz", cmds)).toEqual([]);
  });
  it("marks commands that run immediately vs. need arguments", () => {
    const by = Object.fromEntries(slashItems("/", cmds).map((i) => [i.command.name, i]));
    expect(by.neu).toMatchObject({ runs: true, insert: "/neu" });
    expect(by.modell).toMatchObject({ runs: false, insert: "/modell " });
    expect(by.merke).toMatchObject({ runs: false, insert: "/merke " });
    expect(takesArgs(cmds[3]!)).toBe(false);
  });
  it("offers the choices of a command as second step", () => {
    const items = slashItems("/modell o", cmds);
    expect(items.map((i) => i.arg)).toEqual(["opus"]);
    expect(items[0]).toMatchObject({ runs: true, insert: "/modell opus" });
    expect(slashItems("/modell ", cmds)).toHaveLength(4);
    expect(slashItems("/modell opus", cmds)).toEqual([]);
    expect(slashItems("/merke etwas", cmds)).toEqual([]);
  });
});

describe("slash parsing", () => {
  it("parses known commands with arguments, ignores unknown ones", () => {
    expect(parseSlash("/merke  Milch kaufen ", cmds)).toMatchObject({ command: { name: "merke" }, args: "Milch kaufen" });
    expect(parseSlash("/NEU", cmds)).toMatchObject({ command: { name: "neu" }, args: "" });
    expect(parseSlash("/etc/hosts lesen", cmds)).toBeNull();
    expect(parseSlash("kein befehl", cmds)).toBeNull();
  });
  it("builds prompt text and requires arguments where the template wants them", () => {
    expect(promptFor(cmds[2]!, "Milch")).toEqual({ text: "Merke dir: Milch" });
    expect(promptFor(cmds[2]!, "")).toEqual({ error: "Bitte ergänzen: /merke <Text>" });
    expect(promptFor(cmds[3]!, "")).toEqual({ text: "Status?" });
  });
});

describe("monitor formatting", () => {
  it("formats tokens and cost", () => {
    expect(formatTokens(950)).toBe("950");
    expect(formatTokens(12345)).toMatch(/12,3 Tsd\./);
    expect(formatTokens(2_400_000)).toMatch(/2,4 Mio\./);
    expect(formatTokens(null)).toBe("–");
    expect(formatUsd(0.1234)).toBe("0,123 $");
    expect(formatUsd(null)).toBe("–");
  });
});
