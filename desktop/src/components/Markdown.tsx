import { Check, Copy } from "lucide-react";
import { Fragment, useMemo, useState, type ReactNode } from "react";
import { highlight, type TokenKind } from "../lib/highlight";
import { parseMarkdown, safeHref, type MdBlock, type MdSpan } from "../lib/markdown";
import { openExternal } from "../lib/native";

const TOKEN_CLASS: Record<TokenKind, string> = {
  plain: "",
  comment: "italic text-ink-faint",
  string: "text-[#8fd7a4]",
  number: "text-ember",
  keyword: "text-arc",
  type: "text-[#b9a6ff]",
  call: "text-ink",
  add: "text-[#8fd7a4]",
  del: "text-alarm",
  hunk: "text-arc-dim",
};

/** Renders an answer as formatted Markdown. `compact` is the smaller variant for the chat dock. */
export function Markdown({ text, compact = false }: { text: string; compact?: boolean }) {
  const blocks = useMemo(() => parseMarkdown(text), [text]);
  return (
    <div className={`md ${compact ? "text-[0.875rem] leading-relaxed" : "text-[0.9375rem] leading-[1.65]"} flex flex-col gap-3 text-ink`}>
      {blocks.map((b, i) => (
        <Block key={i} b={b} compact={compact} />
      ))}
    </div>
  );
}

function Inline({ spans }: { spans: MdSpan[] }) {
  return (
    <>
      {spans.map((s, i) => {
        let node: ReactNode = s.text.includes("\n")
          ? s.text.split("\n").map((part, k) => (
              <Fragment key={k}>
                {k > 0 && <br />}
                {part}
              </Fragment>
            ))
          : s.text;
        if (s.code) {
          node = <code className="rounded bg-plate-hi px-1 py-px font-mono text-[0.84em] text-arc">{s.text}</code>;
        }
        if (s.bold) node = <strong className="font-semibold text-white">{node}</strong>;
        if (s.italic) node = <em>{node}</em>;
        if (s.strike) node = <del className="text-ink-dim">{node}</del>;
        if (s.href) {
          const href = safeHref(s.href);
          node = href ? (
            <a
              href={href}
              onClick={(e) => {
                e.preventDefault();
                void openExternal(href);
              }}
              className="text-arc underline decoration-arc-dim underline-offset-2 hover:text-white"
              title={href}
            >
              {node}
            </a>
          ) : (
            node
          );
        }
        return <Fragment key={i}>{node}</Fragment>;
      })}
    </>
  );
}

const HEADING_CLASS = [
  "",
  "type-title mt-2 border-b border-seam-hi pb-1.5 text-[1.5rem] leading-tight text-white",
  "type-title mt-2 border-b border-seam pb-1 text-[1.25rem] leading-tight text-white",
  "type-title mt-1 text-[1.0625rem] text-ink",
  "mt-1 text-[0.9375rem] font-semibold text-ink",
  "mt-1 text-[0.8125rem] font-semibold tracking-wide text-ink-dim uppercase",
  "mt-1 text-[0.8125rem] font-semibold tracking-wide text-ink-dim uppercase",
];

function Block({ b, compact, depth = 0, dim = false }: { b: MdBlock; compact: boolean; depth?: number; dim?: boolean }) {
  switch (b.type) {
    case "paragraph":
      return (
        <p className={dim ? "text-ink-dim" : undefined}>
          <Inline spans={b.spans} />
        </p>
      );
    case "heading": {
      const level = Math.min(6, Math.max(1, b.level));
      const Tag = `h${level}` as "h1";
      return (
        <Tag className={HEADING_CLASS[level]}>
          <Inline spans={b.spans} />
        </Tag>
      );
    }
    case "list": {
      const Tag = b.ordered ? "ol" : "ul";
      return (
        <Tag
          start={b.ordered ? b.start : undefined}
          className={`flex flex-col gap-1 pl-6 marker:text-arc-dim ${b.ordered ? "list-decimal" : depth % 2 === 0 ? "list-disc" : "list-[circle]"}`}
        >
          {b.items.map((item, i) => (
            <li key={i} className="pl-1">
              <div className="flex flex-col gap-1">
                {item.map((child, k) => (
                  <Block key={k} b={child} compact={compact} depth={depth + 1} dim={dim} />
                ))}
              </div>
            </li>
          ))}
        </Tag>
      );
    }
    case "quote":
      return (
        <blockquote className="flex flex-col gap-2 border-l-2 border-arc-dim/70 pl-4 text-ink-dim">
          {b.blocks.map((child, i) => (
            <Block key={i} b={child} compact={compact} depth={depth} dim />
          ))}
        </blockquote>
      );
    case "code":
      return <CodeBlock code={b.code} lang={b.lang} />;
    case "table":
      return (
        <div className="max-w-full overflow-x-auto rounded-lg border border-seam-hi">
          <table className="w-full border-collapse text-[0.875rem]">
            <thead className="bg-plate-hi">
              <tr>
                {b.header.map((cell, c) => (
                  <th key={c} style={{ textAlign: b.aligns[c] }} className="border-b border-seam-hi px-3 py-2 font-semibold whitespace-nowrap text-white">
                    <Inline spans={cell} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {b.rows.map((row, r) => (
                <tr key={r} className="odd:bg-plate/60">
                  {row.map((cell, c) => (
                    <td key={c} style={{ textAlign: b.aligns[c] }} className="border-t border-seam px-3 py-2 align-top">
                      <Inline spans={cell} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "rule":
      return <hr className="my-1 border-seam-hi" />;
  }
}

function CodeBlock({ code, lang }: { code: string; lang: string }) {
  const [copied, setCopied] = useState(false);
  const tokens = useMemo(() => highlight(code, lang), [code, lang]);
  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }
  return (
    <div className="overflow-hidden rounded-lg border border-seam-hi bg-void">
      <div className="flex items-center justify-between border-b border-seam bg-hull px-3 py-1">
        <span className="type-data text-ink-faint uppercase">{lang || "code"}</span>
        <button
          type="button"
          onClick={() => void copy()}
          className="type-data inline-flex items-center gap-1.5 rounded px-1.5 py-1 text-ink-dim hover:text-white"
          aria-label="Code kopieren"
        >
          {copied ? <Check className="size-3.5 text-arc" /> : <Copy className="size-3.5" />}
          {copied ? "Kopiert" : "Kopieren"}
        </button>
      </div>
      <pre className="overflow-x-auto px-3.5 py-3 font-mono text-[0.8125rem] leading-[1.55]">
        <code>
          {tokens.map((t, i) => (
            <span key={i} className={TOKEN_CLASS[t.kind]}>
              {t.text}
            </span>
          ))}
        </code>
      </pre>
    </div>
  );
}
