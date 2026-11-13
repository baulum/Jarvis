// Kundensuche: the PJE-Sales-AI Lead-Tool inside Jarvis – overview, leads with filters and the lead file, campaigns,
// approval queue with drafts, outbox. Reading comes from the Core (/api/sales, over the Lead-Tool's MCP server).
// „Neu formulieren“ and „Kampagne starten“ go through the Core's approvals; approving and sending a draft stay in the
// Lead-Tool itself – this view only links there.
import { ExternalLink, Inbox, PenLine, Plus, RefreshCw, Search, Users } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Button, Dialog, EmptyState, ErrorState, Field, KeyValue, Section, Skeleton, StatusText, Tabs, Tag } from "../components/ui";
import { useAction, useQuery } from "../hooks/useQuery";
import { formatDateTime, formatMs, formatRelative, formatUsd } from "../lib/format";
import { openExternal } from "../lib/native";
import {
  actionMessage,
  campaignTone,
  channelLabel,
  formatScore,
  leadToolLink,
  newRequestId,
  orderedCounts,
  sourceLabel,
  stageTone,
  type CampaignInput,
  type SalesCampaign,
  type SalesLead,
  type SalesStatus,
  type SalesStatusOption,
} from "../lib/sales";
import { api, useApp } from "../store/app";

type Tab = "overview" | "leads" | "campaigns" | "review" | "outbox";
const PAGE = 50;

export function SalesView() {
  const status = useQuery(() => api.sales.status(), "sales-status", { topics: ["approval"], pollMs: 30_000 });
  const [tab, setTab] = useState<Tab>("overview");
  const [filter, setFilter] = useState<{ campaign: string; status: string }>({ campaign: "", status: "" });
  const [creating, setCreating] = useState(false);
  const remote = useApp((s) => s.remote);
  const st = status.data;
  const ov = st?.overview;
  const link = leadToolLink(st?.web_url, remote);

  const showLeads = (campaign: string, leadStatus = "") => {
    setFilter({ campaign, status: leadStatus });
    setTab("leads");
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-seam px-6 py-2.5">
        <Tabs<Tab>
          value={tab}
          onChange={setTab}
          items={[
            { id: "overview", label: "Übersicht" },
            { id: "leads", label: "Leads" },
            { id: "campaigns", label: "Kampagnen" },
            { id: "review", label: "Freigabe", count: ov ? ov.review : undefined },
            { id: "outbox", label: "Postausgang", count: ov ? ov.outbox_preview : undefined },
          ]}
        />
        <div className="flex-1" />
        {st && <ConnectionState st={st} />}
        {st?.capabilities.create_campaign && (
          <Button size="sm" variant="ghost" icon={<Plus className="size-3.5" />} onClick={() => setCreating(true)}>
            Kampagne starten
          </Button>
        )}
        {link && (
          <Button size="sm" variant="ghost" icon={<ExternalLink className="size-3.5" />} onClick={() => void openExternal(link)}>
            Lead-Tool
          </Button>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {status.loading && <div className="p-8"><Skeleton rows={6} /></div>}
        {status.error && <div className="p-8"><ErrorState message={status.error} onRetry={status.refetch} /></div>}
        {st && !st.connected && <NotConnected st={st} onRetry={status.refetch} />}
        {st?.connected && (
          <>
            {st.error && (
              <p role="alert" className="mx-6 mt-5 rounded-md border border-alarm/40 bg-alarm-deep/40 px-3 py-2 text-sm text-ink">{st.error}</p>
            )}
            {tab === "overview" && <Overview st={st} onShowLeads={showLeads} />}
            {tab === "leads" && <Leads st={st} filter={filter} setFilter={setFilter} />}
            {tab === "campaigns" && <Campaigns onShowLeads={showLeads} />}
            {tab === "review" && <Review st={st} />}
            {tab === "outbox" && <Outbox st={st} />}
          </>
        )}
      </div>
      {creating && <CampaignDialog onClose={() => setCreating(false)} onDone={() => void status.refetch()} />}
    </div>
  );
}

function ConnectionState({ st }: { st: SalesStatus }) {
  if (!st.connected) return <StatusText tone="bad">Lead-Tool getrennt</StatusText>;
  const workers = st.overview?.workers ?? [];
  if (workers.length === 0) return <StatusText tone="idle">Lead-Tool verbunden</StatusText>;
  const online = workers.some((w) => w.online);
  return <StatusText tone={online ? "ok" : "warn"}>{online ? "Worker läuft" : "Worker offline"}</StatusText>;
}

function NotConnected({ st, onRetry }: { st: SalesStatus; onRetry: () => void }) {
  return (
    <div className="mx-auto max-w-[48rem] px-6 py-6">
      <EmptyState
        icon={<Users className="size-6" />}
        title={st.configured && st.enabled ? "Das Lead-Tool ist gerade nicht erreichbar." : "Das Lead-Tool ist nicht eingerichtet."}
        action={<Button icon={<RefreshCw className="size-3.5" />} onClick={onRetry}>Erneut prüfen</Button>}
      >
        <p>{st.error}</p>
        <p className="mt-2">
          Jarvis liest Leads und Kampagnen über die Schnittstelle des Lead-Tools auf dem Mac (Eintrag <span className="type-data">pje_sales_ai</span> in
          <span className="type-data"> config/mcp_servers.yaml</span>). Läuft die Datenbank des Lead-Tools wieder, verbindet ein Neustart des Cores neu.
        </p>
      </EmptyState>
    </div>
  );
}

// ------------------------------------------------------------------ overview
function Overview({ st, onShowLeads }: { st: SalesStatus; onShowLeads: (campaign: string, status?: string) => void }) {
  const q = useQuery(() => api.sales.analytics(), "sales-analytics", { topics: ["approval"] });
  if (q.loading) return <div className="p-8"><Skeleton rows={6} /></div>;
  if (q.error) return <div className="p-8"><ErrorState message={q.error} onRetry={q.refetch} /></div>;
  const a = q.data!;
  if (a.lead_total === 0) {
    return (
      <div className="mx-auto max-w-[60rem] px-6 py-5">
        <EmptyState icon={<Search className="size-6" />} title="Noch keine Leads.">
          Leads entstehen aus Kampagnen: Region und Branche wählen, das Lead-Tool sucht Betriebe, prüft deren Websites und bereitet Entwürfe zur Freigabe vor.
          {st.capabilities.create_campaign ? " Oben rechts startest du eine Kampagne." : ""}
        </EmptyState>
      </div>
    );
  }
  const max = Math.max(1, ...a.stages.map((s) => s.count));
  const active = st.overview?.campaigns_by_status ?? {};
  const running = (active.created ?? 0) + (active.discovering ?? 0) + (active.processing ?? 0);
  return (
    <div className="mx-auto flex max-w-[60rem] flex-col gap-4 px-6 py-5">
      <dl className="plate grid grid-cols-2 divide-seam sm:grid-cols-5 sm:divide-x">
        <Figure label="Leads" value={a.lead_total} />
        <Figure label="Warten auf Freigabe" value={a.tasks?.review ?? 0} tone={a.tasks?.review ? "warn" : undefined} />
        <Figure label="Im Postausgang" value={a.tasks?.outbox_preview ?? 0} />
        <Figure label="Antworten" value={a.tasks?.inbox ?? 0} />
        <Figure label="Kampagnen aktiv" value={running} hint={`von ${a.campaigns.total}`} />
      </dl>

      <Section title="Stand je Station" meta="wo die Leads gerade stehen">
        <ul className="flex flex-col gap-1.5 px-4 py-3">
          {a.stages.map((s) => (
            <li key={s.key} className="grid grid-cols-[10rem_1fr_3.5rem] items-center gap-3 text-[0.8125rem]">
              <span className="truncate text-ink-dim">{s.label}</span>
              <span className="h-1.5 rounded-full bg-plate-hi" aria-hidden>
                <span
                  className={`block h-full rounded-full ${s.key === "review" ? "bg-ember" : s.key === "contact" ? "bg-arc" : "bg-arc-dim"}`}
                  style={{ width: `${(s.count / max) * 100}%` }}
                />
              </span>
              <span className="type-data text-right text-ink">{s.count}</span>
            </li>
          ))}
          <li className="mt-1.5 flex gap-5 border-t border-seam pt-2.5 text-xs text-ink-dim">
            <span>Aussortiert <span className="type-data text-ink">{a.off_track.out}</span></span>
            <span>Mit Fehler <span className={`type-data ${a.off_track.error ? "text-alarm" : "text-ink"}`}>{a.off_track.error}</span></span>
          </li>
        </ul>
      </Section>

      <Section title="Kampagnen" meta={a.basis}>
        <ul className="divide-y divide-seam">
          {a.per_campaign.map((c) => (
            <li key={c.id}>
              <button type="button" onClick={() => onShowLeads(c.id)} className="flex w-full items-center gap-4 px-4 py-2.5 text-left hover:bg-plate-hi">
                <span className="min-w-0 flex-1 truncate text-[0.8125rem] text-ink">{c.name ?? "Ohne Namen"}</span>
                <span className="w-24 text-xs text-ink-dim">{c.status_label}</span>
                <span className="type-data w-16 text-right text-ink-dim" title="Leads">{c.leads}</span>
                <span className={`type-data w-16 text-right ${c.in_review ? "text-ember" : "text-ink-faint"}`} title="Warten auf Freigabe">{c.in_review}</span>
                <span className={`type-data w-16 text-right ${c.errors ? "text-alarm" : "text-ink-faint"}`} title="Mit Fehler">{c.errors}</span>
              </button>
            </li>
          ))}
        </ul>
        <p className="border-t border-seam px-4 py-2 text-2xs text-ink-faint">Spalten: Leads · warten auf Freigabe · mit Fehler. Kosten und Website-Befunde zeigt die Analyse im Lead-Tool.</p>
      </Section>
    </div>
  );
}

function Figure({ label, value, hint, tone }: { label: string; value: number; hint?: string; tone?: "warn" }) {
  return (
    <div className="flex flex-col gap-1 px-4 py-3">
      <dt className="text-xs text-ink-dim">{label}</dt>
      <dd className="flex items-baseline gap-1.5">
        <span className={`type-data-lg ${tone === "warn" ? "text-ember" : "text-ink"}`}>{value}</span>
        {hint && <span className="text-2xs text-ink-faint">{hint}</span>}
      </dd>
    </div>
  );
}

// ------------------------------------------------------------------ leads
function Leads({
  st,
  filter,
  setFilter,
}: {
  st: SalesStatus;
  filter: { campaign: string; status: string };
  setFilter: (f: { campaign: string; status: string }) => void;
}) {
  const [pages, setPages] = useState(1);
  const [selected, setSelected] = useState<string | null>(null);
  const statuses = useQuery(() => api.sales.statuses(), "sales-statuses");
  const campaigns = useQuery(() => api.sales.campaigns(), "sales-campaigns");
  const key = `${filter.campaign}|${filter.status}|${pages}`;
  const q = useQuery(
    () => api.sales.leads({ campaign_id: filter.campaign || undefined, status: filter.status || undefined, limit: PAGE * pages }),
    `sales-leads-${key}`,
    { topics: ["approval"] },
  );
  const change = (f: { campaign: string; status: string }) => {
    setPages(1);
    setSelected(null);
    setFilter(f);
  };
  const page = q.data;
  return (
    <div className="grid min-h-full grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      <div className="flex min-w-0 flex-col gap-3 border-seam px-6 py-5 lg:border-r">
        <div className="flex flex-wrap gap-2">
          <select aria-label="Nach Status filtern" className="field h-8 w-auto min-w-0 flex-1" value={filter.status} onChange={(e) => change({ ...filter, status: e.target.value })}>
            <option value="">Alle Status</option>
            {statuses.data?.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
          <select aria-label="Nach Kampagne filtern" className="field h-8 w-auto min-w-0 flex-1" value={filter.campaign} onChange={(e) => change({ ...filter, campaign: e.target.value })}>
            <option value="">Alle Kampagnen</option>
            {campaigns.data?.map((c) => <option key={c.id} value={c.id}>{c.name ?? c.id}</option>)}
          </select>
        </div>
        {q.loading && <Skeleton rows={8} />}
        {q.error && <ErrorState message={q.error} onRetry={q.refetch} />}
        {page && (
          <>
            <p className="text-xs text-ink-faint" aria-live="polite">{page.total === 1 ? "1 Lead" : `${page.total} Leads`}{filter.status || filter.campaign ? " für diesen Filter" : ""}</p>
            {page.leads.length === 0 ? (
              <EmptyState title="Keine Leads für diesen Filter.">
                {filter.status || filter.campaign ? (
                  <Button size="sm" variant="ghost" onClick={() => change({ campaign: "", status: "" })}>Filter zurücksetzen</Button>
                ) : "Sobald eine Kampagne Betriebe gefunden hat, erscheinen sie hier."}
              </EmptyState>
            ) : (
              <ul className="plate divide-y divide-seam">
                {page.leads.map((l) => <LeadRow key={l.id} lead={l} active={l.id === selected} onSelect={() => setSelected(l.id)} />)}
              </ul>
            )}
            {page.more > 0 && (
              <div><Button size="sm" busy={q.refreshing} onClick={() => setPages(pages + 1)}>Weitere {Math.min(PAGE, page.more)} laden</Button></div>
            )}
          </>
        )}
      </div>
      <div className="min-w-0 px-6 py-5">
        {selected ? <LeadFile id={selected} st={st} /> : (
          <p className="pt-10 text-center text-sm text-ink-faint">Lead wählen, um Akte, Entwurf und Belege zu sehen.</p>
        )}
      </div>
    </div>
  );
}

function LeadRow({ lead, active, onSelect }: { lead: SalesLead; active: boolean; onSelect: () => void }) {
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={active ? "true" : undefined}
        className={`flex w-full items-center gap-3 px-3.5 py-2.5 text-left transition-colors ${active ? "bg-plate-hi" : "hover:bg-plate-hi/60"}`}
      >
        <div className="min-w-0 flex-1">
          <p className="truncate text-[0.8125rem] text-ink">{lead.name ?? "Ohne Namen"}</p>
          <p className="truncate text-xs text-ink-faint">{[lead.campaign_name, lead.address].filter(Boolean).join(" · ")}</p>
        </div>
        <StatusText tone={stageTone(lead.stage)}>{lead.status_label}</StatusText>
        <span className="type-data w-14 text-right text-ink-dim" title="Score · Priorität">{formatScore(lead.score, lead.priority)}</span>
      </button>
    </li>
  );
}

function LeadFile({ id, st }: { id: string; st: SalesStatus }) {
  const q = useQuery(() => api.sales.lead(id), `sales-lead-${id}`, { topics: ["approval"] });
  const remote = useApp((s) => s.remote);
  const [redraft, setRedraft] = useState(false);
  if (q.loading) return <Skeleton rows={10} />;
  if (q.error) return <ErrorState message={q.error} onRetry={q.refetch} />;
  const d = q.data!;
  const toLeadTool = leadToolLink(st.web_url, remote, d.status === "AWAITING_APPROVAL" ? `/review?lead=${d.id}` : `/leads/${d.id}`);
  const link = (href: string | null, label?: string): ReactNode =>
    href ? <button type="button" className="text-arc hover:underline" onClick={() => void openExternal(href)}>{label ?? href}</button> : "–";
  const evidence = new Set(d.draft?.evidence_ids ?? []);
  return (
    <article className="flex flex-col gap-4" aria-label={`Lead-Akte ${d.company.name ?? ""}`}>
      <header className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="type-title truncate text-[1rem] text-ink">{d.company.name ?? "Ohne Namen"}</h3>
          <p className="mt-0.5 text-xs text-ink-faint">{d.campaign_name}</p>
        </div>
        <StatusText tone={stageTone(d.stage)}>{d.status_label}</StatusText>
      </header>
      {d.last_error && <p className="rounded-md border border-alarm/40 bg-alarm-deep/40 px-3 py-2 text-xs text-ink">{d.last_error}</p>}

      <KeyValue
        rows={[
          ["Branche", d.company.industry ?? "–"],
          ["Website", link(d.company.website)],
          ["E-Mail", d.company.email ? link(`mailto:${d.company.email}`, d.company.email) : "–"],
          ["Telefon", d.company.phone ? <span className="type-data">{d.company.phone}</span> : "–"],
          ["Adresse", d.company.address || "–"],
          ["Score", d.score ? <span className="type-data">{formatScore(d.score.value, d.score.priority)}{d.score.version ? ` · Regeln ${d.score.version}` : ""}</span> : "noch nicht bewertet"],
        ]}
      />

      <Section
        title="Entwurf"
        meta={d.draft ? `${channelLabel(d.draft.channel)} · Runde ${d.draft.iteration ?? 1}` : undefined}
        actions={
          <>
            {d.can_redraft && st.capabilities.redraft && (
              <Button size="sm" variant="ghost" icon={<PenLine className="size-3.5" />} onClick={() => setRedraft(true)}>Neu formulieren</Button>
            )}
            {toLeadTool && d.status === "AWAITING_APPROVAL" && (
              <Button size="sm" variant="primary" icon={<ExternalLink className="size-3.5" />} onClick={() => void openExternal(toLeadTool)}>Im Lead-Tool freigeben</Button>
            )}
          </>
        }
      >
        {d.draft ? (
          <div className="flex flex-col gap-2 px-4 py-3">
            {d.draft.subject && <p className="text-[0.8125rem] text-ink">{d.draft.subject}</p>}
            <p className="selectable max-w-[70ch] text-[0.8125rem] leading-relaxed whitespace-pre-wrap text-ink-dim">{d.draft.body}</p>
          </div>
        ) : (
          <p className="px-4 py-3 text-sm text-ink-dim">Für diesen Lead gibt es noch keinen ausgewählten Entwurf.</p>
        )}
        {d.status === "AWAITING_APPROVAL" && (
          <p className="border-t border-seam px-4 py-2 text-2xs text-ink-faint">
            Freigeben und Versenden passieren im Lead-Tool, mit dessen Prüfungen (Sperrliste, Abstand, nur Entwurf bis zur rechtlichen Prüfung).
            {!toLeadTool && " Das Lead-Tool ist von diesem Gerät aus nicht erreichbar; bitte am Mac freigeben."}
          </p>
        )}
      </Section>

      {d.evidence.length > 0 && (
        <Section title="Belege" meta={`${d.evidence.length}`}>
          <ul className="divide-y divide-seam">
            {d.evidence.map((e, i) => (
              <li key={e.id ?? i} className="flex items-start gap-3 px-4 py-2 text-[0.8125rem]">
                <div className="min-w-0 flex-1">
                  <p className="text-ink">{e.claim}</p>
                  <p className="mt-0.5 truncate text-xs text-ink-faint">{[e.key, e.source_url].filter(Boolean).join(" · ")}</p>
                </div>
                {e.id && evidence.has(e.id) && <Tag tone="ok">im Entwurf</Tag>}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {d.history.length > 0 && (
        <Section title="Verlauf">
          <ol className="flex flex-col gap-1.5 px-4 py-3 text-xs">
            {d.history.map((h, i) => (
              <li key={i} className="flex gap-3">
                <span className="type-data w-28 shrink-0 text-ink-faint" title={formatDateTime(h.at)}>{formatRelative(h.at)}</span>
                <span className="min-w-0 text-ink-dim">
                  <span className="text-ink">{h.to_label}</span>
                  {h.reason ? ` · ${h.reason}` : ""}
                </span>
              </li>
            ))}
          </ol>
        </Section>
      )}

      {redraft && <RedraftDialog leadId={d.id} name={d.company.name} onClose={() => setRedraft(false)} onDone={() => void q.refetch()} />}
    </article>
  );
}

function RedraftDialog({ leadId, name, onClose, onDone }: { leadId: string; name: string | null; onClose: () => void; onDone: () => void }) {
  const [note, setNote] = useState("");
  const [requestId] = useState(newRequestId);
  const act = useAction();
  const pushToast = useApp((s) => s.pushToast);
  const valid = note.trim().length >= 5;
  const submit = async () => {
    const r = await act.run("redraft", () => api.sales.redraft(leadId, note.trim(), requestId));
    if (!r) return;
    pushToast(actionMessage(r, "Neu formulieren eingereiht"));
    onDone();
    onClose();
  };
  return (
    <Dialog
      open
      onClose={onClose}
      title="Entwurf neu formulieren"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Abbrechen</Button>
          <Button variant="primary" busy={act.busy === "redraft"} disabled={!valid} onClick={() => void submit()}>Neu formulieren lassen</Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p>Der Texter des Lead-Tools schreibt den Entwurf für {name ?? "diesen Lead"} mit deiner Anweisung neu. Der neue Entwurf wartet danach wieder auf deine Freigabe.</p>
        <Field label="Anweisung" hint="5 bis 500 Zeichen, z. B. „kürzer und ohne Preisnennung“">
          {(fid) => (
            <textarea id={fid} data-autofocus className="field min-h-24 resize-y py-2" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
          )}
        </Field>
      </div>
    </Dialog>
  );
}

// ------------------------------------------------------------------ campaigns
function Campaigns({ onShowLeads }: { onShowLeads: (campaign: string, status?: string) => void }) {
  const q = useQuery(() => api.sales.campaigns(), "sales-campaigns", { topics: ["approval"], pollMs: 30_000 });
  const statuses = useQuery(() => api.sales.statuses(), "sales-statuses");
  const [open, setOpen] = useState<string | null>(null);
  if (q.loading) return <div className="p-8"><Skeleton rows={6} /></div>;
  if (q.error) return <div className="p-8"><ErrorState message={q.error} onRetry={q.refetch} /></div>;
  return (
    <div className="mx-auto max-w-[60rem] px-6 py-5">
      {q.data?.length === 0 ? (
        <EmptyState icon={<Search className="size-6" />} title="Noch keine Kampagne.">
          Eine Kampagne sucht Betriebe einer Branche in einer Region. Starten kannst du sie oben rechts oder im Lead-Tool.
        </EmptyState>
      ) : (
        <Section title="Kampagnen" meta="neueste zuerst">
          <ul className="divide-y divide-seam">
            {q.data?.map((c) => (
              <CampaignRow key={c.id} c={c} open={open === c.id} onToggle={() => setOpen(open === c.id ? null : c.id)} statuses={statuses.data ?? []} onShowLeads={onShowLeads} />
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}

function CampaignRow({
  c,
  open,
  onToggle,
  statuses,
  onShowLeads,
}: {
  c: SalesCampaign;
  open: boolean;
  onToggle: () => void;
  statuses: SalesStatusOption[];
  onShowLeads: (campaign: string, status?: string) => void;
}) {
  const counts = useMemo(() => orderedCounts(c.leads_by_status, statuses), [c.leads_by_status, statuses]);
  return (
    <li>
      <button type="button" onClick={onToggle} aria-expanded={open} className="flex w-full items-center gap-4 px-4 py-3 text-left hover:bg-plate-hi">
        <div className="min-w-0 flex-1">
          <p className="truncate text-[0.8125rem] text-ink">{c.name ?? "Ohne Namen"}</p>
          <p className="truncate text-xs text-ink-faint">{[c.industry, c.region, c.created_at ? formatDateTime(c.created_at) : null].filter(Boolean).join(" · ")}</p>
        </div>
        <StatusText tone={campaignTone(c)} pulse={c.active}>{c.status_label}</StatusText>
        <span className="type-data w-20 text-right text-ink-dim">{c.lead_count} Leads</span>
        <span className={`type-data w-24 text-right ${c.in_review ? "text-ember" : "text-ink-faint"}`}>{c.in_review} Freigabe</span>
      </button>
      {open && <CampaignDetail c={c} counts={counts} onShowLeads={onShowLeads} />}
    </li>
  );
}

function CampaignDetail({
  c,
  counts,
  onShowLeads,
}: {
  c: SalesCampaign;
  counts: ReturnType<typeof orderedCounts>;
  onShowLeads: (campaign: string, status?: string) => void;
}) {
  const q = useQuery(() => api.sales.campaign(c.id), `sales-campaign-${c.id}`);
  const d = q.data;
  return (
    <div className="flex flex-col gap-4 border-t border-seam bg-void/40 px-4 py-4">
      {c.last_error && <p className="rounded-md border border-alarm/40 bg-alarm-deep/40 px-3 py-2 text-xs text-ink">{c.last_error}</p>}
      {q.loading && <Skeleton rows={3} />}
      {q.error && <ErrorState message={q.error} onRetry={q.refetch} />}
      {d && (
        <KeyValue
          rows={[
            ["Kanal", channelLabel(d.channel)],
            ["Ziel", <span className="type-data">{d.target_count ?? "–"} Betriebe · Top {d.top_n ?? "–"}</span>],
            ["Quellen", d.sources.map(sourceLabel).join(", ") || "–"],
            ["KI-Kosten", <span className="type-data">{formatUsd(d.cost_usd)}{d.budget_usd !== null ? ` von ${formatUsd(d.budget_usd)}` : ""}</span>],
          ]}
        />
      )}
      {d && d.source_runs.length > 0 && (
        <table className="w-full text-left text-xs">
          <caption className="pb-1.5 text-left text-ink-dim">Quellenläufe</caption>
          <thead className="text-ink-faint">
            <tr><th className="py-1 font-normal">Quelle</th><th className="font-normal">Status</th><th className="text-right font-normal">Treffer</th><th className="text-right font-normal">Neu</th><th className="text-right font-normal">Dauer</th></tr>
          </thead>
          <tbody className="divide-y divide-seam">
            {d.source_runs.map((r, i) => (
              <tr key={i} title={r.error ?? undefined}>
                <td className="py-1.5 text-ink">{sourceLabel(r.source)}</td>
                <td className={r.status === "failed" ? "text-alarm" : "text-ink-dim"}>{r.status === "failed" ? `fehlgeschlagen${r.error ? `: ${r.error}` : ""}` : r.status === "running" ? "läuft" : "fertig"}</td>
                <td className="type-data text-right">{r.hits ?? "–"}</td>
                <td className="type-data text-right">{r.new_companies ?? "–"}</td>
                <td className="type-data text-right">{formatMs(r.duration_ms)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {counts.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {counts.map((s) => (
            <button key={s.id} type="button" onClick={() => onShowLeads(c.id, s.id)} className="rounded border border-seam-hi px-2 py-0.5 text-2xs text-ink-dim hover:border-ink-faint hover:text-ink">
              {s.label} <span className="type-data text-ink">{s.count}</span>
            </button>
          ))}
        </div>
      )}
      <div><Button size="sm" onClick={() => onShowLeads(c.id)}>Alle Leads dieser Kampagne</Button></div>
    </div>
  );
}

function CampaignDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [form, setForm] = useState<CampaignInput & { industry_label: string; budget: string }>({
    region: "", industry_key: "", industry_label: "", target_count: 50, select_top_n: 10, channel: "email", budget: "",
  });
  const [query, setQuery] = useState("");
  const [requestId] = useState(newRequestId);
  const act = useAction();
  const pushToast = useApp((s) => s.pushToast);
  const found = useQuery(() => api.sales.industries(query.trim()), `sales-industries-${query.trim()}`, { enabled: query.trim().length >= 2 });
  const set = <K extends keyof typeof form>(k: K, v: (typeof form)[K]) => setForm({ ...form, [k]: v });
  const valid = form.region.trim().length >= 2 && form.industry_key !== "";
  const submit = async () => {
    const budget = form.budget.trim() === "" ? undefined : Number(form.budget.replace(",", "."));
    const body: CampaignInput = {
      region: form.region.trim(), industry_key: form.industry_key, target_count: form.target_count,
      select_top_n: form.select_top_n, channel: form.channel, ...(budget !== undefined && !Number.isNaN(budget) ? { budget_usd: budget } : {}),
    };
    const r = await act.run("create", () => api.sales.createCampaign(body, requestId));
    if (!r) return;
    pushToast(actionMessage(r, "Lead-Suche eingereiht"));
    onDone();
    onClose();
  };
  return (
    <Dialog
      open
      onClose={onClose}
      title="Kampagne starten"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Abbrechen</Button>
          <Button variant="primary" busy={act.busy === "create"} disabled={!valid} onClick={() => void submit()}>Lead-Suche starten</Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <Field label="Region" hint="Stadt, Landkreis oder Stadtteil">
          {(fid) => <input id={fid} data-autofocus className="field" maxLength={120} value={form.region} onChange={(e) => set("region", e.target.value)} />}
        </Field>
        <Field label="Branche" hint={form.industry_key ? `Gewählt: ${form.industry_label}` : "Mindestens 2 Zeichen, dann aus der Liste wählen"}>
          {(fid) => <input id={fid} className="field" maxLength={60} value={query} onChange={(e) => setQuery(e.target.value)} />}
        </Field>
        {query.trim().length >= 2 && (
          <ul className="flex flex-wrap gap-1.5" aria-label="Gefundene Branchen">
            {found.data?.items.map((i) => (
              <li key={i.key}>
                <button
                  type="button"
                  onClick={() => setForm({ ...form, industry_key: i.key, industry_label: i.label })}
                  aria-pressed={form.industry_key === i.key}
                  className={`rounded border px-2 py-0.5 text-xs ${form.industry_key === i.key ? "border-arc-dim bg-arc-deep text-arc" : "border-seam-hi text-ink-dim hover:text-ink"}`}
                >
                  {i.label}
                </button>
              </li>
            ))}
            {found.data && !found.data.in_catalog && <li className="text-2xs text-ink-faint">{found.data.hint}</li>}
          </ul>
        )}
        <div className="grid grid-cols-2 gap-3">
          <Field label="Betriebe suchen">
            {(fid) => <input id={fid} type="number" min={1} max={500} className="field" value={form.target_count} onChange={(e) => set("target_count", Number(e.target.value))} />}
          </Field>
          <Field label="Davon Entwürfe (Top N)">
            {(fid) => <input id={fid} type="number" min={1} max={200} className="field" value={form.select_top_n} onChange={(e) => set("select_top_n", Number(e.target.value))} />}
          </Field>
          <Field label="Kanal">
            {(fid) => (
              <select id={fid} className="field" value={form.channel} onChange={(e) => set("channel", e.target.value as CampaignInput["channel"])}>
                <option value="email">E-Mail</option><option value="phone">Telefon</option><option value="letter">Brief</option>
              </select>
            )}
          </Field>
          <Field label="KI-Budget in USD" hint="leer = Standard des Lead-Tools">
            {(fid) => <input id={fid} inputMode="decimal" className="field" value={form.budget} onChange={(e) => set("budget", e.target.value)} />}
          </Field>
        </div>
        <p className="text-xs text-ember">Startet Abfragen bei OpenStreetMap und Google Maps und später KI-Kosten. Versendet wird nichts.</p>
      </div>
    </Dialog>
  );
}

// ------------------------------------------------------------------ review queue
function Review({ st }: { st: SalesStatus }) {
  const q = useQuery(() => api.sales.review(), "sales-review", { topics: ["approval"], pollMs: 30_000 });
  const [selected, setSelected] = useState<string | null>(null);
  if (q.loading) return <div className="p-8"><Skeleton rows={6} /></div>;
  if (q.error) return <div className="p-8"><ErrorState message={q.error} onRetry={q.refetch} /></div>;
  const items = q.data ?? [];
  if (items.length === 0) {
    return (
      <div className="mx-auto max-w-[60rem] px-6 py-5">
        <EmptyState icon={<Inbox className="size-6" />} title="Kein Entwurf wartet auf Freigabe.">
          Neue Entwürfe erscheinen hier, sobald das Lead-Tool qualifizierte Leads angeschrieben hat.
        </EmptyState>
      </div>
    );
  }
  const current = selected ?? items[0]!.id;
  return (
    <div className="grid min-h-full grid-cols-1 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
      <div className="px-6 py-5 lg:border-r lg:border-seam">
        <ul className="plate divide-y divide-seam" aria-label="Freigabe-Warteschlange">
          {items.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                onClick={() => setSelected(r.id)}
                aria-current={r.id === current ? "true" : undefined}
                className={`flex w-full items-center gap-3 px-3.5 py-2.5 text-left ${r.id === current ? "bg-plate-hi" : "hover:bg-plate-hi/60"}`}
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[0.8125rem] text-ink">{r.name ?? "Ohne Namen"}</p>
                  <p className="truncate text-xs text-ink-faint">{r.campaign_name} · seit {formatRelative(r.since)}</p>
                </div>
                <span className="type-data text-ink-dim" title="Score">{formatScore(r.score)}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div className="min-w-0 px-6 py-5"><LeadFile key={current} id={current} st={st} /></div>
    </div>
  );
}

// ------------------------------------------------------------------ outbox
function Outbox({ st }: { st: SalesStatus }) {
  const q = useQuery(() => api.sales.outbox(), "sales-outbox", { topics: ["approval"] });
  const remote = useApp((s) => s.remote);
  const link = leadToolLink(st.web_url, remote, "/outbox");
  if (q.loading) return <div className="p-8"><Skeleton rows={3} /></div>;
  if (q.error) return <div className="p-8"><ErrorState message={q.error} onRetry={q.refetch} /></div>;
  const o = q.data!;
  return (
    <div className="mx-auto max-w-[48rem] px-6 py-5">
      <Section
        title="Postausgang"
        meta={o.outreach_mode === "draft_only" ? "nur Entwürfe, kein automatischer Versand" : undefined}
        actions={link && <Button size="sm" variant="primary" icon={<ExternalLink className="size-3.5" />} onClick={() => void openExternal(link)}>Postausgang im Lead-Tool</Button>}
      >
        <div className="flex flex-col gap-2 px-4 py-4 text-sm">
          <p className="text-ink">
            <span className="type-data-lg mr-2">{o.preview_count}</span>
            {o.preview_count === 1 ? "Nachricht wartet" : "Nachrichten warten"} im Postausgang auf Prüfung und Versand.
          </p>
          <p className="max-w-[65ch] text-ink-dim">{o.reason}</p>
          {!link && <p className="text-xs text-ink-faint">Das Lead-Tool ist von diesem Gerät aus nicht erreichbar; bitte am Mac öffnen.</p>}
        </div>
      </Section>
    </div>
  );
}
