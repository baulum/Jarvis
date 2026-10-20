import { describe, expect, it } from "vitest";
import { highlight } from "./highlight";
import { parseMarkdown, safeHref, spansText, type MdBlock } from "./markdown";

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

  it("only opens web and mail links", () => {
    expect(safeHref("https://example.com/a")).toBe("https://example.com/a");
    expect(safeHref("javascript:alert(1)")).toBeNull();
    expect(safeHref("file:///etc/passwd")).toBeNull();
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
