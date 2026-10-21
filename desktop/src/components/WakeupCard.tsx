// "Hey Jarvis, wake up" / "Hey Jarvis, wach auf": Sir's MP3 and the programs Jarvis opens. macOS only – on other
// platforms (or when the core does not run on a Mac) the card does not appear.
import { AlertTriangle, ArrowDown, ArrowUp, FolderOpen, Play, Plus, Trash2, X } from "lucide-react";
import { useState } from "react";
import { Button, IconButton, Section, Skeleton, Toggle } from "./ui";
import { useAction, useQuery } from "../hooks/useQuery";
import { inTauri, isMac, pickPath } from "../lib/native";
import type { WakeupResult, WakeupSettings } from "../lib/types";
import { api } from "../store/app";

const baseName = (p: string) => p.split("/").filter(Boolean).pop() ?? p;

export function WakeupCard() {
  const mac = isMac();
  const q = useQuery(() => api.wakeup(), "wakeup", { topics: ["system"], enabled: mac });
  const act = useAction();
  const [newApp, setNewApp] = useState("");
  const [soundDraft, setSoundDraft] = useState<string | null>(null);
  const [volume, setVolume] = useState<number | null>(null);
  const [result, setResult] = useState<WakeupResult | null>(null);
  const w = q.data;

  if (!mac || w?.supported === false) return null;

  async function save(patch: Partial<Pick<WakeupSettings, "enabled" | "sound" | "volume" | "apps">>) {
    const r = await act.run("save", () => api.setWakeup(patch));
    if (r) q.setData(() => r);
    return r;
  }

  async function chooseSound() {
    const path = await pickPath({ title: "Weckmusik wählen", filterName: "MP3-Audio", extensions: ["mp3"] });
    if (path) await save({ sound: path });
  }

  async function chooseApp() {
    const path = await pickPath({ title: "Programm wählen", filterName: "Programme", extensions: ["app"], defaultPath: "/Applications" });
    if (path && w) await save({ apps: [...w.apps, path] });
  }

  function addApp() {
    const name = newApp.trim();
    if (!name || !w) return;
    void save({ apps: [...w.apps, name] }).then((r) => r && setNewApp(""));
  }

  function move(i: number, by: -1 | 1) {
    if (!w) return;
    const apps = [...w.apps];
    const [item] = apps.splice(i, 1);
    apps.splice(i + by, 0, item!);
    void save({ apps });
  }

  function commitVolume() {
    if (volume === null || !w || volume === w.volume) return;
    void save({ volume }).then(() => setVolume(null));
  }

  const soundShown = soundDraft ?? w?.sound ?? "";
  const vol = volume ?? w?.volume ?? 1;

  return (
    <Section
      title="Weckroutine"
      meta="„Hey Jarvis, wake up“ · „Hey Jarvis, wach auf“"
      actions={
        w && (
          <>
            <Button
              size="sm"
              variant="ghost"
              icon={<Play className="size-3.5" />}
              busy={act.busy === "run"}
              disabled={act.busy !== null}
              onClick={() => void act.run("run", () => api.runWakeup()).then((r) => r && setResult(r))}
              title="Spielt die MP3 und öffnet die Programme jetzt"
            >
              Testen
            </Button>
            <Toggle label="Weckroutine aktiv" checked={w.enabled} disabled={act.busy !== null} onChange={(v) => void save({ enabled: v })} />
          </>
        )
      }
    >
      {q.loading && <div className="p-4"><Skeleton rows={3} /></div>}
      {q.error && !w && <p role="alert" className="p-4 text-sm text-alarm">{q.error}</p>}
      {w && (
        <div className={`flex flex-col gap-5 p-4 text-sm ${w.enabled ? "" : "opacity-60"}`}>
          <div className="flex flex-col gap-2">
            <label htmlFor="wakeup-sound" className="text-xs text-ink-dim">Weckmusik (MP3)</label>
            <div className="flex items-center gap-2">
              <input
                id="wakeup-sound"
                className="field type-data"
                value={soundShown}
                placeholder="Keine – eingebauter Ton"
                spellCheck={false}
                onChange={(e) => setSoundDraft(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
                onBlur={() => {
                  if (soundDraft === null) return;
                  if (soundDraft.trim() === w.sound) setSoundDraft(null);
                  else void save({ sound: soundDraft.trim() }).then((r) => r && setSoundDraft(null));
                }}
              />
              {inTauri() && (
                <Button size="sm" icon={<FolderOpen className="size-3.5" />} disabled={act.busy !== null} onClick={() => void chooseSound()}>
                  Auswählen …
                </Button>
              )}
              {w.sound && (
                <IconButton label="MP3 entfernen" disabled={act.busy !== null} onClick={() => void save({ sound: "" }).then(() => setSoundDraft(null))}>
                  <X className="size-3.5" />
                </IconButton>
              )}
            </div>
            {w.sound_error && (
              <p role="alert" className="flex items-start gap-2 text-xs text-alarm">
                <AlertTriangle className="mt-px size-3.5 shrink-0" aria-hidden />
                {w.sound_error}
              </p>
            )}
            <div className="flex items-center gap-3">
              <label htmlFor="wakeup-volume" className="w-20 text-xs text-ink-dim">Lautstärke</label>
              <input
                id="wakeup-volume"
                type="range"
                min={0}
                max={100}
                step={5}
                value={Math.round(vol * 100)}
                disabled={!w.sound}
                onChange={(e) => setVolume(Number(e.target.value) / 100)}
                onPointerUp={commitVolume}
                onKeyUp={commitVolume}
                onBlur={commitVolume}
                className="flex-1 accent-[var(--color-arc)] disabled:opacity-40"
              />
              <span className="type-data w-10 text-right text-ink-dim">{Math.round(vol * 100)} %</span>
            </div>
          </div>

          <div className="flex flex-col gap-2">
            <span className="text-xs text-ink-dim">Programme – werden in dieser Reihenfolge geöffnet</span>
            {w.apps.length === 0 ? (
              <p className="text-xs text-ink-faint">Noch keine Programme.</p>
            ) : (
              <ol className="divide-y divide-seam rounded-md border border-seam">
                {w.apps.map((app, i) => (
                  <li key={app} className="flex items-center gap-2 px-3 py-1.5">
                    <span className="type-data w-5 text-ink-faint">{i + 1}</span>
                    <span className="min-w-0 flex-1 truncate text-ink" title={app}>
                      {app.includes("/") ? baseName(app).replace(/\.app$/i, "") : app}
                      {app.includes("/") && <span className="type-data ml-2 text-ink-faint">{app}</span>}
                    </span>
                    <IconButton label="Nach oben" disabled={i === 0 || act.busy !== null} onClick={() => move(i, -1)}>
                      <ArrowUp className="size-3.5" />
                    </IconButton>
                    <IconButton label="Nach unten" disabled={i === w.apps.length - 1 || act.busy !== null} onClick={() => move(i, 1)}>
                      <ArrowDown className="size-3.5" />
                    </IconButton>
                    <IconButton label={`${app} entfernen`} disabled={act.busy !== null} onClick={() => void save({ apps: w.apps.filter((a) => a !== app) })}>
                      <Trash2 className="size-3.5" />
                    </IconButton>
                  </li>
                ))}
              </ol>
            )}
            <form
              className="flex items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                addApp();
              }}
            >
              <input
                className="field"
                value={newApp}
                onChange={(e) => setNewApp(e.target.value)}
                placeholder="App-Name (z. B. Safari) oder /Applications/….app"
                aria-label="Programm hinzufügen"
                spellCheck={false}
              />
              <Button size="sm" type="submit" icon={<Plus className="size-3.5" />} disabled={!newApp.trim() || act.busy !== null}>
                Hinzufügen
              </Button>
              {inTauri() && (
                <Button size="sm" icon={<FolderOpen className="size-3.5" />} disabled={act.busy !== null} onClick={() => void chooseApp()}>
                  App wählen …
                </Button>
              )}
            </form>
          </div>

          {result && (
            <div role="status" className="flex flex-col gap-1 rounded-md border border-seam px-3 py-2 text-xs">
              <span className="text-ink-dim">
                Test: {result.opened.length} von {result.opened.length + result.failed.length} Programmen geöffnet
                {result.sound ? (result.sound_error ? " · MP3 fehlgeschlagen" : " · MP3 läuft") : " · keine MP3"}
              </span>
              {result.sound_error && <span className="text-alarm">{result.sound_error}</span>}
              {result.failed.map((f) => (
                <span key={f.app} className="text-ember">
                  {f.app}: {f.reason}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </Section>
  );
}
