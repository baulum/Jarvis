// Tiny syntax highlighter for chat code blocks. One scanner for all C-like / script languages, parameterised by
// keyword sets and comment syntax; JSON, diff and HTML/XML get their own small rules. Output is a flat token list
// the React layer renders as <span>s – no HTML strings.

export type TokenKind = "kw" | "str" | "num" | "com" | "fn" | "type" | "attr";
export interface Token {
  v: string;
  k?: TokenKind;
}

interface Lang {
  kw: Set<string>;
  line?: string[];
  block?: [string, string];
  /** quote characters that start strings ("`" and triple quotes may span lines) */
  quotes?: string;
  triple?: boolean;
  dollar?: boolean;
  caseInsensitive?: boolean;
}

const words = (s: string) => new Set(s.split(/\s+/).filter(Boolean));

const JS = words(
  "const let var function return if else for while do switch case break continue new class extends super this import export default from as async await yield try catch finally throw typeof instanceof in of void delete null undefined true false interface type enum implements private public protected readonly static abstract declare namespace keyof satisfies get set",
);
const PY = words(
  "def class return if elif else for while break continue pass import from as with try except finally raise lambda yield async await in is not and or None True False self global nonlocal del assert match case",
);
const SH = words(
  "if then else elif fi for while until do done case esac in function return exit local export readonly set unset source alias cd echo true false",
);
const RUST = words(
  "fn let mut const static struct enum impl trait pub use mod crate self Self super match if else for while loop break continue return as in ref move async await dyn where type unsafe extern true false",
);
const GO = words(
  "func var const type struct interface map chan package import return if else for range switch case default break continue go defer select fallthrough nil true false",
);
const SWIFT = words(
  "func let var class struct enum protocol extension init deinit return if else guard switch case default for while repeat break continue import in is as try catch throw throws async await actor nil true false self super private public internal fileprivate static final override some any where",
);
const KOTLIN = words(
  "fun val var class object interface data sealed enum companion return if else when for while do break continue import package in is as try catch finally throw null true false this super override open abstract private public internal protected suspend inline lateinit by init typealias",
);
const DART = words(
  "class extends implements with mixin abstract final const var void dynamic late required static return if else for while do switch case default break continue import export part library as is in try catch finally throw on rethrow new null true false this super async await yield Future Stream enum typedef factory get set operator",
);
const JAVA = words(
  "class interface enum extends implements public private protected static final abstract void int long double float boolean char byte short return if else for while do switch case default break continue new import package try catch finally throw throws this super null true false instanceof synchronized volatile var record",
);
const CLIKE = words(
  "int long short char float double void unsigned signed const static struct union enum typedef class public private protected namespace template typename using return if else for while do switch case default break continue new delete sizeof nullptr NULL true false include define auto inline virtual override",
);
const SQL = words(
  "select from where group by order having limit offset insert into values update set delete create table alter drop index join left right inner outer full on as and or not null is in like between exists union all distinct case when then else end primary key foreign references default unique count sum avg min max asc desc with",
);
const CSS = words("important inherit initial unset none auto");

const LANGS: Record<string, Lang> = {
  js: { kw: JS, line: ["//"], block: ["/*", "*/"], quotes: "\"'`" },
  py: { kw: PY, line: ["#"], quotes: "\"'", triple: true },
  sh: { kw: SH, line: ["#"], quotes: "\"'", dollar: true },
  rust: { kw: RUST, line: ["//"], block: ["/*", "*/"], quotes: '"' },
  go: { kw: GO, line: ["//"], block: ["/*", "*/"], quotes: "\"'`" },
  swift: { kw: SWIFT, line: ["//"], block: ["/*", "*/"], quotes: '"' },
  kotlin: { kw: KOTLIN, line: ["//"], block: ["/*", "*/"], quotes: '"' },
  dart: { kw: DART, line: ["//"], block: ["/*", "*/"], quotes: "\"'" },
  java: { kw: JAVA, line: ["//"], block: ["/*", "*/"], quotes: '"' },
  c: { kw: CLIKE, line: ["//"], block: ["/*", "*/"], quotes: "\"'" },
  sql: { kw: SQL, line: ["--"], block: ["/*", "*/"], quotes: "'\"", caseInsensitive: true },
  css: { kw: CSS, block: ["/*", "*/"], quotes: "\"'" },
  yaml: { kw: words("true false null yes no"), line: ["#"], quotes: "\"'" },
  toml: { kw: words("true false"), line: ["#"], quotes: "\"'" },
};

const ALIASES: Record<string, string> = {
  javascript: "js", jsx: "js", ts: "js", tsx: "js", typescript: "js", mjs: "js", cjs: "js",
  python: "py", python3: "py",
  bash: "sh", shell: "sh", zsh: "sh", sh: "sh", console: "sh", terminal: "sh",
  rs: "rust", golang: "go", kt: "kotlin", kts: "kotlin",
  cpp: "c", "c++": "c", h: "c", hpp: "c", cc: "c", cs: "java", csharp: "java", objc: "c",
  postgres: "sql", postgresql: "sql", mysql: "sql", sqlite: "sql",
  scss: "css", less: "css", yml: "yaml",
};

export function canonicalLang(lang: string): string {
  const l = lang.toLowerCase();
  return ALIASES[l] ?? l;
}

const NUMBER = /(?:0[xX][\da-fA-F_]+|0[bB][01_]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?)/y;
const IDENT = /[A-Za-z_$][\w$]*/y;
const MAX_HIGHLIGHT = 20000;

function sticky(re: RegExp, text: string, at: number): string | null {
  re.lastIndex = at;
  return re.exec(text)?.[0] ?? null;
}

function push(out: Token[], v: string, k?: TokenKind) {
  if (!v) return;
  const last = out[out.length - 1];
  if (last && last.k === k) last.v += v;
  else out.push(k ? { v, k } : { v });
}

function scan(code: string, lang: Lang): Token[] {
  const out: Token[] = [];
  let i = 0;
  const n = code.length;
  while (i < n) {
    const rest = code.slice(i, i + 3);
    const lineC = lang.line?.find((c) => code.startsWith(c, i));
    if (lineC) {
      let end = code.indexOf("\n", i);
      if (end === -1) end = n;
      push(out, code.slice(i, end), "com");
      i = end;
      continue;
    }
    if (lang.block && code.startsWith(lang.block[0], i)) {
      const end = code.indexOf(lang.block[1], i + lang.block[0].length);
      const stop = end === -1 ? n : end + lang.block[1].length;
      push(out, code.slice(i, stop), "com");
      i = stop;
      continue;
    }
    const ch = code[i]!;
    if (lang.quotes?.includes(ch)) {
      const triple = lang.triple && rest === ch.repeat(3);
      const close = triple ? ch.repeat(3) : ch;
      const multi = triple || ch === "`";
      let j = i + close.length;
      while (j < n) {
        if (code[j] === "\\") {
          j += 2;
          continue;
        }
        if (code.startsWith(close, j)) {
          j += close.length;
          break;
        }
        if (!multi && code[j] === "\n") break; // unterminated: stop at the line end
        j++;
      }
      push(out, code.slice(i, Math.min(j, n)), "str");
      i = Math.min(j, n);
      continue;
    }
    if (lang.dollar && ch === "$") {
      const m = /^\$(?:\{[^}]*\}|[A-Za-z_]\w*|[0-9@#?*!$-])/.exec(code.slice(i));
      if (m) {
        push(out, m[0], "attr");
        i += m[0].length;
        continue;
      }
    }
    if (/\d/.test(ch) && !/[\w$]/.test(code[i - 1] ?? "")) {
      const m = sticky(NUMBER, code, i);
      if (m) {
        push(out, m, "num");
        i += m.length;
        continue;
      }
    }
    if (/[A-Za-z_$]/.test(ch)) {
      const m = sticky(IDENT, code, i)!;
      const key = lang.caseInsensitive ? m.toLowerCase() : m;
      let kind: TokenKind | undefined;
      if (lang.kw.has(key)) kind = "kw";
      else if (/^\s*\(/.test(code.slice(i + m.length, i + m.length + 8))) kind = "fn";
      else if (/^[A-Z][a-z0-9]/.test(m)) kind = "type";
      push(out, m, kind);
      i += m.length;
      continue;
    }
    push(out, ch);
    i++;
  }
  return out;
}

function highlightJson(code: string): Token[] {
  const out: Token[] = [];
  const re = /"(?:[^"\\\n]|\\.)*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\btrue\b|\bfalse\b|\bnull\b/g;
  let last = 0;
  for (let m = re.exec(code); m; m = re.exec(code)) {
    push(out, code.slice(last, m.index));
    const v = m[0];
    const isKey = v.startsWith('"') && /^\s*:/.test(code.slice(m.index + v.length, m.index + v.length + 6));
    push(out, v, isKey ? "attr" : v.startsWith('"') ? "str" : /^[-\d]/.test(v) ? "num" : "kw");
    last = m.index + v.length;
  }
  push(out, code.slice(last));
  return out;
}

function highlightMarkup(code: string): Token[] {
  const out: Token[] = [];
  const re = /<!--[\s\S]*?-->|<\/?[A-Za-z][\w:-]*|\/?>|[A-Za-z_:][\w:.-]*(?==)|"[^"\n]*"|'[^'\n]*'/g;
  let last = 0;
  for (let m = re.exec(code); m; m = re.exec(code)) {
    push(out, code.slice(last, m.index));
    const v = m[0];
    push(out, v, v.startsWith("<!--") ? "com" : v.startsWith("<") || v.endsWith(">") ? "kw" : /^["']/.test(v) ? "str" : "attr");
    last = m.index + v.length;
  }
  push(out, code.slice(last));
  return out;
}

function highlightDiff(code: string): Token[] {
  const out: Token[] = [];
  const lines = code.split("\n");
  lines.forEach((l, idx) => {
    const kind: TokenKind | undefined =
      l.startsWith("+++") || l.startsWith("---") || l.startsWith("diff ") || l.startsWith("index ")
        ? "com"
        : l.startsWith("@@")
          ? "type"
          : l.startsWith("+")
            ? "fn"
            : l.startsWith("-")
              ? "attr"
              : undefined;
    out.push(kind ? { v: l, k: kind } : { v: l });
    if (idx < lines.length - 1) out.push({ v: "\n" });
  });
  return out;
}

/** Token list for `code`; unknown languages come back as one plain token. */
export function highlight(code: string, lang: string): Token[] {
  const l = canonicalLang(lang);
  if (code.length > MAX_HIGHLIGHT) return [{ v: code }];
  if (l === "json" || l === "jsonc" || l === "json5") return highlightJson(code);
  if (l === "diff" || l === "patch") return highlightDiff(code);
  if (l === "html" || l === "xml" || l === "svg" || l === "vue") return highlightMarkup(code);
  const spec = LANGS[l];
  if (!spec) return [{ v: code }];
  return scan(code, spec);
}
