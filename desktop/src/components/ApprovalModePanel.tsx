import { useMemo, useState } from "react";
import { useAction, useQuery } from "../hooks/useQuery";
import type { ApprovalMode, ApprovalModeName, RiskLevel } from "../lib/types";
import { api } from "../store/app";
import { RiskBadge, Section, Tag, Toggle } from "./ui";

const MODES: { id: ApprovalModeName; title: string; text: string; tone?: "warn" }[] = [
  { id: "yolo", title: "YOLO", text: "Nichts muss freigegeben werden. Jarvis handelt sofort.", tone: "warn" },
  { id: "block_dangerous", title: "Gefährliches blockieren", text: "Lesen und kleine Änderungen sofort; Senden, Tippen, Shell, Geld und Löschen nur nach Freigabe." },
  { id: "block_everything", title: "Alles blockieren", text: "Nur Lesen läuft direkt. Jede Aktion mit Auswirkung wartet auf deine Freigabe." },
  { id: "custom", title: "Eigene Regeln", text: "Pro Risikostufe und pro Werkzeug selbst festlegen." },
];
const LEVELS: { id: RiskLevel; label: string }[] = [
  { id: "low", label: "Niedrig – lokal, umkehrbar (App öffnen, Notiz)" },
  { id: "medium", label: "Mittel – extern, noch unsichtbar (Entwurf, Termin, Kontakt)" },
  { id: "high", label: "Hoch – für Dritte sichtbar (Mail senden, Tippen, Shell, Push)" },
  { id: "critical", label: "Kritisch – Geld, Löschen, Zugangsdaten" },
];
type Override = "auto" | "ask" | "deny";

function Segmented<T extends string>({ value, options, onChange, disabled }: {
  value: T; options: { id: T; label: string }[]; onChange: (v: T) => void; disabled?: boolean;
}) {
  return (
    <div className="inline-flex shrink-0 rounded-md border border-seam-hi p-0.5" role="radiogroup">
      {options.map((o) => (
        <button key={o.id} type="button" role="radio" aria-checked={value === o.id} disabled={disabled}
                onClick={() => onChange(o.id)}
                className={`rounded px-2 py-0.5 text-xs transition-colors disabled:opacity-40 ${value === o.id ? "bg-arc-deep text-arc" : "text-ink-dim hover:text-ink"}`}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function ApprovalModePanel() {
  const q = useQuery(() => api.approvalMode(), "approval-mode", { topics: ["approval"] });
  const tools = useQuery(() => api.tools(), "tools");
  const act = useAction();
  const [needle, setNeedle] = useState("");
  const m = q.data;

  const save = (body: Partial<ApprovalMode>, msg?: string) =>
    void act.run("mode", () => api.setApprovalMode(body), msg).then(() => q.refetch());

  const toolList = useMemo(() => {
    const n = needle.trim().toLowerCase();
    return (tools.data ?? [])
      .filter((t) => t.risk !== "read")
      .filter((t) => (n ? t.name.toLowerCase().includes(n) || t.description.toLowerCase().includes(n) : m && t.name in m.tool_overrides))
      .slice(0, 60);
  }, [tools.data, needle, m]);

  if (!m) return null;
  const busy = act.busy !== null;

  return (
    <Section title="Freigabe-Modus" meta="gilt für Chat, Stimme, Telegram, Telefon und geplante Aufgaben">
      <div className="flex flex-col gap-4 p-4">
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4" role="radiogroup" aria-label="Freigabe-Modus">
          {MODES.map((o) => (
            <button key={o.id} type="button" role="radio" aria-checked={m.mode === o.id} disabled={busy}
                    onClick={() => m.mode !== o.id && save({ mode: o.id }, `Freigabe-Modus: ${o.title}`)}
                    className={`flex flex-col gap-1 rounded-lg border p-3 text-left transition-colors disabled:opacity-60 ${m.mode === o.id ? "border-arc-dim bg-arc-deep/40" : "border-seam hover:border-seam-hi"}`}>
              <span className="flex items-center gap-2">
                <span className={`type-title text-sm ${m.mode === o.id ? "text-arc" : "text-ink"}`}>{o.title}</span>
                {o.tone === "warn" && <Tag tone="warn">riskant</Tag>}
              </span>
              <span className="text-xs text-ink-dim">{o.text}</span>
            </button>
          ))}
        </div>

        {m.mode === "custom" && (
          <div className="flex flex-col gap-4 border-t border-seam pt-4">
            <ul className="flex flex-col gap-2">
              {LEVELS.map((l) => (
                <li key={l.id} className="flex items-center gap-3 text-sm">
                  <RiskBadge risk={l.id} />
                  <span className="min-w-0 flex-1 text-ink-dim">{l.label}</span>
                  <Segmented value={m.custom_levels[l.id]} disabled={busy}
                             options={[{ id: "auto", label: "Sofort" }, { id: "ask", label: "Fragen" }]}
                             onChange={(v) => save({ custom_levels: { ...m.custom_levels, [l.id]: v } })} />
                </li>
              ))}
            </ul>
            <label className="flex items-center gap-3 text-sm text-ink-dim">
              <Toggle label="Bei externen Inhalten immer fragen" checked={m.taint_strict} disabled={busy}
                      onChange={(v) => save({ taint_strict: v })} />
              Nach Webseiten, E-Mails oder Dokumenten in der Sitzung jede Aktion mit Auswirkung erst freigeben (Schutz vor Prompt-Injection)
            </label>
            <div className="flex flex-col gap-2">
              <div className="flex items-center gap-3">
                <h3 className="type-title flex-1 text-sm text-ink">Ausnahmen pro Werkzeug</h3>
                <input className="field w-64" placeholder="Werkzeug suchen …" value={needle}
                       onChange={(e) => setNeedle(e.target.value)} aria-label="Werkzeug suchen" />
              </div>
              {toolList.length === 0 && <p className="text-xs text-ink-faint">{needle ? "Kein Werkzeug gefunden." : "Keine Ausnahmen. Suche ein Werkzeug, um eine festzulegen."}</p>}
              <ul className="divide-y divide-seam">
                {toolList.map((t) => {
                  const cur = (m.tool_overrides[t.name] ?? "default") as Override | "default";
                  return (
                    <li key={t.name} className="flex items-center gap-3 py-2 text-sm">
                      <RiskBadge risk={t.risk} />
                      <div className="min-w-0 flex-1">
                        <div className="type-data truncate text-ink">{t.name}</div>
                        <div className="truncate text-xs text-ink-dim">{t.description}</div>
                      </div>
                      <Segmented value={cur} disabled={busy}
                                 options={[{ id: "default", label: "Stufe" }, { id: "auto", label: "Sofort" }, { id: "ask", label: "Fragen" }, { id: "deny", label: "Sperren" }]}
                                 onChange={(v) => {
                                   const next = { ...m.tool_overrides };
                                   if (v === "default") delete next[t.name];
                                   else next[t.name] = v;
                                   save({ tool_overrides: next });
                                 }} />
                    </li>
                  );
                })}
              </ul>
            </div>
          </div>
        )}

        <p className="text-xs text-ink-faint">
          Unabhängig vom Modus gelten immer: Not-Aus, die Rechte jeder Rolle (z. B. darf ein Anrufer ohne PIN nur lesen),
          Rate-Limits und das Audit-Protokoll. Kritische Aktionen in Sitzungen mit externen Inhalten werden auch bei YOLO erst
          freigegeben.
        </p>
      </div>
    </Section>
  );
}
