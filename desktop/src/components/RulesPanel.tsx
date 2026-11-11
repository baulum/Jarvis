// Gedächtnis → Verhaltensregeln: lasting rules Sir gave ("immer …", "ab sofort …"). Unlike ordinary memories they are
// loaded into the system prompt on every agent run – this list shows which ones apply, and lets Sir edit, switch off
// and add them. Same vault notes (folder Regeln/), no second store.
import { Pencil, Plus, Wand2 } from "lucide-react";
import { useState } from "react";
import { Button, EmptyState, Field, Section, Skeleton, Tag, Toggle } from "./ui";
import { useAction, useQuery } from "../hooks/useQuery";
import { RULE_FILTERS, filterRules, ruleState, scopeLabel, type RuleFilter } from "../lib/rules";
import type { BehaviourRule } from "../lib/types";
import { api } from "../store/app";

interface Draft {
  id: string | null;
  title: string;
  text: string;
  scope: "global" | "project";
  project: string;
}

const EMPTY: Draft = { id: null, title: "", text: "", scope: "global", project: "" };

export function RulesPanel() {
  const q = useQuery(() => api.memoryRules(), "memory-rules", { topics: ["memory"] });
  const act = useAction();
  const [filter, setFilter] = useState<RuleFilter>("in_force");
  const [draft, setDraft] = useState<Draft | null>(null);

  const rules = q.data?.rules ?? [];
  const shown = filterRules(rules, filter);

  const save = async () => {
    if (!draft) return;
    const body = { title: draft.title.trim(), text: draft.text.trim(), scope: draft.scope, project: draft.project.trim() };
    const r = await act.run(
      "save",
      () => (draft.id ? api.memoryRuleUpdate(draft.id, body) : api.memoryRuleCreate(body)),
      draft.id ? "Regel gespeichert" : "Regel angelegt",
    );
    if (r) {
      setDraft(null);
      await q.refetch();
    }
  };
  const toggle = async (r: BehaviourRule, active: boolean) => {
    await act.run(`toggle:${r.id}`, () => api.memoryRuleUpdate(r.id, { active }));
    await q.refetch();
  };
  const migrate = async () => {
    const res = await act.run("migrate", () => api.memoryRulesMigrate(false));
    if (res) await q.refetch();
  };
  const valid = !!draft && draft.title.trim().length >= 2 && draft.text.trim().length >= 3 && (draft.scope === "global" || draft.project.trim().length >= 3);

  return (
    <Section
      title="Verhaltensregeln"
      meta={q.data ? `${q.data.in_force} gelten bei jedem Lauf` : "gelten bei jedem Lauf"}
      className="m-3"
      actions={
        <Button size="sm" variant="ghost" icon={<Plus className="size-3.5" />} onClick={() => setDraft({ ...EMPTY })}>
          Neue Regel
        </Button>
      }
    >
      <div className="flex flex-wrap gap-1 border-b border-seam px-3 py-2" role="tablist" aria-label="Regeln filtern">
        {RULE_FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            role="tab"
            aria-selected={filter === f.id}
            onClick={() => setFilter(f.id)}
            className={`rounded px-2 py-0.5 text-xs ${filter === f.id ? "bg-arc-deep text-arc" : "text-ink-dim hover:text-ink"}`}
          >
            {f.label}
          </button>
        ))}
      </div>
      {q.loading && <div className="p-3"><Skeleton rows={3} /></div>}
      {q.error && !q.data && <p role="alert" className="p-3 text-xs text-alarm">{q.error}</p>}
      {q.data && shown.length === 0 && (
        <div className="p-3">
          <EmptyState title="Keine Regeln in dieser Ansicht.">
            Sagt Sir „ab sofort immer …“, legt Jarvis daraus eine Regel an. Alte Präferenz-Notizen lassen sich übernehmen.
          </EmptyState>
          <Button size="sm" variant="outline" className="mt-2" busy={act.busy === "migrate"} icon={<Wand2 className="size-3.5" />} onClick={() => void migrate()}>
            Präferenzen als Regeln übernehmen
          </Button>
        </div>
      )}
      <ul className="divide-y divide-seam">
        {shown.map((r) => {
          const state = ruleState(r);
          return (
            <li key={r.id} className="flex flex-col gap-1.5 px-3 py-2 text-sm" data-testid="rule">
              <div className="flex flex-wrap items-center gap-2">
                <span className={r.in_force ? "text-ink" : "text-ink-dim line-through"}>{r.title}</span>
                <Tag tone={r.scope === "project" ? "info" : "idle"}>{scopeLabel(r)}</Tag>
                {state && <Tag tone="warn">{state}</Tag>}
                <span className="ml-auto flex items-center gap-2">
                  <button
                    type="button"
                    aria-label={`Regel bearbeiten: ${r.title}`}
                    className="text-ink-dim hover:text-arc"
                    onClick={() => setDraft({ id: r.id, title: r.title, text: r.text, scope: r.scope, project: r.project })}
                  >
                    <Pencil className="size-3.5" />
                  </button>
                  {!r.superseded && (
                    <Toggle label={`Regel „${r.title}“ ${r.active ? "deaktivieren" : "aktivieren"}`} checked={r.active} disabled={act.busy === `toggle:${r.id}`} onChange={(v) => void toggle(r, v)} />
                  )}
                </span>
              </div>
              <p className="selectable whitespace-pre-wrap text-xs text-ink-dim">{r.text}</p>
              {r.note && <p className="text-xs text-ink-faint">{r.note}</p>}
            </li>
          );
        })}
      </ul>
      {draft && (
        <form
          className="flex flex-col gap-2 border-t border-seam p-3"
          aria-label={draft.id ? "Regel bearbeiten" : "Neue Regel"}
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <Field label="Titel">
            {(id) => <input id={id} className="field" value={draft.title} maxLength={200} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />}
          </Field>
          <Field label="Regel (als Anweisung an Jarvis)">
            {(id) => <textarea id={id} className="field min-h-20" value={draft.text} maxLength={4000} onChange={(e) => setDraft({ ...draft, text: e.target.value })} />}
          </Field>
          <Field label="Geltungsbereich">
            {(id) => (
              <select id={id} className="field" value={draft.scope} onChange={(e) => setDraft({ ...draft, scope: e.target.value as "global" | "project" })}>
                <option value="global">Global (jeder Lauf)</option>
                <option value="project">Nur für ein Projekt</option>
              </select>
            )}
          </Field>
          {draft.scope === "project" && (
            <Field label="Projekt (Name, wie er in Pfad oder Auftrag vorkommt)">
              {(id) => <input id={id} className="field" value={draft.project} maxLength={120} onChange={(e) => setDraft({ ...draft, project: e.target.value })} />}
            </Field>
          )}
          <div className="flex gap-2">
            <Button size="sm" variant="primary" type="submit" disabled={!valid} busy={act.busy === "save"}>Speichern</Button>
            <Button size="sm" variant="ghost" onClick={() => setDraft(null)}>Abbrechen</Button>
          </div>
        </form>
      )}
    </Section>
  );
}
