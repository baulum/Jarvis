import { Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { PersonaCard } from "../components/PersonaCard";
import { WakeupCard } from "../components/WakeupCard";
import { Button, CodeBlock, Dialog, EmptyState, ErrorState, Field, KeyValue, Section, Skeleton, Tag, Toggle } from "../components/ui";
import { useAction, useQuery } from "../hooks/useQuery";
import { formatRelative } from "../lib/format";
import { autostartEnabled, inTauri, setAutostart } from "../lib/native";
import { api } from "../store/app";

function render(v: unknown): string {
  if (v == null) return "–";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function SettingsView() {
  const settings = useQuery(() => api.settings(), "settings", { topics: ["system"] }); // persona.changed bumps "system"
  const devices = useQuery(() => api.devices(), "devices");
  const act = useAction();
  const [autostart, setAuto] = useState<boolean | null>(null);
  const [pairing, setPairing] = useState(false);
  const [newName, setNewName] = useState("");
  const [newRole, setNewRole] = useState("operator");
  const [newToken, setNewToken] = useState<string | null>(null);

  useEffect(() => { void autostartEnabled().then(setAuto); }, []);

  const s = settings.data;
  const personality = (s?.personality ?? {}) as Record<string, unknown>;
  const voice = (s?.voice ?? {}) as Record<string, unknown>;
  const llm = (s?.llm ?? {}) as Record<string, unknown>;
  const paths = (s?.paths ?? {}) as Record<string, unknown>;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-[64rem] flex-col gap-6 px-8 py-6">
        <p className="text-sm text-ink-dim">Die Konfiguration liegt in <span className="type-data text-ink">config/*.yaml</span> (validiert beim Start). Diese Ansicht zeigt den geladenen Stand.</p>
        {inTauri() && (
          <Section title="App">
            <div className="p-4">
              <Toggle label="Beim Anmelden starten" checked={!!autostart} disabled={autostart === null}
                      onChange={(v) => void act.run("autostart", () => setAutostart(v)).then(() => setAuto(v))} />
            </div>
          </Section>
        )}
        <PersonaCard />
        <WakeupCard />
        {settings.loading && <Skeleton rows={6} />}
        {settings.error && <ErrorState message={settings.error} onRetry={settings.refetch} />}
        {s && (
          <div className="grid gap-6 md:grid-cols-2">
            <Section title="Aktive Persönlichkeit" meta={personality.id === "jarvis" ? "config/personality.yaml" : `config/personalities/${render(personality.id)}.yaml`}>
              <div className="p-4"><KeyValue rows={[
                ["Name", render(personality.name)], ["Anrede", render(personality.owner_title)],
                ["Sprachen", render(personality.languages)], ["Begrüßung", render((personality.greetings as Record<string, unknown> | undefined)?.morning)],
              ]} /></div>
            </Section>
            <Section title="Modelle" meta="Ollama">
              <div className="p-4"><KeyValue rows={[
                ["Agent", render(llm.agent_model)], ["Hilfsaufgaben", render(llm.utility_model)],
                ["Embeddings", render(llm.embedding_model)], ["Keep-Alive", render(llm.keep_alive)],
              ]} /></div>
            </Section>
            <Section title="Stimme">
              <div className="p-4"><KeyValue rows={[
                ["Wake Word", render(voice.wake_word)], ["Schwelle", render(voice.wake_threshold)],
                ["TTS", render((voice.tts as Record<string, unknown> | undefined)?.provider)],
                ["Gesprächsende nach", `${render(voice.end_of_turn_silence_ms)} ms Stille`],
              ]} /></div>
            </Section>
            <Section title="Pfade">
              <div className="p-4"><KeyValue rows={Object.entries(paths).map(([k, v]) => [k, <span key={k} className="type-data break-all text-xs">{render(v)}</span>])} /></div>
            </Section>
          </div>
        )}

        <Section title="Geräte" actions={<Button size="sm" icon={<Plus className="size-3.5" />} onClick={() => { setPairing(true); setNewToken(null); }}>Gerät koppeln</Button>}>
          {devices.loading && <div className="p-4"><Skeleton rows={3} /></div>}
          {devices.error && <div className="p-4"><ErrorState message={devices.error} onRetry={devices.refetch} /></div>}
          {devices.data && devices.data.length === 0 && <div className="px-4"><EmptyState title="Keine Geräte." /></div>}
          <ul className="divide-y divide-seam">
            {devices.data?.map((d) => (
              <li key={d.id} className={`flex items-center gap-3 px-4 py-2.5 text-sm ${d.revoked ? "opacity-50" : ""}`}>
                <span className="flex-1 text-ink">{d.name}</span>
                <Tag>{d.role}</Tag>
                <span className="w-32 text-right text-xs text-ink-faint">{d.last_seen_at ? formatRelative(d.last_seen_at) : "nie gesehen"}</span>
                {d.revoked ? <Tag tone="bad">widerrufen</Tag> : d.role !== "owner" && (
                  <Button size="sm" variant="ghost" icon={<Trash2 className="size-3.5" />} aria-label="Widerrufen"
                          onClick={() => void act.run("revoke", () => api.revokeDevice(d.id), "Zugang widerrufen").then(devices.refetch)} />
                )}
              </li>
            ))}
          </ul>
        </Section>
      </div>
      <Dialog open={pairing} onClose={() => setPairing(false)} title="Gerät koppeln"
              footer={newToken ? <Button variant="primary" onClick={() => setPairing(false)}>Fertig</Button> : <>
                <Button variant="ghost" onClick={() => setPairing(false)}>Abbrechen</Button>
                <Button variant="primary" disabled={newName.trim().length < 2} busy={act.busy === "pair"}
                        onClick={() => void act.run("pair", () => api.createDevice(newName.trim(), newRole)).then((r) => { if (r) { setNewToken(r.token); void devices.refetch(); } })}>Erzeugen</Button></>}>
        {newToken ? (
          <div className="flex flex-col gap-3 text-sm">
            <p className="text-ink-dim">Token nur jetzt sichtbar. Sicher übertragen:</p>
            <CodeBlock value={newToken} />
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            <Field label="Name">{(id) => <input id={id} className="field" value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="iPhone" />}</Field>
            <Field label="Rolle">{(id) => (
              <select id={id} className="field" value={newRole} onChange={(e) => setNewRole(e.target.value)}>
                <option value="operator">operator – vertrautes Gerät</option>
                <option value="viewer">viewer – nur lesen</option>
              </select>
            )}</Field>
          </div>
        )}
      </Dialog>
    </div>
  );
}
