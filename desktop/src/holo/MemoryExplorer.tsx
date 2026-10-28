// Memory explorer (left panel of the "Erinnerungen" view): search, filter by kind, project and time range; the
// brain shows only the matching memories, a click opens one with its metadata and real relations.
import { Search, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { formatRelative } from "../lib/format";
import { api } from "../store/app";
import { exploreMemories, isFiltering, projectsOf, RANGE_LABEL, usage, type ExploreFilter, type Range, type Sort } from "./graph/explore";
import { CATEGORY_COLOR, CATEGORY_LABEL, KIND_LABEL, type Category } from "./graph/types";
import { classOf } from "./graph/anatomy";
import { useBrain } from "./store";

const LIMIT = 150;

function rgb(c: [number, number, number]): string {
  return `rgb(${Math.round(c[0] * 255)} ${Math.round(c[1] * 255)} ${Math.round(c[2] * 255)})`;
}

export function MemoryExplorer() {
  const model = useBrain((s) => s.model);
  const selected = useBrain((s) => s.selected);
  const select = useBrain((s) => s.select);
  const hover = useBrain((s) => s.hover);
  const setHighlight = useBrain((s) => s.setHighlight);
  const focusNodes = useBrain((s) => s.focusNodes);
  const [query, setQuery] = useState("");
  const [semantic, setSemantic] = useState<{ q: string; ids: string[] }>({ q: "", ids: [] });
  const [categories, setCategories] = useState<Set<Category>>(new Set());
  const [project, setProject] = useState<string | null>(null);
  const [range, setRange] = useState<Range>("all");
  const [sort, setSort] = useState<Sort>("recent");

  // the same hybrid search Jarvis uses – finds memories by meaning, not only by title
  useEffect(() => {
    const q = query.trim();
    if (q.length < 3) return;
    const t = setTimeout(() => {
      api
        .memorySearch(q, 24)
        .then((hits) => setSemantic({ q, ids: hits.map((h) => `memory:${h.id}`) }))
        .catch(() => setSemantic({ q, ids: [] }));
    }, 250);
    return () => clearTimeout(t);
  }, [query]);

  const filter: ExploreFilter = useMemo(
    () => ({ query, semantic: semantic.q === query.trim() ? semantic.ids : [], categories, project, range, sort: query.trim() ? "relevance" : sort }),
    [query, semantic, categories, project, range, sort],
  );
  const results = useMemo(() => (model ? exploreMemories(model, filter) : []), [model, filter]);
  const filtering = isFiltering(filter);

  // the brain shows exactly the matching memories
  useEffect(() => {
    setHighlight(filtering ? new Set(results.map((n) => n.id)) : null);
  }, [results, filtering, setHighlight]);
  useEffect(() => () => setHighlight(null), [setHighlight]);

  const kinds = useMemo(() => {
    const c = new Map<Category, number>();
    for (const n of model?.nodes ?? []) if (classOf(n) === "longterm") c.set(n.category, (c.get(n.category) ?? 0) + 1);
    return [...c.entries()].sort((a, b) => b[1] - a[1]);
  }, [model]);
  const projects = useMemo(() => (model ? projectsOf(model) : []), [model]);
  const relations = useMemo(() => {
    if (!model) return 0;
    const ids = new Set(results.map((n) => n.index));
    return model.edges.filter((e) => ids.has(e.source) && ids.has(e.target)).length;
  }, [model, results]);

  const toggleCat = (c: Category) =>
    setCategories((prev) => {
      const next = new Set(prev);
      if (next.has(c)) next.delete(c);
      else next.add(c);
      return next;
    });

  return (
    <section className="holo-panel pointer-events-auto flex min-h-0 flex-col" aria-label="Erinnerungen erkunden">
      <div className="flex items-center gap-2 border-b border-seam px-3">
        <Search className="size-3.5 text-ink-faint" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Erinnerungen durchsuchen"
          className="h-10 min-w-0 flex-1 bg-transparent text-[0.8125rem] text-ink placeholder:text-ink-faint focus:outline-none"
          aria-label="Erinnerungen durchsuchen"
        />
        {query && (
          <button type="button" onClick={() => setQuery("")} aria-label="Suche leeren" className="text-ink-faint hover:text-ink">
            <X className="size-3.5" />
          </button>
        )}
      </div>
      <div className="flex flex-col gap-2.5 border-b border-seam px-3 py-2.5">
        <div className="flex flex-wrap gap-1" role="group" aria-label="Thema">
          {kinds.map(([cat, n]) => {
            const on = categories.has(cat);
            return (
              <button
                key={cat}
                type="button"
                onClick={() => toggleCat(cat)}
                aria-pressed={on}
                className={`flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-2xs transition-colors ${on ? "border-arc-dim bg-arc-deep/60 text-ink" : "border-seam text-ink-faint hover:text-ink-dim"}`}
              >
                <span className="size-1.5 rounded-full" style={{ background: rgb(CATEGORY_COLOR[cat]) }} aria-hidden />
                {CATEGORY_LABEL[cat]}
                <span className="type-data text-[0.58rem] text-ink-faint">{n}</span>
              </button>
            );
          })}
        </div>
        <div className="flex items-center gap-2">
          <select
            value={project ?? ""}
            onChange={(e) => setProject(e.target.value || null)}
            className="field h-7 min-w-0 flex-1 py-0 text-2xs"
            aria-label="Projekt"
          >
            <option value="">Alle Projekte</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
          <select value={range} onChange={(e) => setRange(e.target.value as Range)} className="field h-7 w-24 py-0 text-2xs" aria-label="Zeitraum">
            {(Object.keys(RANGE_LABEL) as Range[]).map((r) => (
              <option key={r} value={r}>
                {RANGE_LABEL[r]}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-center justify-between">
          <p className="type-data text-[0.6rem] text-ink-faint">
            {results.length} Erinnerungen · {relations} Beziehungen
          </p>
          {!query.trim() && (
            <div className="flex gap-2 text-2xs" role="group" aria-label="Sortierung">
              {(["recent", "used"] as Sort[]).map((s) => (
                <button key={s} type="button" onClick={() => setSort(s)} aria-pressed={sort === s} className={sort === s ? "text-arc" : "text-ink-faint hover:text-ink-dim"}>
                  {s === "recent" ? "Neueste" : "Meistgenutzt"}
                </button>
              ))}
            </div>
          )}
        </div>
        {filtering && results.length > 1 && (
          <button type="button" onClick={() => focusNodes(results.slice(0, 300).map((n) => n.id))} className="self-start text-2xs text-arc hover:underline">
            Treffer im Gehirn zeigen
          </button>
        )}
      </div>
      <ul className="min-h-0 flex-1 overflow-y-auto p-1.5" aria-label="Ergebnisse">
        {results.slice(0, LIMIT).map((n) => {
          const date = typeof n.meta.updated_at === "string" ? n.meta.updated_at : typeof n.meta.created_at === "string" ? n.meta.created_at : null;
          const used = usage(n);
          return (
            <li key={n.id}>
              <button
                type="button"
                onClick={() => select(n.id)}
                onMouseEnter={() => hover(n.id)}
                onMouseLeave={() => hover(null)}
                aria-current={selected === n.id ? "true" : undefined}
                className={`flex w-full items-start gap-2.5 rounded-md px-2 py-1.5 text-left ${selected === n.id ? "bg-plate-hi" : "hover:bg-plate/70"}`}
              >
                <span className="mt-1.5 size-1.5 shrink-0 rounded-full" style={{ background: rgb(CATEGORY_COLOR[n.category]) }} aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs text-ink">{n.label}</span>
                  <span className="block truncate text-2xs text-ink-faint">
                    {[KIND_LABEL[n.kind] ?? n.kind, date ? formatRelative(date) : null, used ? `${used}× genutzt` : null, n.degree ? `${n.degree} Bezüge` : null].filter(Boolean).join(" · ")}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
        {results.length > LIMIT && <li className="px-2 py-2 text-2xs text-ink-faint">… und {results.length - LIMIT} weitere – Suche oder Filter verfeinern.</li>}
        {!results.length && <li className="px-2 py-3 text-2xs text-ink-faint">Keine Erinnerung passt zu diesen Filtern.</li>}
      </ul>
    </section>
  );
}
