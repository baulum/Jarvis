import { ExternalLink, KeyRound, Phone, Plug, RefreshCw } from "lucide-react";
import { useState } from "react";
import { Button, Dialog, Dot, EmptyState, ErrorState, Field, RiskBadge, Section, Skeleton, Tag, Toggle } from "../components/ui";
import { useAction, useQuery } from "../hooks/useQuery";
import { formatRelative, healthLabel, healthTone } from "../lib/format";
import { openExternal } from "../lib/native";
import type { Connector } from "../lib/types";
import { api, useApp } from "../store/app";

function ConfigureDialog({ c, onClose, onSaved }: { c: Connector; onClose: () => void; onSaved: () => void }) {
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const props = c.settings_schema.properties ?? {};
  const [settings, setSettings] = useState<Record<string, string>>(
    Object.fromEntries(Object.keys(props).map((k) => [k, c.settings[k] == null ? "" : typeof c.settings[k] === "object" ? JSON.stringify(c.settings[k]) : String(c.settings[k])])),
  );
  const act = useAction();

  const save = async () => {
    const parsed: Record<string, unknown> = {};
    for (const [k, raw] of Object.entries(settings)) {
      if (raw === "") continue;
      const type = props[k]?.type;
      if (type === "integer" || type === "number") parsed[k] = Number(raw);
      else if (type === "array" || type === "object") parsed[k] = JSON.parse(raw);
      else if (type === "boolean") parsed[k] = raw === "true";
      else parsed[k] = raw;
    }
    const filled = Object.fromEntries(Object.entries(secrets).filter(([, v]) => v.trim()));
    const res = await act.run("save", () => api.configureConnector(c.id, { settings: parsed, secrets: filled }), `${c.name} gespeichert`);
    if (res) { onSaved(); onClose(); }
  };

  return (
    <Dialog open onClose={onClose} title={`${c.name} einrichten`}
            footer={<><Button variant="ghost" onClick={onClose}>Abbrechen</Button><Button variant="primary" busy={act.busy === "save"} onClick={() => void save()}>Speichern</Button></>}>
      <div className="flex flex-col gap-4 text-sm">
        {c.setup_instructions && <p className="text-ink-dim">{c.setup_instructions}</p>}
        {c.secrets.map((s) => (
          <Field key={s.name} label={`${s.label}${c.secrets_present[s.name] ? " (gesetzt)" : ""}`} hint={s.help}>
            {(id) => (
              <input id={id} type="password" autoComplete="off" className="field" placeholder={c.secrets_present[s.name] ? "•••••••• (unverändert lassen)" : ""}
                     value={secrets[s.name] ?? ""} onChange={(e) => setSecrets({ ...secrets, [s.name]: e.target.value })} />
            )}
          </Field>
        ))}
        {Object.entries(props).filter(([k]) => !["pairing_code", "pairing_expires"].includes(k)).map(([k, schema]) => (
          <Field key={k} label={k} hint={schema.description ?? (schema.type === "array" || schema.type === "object" ? "JSON" : undefined)}>
            {(id) => schema.enum ? (
              <select id={id} className="field" value={settings[k]} onChange={(e) => setSettings({ ...settings, [k]: e.target.value })}>
                <option value="">–</option>
                {schema.enum.map((v) => <option key={String(v)} value={String(v)}>{String(v)}</option>)}
              </select>
            ) : (
              <input id={id} className="field" value={settings[k]} onChange={(e) => setSettings({ ...settings, [k]: e.target.value })} />
            )}
          </Field>
        ))}
        <p className="text-xs text-ink-faint">Zugangsdaten werden ausschließlich im macOS-Schlüsselbund gespeichert und nie angezeigt.</p>
      </div>
    </Dialog>
  );
}

function ConnectorCard({ c, refetch }: { c: Connector; refetch: () => Promise<void> }) {
  const [configuring, setConfiguring] = useState(false);
  const [pairCode, setPairCode] = useState<string | null>(null);
  const act = useAction();
  const h = c.health;
  return (
    <article className="plate flex flex-col gap-3 p-4">
      <header className="flex items-start gap-3">
        <Dot tone={healthTone(h?.status)} className="mt-1.5" />
        <div className="min-w-0 flex-1">
          <h3 className="type-title text-ink">{c.name}</h3>
          <p className="text-xs text-ink-dim">{c.description}</p>
        </div>
        <Toggle label="Aktiv" checked={c.enabled} disabled={act.busy !== null}
                onChange={(v) => void act.run("toggle", () => api.configureConnector(c.id, { enabled: v }), v ? `${c.name} aktiviert` : `${c.name} deaktiviert`).then(refetch)} />
      </header>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Tag tone={healthTone(h?.status)}>{healthLabel(h?.status)}</Tag>
        {h?.message && <span className="text-ink-dim">{h.message}</span>}
        {h?.checked_at && <span className="text-ink-faint">· geprüft {formatRelative(h.checked_at)}</span>}
      </div>
      {c.missing.length > 0 && h?.status !== "not_configured" && <p className="text-xs text-ember">Fehlt: {c.missing.join(", ")}</p>}
      {c.mcp_note && <p className="text-xs text-ink-faint">MCP: {c.mcp_note}</p>}
      {pairCode && <p className="rounded-md border border-arc-dim/40 bg-arc-deep/40 px-3 py-2 text-sm text-arc">Sende deinem Bot: <span className="type-data">/pair {pairCode}</span> (10 Minuten gültig)</p>}
      <details className="text-xs">
        <summary className="cursor-pointer text-ink-dim hover:text-ink">{c.tools.length} Werkzeuge</summary>
        <ul className="mt-2 flex flex-col gap-1">
          {c.tools.map((t) => <li key={t.name} className="flex items-center gap-2"><RiskBadge risk={t.risk} /><span className="type-data text-ink">{t.name}</span></li>)}
        </ul>
      </details>
      <footer className="mt-auto flex flex-wrap gap-2">
        {c.id !== "whatsapp" && (c.secrets.length > 0 || Object.keys(c.settings_schema.properties ?? {}).length > 0) && (
          <Button size="sm" icon={<KeyRound className="size-3.5" />} onClick={() => setConfiguring(true)}>Einrichten</Button>
        )}
        <Button size="sm" variant="ghost" busy={act.busy === "test"} icon={<RefreshCw className="size-3.5" />}
                onClick={() => void act.run("test", () => api.testConnector(c.id)).then(refetch)}>Testen</Button>
        {c.id === "whatsapp" && (
          <Button size="sm" variant={c.enabled ? "primary" : "ghost"} onClick={() => useApp.getState().setView("whatsapp")}>
            {c.enabled && h?.status === "ok" ? "Chats öffnen" : "Verbinden und verwalten"}
          </Button>
        )}
        {c.id === "uptik" && (
          <Button size="sm" variant={c.enabled ? "primary" : "ghost"} onClick={() => useApp.getState().setView("uptik")}>
            Konten und Warteschlange
          </Button>
        )}
        {c.id === "telegram" && (
          <Button size="sm" variant="ghost" onClick={() => void act.run("pair", () => api.telegramPair()).then((r) => r && setPairCode(r.code))}>Koppeln</Button>
        )}
        {c.id === "google" && (
          <Button size="sm" variant="ghost" icon={<ExternalLink className="size-3.5" />}
                  onClick={() => void act.run("oauth", async () => {
                    const r = await api.googleOAuthStart();
                    await openExternal(r.auth_url); // errors surface as a toast via act.run
                    return r;
                  }, "Google-Anmeldung im Browser geöffnet")}>Mit Google verbinden</Button>
        )}
        {c.id === "telephony" && c.enabled && h?.status === "ok" && (
          <Button size="sm" variant="ghost" busy={act.busy === "call"} icon={<Phone className="size-3.5" />}
                  onClick={() => void act.run("call", () => api.telephonyCallMe(), "Jarvis ruft dich jetzt an")}>Testanruf an mich</Button>
        )}
        {c.docs_url && <Button size="sm" variant="quiet" onClick={() => void act.run("docs", () => openExternal(c.docs_url))}>Doku</Button>}
      </footer>
      {configuring && <ConfigureDialog c={c} onClose={() => setConfiguring(false)} onSaved={() => void refetch()} />}
    </article>
  );
}

export function ConnectorsView() {
  const q = useQuery(() => api.connectors(), "connectors", { topics: ["connector"] });
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-[76rem] flex-col gap-6 px-8 py-6">
        {q.loading && <Skeleton rows={6} />}
        {q.error && <ErrorState message={q.error} onRetry={q.refetch} />}
        {q.data && q.data.connectors.length === 0 && <EmptyState icon={<Plug className="size-6" />} title="Keine Connectoren registriert." />}
        {q.data && (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {q.data.connectors.map((c) => <ConnectorCard key={c.id} c={c} refetch={q.refetch} />)}
          </div>
        )}
        {q.data && q.data.mcp_servers.length > 0 && (
          <Section title="Externe MCP-Server" meta="config/mcp_servers.yaml">
            <ul className="divide-y divide-seam">
              {q.data.mcp_servers.map((m) => (
                <li key={m.name} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                  <Dot tone={m.connected ? "ok" : m.enabled ? "bad" : "idle"} />
                  <span className="type-data text-ink">{m.name}</span>
                  <span className="flex-1 truncate text-xs text-ink-dim">{m.description}</span>
                  <Tag>{m.transport}</Tag>
                  <span className="text-xs text-ink-faint">{m.enabled ? (m.connected ? `${m.tools} Tools` : m.error ?? "getrennt") : "aus"}</span>
                </li>
              ))}
            </ul>
          </Section>
        )}
      </div>
    </div>
  );
}
