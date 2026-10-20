// Light-weight syntax colouring for code blocks in chat (comments, strings, numbers, keywords, types, calls).
// No dependency: good enough to read code answers, not a full grammar.

export type TokenKind = "plain" | "comment" | "string" | "number" | "keyword" | "type" | "call" | "add" | "del" | "hunk";
export interface Token {
  text: string;
  kind: TokenKind;
}

const KEYWORDS = new Set(
  (
    "abstract as async await break case catch class const continue def default del do elif else enum except export " +
    "extends false final finally fn for from func function if implements import in interface is lambda let match mod " +
    "new nil none null of override package pass private protected pub public raise return self static struct super " +
    "switch this throw trait true try type typeof undefined use var void while with yield select insert update " +
    "delete where join create table into values order by group limit and or not echo fi then done sudo git npm cd " +
    "ls dart flutter uv pip"
  ).split(" "),
);
const LITERALS = new Set(["True", "False", "None", "NULL"]);

const HASH = new Set("python py sh bash zsh shell yaml yml toml ruby rb dockerfile makefile ini conf r perl env properties".split(" "));
const DASH = new Set(["sql", "lua", "haskell"]);

const isKeyword = (w: string) =>
  KEYWORDS.has(w) || LITERALS.has(w) || (w.length > 1 && w === w.toUpperCase() && KEYWORDS.has(w.toLowerCase()));

function tokenizer(lang: string): RegExp {
  const l = lang.toLowerCase();
  const comment = HASH.has(l) ? "#[^\\n]*" : DASH.has(l) ? "--[^\\n]*" : "//[^\\n]*|/\\*[\\s\\S]*?(?:\\*/|$)";
  return new RegExp(
    `(?<comment>${comment})` +
      `|(?<string>"""[\\s\\S]*?(?:"""|$)|"(?:\\\\.|[^"\\\\\\n])*"?|'(?:\\\\.|[^'\\\\\\n])*'?|\`(?:\\\\.|[^\`\\\\])*\`?)` +
      "|(?<number>\\b0x[0-9a-fA-F]+\\b|\\b\\d[\\d_]*(?:\\.\\d+)?(?:[eE][+-]?\\d+)?\\b)" +
      "|(?<word>[A-Za-z_][A-Za-z0-9_]*)",
    "g",
  );
}

export function highlight(code: string, lang: string): Token[] {
  if (lang.toLowerCase() === "diff") {
    return code.split("\n").flatMap((line, i, all): Token[] => [
      {
        text: i < all.length - 1 ? `${line}\n` : line,
        kind: line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : line.startsWith("@@") ? "hunk" : "plain",
      },
    ]);
  }
  const out: Token[] = [];
  let last = 0;
  for (const m of code.matchAll(tokenizer(lang))) {
    const start = m.index ?? 0;
    if (start > last) out.push({ text: code.slice(last, start), kind: "plain" });
    const text = m[0];
    const g = m.groups ?? {};
    let kind: TokenKind = "plain";
    if (g.comment !== undefined) kind = "comment";
    else if (g.string !== undefined) kind = "string";
    else if (g.number !== undefined) kind = "number";
    else if (isKeyword(text)) kind = "keyword";
    else if (/^[A-Z]/.test(text)) kind = "type";
    else if (code[start + text.length] === "(") kind = "call";
    out.push({ text, kind });
    last = start + text.length;
  }
  if (last < code.length) out.push({ text: code.slice(last), kind: "plain" });
  return out;
}
