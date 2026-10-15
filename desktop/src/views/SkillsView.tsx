import { useMemo, useState } from "react";
import { EmptyState, ErrorState, RiskBadge, Section, Skeleton, Tabs, Tag, Toggle } from "../components/ui";
import { useAction, useQuery } from "../hooks/useQuery";
import type { ClaudeSkill } from "../lib/types";
import { api } from "../store/app";

type Tab = "claude" | "playbooks" | "tools";

export function SkillsView() {
  const [tab, setTab] = useState<Tab>("claude");
  const act = useAction();
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
          { id: "claude", label: "Claude-Skills", count: skills.data?.claude.filter((c) => c.enabled).length },
          { id: "playbooks", label: "Playbooks", count: playbooks.data?.length },
          { id: "tools", label: "Werkzeuge", count: tools.data?.length },
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
          {tab === "claude" && (
            skills.loading ? <Skeleton /> : skills.error ? <ErrorState message={skills.error} onRetry={skills.refetch} /> :
            (skills.data?.claude.length ?? 0) === 0 ? <EmptyState title="Keine globalen Claude-Skills gefunden." >Skills liegen in ~/.claude/skills oder in aktivierten Claude-Code-Plugins.</EmptyState> : (
              <>
                <p className="text-sm text-ink-dim">
                  Aktivierte Skills nutzt J.A.R.V.I.S. im Gespräch und bei Coding-Aufträgen (z. B. Website-Design). Änderungen gelten ab der nächsten Antwort.
                </p>
                {Object.entries(
                  skills.data!.claude.filter((c) => match(c.name, c.description)).reduce<Record<string, ClaudeSkill[]>>((acc, c) => {
                    (acc[c.source] ??= []).push(c);
                    return acc;
                  }, {}),
                ).map(([source, list]) => (
                  <Section key={source} title={source === "user" ? "Eigene Skills (~/.claude/skills)" : `Plugin ${source.replace("plugin:", "")}`}
                           meta={`${list.filter((c) => c.enabled).length}/${list.length} aktiv`}>
                    <ul className="divide-y divide-seam">
                      {list.map((c) => (
                        <li key={c.id} className="flex items-start gap-4 px-4 py-2.5 text-sm">
                          <div className="min-w-0 flex-1">
                            <div className="type-data text-ink">{c.name}</div>
                            <p className="line-clamp-2 text-xs text-ink-dim">{c.description}</p>
                          </div>
                          <Toggle label="Aktiv" checked={c.enabled} disabled={act.busy !== null}
                                  onChange={(v) => void act.run("skill", () => api.toggleClaudeSkill(c.id, v)).then(() => skills.refetch())} />
                        </li>
                      ))}
                    </ul>
                  </Section>
                ))}
              </>
            )
          )}
        </div>
      </div>
    </div>
  );
}
