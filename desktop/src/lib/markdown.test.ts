import { describe, expect, it } from "vitest";
import { highlight } from "./highlight";
import { parseInline, parseMarkdown, safeHref, spansText, type MdBlock } from "./markdown";

function at<T>(a: T[], i: number): T {
  const v = a[i];
  if (v === undefined) throw new Error(`index ${i} missing`);
  return v;
}
const para = (b: MdBlock) => (b.type === "paragraph" ? b.spans : []);

describe("markdown parser", () => {
  it("removes heading, emphasis and code markers", () => {
    const b = parseMarkdown("## Titel\n\nEin **fetter** und *kursiver* Text mit `code` und ~~weg~~.");
    expect(at(b, 0)).toMatchObject({ type: "heading", level: 2 });
    const p = para(at(b, 1));
    expect(spansText(p)).toBe("Ein fetter und kursiver Text mit code und weg.");
    expect(p.filter((s) => s.bold).map((s) => s.text)).toEqual(["fetter"]);
    expect(p.filter((s) => s.italic).map((s) => s.text)).toEqual(["kursiver"]);
    expect(p.filter((s) => s.code).map((s) => s.text)).toEqual(["code"]);
    expect(p.filter((s) => s.strike).map((s) => s.text)).toEqual(["weg"]);
  });

  it("parses nested bullet and numbered lists", () => {
    const b = parseMarkdown("- eins\n  - unter a\n  - unter b\n- zwei\n\n1. erst\n2. dann");
    const bullets = at(b, 0);
    expect(bullets).toMatchObject({ type: "list", ordered: false });
    if (bullets.type !== "list") throw new Error("list expected");
    expect(bullets.items).toHaveLength(2);
    expect(at(at(bullets.items, 0), 1)).toMatchObject({ type: "list" });
    expect(at(b, 1)).toMatchObject({ type: "list", ordered: true });
  });

  it("parses tables with alignment, quotes, rules and links", () => {
    const b = parseMarkdown("| A | B |\n|:--|--:|\n| 1 | 2 |\n\n> zitiert\n\n---\n\n[Doku](https://example.com/x) und https://a.de/b.");
    expect(at(b, 0)).toMatchObject({ type: "table", aligns: ["left", "right"] });
    expect(at(b, 1).type).toBe("quote");
    expect(at(b, 2).type).toBe("rule");
    const links = para(at(b, 3)).filter((s) => s.href);
    expect(links.map((s) => s.href)).toEqual(["https://example.com/x", "https://a.de/b"]);
  });

  it("keeps fenced code verbatim and treats an unclosed fence as code", () => {
    const done = parseMarkdown("```dart\nvar a = **1**;\n```\nDanach");
    expect(at(done, 0)).toMatchObject({ type: "code", lang: "dart", code: "var a = **1**;" });
    expect(at(done, 1).type).toBe("paragraph");
    expect(at(parseMarkdown("```py\nprint(1)"), 0)).toMatchObject({ type: "code", code: "print(1)" });
  });

  it("leaves snake_case, lone stars and unclosed markers literal", () => {
    expect(spansText(para(at(parseMarkdown("mobile_call_owner und 2 * 3 und **offen"), 0)))).toBe("mobile_call_owner und 2 * 3 und **offen");
  });

  it("closes emphasis nested in bold at a shared marker run", () => {
    const p = para(at(parseMarkdown("**Fett *mit kursiv***"), 0));
    expect(spansText(p)).toBe("Fett mit kursiv");
    expect(p.filter((s) => s.bold && s.italic).map((s) => s.text)).toEqual(["mit kursiv"]);
    expect(spansText(para(at(parseMarkdown("**fett***"), 0)))).toBe("fett*");
  });

  it("measures list indentation with tabs like spaces", () => {
    const b = parseMarkdown("\t- eins\n\t- zwei\n\t\t- unter");
    expect(b).toHaveLength(1);
    const l = at(b, 0);
    if (l.type !== "list") throw new Error("list expected");
    expect(l.items).toHaveLength(2);
    expect(at(at(l.items, 1), 1)).toMatchObject({ type: "list" });
  });

  it("only opens web and mail links", () => {
    expect(safeHref("https://example.com/a")).toBe("https://example.com/a");
    expect(safeHref("javascript:alert(1)")).toBeNull();
    expect(safeHref("file:///etc/passwd")).toBeNull();
  });
});

describe("markdown parser (cases taken over from the former second renderer)", () => {
  const kinds = (blocks: MdBlock[]) => blocks.map((b) => b.type);
  const list = (b: MdBlock) => {
    if (b.type !== "list") throw new Error("list expected");
    return b;
  };

  it("parses headings, paragraphs and rules", () => {
    const b = parseMarkdown("# Titel\n\nErster Absatz\nzweite Zeile\n\n---\n\n### Klein");
    expect(kinds(b)).toEqual(["heading", "paragraph", "rule", "heading"]);
    expect(at(b, 0)).toMatchObject({ level: 1 });
    expect(at(b, 3)).toMatchObject({ level: 3 });
    expect(spansText(para(at(b, 1)))).toBe("Erster Absatz\nzweite Zeile");
  });

  it("keeps fenced code verbatim, also with blank lines and a longer fence", () => {
    const b = parseMarkdown("Text\n\n```ts\nconst a = 1;\n\n  indented\n```\nDanach");
    expect(kinds(b)).toEqual(["paragraph", "code", "paragraph"]);
    expect(at(b, 1)).toEqual({ type: "code", lang: "ts", code: "const a = 1;\n\n  indented" });
    expect(at(parseMarkdown("````md\n```js\nx\n```\n````"), 0)).toMatchObject({ type: "code", lang: "md", code: "```js\nx\n```" });
  });

  it("parses task lists next to normal lists", () => {
    const b = parseMarkdown("- [x] fertig\n- [ ] offen\n- normal");
    const l = list(at(b, 0));
    expect(l.checked).toEqual([true, false, null]);
    expect(spansText(para(at(at(l.items, 0), 0)))).toBe("fertig");
    expect(list(at(parseMarkdown("- eins\n- zwei"), 0)).checked).toEqual([null, null]);
  });

  it("lets a list follow a paragraph line without blank line", () => {
    expect(kinds(parseMarkdown("Zutaten:\n- Mehl\n- Zucker"))).toEqual(["paragraph", "list"]);
  });

  it("pads short table rows and does not mistake a pipe in text for a table", () => {
    const t = at(parseMarkdown("| Name | Wert | Rest |\n|:--|--:|:-:|\n| a | 1 |"), 0);
    if (t.type !== "table") throw new Error("table expected");
    expect(t.aligns).toEqual(["left", "right", "center"]);
    expect(at(t.rows, 0).map(spansText)).toEqual(["a", "1", ""]);
    expect(kinds(parseMarkdown("a | b\nkein Trenner"))).toEqual(["paragraph"]);
  });

  it("parses block quotes recursively", () => {
    const q = at(parseMarkdown("> Zitat\n> - Punkt\n>\n> Ende"), 0);
    if (q.type !== "quote") throw new Error("quote expected");
    expect(kinds(q.blocks)).toEqual(["paragraph", "list", "paragraph"]);
  });

  it("keeps formatting out of code spans and raw html as text", () => {
    expect(parseInline("`a*b*c`")).toEqual([{ text: "a*b*c", code: true }]);
    expect(spansText(parseInline("<img src=x onerror=alert(1)>"))).toBe("<img src=x onerror=alert(1)>");
    expect(spansText(parseInline("\\*kein em\\*"))).toBe("*kein em*");
  });

  it("keeps balanced parentheses in link targets and trims autolink punctuation", () => {
    const links = parseInline("Siehe [Doku](https://x.de/a_(b)) oder https://y.de/pfad, ok.").filter((s) => s.href);
    expect(links.map((s) => s.href)).toEqual(["https://x.de/a_(b)", "https://y.de/pfad"]);
  });

  it("only opens web and mail links, also for odd spacing", () => {
    expect(safeHref("mailto:a@b.de")).toBe("mailto:a@b.de");
    expect(safeHref("data:text/html,hi")).toBeNull();
    expect(safeHref("  https://ok.de")).toBe("https://ok.de/");
  });
});

describe("syntax colouring", () => {
  it("marks keywords, strings, numbers and comments", () => {
    const kinds = Object.fromEntries(highlight('def f(x):\n  return "a" + 42  # fertig', "python").map((t) => [t.text, t.kind]));
    expect(kinds["def"]).toBe("keyword");
    expect(kinds['"a"']).toBe("string");
    expect(kinds["42"]).toBe("number");
    expect(kinds["# fertig"]).toBe("comment");
  });

  it("colours diff lines", () => {
    const kinds = highlight("+neu\n-alt\n gleich", "diff").map((t) => t.kind);
    expect(kinds).toEqual(["add", "del", "plain"]);
  });
});
