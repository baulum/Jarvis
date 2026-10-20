// Chat Markdown: renders the tree from lib/markdown.ts. Compact variant for the dock, roomy one for the chat view.
import { Check, Copy } from "lucide-react";
import { memo, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { copyText } from "../lib/clipboard";
import { highlight } from "../lib/highlight";
import { parseMarkdown, type Block, type Inline } from "../lib/markdown";
import { openExternal } from "../lib/native";

export function CopyButton({ text, label = "Kopieren", className = "" }: { text: string; label?: string; className?: string }) {
  const [done, setDone] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  return (
    <button
      type="button"
      onClick={() => {
        void copyText(text).then((ok) => {
          setDone(ok);
          clearTimeout(timer.current);
          timer.current = setTimeout(() => setDone(false), 1600);
        });
      }}
      aria-label={done ? "Kopiert" : label}
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-2xs text-ink-faint transition-colors hover:bg-plate-hi hover:text-ink ${className}`}
    >
      {done ? <Check className="size-3 text-arc" /> : <Copy className="size-3" />}
      {done ? "Kopiert" : label}
    </button>
  );
}

function CodeBlockView({ lang, value, open }: { lang: string; value: string; open: boolean }) {
  const tokens = useMemo(() => highlight(value, lang), [value, lang]);
  return (
    <figure className="md-code my-3 overflow-hidden rounded-lg border border-seam bg-void">
      <figcaption className="flex items-center justify-between border-b border-seam px-3 py-1">
        <span className="type-data text-ink-faint">{lang || "text"}</span>
        <CopyButton text={value} />
      </figcaption>
      <pre className="type-data selectable overflow-x-auto p-3 text-[0.75rem] leading-[1.6] text-ink" tabIndex={0}>
        <code>
          {tokens.map((t, i) => (t.k ? <span key={i} className={`tok-${t.k}`}>{t.v}</span> : t.v))}
          {open && <span className="caret" />}
        </code>
      </pre>
    </figure>
  );
}

function Inlines({ nodes }: { nodes: Inline[] }): ReactNode {
  return nodes.map((n, i) => {
    switch (n.t) {
      case "text":
        return n.v;
      case "br":
        return <br key={i} />;
      case "code":
        return <code key={i} className="type-data rounded bg-plate-hi px-1 py-px text-[0.85em] text-arc">{n.v}</code>;
      case "strong":
        return <strong key={i} className="font-semibold text-ink"><Inlines nodes={n.c} /></strong>;
      case "em":
        return <em key={i}><Inlines nodes={n.c} /></em>;
      case "del":
        return <del key={i} className="text-ink-dim"><Inlines nodes={n.c} /></del>;
      case "link":
        return (
          <a
            key={i}
            href={n.href}
            title={n.href}
            rel="noopener noreferrer"
            className="text-arc underline decoration-arc-dim/50 underline-offset-2 hover:decoration-arc"
            onClick={(e) => {
              e.preventDefault();
              void openExternal(n.href);
            }}
          >
            <Inlines nodes={n.c} />
          </a>
        );
    }
  });
}

const HEADING_CLASS = [
  "",
  "mt-5 mb-2 text-[1.25rem] font-semibold leading-snug text-ink",
  "mt-5 mb-2 text-[1.1rem] font-semibold leading-snug text-ink",
  "mt-4 mb-1.5 text-[1rem] font-semibold text-ink",
  "mt-4 mb-1 text-[0.9375rem] font-semibold text-ink",
  "mt-3 mb-1 text-[0.875rem] font-semibold text-ink-dim",
  "mt-3 mb-1 text-[0.8125rem] font-semibold text-ink-dim",
];

const ALIGN = { left: "text-left", center: "text-center", right: "text-right" } as const;

function Blocks({ blocks }: { blocks: Block[] }): ReactNode {
  return blocks.map((b, i) => {
    switch (b.t) {
      case "heading": {
        const Tag = `h${Math.min(b.level, 6)}` as "h1";
        return <Tag key={i} className={`${HEADING_CLASS[b.level]} first:mt-0`}><Inlines nodes={b.c} /></Tag>;
      }
      case "para":
        return <p key={i} className="my-2 first:mt-0 last:mb-0"><Inlines nodes={b.c} /></p>;
      case "code":
        return <CodeBlockView key={i} lang={b.lang} value={b.v} open={b.open} />;
      case "quote":
        return (
          <blockquote key={i} className="my-3 border-l-2 border-arc-dim/60 pl-3.5 text-ink-dim">
            <Blocks blocks={b.c} />
          </blockquote>
        );
      case "hr":
        return <hr key={i} className="my-4 border-seam" />;
      case "list": {
        const Tag = b.ordered ? "ol" : "ul";
        return (
          <Tag
            key={i}
            start={b.ordered ? b.start : undefined}
            className={`my-2 flex flex-col gap-1 pl-5 ${b.ordered ? "list-decimal" : "list-disc"} marker:text-ink-faint`}
          >
            {b.items.map((it, k) => (
              <li key={k} className={it.checked !== null ? "-ml-5 flex list-none items-start gap-2" : "pl-1"}>
                {it.checked !== null && (
                  <span
                    aria-hidden
                    className={`mt-[0.35em] flex size-3.5 shrink-0 items-center justify-center rounded-[3px] border ${it.checked ? "border-arc-dim bg-arc-deep text-arc" : "border-seam-hi"}`}
                  >
                    {it.checked && <Check className="size-2.5" strokeWidth={3} />}
                  </span>
                )}
                <div className="min-w-0 flex-1 [&>p]:my-0.5"><Blocks blocks={it.c} /></div>
              </li>
            ))}
          </Tag>
        );
      }
      case "table":
        return (
          <div key={i} className="md-table-wrap my-3 max-w-full overflow-x-auto rounded-lg border border-seam" tabIndex={0}>
            <table className="w-full border-collapse text-[0.8125rem]">
              <thead className="bg-plate">
                <tr>
                  {b.head.map((c, k) => (
                    <th key={k} className={`border-b border-seam px-3 py-1.5 font-semibold whitespace-nowrap text-ink ${ALIGN[b.align[k] ?? "left"]}`}>
                      <Inlines nodes={c} />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {b.rows.map((r, k) => (
                  <tr key={k} className="border-b border-seam last:border-0">
                    {r.map((c, m) => (
                      <td key={m} className={`min-w-[6rem] px-3 py-1.5 align-top ${ALIGN[b.align[m] ?? "left"]}`}>
                        <Inlines nodes={c} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
    }
  });
}

/** Rendered Markdown. `streaming` adds the live caret after the last block. */
export const Markdown = memo(function Markdown({ text, streaming = false, className = "" }: { text: string; streaming?: boolean; className?: string }) {
  const blocks = useMemo(() => parseMarkdown(text), [text]);
  const last = blocks[blocks.length - 1];
  const caret = streaming && last?.t !== "code";
  return (
    <div className={`md selectable break-words ${caret ? "md-streaming" : ""} ${className}`}>
      <Blocks blocks={blocks} />
    </div>
  );
});
