// Small dependency-free Markdown parser for chat answers (same rules as apps/jarvis_mobile/lib/core/markdown.dart):
// headings, paragraphs, bullet / numbered lists (nested), block quotes, fenced code, tables, rules and inline
// bold / italic / strike / code / links. Tolerant of half-finished text while an answer is streaming: an unclosed
// code fence is code until the end, an unclosed `**` stays literal until its closing marker arrives.

export type MdAlign = "left" | "center" | "right";

export interface MdSpan {
  text: string;
  bold?: boolean;
  italic?: boolean;
  strike?: boolean;
  code?: boolean;
  href?: string;
}

/** `checked[i]` is the task-list state of item i (`- [x]` / `- [ ]`), null for a normal item. `tasks[i]` numbers the
 * task items of the whole document in reading order – the key under which a toggled state is stored. */
export type MdList = {
  type: "list";
  ordered: boolean;
  start: number;
  items: MdBlock[][];
  checked: (boolean | null)[];
  tasks: (number | null)[];
};
export type MdBlock =
  | { type: "heading"; level: number; spans: MdSpan[] }
  | { type: "paragraph"; spans: MdSpan[] }
  | { type: "code"; lang: string; code: string }
  | { type: "quote"; blocks: MdBlock[] }
  | MdList
  | { type: "table"; header: MdSpan[][]; aligns: MdAlign[]; rows: MdSpan[][][] }
  | { type: "rule" };

const FENCE = /^\s{0,3}(`{3,}|~{3,})\s*([^\s`]*).*$/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.+?)(?:\s+#+)?\s*$/;
const RULE = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;
const QUOTE = /^\s{0,3}>\s?/;
const LIST = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const TASK = /^\[([ xX])\]\s+/;
const TABLE_SEP =/^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

// `noUncheckedIndexedAccess`: line / character / capture-group access that yields "" instead of undefined
const at = (lines: string[], i: number): string => lines[i] ?? "";
const ch = (s: string, i: number): string => s.charAt(i);
const grp = (m: RegExpExecArray, k: number): string => m[k] ?? "";

export function parseMarkdown(src: string): MdBlock[] {
  const out = blocks(src.replace(/\r\n?/g, "\n").split("\n"));
  numberTasks(out, { n: 0 });
  return out;
}

function numberTasks(list: MdBlock[], counter: { n: number }): void {
  for (const b of list) {
    if (b.type === "list") {
      b.items.forEach((item, i) => {
        if (b.checked[i] != null) b.tasks[i] = counter.n++;
        numberTasks(item, counter);
      });
    } else if (b.type === "quote") numberTasks(b.blocks, counter);
  }
}

const isOrdered = (marker: string) => marker >= "0" && marker <= "9";
const indentOf = (s: string) => {
  let n = 0;
  for (const c of s) {
    if (c === " ") n += 1;
    else if (c === "\t") n += 4;
    else break;
  }
  return n;
};
const closesFence = (line: string, marker: string) => {
  const t = line.trim();
  return t.length >= marker.length && [...t].every((c) => c === ch(marker, 0));
};
const isTableStart = (lines: string[], i: number) =>
  i + 1 < lines.length &&
  at(lines, i).includes("|") &&
  at(lines, i + 1).includes("|") &&
  at(lines, i + 1).includes("-") &&
  TABLE_SEP.test(at(lines, i + 1));
const startsBlock = (lines: string[], i: number) => {
  const l = at(lines, i);
  return FENCE.test(l) || HEADING.test(l) || RULE.test(l) || QUOTE.test(l) || LIST.test(l) || isTableStart(lines, i);
};

function splitRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  const cells: string[] = [];
  let b = "";
  for (let i = 0; i < s.length; i++) {
    const c = ch(s, i);
    if (c === "\\" && ch(s, i + 1) === "|") {
      b += "|";
      i++;
    } else if (c === "|") {
      cells.push(b.trim());
      b = "";
    } else b += c;
  }
  cells.push(b.trim());
  return cells;
}

function alignOf(sep: string): MdAlign {
  const t = sep.trim();
  const l = t.startsWith(":");
  const r = t.endsWith(":");
  if (l && r) return "center";
  return r ? "right" : "left";
}

function blocks(lines: string[]): MdBlock[] {
  const out: MdBlock[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = at(lines, i);
    if (line.trim() === "") {
      i++;
      continue;
    }
    const fence = FENCE.exec(line);
    if (fence) {
      const marker = grp(fence, 1);
      const code: string[] = [];
      i++;
      while (i < lines.length && !closesFence(at(lines, i), marker)) code.push(at(lines, i++));
      if (i < lines.length) i++; // closing fence; while streaming it is missing and the rest is code
      out.push({ type: "code", lang: grp(fence, 2), code: code.join("\n") });
      continue;
    }
    const h = HEADING.exec(line);
    if (h) {
      out.push({ type: "heading", level: grp(h, 1).length, spans: parseInline(grp(h, 2)) });
      i++;
      continue;
    }
    if (RULE.test(line)) {
      out.push({ type: "rule" });
      i++;
      continue;
    }
    if (isTableStart(lines, i)) {
      const header = splitRow(line);
      const aligns = splitRow(at(lines, i + 1)).map(alignOf);
      const rows: MdSpan[][][] = [];
      i += 2;
      while (i < lines.length && at(lines, i).trim() !== "" && at(lines, i).includes("|")) {
        const cells = splitRow(at(lines, i));
        rows.push(header.map((_, c) => parseInline(cells[c] ?? "")));
        i++;
      }
      out.push({
        type: "table",
        header: header.map((c) => parseInline(c)),
        aligns: header.map((_, c) => aligns[c] ?? "left"),
        rows,
      });
      continue;
    }
    if (QUOTE.test(line)) {
      const inner: string[] = [];
      while (i < lines.length && QUOTE.test(at(lines, i))) inner.push(at(lines, i++).replace(QUOTE, ""));
      out.push({ type: "quote", blocks: blocks(inner) });
      continue;
    }
    if (LIST.test(line)) {
      const [list, next] = parseList(lines, i);
      out.push(list);
      i = next;
      continue;
    }
    const para = [line.trim()];
    i++;
    while (i < lines.length && at(lines, i).trim() !== "" && !startsBlock(lines, i)) para.push(at(lines, i++).trim());
    out.push({ type: "paragraph", spans: parseInline(para.join("\n")) });
  }
  return out;
}

function parseList(lines: string[], start: number): [MdList, number] {
  const first = LIST.exec(at(lines, start));
  const base = first ? indentOf(grp(first, 1)) : 0;
  const marker = first ? grp(first, 2) : "-";
  const ordered = isOrdered(marker);
  const startNo = ordered ? parseInt(marker, 10) || 1 : 1;
  const items: MdBlock[][] = [];
  const checked: (boolean | null)[] = [];
  let i = start;
  while (i < lines.length) {
    const m = LIST.exec(at(lines, i));
    if (!m || indentOf(grp(m, 1)) !== base || isOrdered(grp(m, 2)) !== ordered) break;
    const task = TASK.exec(grp(m, 3));
    checked.push(task ? grp(task, 1) !== " " : null);
    const item = [task ? grp(m, 3).slice(task[0].length) : grp(m, 3)];
    i++;
    while (i < lines.length) {
      const l = at(lines, i);
      if (l.trim() === "") {
        let k = i + 1;
        while (k < lines.length && at(lines, k).trim() === "") k++;
        if (k < lines.length && indentOf(at(lines, k)) > base) {
          item.push("");
          i++;
          continue;
        }
        break;
      }
      if (indentOf(l) <= base) break;
      const lead = l.length - l.trimStart().length;
      item.push(l.slice(Math.min(lead, base + 2)));
      i++;
    }
    items.push(blocks(item));
    // a blank line between two items of the same list does not end it
    let k = i;
    while (k < lines.length && at(lines, k).trim() === "") k++;
    if (k > i && k < lines.length) {
      const n = LIST.exec(at(lines, k));
      if (n && indentOf(grp(n, 1)) === base && isOrdered(grp(n, 2)) === ordered) i = k;
    }
  }
  return [{ type: "list", ordered, start: startNo, items, checked, tasks: checked.map(() => null) }, i];
}

// ------------------------------------------------------------------ inline
const ESCAPABLE = "\\`*_{}[]()#+-.!|~>";
const isWord = (c: string) => /[A-Za-z0-9]/.test(c);
const isSpace = (c: string) => c === "" || c.trim() === "";

export function parseInline(s: string): MdSpan[] {
  const out: MdSpan[] = [];
  inline(s.replace(/<br\s*\/?>/gi, "\n"), { bold: false, italic: false, strike: false }, undefined, out);
  return out;
}

/** Index of the `)` closing a link target that starts at `from`; balanced parentheses belong to the URL. */
function closingParen(s: string, from: number): number {
  let depth = 1;
  for (let j = from; j < s.length; j++) {
    const c = ch(s, j);
    if (c === "\n") return -1;
    if (c === "(") depth++;
    else if (c === ")" && --depth === 0) return j;
  }
  return -1;
}

function closeStrong(s: string, marker: string, from: number): number {
  let j = s.indexOf(marker, from);
  while (j !== -1) {
    if (j > from && !isSpace(ch(s, j - 1))) return j;
    j = s.indexOf(marker, j + 1);
  }
  return -1;
}

/** Closing `**` / `__`. In `**a *b***` the closing run also ends the inner emphasis: strong is its last two markers. */
function closeDouble(s: string, c: string, from: number): number {
  const j = closeStrong(s, c + c, from);
  if (j === -1) return j;
  let end = j;
  while (ch(s, end) === c) end++;
  const open = [...s.slice(from, j)].filter((x) => x === c).length % 2 === 1;
  return end - j > 2 && open ? end - 2 : j;
}

function closeEm(s: string, c: string, from: number): number {
  let j = from;
  while (j < s.length) {
    if (ch(s, j) === "\\") {
      j += 2;
      continue;
    }
    if (ch(s, j) === c) {
      if (ch(s, j + 1) === c) {
        j += 2; // a `**` inside the emphasis
        continue;
      }
      if (j > from && !isSpace(ch(s, j - 1)) && (c !== "_" || !isWord(ch(s, j + 1)))) return j;
    }
    j++;
  }
  return -1;
}

interface Style {
  bold: boolean;
  italic: boolean;
  strike: boolean;
}

function inline(s: string, st: Style, href: string | undefined, out: MdSpan[]): void {
  let buf = "";
  const span = (text: string, extra: Partial<MdSpan> = {}): MdSpan => ({
    text,
    ...(st.bold && { bold: true }),
    ...(st.italic && { italic: true }),
    ...(st.strike && { strike: true }),
    ...(href && { href }),
    ...extra,
  });
  const flush = () => {
    if (buf) out.push(span(buf));
    buf = "";
  };
  let i = 0;
  while (i < s.length) {
    const c = ch(s, i);
    if (c === "\\" && i + 1 < s.length && ESCAPABLE.includes(ch(s, i + 1))) {
      buf += ch(s, i + 1);
      i += 2;
      continue;
    }
    if (c === "`") {
      const j = s.indexOf("`", i + 1);
      if (j > i + 1) {
        flush();
        out.push(span(s.slice(i + 1, j).trim(), { code: true }));
        i = j + 1;
        continue;
      }
    }
    if (c === "*" || c === "_") {
      const startsWord = c === "_" && i > 0 && isWord(ch(s, i - 1));
      if (!startsWord) {
        if (s.startsWith(c.repeat(3), i) && i + 3 < s.length && !isSpace(ch(s, i + 3))) {
          const j = closeStrong(s, c.repeat(3), i + 3);
          if (j !== -1) {
            flush();
            inline(s.slice(i + 3, j), { ...st, bold: true, italic: true }, href, out);
            i = j + 3;
            continue;
          }
        }
        if (s.startsWith(c.repeat(2), i) && i + 2 < s.length && !isSpace(ch(s, i + 2))) {
          const j = closeDouble(s, c, i + 2);
          if (j !== -1) {
            flush();
            inline(s.slice(i + 2, j), { ...st, bold: true }, href, out);
            i = j + 2;
            continue;
          }
        } else if (i + 1 < s.length && ch(s, i + 1) !== c && !isSpace(ch(s, i + 1))) {
          const j = closeEm(s, c, i + 1);
          if (j !== -1) {
            flush();
            inline(s.slice(i + 1, j), { ...st, italic: true }, href, out);
            i = j + 1;
            continue;
          }
        }
      }
    }
    if (c === "~" && s.startsWith("~~", i) && i + 2 < s.length && !isSpace(ch(s, i + 2))) {
      const j = closeStrong(s, "~~", i + 2);
      if (j !== -1) {
        flush();
        inline(s.slice(i + 2, j), { ...st, strike: true }, href, out);
        i = j + 2;
        continue;
      }
    }
    if (c === "[" && href === undefined) {
      const close = s.indexOf("]", i + 1);
      if (close > i && ch(s, close + 1) === "(") {
        const end = closingParen(s, close + 2);
        if (end > close + 1) {
          const url = s.slice(close + 2, end).trim().split(/\s+/)[0] ?? "";
          if (url) {
            flush();
            inline(s.slice(i + 1, close), st, url, out);
            i = end + 1;
            continue;
          }
        }
      }
    }
    if (c === "h" && href === undefined && (s.startsWith("http://", i) || s.startsWith("https://", i)) && !isWord(ch(s, i - 1))) {
      let end = i;
      while (end < s.length && !isSpace(ch(s, end)) && !'<>"'.includes(ch(s, end))) end++;
      while (end > i && ".,;:!?)]".includes(ch(s, end - 1))) end--;
      if (end > i + 8) {
        flush();
        const url = s.slice(i, end);
        out.push(span(url, { href: url }));
        i = end;
        continue;
      }
    }
    buf += c;
    i++;
  }
  flush();
}

/** Only web and mail links are ever opened. */
export function safeHref(href: string): string | null {
  try {
    const u = new URL(href);
    return ["http:", "https:", "mailto:"].includes(u.protocol) ? u.href : null;
  } catch {
    return null;
  }
}

export const spansText = (spans: MdSpan[]): string => spans.map((s) => s.text).join("");
