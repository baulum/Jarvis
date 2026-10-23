// Distributed Jarvis: which Core this client talks to, what the Core host can do (capability flags with
// reasons), and the computers Jarvis controls through the Device Agent (pairing, status, capabilities, revoke).
import { ChevronDown, ChevronRight, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";
import { RemoteCoreForm } from "../components/ConnectionForms";
import { Button, CodeBlock, Dialog, Dot, EmptyState, ErrorState, KeyValue, Section, Skeleton, Tag } from "../components/ui";
import { useAction, useQuery } from "../hooks/useQuery";
import { clearRemoteCore, clearWebToken } from "../lib/connection";
import type { Tone } from "../lib/format";
import { inTauri, isMac } from "../lib/native";
import type { AgentPairing, CapabilityStatus, DeviceAgent } from "../lib/types";
import { api, useApp } from "../store/app";

const TONE: Record<CapabilityStatus, Tone> = { available: "ok", degraded: "warn", unavailable: "bad" };
const STATUS_LABEL: Record<CapabilityStatus, string> = { available: "verfügbar", degraded: "eingeschränkt", unavailable: "nicht verfügbar" };

export function DevicesView() {
  const caps = useQuery(() => api.capabilities(), "capabilities", { topics: ["system"] });
  const agents = useQuery(() => api.deviceAgents(), "device-agents", { topics: ["system"], pollMs: 30_000 });
  const act = useAction();
  const [pairing, setPairing] = useState<AgentPairing | null>(null);
  const [pairOpen, setPairOpen] = useState(false);

  const createPairing = () =>
    void act.run("pair", () => api.createAgentPairing("")).then((p) => {
      if (p) {
        setPairing(p);
        setPairOpen(true);
      }
    });

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-[72rem] flex-col gap-6 px-8 py-6">
        <ConnectionSection />

        <Section
          title="Jarvis Device Agents"
          meta="Rechner, die Jarvis steuern darf (Windows, Linux, Mac) – über Tailscale verbunden"
          actions={<Button size="sm" icon={<Plus className="size-3.5" />} busy={act.busy === "pair"} onClick={createPairing}>Gerät koppeln</Button>}
        >
          {agents.loading && <div className="p-4"><Skeleton rows={3} /></div>}
          {agents.error && <div className="p-4"><ErrorState message={agents.error} onRetry={agents.refetch} /></div>}
          {agents.data && agents.data.length === 0 && (
            <div className="px-4">
              <EmptyState title="Noch kein Gerät gekoppelt." />
              <p className="pb-4 text-xs text-ink-faint">Auf dem Windows-/Linux-Rechner den Installer ausführen (scripts\windows\install.ps1 bzw. scripts/linux/install.sh, Rolle „device“) und den Kopplungscode von hier eintragen.</p>
            </div>
          )}
          <ul className="divide-y divide-seam">
            {agents.data?.map((d) => (
              <AgentRow key={d.id} agent={d} busy={act.busy === `revoke:${d.id}`}
                        onRevoke={() => void act.run(`revoke:${d.id}`, () => api.revokeAgent(d.id), `${d.name} widerrufen`).then(agents.refetch)} />
            ))}
          </ul>
        </Section>

        <Section
          title="Core-Host"
          meta={caps.data ? `${caps.data.platform.label} · ${caps.data.platform.session} · ${caps.data.platform.machine} · ${caps.data.platform.hostname}` : undefined}
          actions={<Button size="sm" variant="ghost" icon={<RefreshCw className="size-3.5" />} busy={act.busy === "probe"}
                           onClick={() => void act.run("probe", () => api.capabilities(true)).then((r) => r && caps.setData(() => r))}>Neu prüfen</Button>}
        >
          {caps.loading && <div className="p-4"><Skeleton rows={6} /></div>}
          {caps.error && <div className="p-4"><ErrorState message={caps.error} onRetry={caps.refetch} /></div>}
          {caps.data && (
            <ul className="divide-y divide-seam">
              {Object.entries(caps.data.capabilities).map(([id, st]) => (
                <li key={id} className="flex items-start gap-3 px-4 py-2 text-sm">
                  <Dot tone={TONE[st.status]} className="mt-1.5" />
                  <div className="min-w-0 flex-1">
                    <div className="text-ink">{st.label}</div>
                    {st.reason && <div className="text-xs text-ink-faint">{st.reason}</div>}
                  </div>
                  <span className="type-data text-xs text-ink-faint">{id}</span>
                </li>
              ))}
            </ul>
          )}
        </Section>

        {caps.data && caps.data.unavailable_tools.length > 0 && (
          <Section title="Auf diesem Core-Host nicht verfügbare Werkzeuge" meta={`${caps.data.unavailable_tools.length} Werkzeuge`}>
            <ul className="max-h-[20rem] divide-y divide-seam overflow-y-auto">
              {caps.data.unavailable_tools.map((t) => (
                <li key={t.name} className="flex items-center gap-3 px-4 py-1.5 text-sm">
                  <span className="type-data w-56 shrink-0 truncate text-ink-dim">{t.name}</span>
                  <span className="text-xs text-ink-faint">{t.reason}</span>
                </li>
              ))}
            </ul>
          </Section>
        )}
      </div>

      <Dialog open={pairOpen} onClose={() => setPairOpen(false)} title="Gerät koppeln"
              footer={<Button variant="primary" onClick={() => { setPairOpen(false); void agents.refetch(); }}>Fertig</Button>}>
        {pairing && (
          <div className="flex flex-col gap-3 text-sm">
            <p className="text-ink-dim">Auf dem zu steuernden Rechner (nach der Installation des Device Agents) ausführen – gültig 10 Minuten, nur einmal:</p>
            <CodeBlock value={pairing.command} />
            <KeyValue rows={[
              ["Kopplungscode", <span key="c" className="type-data">{pairing.code}</span>],
              ["Adressen", <span key="u" className="type-data text-xs">{pairing.urls.join(" · ") || "–"}</span>],
              ["Fingerabdruck", <span key="f" className="type-data break-all text-xs">{pairing.fingerprint ?? "Zertifikat über Tailscale/Proxy"}</span>],
            ]} />
          </div>
        )}
      </Dialog>
    </div>
  );
}

function AgentRow({ agent, busy, onRevoke }: { agent: DeviceAgent; busy: boolean; onRevoke: () => void }) {
  const [open, setOpen] = useState(false);
  const caps = Object.entries(agent.capabilities);
  const missing = caps.filter(([, st]) => st.status === "unavailable").length;
  return (
    <li className="px-4 py-2.5 text-sm">
      <div className="flex items-center gap-3">
        <button type="button" className="text-ink-faint hover:text-ink" aria-label={open ? "Fähigkeiten ausblenden" : "Fähigkeiten zeigen"}
                onClick={() => setOpen(!open)} disabled={!caps.length}>
          {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
        </button>
        <Dot tone={agent.online ? "ok" : "idle"} pulse={agent.online} />
        <span className="flex-1 truncate text-ink">{agent.name}</span>
        {agent.platform && <Tag>{agent.platform}</Tag>}
        {agent.hostname && <span className="type-data text-xs text-ink-faint">{agent.hostname}</span>}
        <span className="w-28 text-right text-xs text-ink-faint">{agent.online ? (missing ? `${missing} nicht verfügbar` : "alles verfügbar") : "offline"}</span>
        <Button size="sm" variant="ghost" icon={<Trash2 className="size-3.5" />} aria-label={`${agent.name} widerrufen`} busy={busy} onClick={onRevoke} />
      </div>
      {open && (
        <ul className="mt-2 grid gap-1 pl-10 md:grid-cols-2">
          {caps.map(([id, st]) => (
            <li key={id} className="flex items-start gap-2 text-xs">
              <Dot tone={TONE[st.status]} className="mt-1" />
              <span className="text-ink-dim">{st.label ?? id}</span>
              <span className="text-ink-faint">{st.status === "available" ? "" : `– ${STATUS_LABEL[st.status]}${st.reason ? `: ${st.reason}` : ""}`}</span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function ConnectionSection() {
  const coreUrl = useApp((s) => s.coreUrl);
  const remote = useApp((s) => s.remote);
  const act = useAction();
  const [editing, setEditing] = useState(false);
  const desktop = inTauri();
  const mode = !desktop ? "Browser-Client" : remote ? "Desktop-App, Core auf einem anderen Rechner" : "Desktop-App, Core auf diesem Rechner";
  return (
    <Section title="Verbindung" meta={mode}
             actions={desktop && !isMac() && !editing ? <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>Ändern</Button> : undefined}>
      <div className="flex flex-col gap-4 p-4">
        <KeyValue rows={[["Core", <span key="u" className="type-data">{coreUrl}</span>]]} />
        {editing && <RemoteCoreForm initialUrl={remote ? coreUrl : ""} onSaved={() => window.location.reload()} />}
        <div className="flex gap-2">
          {desktop && remote && (
            <Button size="sm" variant="outline" busy={act.busy === "local"}
                    onClick={() => void act.run("local", clearRemoteCore).then(() => window.location.reload())}>Zum Core auf diesem Rechner</Button>
          )}
          {!desktop && (
            <Button size="sm" variant="outline" onClick={() => { clearWebToken(); window.location.reload(); }}>Abmelden</Button>
          )}
        </div>
      </div>
    </Section>
  );
}
