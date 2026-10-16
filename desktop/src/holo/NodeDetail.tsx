// Right: details of the focused node, while the brain stays visible next to it.
import { AnimatePresence, motion } from "framer-motion";
import { ArrowUpRight, Check, Pencil, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { formatDateTime, formatRelative } from "../lib/format";
import type { MemoryItemDetail } from "../lib/types";
import { api, useApp } from "../store/app";
import { CATEGORY_LABEL, KIND_LABEL } from "./graph/types";
import { useBrain } from "./store";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[6.5rem_1fr] gap-3 py-1.5 text-xs">
      <span className="text-ink-faint">{label}</span>
      <span className="min-w-0 text-ink-dim">{children}</span>
    </div>
  );
}

function sourceText(src: Record<string, unknown> | undefined): string {
  if (!src) return "–";
  const type = String(src.type ?? "");
  const map: Record<string, string> = {
    conversation: "aus einem Gespräch",
    conversation_summary: "Zusammenfassung eines Gesprächs",
    agent: "von Jarvis gespeichert",
    correction: "Korrektur",
  };
  const channel = src.channel ? ` (${String(src.channel)})` : "";
  return (map[type] ?? (type || "Obsidian")) + channel;
}

function MemoryBody({ id }: { id: string }) {
  const [item, setItem] = useState<MemoryItemDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ title: "", content: "" });
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const select = useBrain((s) => s.select);
  const memoryVersion = useApp((s) => s.versions.memory);
  const pushToast = useApp((s) => s.pushToast);

  useEffect(() => {
    let alive = true;
    api
      .memoryItem(id)
      .then((it) => {
        if (!alive) return;
        setItem(it);
        setError(null);
      })
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [id, memoryVersion]);

  if (error) return <p className="text-xs text-alarm">{error}</p>;
  if (!item) return <div className="h-24 animate-pulse rounded bg-plate" />;

  async function save() {
    if (!item) return;
    setBusy(true);
    try {
      await api.memoryEdit(item.id, { title: draft.title.trim() || undefined, content: draft.content });
      setEditing(false);
      pushToast({ tone: "ok", title: "Erinnerung gespeichert", body: "Auch im Obsidian-Vault aktualisiert." });
    } catch (e) {
      pushToast({ tone: "bad", title: "Speichern fehlgeschlagen", body: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!item) return;
    setBusy(true);
    try {
      await api.memoryDelete(item.id);
      select(null, false);
      pushToast({ tone: "ok", title: "Erinnerung gelöscht" });
    } catch (e) {
      pushToast({ tone: "bad", title: "Löschen fehlgeschlagen", body: (e as Error).message });
    } finally {
      setBusy(false);
      setConfirmDelete(false);
    }
  }

  const people = (item.related ?? []).filter((r) => ["person", "customer", "company", "project"].includes(r.kind));
  const others = (item.related ?? []).filter((r) => !people.includes(r));

  return (
    <>
      {editing ? (
        <div className="flex flex-col gap-2">
          <input className="field" value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} aria-label="Titel" />
          <textarea
            className="field selectable min-h-48 font-[inherit] leading-relaxed"
            value={draft.content}
            onChange={(e) => setDraft({ ...draft, content: e.target.value })}
            aria-label="Inhalt"
          />
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setEditing(false)} className="rounded-md px-3 py-1.5 text-xs text-ink-dim hover:text-ink">
              Abbrechen
            </button>
            <button type="button" disabled={busy} onClick={() => void save()} className="flex items-center gap-1.5 rounded-md bg-arc px-3 py-1.5 text-xs text-void disabled:opacity-50">
              <Check className="size-3.5" /> Speichern
            </button>
          </div>
        </div>
      ) : (
        <p className="selectable text-[0.875rem] leading-relaxed whitespace-pre-wrap text-ink">{item.content}</p>
      )}
      <div className="mt-5 border-t border-seam pt-3">
        <Row label="Kategorie">
          {KIND_LABEL[item.kind] ?? item.kind}
          {item.layer ? <span className="text-ink-faint"> · {item.layer}</span> : null}
        </Row>
        <Row label="Erstellt">{formatDateTime(item.created_at)}</Row>
        <Row label="Aktualisiert">{formatRelative(item.updated_at)}</Row>
        <Row label="Quelle">{sourceText(item.source)}</Row>
        {typeof item.confidence === "number" && item.confidence < 1 && <Row label="Konfidenz">{Math.round(item.confidence * 100)} %</Row>}
        {item.aliases && item.aliases.length > 0 && <Row label="Auch genannt">{item.aliases.join(", ")}</Row>}
        {item.sensitivity === "sensitive" && <Row label="Schutz">sensibel – nur am Desktop sichtbar</Row>}
        <Row label="Datei">
          <span className="type-data break-all">{item.path}</span>
        </Row>
      </div>
      {people.length > 0 && (
        <div className="mt-4">
          <p className="mb-1.5 text-2xs tracking-[0.12em] text-ink-faint uppercase">Personen & Projekte</p>
          <RelatedList items={people} onPick={(n) => select(n)} />
        </div>
      )}
      {others.length > 0 && (
        <div className="mt-4">
          <p className="mb-1.5 text-2xs tracking-[0.12em] text-ink-faint uppercase">Verknüpft</p>
          <RelatedList items={others} onPick={(n) => select(n)} />
        </div>
      )}
      <div className="mt-6 flex items-center gap-2">
        {!editing && (
          <button
            type="button"
            onClick={() => {
              setDraft({ title: item.title, content: item.content });
              setEditing(true);
            }}
            className="flex items-center gap-1.5 rounded-md border border-seam-hi px-2.5 py-1.5 text-xs text-ink-dim hover:text-ink"
          >
            <Pencil className="size-3.5" /> Bearbeiten
          </button>
        )}
        {confirmDelete ? (
          <span className="flex items-center gap-2 text-xs">
            <span className="text-alarm">Endgültig löschen?</span>
            <button type="button" disabled={busy} onClick={() => void remove()} className="rounded-md bg-alarm px-2.5 py-1.5 text-void">
              Löschen
            </button>
            <button type="button" onClick={() => setConfirmDelete(false)} className="text-ink-dim hover:text-ink">
              Nein
            </button>
          </span>
        ) : (
          <button
            type="button"
            onClick={() => setConfirmDelete(true)}
            className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs text-ink-faint hover:text-alarm"
          >
            <Trash2 className="size-3.5" /> Löschen
          </button>
        )}
      </div>
    </>
  );
}

function RelatedList({ items, onPick }: { items: { node: string; title: string; relation: string; kind: string }[]; onPick: (n: string) => void }) {
  return (
    <ul className="flex flex-col">
      {items.map((r) => (
        <li key={`${r.node}-${r.relation}`}>
          <button type="button" onClick={() => onPick(r.node)} className="group flex w-full items-baseline gap-2 py-1 text-left text-[0.8125rem]">
            <span className="truncate text-ink-dim group-hover:text-ink">{r.title}</span>
            <span className="shrink-0 text-2xs text-ink-faint">{r.relation}</span>
            <ArrowUpRight className="ml-auto size-3 shrink-0 text-ink-faint opacity-0 group-hover:opacity-100" />
          </button>
        </li>
      ))}
    </ul>
  );
}

function SystemBody({ id }: { id: string }) {
  const model = useBrain((s) => s.model);
  const live = useBrain((s) => s.live);
  const select = useBrain((s) => s.select);
  const open = useBrain((s) => s.openOverlay);
  const setActive = useApp((s) => s.setActiveSession);
  const node = model?.nodes[model.byId.get(id) ?? -1];
  const neighbours = useMemo(
    () => (node && model ? (model.neighbors[node.index] ?? []).map((i) => model.nodes[i]!).slice(0, 14) : []),
    [node, model],
  );
  if (!node) return null;
  const m = node.meta;
  const act = live.nodes[id];
  const lines = live.lines.filter((l) => l.node === id).slice(-6).reverse();
  const view = node.kind === "task" ? "tasks" : node.kind === "coding" ? "agents" : node.kind === "schedule" ? "scheduler" : node.kind === "connector" ? "connectors" : node.kind === "skill" ? "skills" : node.kind === "tool" ? "permissions" : null;
  return (
    <>
      {typeof m.description === "string" && <p className="text-[0.875rem] leading-relaxed text-ink">{m.description}</p>}
      {typeof m.preview === "string" && <p className="text-[0.875rem] leading-relaxed text-ink">{m.preview}</p>}
      <div className="mt-4 border-t border-seam pt-3">
        <Row label="Art">{KIND_LABEL[node.kind] ?? node.kind}</Row>
        {typeof m.status === "string" && <Row label="Status">{m.status}</Row>}
        {typeof m.risk === "string" && <Row label="Risiko">{m.risk}</Row>}
        {typeof m.connector === "string" && <Row label="Connector">{m.connector}</Row>}
        {typeof m.active === "boolean" && <Row label="Eingerichtet">{m.active ? "ja" : "nein"}</Row>}
        {typeof m.branch === "string" && m.branch && <Row label="Branch"><span className="type-data">{m.branch}</span></Row>}
        {typeof m.cron === "string" && <Row label="Zeitplan"><span className="type-data">{m.cron}</span></Row>}
        {typeof m.channel === "string" && <Row label="Kanal">{m.channel}</Row>}
        {typeof m.updated_at === "string" && <Row label="Zuletzt">{formatRelative(m.updated_at)}</Row>}
        {typeof m.last_activity_at === "string" && <Row label="Zuletzt">{formatRelative(m.last_activity_at)}</Row>}
        {act && <Row label="Gerade">{act.status === "active" ? "aktiv" : act.status === "error" ? `Fehler${act.detail ? `: ${act.detail}` : ""}` : act.status === "approval" ? "wartet auf Freigabe" : act.status}</Row>}
      </div>
      {lines.length > 0 && (
        <div className="mt-4">
          <p className="mb-1.5 text-2xs tracking-[0.12em] text-ink-faint uppercase">Zuletzt beobachtet</p>
          <ul className="flex flex-col gap-1 text-xs text-ink-dim">
            {lines.map((l) => (
              <li key={l.ts}>{l.text}</li>
            ))}
          </ul>
        </div>
      )}
      {neighbours.length > 0 && (
        <div className="mt-4">
          <p className="mb-1.5 text-2xs tracking-[0.12em] text-ink-faint uppercase">Verbunden mit</p>
          <RelatedList items={neighbours.map((n) => ({ node: n.id, title: n.label, relation: KIND_LABEL[n.kind] ?? n.kind, kind: n.kind }))} onPick={select} />
        </div>
      )}
      <div className="mt-6 flex gap-2">
        {node.kind === "conversation" && (
          <button type="button" onClick={() => setActive(id.slice("session:".length))} className="rounded-md border border-seam-hi px-2.5 py-1.5 text-xs text-ink-dim hover:text-ink">
            Gespräch unten öffnen
          </button>
        )}
        {view && (
          <button type="button" onClick={() => open("system", view)} className="rounded-md border border-seam-hi px-2.5 py-1.5 text-xs text-ink-dim hover:text-ink">
            In der Systemansicht öffnen
          </button>
        )}
      </div>
    </>
  );
}

export function NodeDetail() {
  const selected = useBrain((s) => s.selected);
  const model = useBrain((s) => s.model);
  const select = useBrain((s) => s.select);
  const node = selected && model ? model.nodes[model.byId.get(selected) ?? -1] : undefined;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && useBrain.getState().selected && useBrain.getState().overlay === null) useBrain.getState().overview();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <AnimatePresence>
      {node && (
        <motion.aside
          key="detail"
          initial={{ opacity: 0, x: 24 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: 24 }}
          transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
          className="holo-panel pointer-events-auto absolute top-20 right-5 bottom-28 flex w-[min(25rem,calc(100vw-2.5rem))] flex-col"
          aria-label={`Details: ${node.label}`}
        >
          <header className="flex items-start gap-3 px-5 pt-4 pb-3">
            <div className="min-w-0 flex-1">
              <p className="type-data text-[0.62rem] tracking-[0.14em] text-arc-dim uppercase">{CATEGORY_LABEL[node.category]}</p>
              <h2 className="type-title mt-1 text-[1.05rem] leading-snug text-ink">{node.label}</h2>
            </div>
            <button type="button" onClick={() => select(null, false)} aria-label="Schließen" className="rounded p-1 text-ink-faint hover:text-ink">
              <X className="size-4" />
            </button>
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5">
            {node.id.startsWith("memory:") ? <MemoryBody key={node.id} id={node.id.slice(7)} /> : <SystemBody id={node.id} />}
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}
