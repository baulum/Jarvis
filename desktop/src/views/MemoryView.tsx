import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, type SimulationLinkDatum, type SimulationNodeDatum } from "d3-force";
import { Download, Search, Waypoints } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Button, EmptyState, ErrorState, Section, Skeleton, Tag } from "../components/ui";
import { useAction, useQuery } from "../hooks/useQuery";
import { formatDateTime } from "../lib/format";
import { saveBytes } from "../lib/native";
import type { MemoryGraph } from "../lib/types";
import { api, useApp } from "../store/app";

interface GNode extends SimulationNodeDatum { id: string; label: string; kind: string; degree: number }
type GLink = SimulationLinkDatum<GNode>;

const KIND_COLOR: Record<string, string> = {
  customer: "#6ad3e4", project: "#9fe0c3", preference: "#e2a14c", decision: "#d6a6f0", process: "#8fb7ff",
  technical: "#a7c4cf", person: "#f0c38c", recurring: "#c9d98a", entity: "#52666f",
};

function GraphCanvas({ graph, selected, onSelect }: { graph: MemoryGraph; selected: string | null; onSelect: (id: string) => void }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const nodesRef = useRef<GNode[]>([]);
  const { nodes, links } = useMemo(() => {
    const deg: Record<string, number> = {};
    graph.edges.forEach((e) => { deg[e.source] = (deg[e.source] ?? 0) + 1; deg[e.target] = (deg[e.target] ?? 0) + 1; });
    const ns: GNode[] = graph.nodes.map((n) => ({ ...n, degree: deg[n.id] ?? 0 }));
    const ids = new Set(ns.map((n) => n.id));
    const ls: GLink[] = graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target)).map((e) => ({ source: e.source, target: e.target }));
    return { nodes: ns, links: ls };
  }, [graph]);

  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const ctx = el.getContext("2d");
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const resize = () => {
      el.width = el.clientWidth * dpr;
      el.height = el.clientHeight * dpr;
    };
    resize();
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const sim = forceSimulation<GNode>(nodes)
      .force("link", forceLink<GNode, GLink>(links).id((d) => d.id).distance(70))
      .force("charge", forceManyBody().strength(-160))
      .force("center", forceCenter(0, 0))
      .force("collide", forceCollide<GNode>().radius((d) => 8 + d.degree * 1.5));
    nodesRef.current = nodes;
    const draw = () => {
      const w = el.width / dpr;
      const h = el.height / dpr;
      ctx.setTransform(dpr, 0, 0, dpr, w / 2 * dpr, h / 2 * dpr);
      ctx.clearRect(-w / 2, -h / 2, w, h);
      ctx.strokeStyle = "rgba(106, 211, 228, 0.16)";
      ctx.lineWidth = 1;
      for (const l of links) {
        const s = l.source as GNode, t = l.target as GNode;
        ctx.beginPath(); ctx.moveTo(s.x ?? 0, s.y ?? 0); ctx.lineTo(t.x ?? 0, t.y ?? 0); ctx.stroke();
      }
      for (const n of nodes) {
        const r = 3.5 + Math.min(n.degree, 8);
        ctx.beginPath(); ctx.arc(n.x ?? 0, n.y ?? 0, r, 0, Math.PI * 2);
        ctx.fillStyle = KIND_COLOR[n.kind] ?? "#8a9ea9";
        ctx.globalAlpha = n.kind === "entity" ? 0.55 : 0.95; ctx.fill(); ctx.globalAlpha = 1;
        if (n.id === selected) { ctx.strokeStyle = "#6ad3e4"; ctx.lineWidth = 2; ctx.stroke(); }
        if (n.degree > 1 || n.id === selected || nodes.length < 40) {
          ctx.fillStyle = n.id === selected ? "#d6e2e8" : "#8a9ea9";
          ctx.font = "11px 'Instrument Sans Variable', sans-serif";
          ctx.fillText(n.label.slice(0, 32), (n.x ?? 0) + r + 4, (n.y ?? 0) + 4);
        }
      }
    };
    if (reduce) { sim.stop(); sim.tick(300); draw(); } else sim.on("tick", draw);
    const ro = new ResizeObserver(() => { resize(); draw(); });
    ro.observe(el);
    return () => { sim.stop(); ro.disconnect(); };
  }, [nodes, links, selected]);

  const click = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const el = canvas.current!;
    const rect = el.getBoundingClientRect();
    const x = e.clientX - rect.left - rect.width / 2, y = e.clientY - rect.top - rect.height / 2;
    let best: GNode | null = null, bestD = 14 * 14;
    for (const n of nodesRef.current) {
      const d = ((n.x ?? 0) - x) ** 2 + ((n.y ?? 0) - y) ** 2;
      if (d < bestD) { best = n; bestD = d; }
    }
    if (best && best.kind !== "entity") onSelect(best.id);
  };

  return <canvas ref={canvas} onClick={click} className="h-full w-full cursor-crosshair" aria-label="Wissensgraph" role="img" />;
}

export function MemoryView() {
  const graph = useQuery(() => api.memoryGraph(), "memory-graph", { topics: ["memory"] });
  const pending = useQuery(() => api.memoryItems({ status: "pending" }), "memory-pending", { topics: ["memory"] });
  const [selected, setSelected] = useState<string | null>(null);
  const item = useQuery(() => (selected ? api.memoryItem(selected) : Promise.resolve(null)), `memory-item-${selected}`, { enabled: !!selected });
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Awaited<ReturnType<typeof api.memorySearch>> | null>(null);
  const act = useAction();

  const search = () => q.trim().length > 1 && void act.run("search", () => api.memorySearch(q.trim())).then((r) => r && setHits(r));
  const pushToast = useApp((s) => s.pushToast);
  const exportAll = () =>
    void act
      .run("export", async () => saveBytes(`jarvis-memory-${new Date().toISOString().slice(0, 10)}.zip`, await api.memoryExport()))
      .then((path) => path && pushToast({ tone: "ok", title: "Export gespeichert", body: path }));

  return (
    <div className="grid h-full grid-cols-[1fr_24rem] divide-x divide-seam">
      <div className="relative min-h-0">
        {graph.loading && <div className="p-8"><Skeleton rows={6} /></div>}
        {graph.error && <div className="p-8"><ErrorState message={graph.error} onRetry={graph.refetch} /></div>}
        {graph.data && graph.data.nodes.length === 0 && (
          <div className="p-8"><EmptyState icon={<Waypoints className="size-6" />} title="Das Gedächtnis ist noch leer.">
            J.A.R.V.I.S. legt Wissen automatisch aus Gesprächen an. Alles liegt als Markdown im Obsidian-Vault.
          </EmptyState></div>
        )}
        {graph.data && graph.data.nodes.length > 0 && <GraphCanvas graph={graph.data} selected={selected} onSelect={setSelected} />}
        <div className="absolute left-4 top-4 flex gap-2">
          <Button size="sm" variant="ghost" busy={act.busy === "export"} icon={<Download className="size-3.5" />} onClick={exportAll}>Export</Button>
        </div>
      </div>
      <aside className="flex min-h-0 flex-col overflow-y-auto">
        <div className="flex gap-2 border-b border-seam p-3">
          <input className="field flex-1" placeholder="Semantisch suchen …" value={q} onChange={(e) => setQ(e.target.value)}
                 onKeyDown={(e) => e.key === "Enter" && search()} aria-label="Gedächtnis durchsuchen" />
          <Button size="sm" busy={act.busy === "search"} icon={<Search className="size-3.5" />} onClick={search} aria-label="Suchen" />
        </div>
        {(pending.data?.items.length ?? 0) > 0 && (
          <Section title="Sensibel – wartet auf Bestätigung" className="m-3">
            <ul className="divide-y divide-seam">
              {pending.data!.items.map((p) => (
                <li key={p.id} className="flex flex-col gap-2 px-3 py-2 text-sm">
                  <span className="text-ink">{p.title}</span>
                  <span className="text-xs text-ink-dim">{p.preview}</span>
                  <div className="flex gap-2">
                    <Button size="sm" variant="primary" onClick={() => void act.run("p", () => api.memoryPendingDecide(p.id, true), "Gespeichert").then(pending.refetch)}>Speichern</Button>
                    <Button size="sm" variant="ghost" onClick={() => void act.run("p", () => api.memoryPendingDecide(p.id, false), "Verworfen").then(pending.refetch)}>Verwerfen</Button>
                  </div>
                </li>
              ))}
            </ul>
          </Section>
        )}
        {hits && (
          <ul className="divide-y divide-seam border-b border-seam">
            {hits.length === 0 && <li className="p-3 text-sm text-ink-dim">Keine Treffer.</li>}
            {hits.map((h) => (
              <li key={h.id}>
                <button className="w-full px-3 py-2 text-left hover:bg-plate-hi" onClick={() => setSelected(h.id)}>
                  <div className="flex items-baseline gap-2"><span className="text-sm text-ink">{h.title}</span><span className="type-data ml-auto text-xs text-arc-dim">{h.score.toFixed(2)}</span></div>
                  <p className="line-clamp-2 text-xs text-ink-dim">{h.content}</p>
                </button>
              </li>
            ))}
          </ul>
        )}
        {item.data && (
          <div className="flex flex-col gap-2 p-4 text-sm">
            <div className="flex flex-wrap items-center gap-2"><h3 className="type-title text-ink">{item.data.title}</h3><Tag>{item.data.kind}</Tag>
              {item.data.sensitivity === "sensitive" && <Tag tone="warn">sensibel</Tag>}{item.data.status !== "active" && <Tag tone="bad">{item.data.status}</Tag>}</div>
            <p className="selectable whitespace-pre-wrap text-ink-dim">{item.data.content}</p>
            <p className="type-data text-xs text-ink-faint">{item.data.path}</p>
            <p className="text-xs text-ink-faint">Aktualisiert {formatDateTime(item.data.updated_at)}</p>
            {item.data.backlinks.length > 0 && <p className="text-xs text-ink-dim">Verweise von: {item.data.backlinks.join(", ")}</p>}
          </div>
        )}
        {!item.data && !hits && graph.data && graph.data.nodes.length > 0 && (
          <p className="p-4 text-sm text-ink-dim">Knoten anklicken oder suchen, um eine Erinnerung mit Quelle zu sehen.</p>
        )}
      </aside>
    </div>
  );
}
