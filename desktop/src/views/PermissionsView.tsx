import { Dot, ErrorState, RiskBadge, Section, Skeleton, Tag, Button } from "../components/ui";
import { useAction, useQuery } from "../hooks/useQuery";
import { riskLabel } from "../lib/format";
import { api } from "../store/app";

const MAC_LABELS: Record<string, string> = {
  accessibility: "Bedienungshilfen (UI-Steuerung, Tippen)",
  screenRecording: "Bildschirmaufnahme (Screenshots)",
  microphone: "Mikrofon (Voice-Daemon)",
};

export function PermissionsView() {
  const q = useQuery(() => api.permissions(), "permissions", { topics: ["connector", "system"] });
  const act = useAction();
  if (q.loading) return <div className="p-8"><Skeleton rows={8} /></div>;
  if (q.error || !q.data) return <div className="p-8"><ErrorState message={q.error ?? "Keine Daten"} onRetry={q.refetch} /></div>;
  const p = q.data;
  const macos = p.macos;
  const macCore = !p.platform || p.platform === "mac"; // older cores do not report the platform: they are Macs

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-[72rem] flex-col gap-6 px-8 py-6">
        {macCore && <Section title="macOS-Berechtigungen" meta={`Bridge: ${macos.bridge ?? "unbekannt"}`}>
          <ul className="divide-y divide-seam">
            {Object.entries(MAC_LABELS).map(([k, label]) => {
              const v = macos[k];
              const granted = v === true || v === "granted";
              return (
                <li key={k} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                  <Dot tone={granted ? "ok" : v == null || v === "unknown" ? "idle" : "warn"} />
                  <span className="flex-1 text-ink">{label}</span>
                  <span className="text-xs text-ink-dim">{granted ? "erteilt" : v == null || v === "unknown" ? "unbekannt" : "fehlt"}</span>
                  {!granted && k !== "microphone" && macos.bridge === "ok" && (
                    <Button size="sm" variant="ghost" busy={act.busy === k}
                            onClick={() => void act.run(k, () => api.requestMacPermission(k as "accessibility" | "screenRecording")).then(q.refetch)}>Anfordern</Button>
                  )}
                </li>
              );
            })}
          </ul>
          <p className="px-4 pb-3 text-xs text-ink-faint">Rechte werden in Systemeinstellungen › Datenschutz & Sicherheit für „JarvisBridge“ bzw. das Terminal des Voice-Daemons erteilt.</p>
        </Section>}

        <Section title="Rollen" meta={p.taint_requires_approval ? "Externe Inhalte erzwingen Freigaben" : undefined}>
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase tracking-[0.12em] text-ink-faint">
              <tr><th className="px-4 py-2">Rolle</th><th>Max. Risiko</th><th>Automatisch bis</th><th>Darf freigeben</th><th className="pr-4">Beschreibung</th></tr>
            </thead>
            <tbody className="divide-y divide-seam">
              {Object.entries(p.roles).map(([name, r]) => (
                <tr key={name}>
                  <td className="type-data px-4 py-2 text-ink">{name}</td>
                  <td><RiskBadge risk={r.max_risk} /></td>
                  <td><RiskBadge risk={r.auto_approve} /></td>
                  <td>{r.can_approve ? <Tag tone="ok">ja</Tag> : <Tag>nein</Tag>}</td>
                  <td className="pr-4 text-xs text-ink-dim">{r.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>

        <div className="grid gap-6 lg:grid-cols-[2fr_1fr]">
          <Section title="Werkzeuge & Risikostufen" meta="Quelle: config/permissions.yaml + Connector-Manifeste">
            <ul className="max-h-[32rem] divide-y divide-seam overflow-y-auto">
              {p.tools.map((t) => (
                <li key={t.name} className={`flex items-center gap-3 px-4 py-1.5 text-sm ${t.enabled ? "" : "opacity-50"}`}
                    title={t.unavailable_reason ? `Auf diesem System nicht verfügbar: ${t.unavailable_reason}` : undefined}>
                  <RiskBadge risk={t.risk} />
                  <span className="type-data flex-1 truncate text-ink">{t.name}</span>
                  <span className="text-xs text-ink-faint">{t.unavailable_reason ? "nicht verfügbar" : t.auto_for_owner ? "automatisch" : `Freigabe (${riskLabel(t.risk)})`}</span>
                </li>
              ))}
            </ul>
          </Section>
          <div className="flex flex-col gap-6">
            <Section title="Shell-Allowlist">
              <div className="flex flex-wrap gap-1.5 p-4">{p.shell_allowlist.map((s) => <Tag key={s}>{s}</Tag>)}</div>
            </Section>
            <Section title="Rate-Limits">
              <ul className="divide-y divide-seam text-sm">
                {Object.entries(p.rate_limits).map(([k, v]) => (
                  <li key={k} className="flex justify-between px-4 py-1.5"><span className="text-ink-dim">{k}</span><span className="type-data text-ink">{v}</span></li>
                ))}
              </ul>
            </Section>
          </div>
        </div>
      </div>
    </div>
  );
}
