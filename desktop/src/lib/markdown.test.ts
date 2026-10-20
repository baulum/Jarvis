import { describe, expect, it } from "vitest";
import { highlight } from "./highlight";
import { inlineText, parseInline, parseMarkdown, safeHref, type Block } from "./markdown";

const kinds = (blocks: Block[]) => blocks.map((b) => b.t);

describe("markdown blocks", () => {
  it("parses headings, paragraphs and rules", () => {
    const b = parseMarkdown("# Titel\n\nErster Absatz\nzweite Zeile\n\n---\n\n### Klein");
    expect(kinds(b)).toEqual(["heading", "para", "hr", "heading"]);
    expect(b[0]).toMatchObject({ level: 1 });
    expect(b[3]).toMatchObject({ level: 3 });
    expect(b[1]!.t === "para" && inlineText(b[1]!.c)).toBe("Erster Absatz\nzweite Zeile");
  });

  it("parses fenced code with language and keeps content verbatim", () => {
    const b = parseMarkdown("Text\n\n```ts\nconst a = 1;\n\n  indented\n```\nDanach");
    expect(kinds(b)).toEqual(["para", "code", "para"]);
    expect(b[1]).toEqual({ t: "code", lang: "ts", v: "const a = 1;\n\n  indented", open: false });
  });

  it("treats an unterminated fence (streaming) as an open code block", () => {
    const b = parseMarkdown("```python\nprint('hi')");
    expect(b).toEqual([{ t: "code", lang: "python", v: "print('hi')", open: true }]);
  });

  it("does not close a fence on a shorter or different marker", () => {
    const b = parseMarkdown("````md\n```js\nx\n```\n````");
    expect(b[0]).toMatchObject({ t: "code", lang: "md", v: "```js\nx\n```", open: false });
  });

  it("parses bullet, ordered and task lists with nesting", () => {
    const b = parseMarkdown("- eins\n- zwei\n  - innen a\n  - innen b\n- drei\n\n1. a\n2. b\n\n- [x] fertig\n- [ ] offen");
    expect(kinds(b)).toEqual(["list", "list", "list"]);
    const l0 = b[0] as Extract<Block, { t: "list" }>;
    expect(l0.items).toHaveLength(3);
    const nested = l0.items[1]!.c.find((x) => x.t === "list") as Extract<Block, { t: "list" }>;
    expect(nested.items).toHaveLength(2);
    expect(b[1]).toMatchObject({ ordered: true, start: 1 });
    const tasks = b[2] as Extract<Block, { t: "list" }>;
    expect(tasks.items.map((i) => i.checked)).toEqual([true, false]);
  });

  it("lets a list follow a paragraph line without blank line", () => {
    expect(kinds(parseMarkdown("Zutaten:\n- Mehl\n- Zucker"))).toEqual(["para", "list"]);
  });

  it("parses GFM tables with alignment and pads short rows", () => {
    const b = parseMarkdown("| Name | Wert | Rest |\n|:--|--:|:-:|\n| a | 1 |\n| `b|c` | 2 | x |");
    const t = b[0] as Extract<Block, { t: "table" }>;
    expect(t.t).toBe("table");
    expect(t.align).toEqual(["left", "right", "center"]);
    expect(t.head.map(inlineText)).toEqual(["Name", "Wert", "Rest"]);
    expect(t.rows).toHaveLength(2);
    expect(t.rows[0]!.map(inlineText)).toEqual(["a", "1", ""]);
    expect(t.rows[1]![0]).toEqual([{ t: "code", v: "b|c" }]);
  });

  it("does not mistake a pipe in text for a table", () => {
    expect(kinds(parseMarkdown("a | b\nkein Trenner"))).toEqual(["para"]);
  });

  it("parses block quotes recursively", () => {
    const b = parseMarkdown("> Zitat\n> - Punkt\n>\n> Ende");
    const q = b[0] as Extract<Block, { t: "quote" }>;
    expect(q.t).toBe("quote");
    expect(kinds(q.c)).toEqual(["para", "list", "para"]);
  });
});

describe("markdown inline", () => {
  it("handles emphasis, strong, strike and code", () => {
    expect(parseInline("**fett** und *kursiv* und ~~weg~~ und `code`")).toEqual([
      { t: "strong", c: [{ t: "text", v: "fett" }] },
      { t: "text", v: " und " },
      { t: "em", c: [{ t: "text", v: "kursiv" }] },
      { t: "text", v: " und " },
      { t: "del", c: [{ t: "text", v: "weg" }] },
      { t: "text", v: " und " },
      { t: "code", v: "code" },
    ]);
  });

  it("keeps snake_case, math stars and unmatched markers literal", () => {
    expect(inlineText(parseInline("my_var_name und 5 * 3 * 2 und **offen"))).toBe("my_var_name und 5 * 3 * 2 und **offen");
    expect(parseInline("my_var_name")).toEqual([{ t: "text", v: "my_var_name" }]);
  });

  it("does not format inside code spans", () => {
    expect(parseInline("`a*b*c`")).toEqual([{ t: "code", v: "a*b*c" }]);
  });

  it("parses links and autolinks, trimming trailing punctuation", () => {
    const n = parseInline("Siehe [Doku](https://x.de/a_(b)) oder https://y.de/pfad, ok.");
    expect(n[1]).toMatchObject({ t: "link", href: "https://x.de/a_(b)" });
    expect(n[3]).toMatchObject({ t: "link", href: "https://y.de/pfad" });
    expect(inlineText(n)).toBe("Siehe Doku oder https://y.de/pfad, ok.");
  });

  it("never produces a link for unsafe schemes", () => {
    const n = parseInline("[klick](javascript:alert(1)) [x](data:text/html,hi)");
    expect(n.some((x) => x.t === "link")).toBe(false);
    expect(safeHref("javascript:alert(1)")).toBeNull();
    expect(safeHref(" https://ok.de ")).toBe("https://ok.de");
    expect(safeHref("mailto:a@b.de")).toBe("mailto:a@b.de");
  });

  it("keeps raw html as text (no markup injection)", () => {
    expect(parseInline("<img src=x onerror=alert(1)>")).toEqual([{ t: "text", v: "<img src=x onerror=alert(1)>" }]);
  });

  it("supports escapes and hard line breaks", () => {
    expect(inlineText(parseInline("\\*kein em\\*"))).toBe("*kein em*");
    expect(parseInline("a\nb")).toEqual([{ t: "text", v: "a" }, { t: "br" }, { t: "text", v: "b" }]);
  });
});

describe("highlight", () => {
  const join = (code: string, lang: string) => highlight(code, lang).map((t) => t.v).join("");
  it("never changes the text", () => {
    const src = 'def f(x):\n    # kommentar\n    return "s" + str(3.5)  # ende\n';
    for (const l of ["python", "ts", "bash", "json", "html", "diff", "sql", "unbekannt", ""]) expect(join(src, l)).toBe(src);
  });
  it("marks keywords, strings, numbers and comments", () => {
    const t = highlight('const x = "hi"; // c\nlet n = 42', "typescript");
    const by = (k: string) => t.filter((x) => x.k === k).map((x) => x.v);
    expect(by("kw")).toEqual(["const", "let"]);
    expect(by("str")).toEqual(['"hi"']);
    expect(by("com")).toEqual(["// c"]);
    expect(by("num")).toEqual(["42"]);
  });
  it("highlights json keys apart from string values", () => {
    const t = highlight('{"a": "b", "n": 1, "ok": true}', "json");
    expect(t.filter((x) => x.k === "attr").map((x) => x.v)).toEqual(['"a"', '"n"', '"ok"']);
    expect(t.filter((x) => x.k === "str").map((x) => x.v)).toEqual(['"b"']);
  });
  it("marks bash variables and python triple strings", () => {
    expect(highlight("echo $HOME ${X}", "sh").filter((x) => x.k === "attr").map((x) => x.v)).toEqual(["$HOME", "${X}"]);
    expect(highlight('x = """a\nb"""', "py").some((x) => x.k === "str" && x.v === '"""a\nb"""')).toBe(true);
  });
  it("survives unterminated strings and comments", () => {
    expect(join("let s = 'offen\nnext", "js")).toBe("let s = 'offen\nnext");
    expect(join("/* offen", "js")).toBe("/* offen");
  });
});
