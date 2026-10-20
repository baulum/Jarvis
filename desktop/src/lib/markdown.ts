// Small, dependency-free Markdown parser for chat output (CommonMark subset + GFM tables, task lists,
// strikethrough, autolinks). It produces a tree that the React layer renders node by node – there is no HTML
// string anywhere, so model output can never inject markup. Tolerant of half-finished input while streaming.

export type Inline =
  | { t: "text"; v: string }
  | { t: "code"; v: string }
  | { t: "strong"; c: Inline[] }
  | { t: "em"; c: Inline[] }
  | { t: "del"; c: Inline[] }
  | { t: "link"; href: string; c: Inline[] }
  | { t: "br" };

export type Align = "left" | "center" | "right" | null;

export type Block =
  | { t: "heading"; level: number; c: Inline[] }
  | { t: "para"; c: Inline[] }
  | { t: "code"; lang: string; v: string; open: boolean }
  | { t: "quote"; c: Block[] }
  | { t: "list"; ordered: boolean; start: number; items: { checked: boolean | null; c: Block[] }[] }
  | { t: "table"; align: Align[]; head: Inline[][]; rows: Inline[][][] }
  | { t: "hr" };

const FENCE = /^ {0,3}(`{3,}|~{3,})\s*([^\s`]*)[^`]*$/;
const HEADING = /^ {0,3}(#{1,6})(?:\s+(.*?))?(?:\s+#+)?\s*$/;
const HR = /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/;
const QUOTE = /^ {0,3}>/;
const LIST_ITEM = /^(\s*)([-*+]|\d{1,9}[.)])(\s+)(.*)$/;
const TABLE_DELIM = /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/;

const isBlank = (l: string) => l.trim() === "";

/** Safe link targets only. Everything else (javascript:, data:, file: …) is rendered as plain text. */
export function safeHref(raw: string): string | null {
  const href = raw.trim();
  return /^(https?:\/\/|mailto:)/i.test(href) ? href : null;
}

export function parseMarkdown(src: string): Block[] {
  return parseBlocks(src.replace(/\r\n?/g, "\n").split("\n"));
}

function splitRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  const cells: string[] = [];
  let cur = "";
  let inCode = false;
  for (let i = 0; i < s.length; i++) {
    const ch = s[i]!;
    if (ch === "\\" && s[i + 1] === "|") {
      cur += "|";
      i++;
    } else if (ch === "`") {
      inCode = !inCode;
      cur += ch;
    } else if (ch === "|" && !inCode) {
      cells.push(cur.trim());
      cur = "";
    } else cur += ch;
  }
  cells.push(cur.trim());
  return cells;
}

function startsTable(lines: string[], i: number): boolean {
  const head = lines[i];
  const delim = lines[i + 1];
  if (head === undefined || delim === undefined || !head.includes("|") || !delim.includes("-")) return false;
  if (!TABLE_DELIM.test(delim)) return false;
  return splitRow(head).length === splitRow(delim).length;
}

function listMarker(line: string): { indent: number; ordered: boolean; num: number; content: number; text: string } | null {
  const m = LIST_ITEM.exec(line);
  if (!m || HR.test(line)) return null;
  const marker = m[2]!;
  const ordered = /\d/.test(marker);
  return {
    indent: m[1]!.length,
    ordered,
    num: ordered ? parseInt(marker, 10) : 0,
    content: m[1]!.length + marker.length + m[3]!.length,
    text: m[4]!,
  };
}

/** Does this line end a running paragraph? */
function interruptsParagraph(lines: string[], i: number): boolean {
  const l = lines[i]!;
  if (FENCE.test(l) || HEADING.test(l) || HR.test(l) || QUOTE.test(l)) return true;
  const lm = listMarker(l);
  if (lm && lm.text.trim() !== "" && (!lm.ordered || lm.num === 1)) return true;
  return startsTable(lines, i);
}

function parseBlocks(lines: string[]): Block[] {
  const out: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    if (isBlank(line)) {
      i++;
      continue;
    }

    const fence = FENCE.exec(line);
    if (fence) {
      const mark = fence[1]!;
      const body: string[] = [];
      let closed = false;
      i++;
      while (i < lines.length) {
        const l = lines[i]!;
        const t = l.trim();
        if (t.startsWith(mark[0]!.repeat(mark.length)) && /^[`~]+$/.test(t)) {
          closed = true;
          i++;
          break;
        }
        body.push(l);
        i++;
      }
      out.push({ t: "code", lang: (fence[2] ?? "").toLowerCase(), v: body.join("\n"), open: !closed });
      continue;
    }

    const h = HEADING.exec(line);
    if (h) {
      out.push({ t: "heading", level: h[1]!.length, c: parseInline((h[2] ?? "").trim()) });
      i++;
      continue;
    }

    if (HR.test(line)) {
      out.push({ t: "hr" });
      i++;
      continue;
    }

    if (startsTable(lines, i)) {
      const delim = splitRow(lines[i + 1]!);
      const align: Align[] = delim.map((c) => {
        const l = c.startsWith(":");
        const r = c.endsWith(":");
        return l && r ? "center" : r ? "right" : l ? "left" : null;
      });
      const head = splitRow(line).map(parseInline);
      const rows: Inline[][][] = [];
      i += 2;
      while (i < lines.length && !isBlank(lines[i]!) && lines[i]!.includes("|")) {
        const cells = splitRow(lines[i]!);
        rows.push(align.map((_, k) => parseInline(cells[k] ?? "")));
        i++;
      }
      out.push({ t: "table", align, head, rows });
      continue;
    }

    if (QUOTE.test(line)) {
      const inner: string[] = [];
      while (i < lines.length && QUOTE.test(lines[i]!)) {
        inner.push(lines[i]!.replace(/^ {0,3}> ?/, ""));
        i++;
      }
      out.push({ t: "quote", c: parseBlocks(inner) });
      continue;
    }

    const lm = listMarker(line);
    if (lm) {
      const { block, next } = parseList(lines, i, lm);
      out.push(block);
      i = next;
      continue;
    }

    const para: string[] = [line];
    i++;
    while (i < lines.length && !isBlank(lines[i]!) && !interruptsParagraph(lines, i)) {
      para.push(lines[i]!);
      i++;
    }
    out.push({ t: "para", c: parseInline(para.map((l) => l.trim()).join("\n")) });
  }
  return out;
}

function parseList(
  lines: string[],
  start: number,
  first: NonNullable<ReturnType<typeof listMarker>>,
): { block: Block; next: number } {
  const items: { checked: boolean | null; c: Block[] }[] = [];
  let i = start;
  let cur = first;
  while (true) {
    // collect this item's lines: its first line, then everything indented under it
    const body: string[] = [cur.text];
    i++;
    while (i < lines.length) {
      const l = lines[i]!;
      if (isBlank(l)) {
        // a blank line continues the item only if indented content follows
        let j = i;
        while (j < lines.length && isBlank(lines[j]!)) j++;
        const after = lines[j];
        if (after !== undefined && after.search(/\S/) >= cur.indent + 2) {
          body.push("");
          i++;
          continue;
        }
        break;
      }
      const ind = l.search(/\S/);
      if (ind >= cur.indent + 2) {
        body.push(l.slice(Math.min(ind, cur.content)));
        i++;
        continue;
      }
      const sibling = listMarker(l);
      if (sibling || FENCE.test(l) || HEADING.test(l) || QUOTE.test(l) || HR.test(l)) break;
      body.push(l.trim()); // lazy continuation of the item's paragraph
      i++;
    }
    let checked: boolean | null = null;
    const task = /^\[([ xX])\]\s+/.exec(body[0]!);
    if (task) {
      checked = task[1] !== " ";
      body[0] = body[0]!.slice(task[0].length);
    }
    items.push({ checked, c: parseBlocks(body) });

    // the next line starts a sibling item?
    let j = i;
    while (j < lines.length && isBlank(lines[j]!)) j++;
    const nextLine = lines[j];
    const nm = nextLine === undefined ? null : listMarker(nextLine);
    if (nm && nm.ordered === first.ordered && nm.indent < cur.indent + 2 && nm.indent + 2 > first.indent) {
      cur = nm;
      i = j;
      continue;
    }
    break;
  }
  return { block: { t: "list", ordered: first.ordered, start: first.num, items }, next: i };
}

// ------------------------------------------------------------------ inline
const PUNCT = /[!-/:-@[-`{-~]/;
const isWord = (c: string | undefined) => c !== undefined && /[\p{L}\p{N}]/u.test(c);

export function parseInline(src: string): Inline[] {
  const out: Inline[] = [];
  let buf = "";
  const flush = () => {
    if (buf) {
      out.push({ t: "text", v: buf });
      buf = "";
    }
  };
  let i = 0;
  while (i < src.length) {
    const ch = src[i]!;

    if (ch === "\\" && i + 1 < src.length) {
      const nx = src[i + 1]!;
      if (nx === "\n") {
        flush();
        out.push({ t: "br" });
        i += 2;
        continue;
      }
      if (PUNCT.test(nx)) {
        buf += nx;
        i += 2;
        continue;
      }
    }

    if (ch === "\n") {
      buf = buf.replace(/ +$/, "");
      flush();
      out.push({ t: "br" });
      i++;
      continue;
    }

    if (ch === "`") {
      let n = 1;
      while (src[i + n] === "`") n++;
      const ticks = "`".repeat(n);
      let end = src.indexOf(ticks, i + n);
      while (end !== -1 && src[end + n] === "`") end = src.indexOf(ticks, end + n + 1);
      if (end !== -1) {
        let v = src.slice(i + n, end).replace(/\n/g, " ");
        if (v.length > 2 && v.startsWith(" ") && v.endsWith(" ")) v = v.slice(1, -1);
        flush();
        out.push({ t: "code", v });
        i = end + n;
        continue;
      }
      buf += ticks;
      i += n;
      continue;
    }

    if (ch === "[") {
      const link = matchLink(src, i);
      if (link) {
        flush();
        const href = safeHref(link.href);
        const children = parseInline(link.text);
        if (href) out.push({ t: "link", href, c: children });
        else out.push(...children);
        i = link.end;
        continue;
      }
    }

    if ((ch === "h" || ch === "H") && !isWord(src[i - 1])) {
      const m = /^https?:\/\/[^\s<>]+/i.exec(src.slice(i));
      if (m) {
        const trimmed = trimUrl(m[0]);
        flush();
        out.push({ t: "link", href: trimmed, c: [{ t: "text", v: trimmed }] });
        i += trimmed.length;
        continue;
      }
    }

    if (ch === "*" || ch === "_" || ch === "~") {
      const run = src.slice(i).match(/^(\*{1,3}|_{1,3}|~{2})/)?.[0];
      if (run && (ch !== "~" || run.length === 2)) {
        const n = ch === "~" ? 2 : run.length;
        const marker = ch.repeat(n);
        const closeAt = findClose(src, i, marker, ch);
        if (closeAt !== -1) {
          const inner = src.slice(i + n, closeAt);
          flush();
          const kids = parseInline(inner);
          if (ch === "~") out.push({ t: "del", c: kids });
          else if (n === 3) out.push({ t: "em", c: [{ t: "strong", c: kids }] });
          else if (n === 2) out.push({ t: "strong", c: kids });
          else out.push({ t: "em", c: kids });
          i = closeAt + n;
          continue;
        }
        buf += run;
        i += run.length;
        continue;
      }
    }

    buf += ch;
    i++;
  }
  flush();
  return out;
}

function trimUrl(url: string): string {
  let u = url;
  while (/[.,;:!?'"*_]$/.test(u)) u = u.slice(0, -1);
  while (u.endsWith(")") && (u.match(/\)/g) ?? []).length > (u.match(/\(/g) ?? []).length) u = u.slice(0, -1);
  return u;
}

/** Index of the closing `marker` for the opener at `open`, or -1. */
function findClose(src: string, open: number, marker: string, ch: string): number {
  const n = marker.length;
  const after = src[open + n];
  if (after === undefined || /\s/.test(after)) return -1; // opener must hug the text
  if (ch === "_" && isWord(src[open - 1])) return -1; // snake_case stays literal
  let from = open + n;
  while (from < src.length) {
    const at = src.indexOf(marker, from);
    if (at === -1) return -1;
    // skip code spans so `a*b` inside backticks never closes emphasis
    const tick = src.indexOf("`", from);
    if (tick !== -1 && tick < at) {
      const endTick = src.indexOf("`", tick + 1);
      if (endTick !== -1 && endTick > at) {
        from = endTick + 1;
        continue;
      }
    }
    const before = src[at - 1];
    const next = src[at + n];
    const longer = next === ch && n < 3; // part of a longer run, e.g. "**" while looking for "*"
    const okClose = before !== undefined && !/\s/.test(before) && !longer && !(ch === "_" && isWord(next));
    if (okClose && at > open + n) return at;
    from = at + (longer ? 2 : 1);
  }
  return -1;
}

function matchLink(src: string, at: number): { text: string; href: string; end: number } | null {
  let depth = 0;
  let i = at;
  for (; i < src.length; i++) {
    const c = src[i];
    if (c === "\\") i++;
    else if (c === "[") depth++;
    else if (c === "]" && --depth === 0) break;
  }
  if (i >= src.length || src[i + 1] !== "(") return null;
  const text = src.slice(at + 1, i);
  let j = i + 2;
  let parens = 1;
  for (; j < src.length; j++) {
    const c = src[j];
    if (c === "\n") return null;
    if (c === "(") parens++;
    else if (c === ")" && --parens === 0) break;
  }
  if (j >= src.length) return null;
  const inner = src.slice(i + 2, j).trim();
  const href = inner.replace(/\s+("[^"]*"|'[^']*')$/, "").replace(/^<(.*)>$/, "$1");
  return { text, href, end: j + 1 };
}

/** Plain text of inline nodes (for aria labels, copy, tests). */
export function inlineText(nodes: Inline[]): string {
  return nodes
    .map((n) => {
      switch (n.t) {
        case "text":
        case "code":
          return n.v;
        case "br":
          return "\n";
        default:
          return inlineText(n.c);
      }
    })
    .join("");
}
