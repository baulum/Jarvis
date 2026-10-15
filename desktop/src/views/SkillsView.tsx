import { useMemo, useState } from "react";
import { EmptyState, ErrorState, RiskBadge, Section, Skeleton, Tabs, Tag } from "../components/ui";
import { useQuery } from "../hooks/useQuery";
import { api } from "../store/app";

type Tab = "playbooks" | "tools" | "hermes";

export function SkillsView() {
  const [tab, setTab] = useState<Tab>("playbooks");
  const [q, setQ] = useState("");
  const skills = useQuery(() => api.skills(), "skills");
  const tools = useQuery(() => api.tools(), "tools", { topics: ["connector"] });
  const playbooks = useQuery(() => api.playbooks(), "playbooks");
  const needle = q.trim().toLowerCase();
  const match = (...s: (string | undefined)[]) => !needle || s.some((x) => x?.toLowerCase().includes(needle));

  const toolGroups = useMemo(() => {
    const groups: Record<string, NonNullable<typeof tools.data>> = {};
    for (const t of tools.data ?? []) if (match(t.name, t.description)) (groups[t.connector] ??= []).push(t);
    return Object.entries(groups).sort(([a], [b]) => a.localeCompare(b));
  }, [tools.data, needle]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-4 border-b border-seam px-8 py-2.5">
        <Tabs value={tab} onChange={setTab} items={[
          { id: "playbooks", label: "Playbooks", count: playbooks.data?.length },
          { id: "tools", label: "Werkzeuge", count: tools.data?.length },
          { id: "hermes", label: "Hermes-Skills", count: skills.data?.hermes.length },
        ]} />
        <input className="field ml-auto w-64" placeholder="Suchen …" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Suchen" />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-[64rem] flex-col gap-4 px-8 py-6">
          {tab === "playbooks" && (
            playbooks.loading ? <Skeleton /> : playbooks.error ? <ErrorState message={playbooks.error} onRetry={playbooks.refetch} /> :
            (playbooks.data ?? []).filter((p) => match(p.title, p.description)).map((p) => (
              <article key={p.name} className="plate p-4">
                <div className="flex items-baseline gap-3"><h3 className="type-title text-ink">{p.title}</h3><span className="type-data text-xs text-ink-faint">{p.name}</span></div>
                <p className="mt-1 text-sm text-ink-dim">{p.description}</p>
                <ol className="mt-3 grid gap-1 text-xs text-ink-dim sm:grid-cols-2">
                  {p.steps.map((s, i) => <li key={s}><span className="type-data mr-2 text-arc-dim">{String(i + 1).padStart(2, "0")}</span>{s}</li>)}
                </ol>
              </article>
            ))
          )}
          {tab === "tools" && (
            tools.loading ? <Skeleton /> : tools.error ? <ErrorState message={tools.error} onRetry={tools.refetch} /> :
            toolGroups.map(([connector, list]) => (
              <Section key={connector} title={connector} meta={`${list.length} Werkzeuge`}>
                <ul className="divide-y divide-seam">
                  {list.map((t) => (
                    <li key={t.name} className={`flex items-start gap-3 px-4 py-2.5 text-sm ${t.enabled ? "" : "opacity-50"}`}>
                      <RiskBadge risk={t.risk} />
                      <div className="min-w-0 flex-1">
                        <div className="type-data text-ink">{t.name}</div>
                        <div className="text-xs text-ink-dim">{t.description}</div>
                      </div>
                      {!t.enabled && <Tag>inaktiv</Tag>}
                      {t.untrusted_output && <Tag tone="warn">externe Inhalte</Tag>}
                    </li>
                  ))}
                </ul>
              </Section>
            ))
          )}
          {tab === "hermes" && (
            skills.loading ? <Skeleton /> : skills.error ? <ErrorState message={skills.error} onRetry={skills.refetch} /> :
            (skills.data?.hermes.length ?? 0) === 0 ? <EmptyState title="Keine Hermes-Skills gefunden." /> : (
              <ul className="grid gap-2 sm:grid-cols-2">
                {skills.data!.hermes.filter((s) => match(String(s.name ?? ""), String(s.description ?? ""))).map((s) => (
                  <li key={String(s.name)} className="plate p-3">
                    <div className="flex items-baseline gap-2"><span className="type-data text-ink">{String(s.name)}</span>
                      {s.category ? <span className="text-xs text-ink-faint">{String(s.category)}</span> : null}</div>
                    <p className="mt-1 text-xs text-ink-dim">{String(s.description ?? "")}</p>
                  </li>
                ))}
              </ul>
            )
          )}
        </div>
      </div>
    </div>
  );
}
