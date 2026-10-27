import { Cpu, HardDrive, MemoryStick, Timer } from "lucide-react";
import type { ReactNode } from "react";
import { AppUpdateCard } from "../components/AppUpdateCard";
import { BackupCard } from "../components/BackupCard";
import { Dot, EmptyState, ErrorState, KeyValue, Section, Skeleton, Tag } from "../components/ui";
import {
  componentLabel,
  formatBytes,
  formatDuration,
  formatMs,
  formatNumber,
  formatPercent,
  formatRelative,
  healthLabel,
  healthTone,
} from "../lib/format";
import { useApp } from "../store/app";

function Gauge({ icon, label, value, ratio, detail }: { icon: ReactNode; label: string; value: string; ratio: number; detail?: string }) {
  const pct = Math.max(0, Math.min(1, ratio));
  const tone = pct > 0.9 ? "bg-alarm" : pct > 0.75 ? "bg-ember" : "bg-arc";
  return (
    <div className="plate brackets flex flex-col gap-3 p-4">
      <div className="flex items-center gap-2 text-xs uppercase tracking-[0.14em] text-ink-faint">
        {icon}
        {label}
      </div>
      <div className="type-data-lg text-ink">{value}</div>
      <div className="h-1 overflow-hidden rounded-full bg-seam">
        <div className={`h-full ${tone} transition-[width] duration-700`} style={{ width: `${pct * 100}%` }} />
      </div>
      {detail && <div className="text-xs text-ink-dim">{detail}</div>}
    </div>
  );
}

export function SystemView() {
  const status = useApp((s) => s.status);
  const error = useApp((s) => s.statusError);
  const refresh = useApp((s) => s.refreshStatus);

  if (!status && error) return <div className="p-8"><ErrorState message={error} onRetry={() => void refresh()} /></div>;
  if (!status) return <div className="p-8"><Skeleton rows={8} /></div>;
  const r = status.resources;
  const comps = Object.entries(status.components).sort(([a], [b]) => a.localeCompare(b));

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-[72rem] flex-col gap-6 px-8 py-6">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Gauge icon={<Timer className="size-3.5" />} label="Laufzeit" value={formatDuration(status.uptime_s)} ratio={0}
                 detail={`Core ${status.version} · Hermes-Neustarts ${status.hermes_restarts ?? 0}`} />
          {r ? (
            <>
              <Gauge icon={<Cpu className="size-3.5" />} label="CPU" value={formatPercent(r.cpu_percent)} ratio={r.cpu_percent / 100} />
              <Gauge icon={<MemoryStick className="size-3.5" />} label="Arbeitsspeicher" value={formatPercent(r.memory_percent)}
                     ratio={r.memory_percent / 100} detail={`${formatBytes(r.memory_used)} von ${formatBytes(r.memory_total)} · Core ${formatBytes(r.core_rss)}`} />
              <Gauge icon={<HardDrive className="size-3.5" />} label="Speicher frei" value={formatBytes(r.disk_free)}
                     ratio={1 - r.disk_free / r.disk_total} detail={`von ${formatBytes(r.disk_total)}`} />
            </>
          ) : (
            <div className="col-span-3 text-sm text-ink-dim">Ressourcendaten nicht verfügbar.</div>
          )}
        </div>

        <BackupCard />

        <AppUpdateCard />

        <div className="grid gap-6 lg:grid-cols-[1.2fr_1fr]">
          <Section title="Komponenten" meta={`${comps.filter(([, v]) => v === "ok").length}/${comps.length} in Ordnung`}>
            <ul className="divide-y divide-seam">
              {comps.map(([k, v]) => (
                <li key={k} className="flex items-center gap-3 px-4 py-2 text-sm">
                  <Dot tone={healthTone(v)} />
                  <span className="flex-1 text-ink">{componentLabel(k)}</span>
                  <span className="text-xs text-ink-dim">{healthLabel(v)}</span>
                </li>
              ))}
            </ul>
          </Section>

          <div className="flex flex-col gap-6">
            <Section title="Geladene Modelle">
              {r && r.models_loaded.length > 0 ? (
                <ul className="divide-y divide-seam">
                  {r.models_loaded.map((m) => (
                    <li key={m.name ?? "?"} className="flex items-center gap-3 px-4 py-2 text-sm">
                      <span className="type-data flex-1 text-ink">{m.name}</span>
                      <span className="text-xs text-ink-dim">{formatBytes(m.size)} · entlädt {formatRelative(m.expires_at)}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="px-4"><EmptyState title="Kein Modell im Speicher.">Ollama lädt das Modell bei der nächsten Anfrage.</EmptyState></div>
              )}
            </Section>
            <Section title="Sprachausgabe & Telefonie">
              <div className="p-4">
                <KeyValue
                  rows={[
                    ["TTS aktiv", <span key="t">{status.tts?.active ?? "–"} {status.tts?.fallback && <Tag tone="warn">Fallback</Tag>}</span>],
                    ["Fish Audio", status.tts?.fish_audio_ready ? "bereit" : "nicht eingerichtet"],
                    ["Telefonie", status.telephony?.enabled ? (status.telephony.twilio_configured ? "Twilio bereit" : "Einrichtung fehlt") : "deaktiviert"],
                    ["Telefon-PIN", status.telephony?.pin_configured ? "gesetzt" : "fehlt"],
                  ]}
                />
                {!!status.telephony?.missing?.length && (
                  <ul className="mt-3 list-disc pl-5 text-xs text-ink-dim">
                    {status.telephony.missing.map((m) => <li key={m}>{m}</li>)}
                  </ul>
                )}
              </div>
            </Section>
          </div>
        </div>

        <Section title="Token- und Modellnutzung heute" meta="lokal, Ollama">
          {status.usage_today && status.usage_today.length > 0 ? (
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-[0.12em] text-ink-faint">
                <tr><th className="px-4 py-2">Zweck</th><th>Anfragen</th><th>Prompt-Tokens</th><th>Antwort-Tokens</th><th className="pr-4">Ø Latenz</th></tr>
              </thead>
              <tbody className="divide-y divide-seam">
                {status.usage_today.map((u) => (
                  <tr key={u.purpose} className="type-data">
                    <td className="px-4 py-2 text-ink">{u.purpose}</td>
                    <td>{formatNumber(u.requests)}</td>
                    <td>{formatNumber(u.prompt_tokens)}</td>
                    <td>{formatNumber(u.completion_tokens)}</td>
                    <td className="pr-4">{formatMs(u.avg_latency_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="px-4"><EmptyState title="Heute noch keine direkten Modellaufrufe." >Agent-Läufe über Hermes werden in den Hermes-Sitzungen gezählt.</EmptyState></div>
          )}
        </Section>

        {status.mcp_servers && status.mcp_servers.length > 0 && (
          <Section title="Externe MCP-Server">
            <ul className="divide-y divide-seam">
              {status.mcp_servers.map((m) => (
                <li key={m.name} className="flex items-center gap-3 px-4 py-2 text-sm">
                  <Dot tone={m.connected ? "ok" : m.enabled ? "bad" : "idle"} />
                  <span className="flex-1 text-ink">{m.name}</span>
                  <span className="text-xs text-ink-dim">{m.enabled ? (m.connected ? `${m.tools} Tools` : m.error ?? "getrennt") : "deaktiviert"}</span>
                </li>
              ))}
            </ul>
          </Section>
        )}
      </div>
    </div>
  );
}
