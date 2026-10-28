// Einstellungen → Persönlichkeit: Jarvis or René Redo (and later personas). A switch changes the system prompt and
// the voice without a restart; the phone follows via `persona.changed`. The Hörprobe plays the persona's sample
// sentence in its own local voice without switching anything.
import { AlertTriangle, Play, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { IconButton, Section, Skeleton } from "./ui";
import { useAction, useQuery } from "../hooks/useQuery";
import type { PersonaInfo } from "../lib/types";
import { api } from "../store/app";

export function PersonaCard() {
  const q = useQuery(() => api.persona(), "persona", { topics: ["system"] });
  const act = useAction();
  const [playing, setPlaying] = useState<string | null>(null);
  const audio = useRef<{ el: HTMLAudioElement; url: string } | null>(null);

  function stop() {
    const a = audio.current;
    audio.current = null;
    if (a) {
      a.el.pause();
      URL.revokeObjectURL(a.url);
    }
    setPlaying(null);
  }

  useEffect(() => stop, []);

  async function preview(p: PersonaInfo) {
    if (playing === p.id) {
      stop();
      return;
    }
    stop();
    const blob = await act.run(`preview:${p.id}`, () => api.personaPreview(p.id));
    if (!blob) return;
    const url = URL.createObjectURL(blob);
    const el = new Audio(url);
    audio.current = { el, url };
    el.onended = () => audio.current?.el === el && stop();
    setPlaying(p.id);
    await el.play().catch(() => stop());
  }

  async function choose(p: PersonaInfo) {
    if (q.data?.active === p.id || act.busy) return;
    const r = await act.run("switch", () => api.setPersona(p.id), `${p.label} ist aktiv`);
    if (r) q.setData(() => r);
  }

  const status = q.data;
  return (
    <Section title="Persönlichkeit" meta="Ton, Systemprompt und Stimme · gleiche Werkzeuge und Erinnerungen">
      {q.loading && <div className="p-4"><Skeleton rows={2} /></div>}
      {q.error && !status && <p role="alert" className="p-4 text-sm text-alarm">{q.error}</p>}
      {status && (
        <div role="radiogroup" aria-label="Persönlichkeit" className="divide-y divide-seam">
          {status.personas.map((p) => {
            const active = status.active === p.id;
            const loading = act.busy === `preview:${p.id}`;
            return (
              <div key={p.id} className={`flex items-center gap-3 px-4 py-3 transition-colors ${active ? "bg-arc-deep/40" : ""}`}>
                <button
                  type="button"
                  role="radio"
                  aria-checked={active}
                  disabled={act.busy === "switch"}
                  onClick={() => void choose(p)}
                  className="flex min-w-0 flex-1 items-start gap-3 text-left disabled:opacity-60"
                >
                  <span
                    aria-hidden
                    className={`mt-1 grid size-3.5 shrink-0 place-items-center rounded-full border ${active ? "border-arc" : "border-seam-hi"}`}
                  >
                    {active && <span className="size-1.5 rounded-full bg-arc" />}
                  </span>
                  <span className="flex min-w-0 flex-col gap-0.5">
                    <span className={`text-sm ${active ? "text-ink" : "text-ink-dim"}`}>{p.label}</span>
                    {p.description && <span className="text-xs text-ink-faint">{p.description}</span>}
                    {!p.voice_ready && (
                      <span className="flex items-center gap-1.5 text-xs text-ember">
                        <AlertTriangle className="size-3 shrink-0" aria-hidden />
                        Eigene Stimme noch nicht geladen – spricht bis zum Neustart des Sprachservers mit der Systemstimme
                      </span>
                    )}
                  </span>
                </button>
                <IconButton
                  label={playing === p.id ? "Hörprobe stoppen" : `Hörprobe: ${p.label}`}
                  disabled={loading || (act.busy !== null && !act.busy.startsWith("preview:"))}
                  onClick={() => void preview(p)}
                >
                  {playing === p.id ? <Square className="size-3.5" /> : <Play className={`size-3.5 ${loading ? "animate-pulse" : ""}`} />}
                </IconButton>
              </div>
            );
          })}
        </div>
      )}
    </Section>
  );
}
