// Model + effort picker: one compact chip that opens a panel with the model choice, the thinking effort
// and (optionally) the real subscription quota windows reported by Claude Code.
import { Brain, ChevronDown, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useAction, useQuery } from "../hooks/useQuery";
import { currentChoice, resetLabel } from "../lib/model";
import type { QuotaWindow } from "../lib/types";
import { api } from "../store/app";

const MODELS = [
  { id: "auto", label: "Auto", hint: "Haiku für Kleinigkeiten, sonst Sonnet" },
  { id: "haiku", label: "Haiku", hint: "Schnell und sparsam" },
  { id: "sonnet", label: "Sonnet", hint: "Ausgewogen – der Standard" },
  { id: "opus", label: "Opus", hint: "Stärkste Denkleistung" },
] as const;

const EFFORTS = [
  { id: "auto", short: "Auto", label: "Automatisch" },
  { id: "low", short: "Niedrig", label: "Niedrig" },
  { id: "medium", short: "Mittel", label: "Mittel" },
  { id: "high", short: "Hoch", label: "Hoch" },
  { id: "xhigh", short: "Sehr hoch", label: "Sehr hoch" },
  { id: "max", short: "Max", label: "Maximal" },
] as const;

function Bar({ label, w }: { label: string; w: QuotaWindow | null }) {
  const u = Math.max(0, Math.min(1, w?.utilization ?? 0));
  const tone = u >= 0.95 ? "bg-alarm" : u >= 0.8 ? "bg-ember" : "bg-arc";
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-ink-dim">{label}</span>
        <span className="type-data text-ink">{w ? `${Math.round(u * 100)} %` : "–"}</span>
      </div>
      <div className="h-1 overflow-hidden rounded-full bg-seam" role="progressbar" aria-label={label} aria-valuenow={Math.round(u * 100)} aria-valuemin={0} aria-valuemax={100}>
        <div className={`h-full ${tone} transition-[width] duration-700`} style={{ width: `${u * 100}%` }} />
      </div>
      {w && <div className="text-[11px] text-ink-faint">{resetLabel(w.resets_in_s)}</div>}
    </div>
  );
}

const SECTION = "text-[11px] uppercase tracking-[0.14em] text-ink-faint";

export function ModelPicker({ placement = "up", showQuota = false }: { placement?: "up" | "down"; showQuota?: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const model = useQuery(() => api.agentModel(null), "agent-model-picker", { topics: ["system"] });
  const usage = useQuery(() => api.agentUsage(), "agent-usage-picker", { topics: ["system"], enabled: showQuota });
  const act = useAction();
  const st = model.data;
  const current = currentChoice(st);
  const effort = st?.effort ?? "auto";
  const modelLabel = MODELS.find((c) => c.id === current)?.label ?? current;
  const effortShort = EFFORTS.find((e) => e.id === effort)?.short ?? effort;
  const u = usage.data;
  const pressure = u?.pressure ?? 0;

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const chooseModel = (id: string) =>
    void act.run("model", () => api.setAgentModel({ model: id === "auto" ? null : id, scope: "default" }), undefined).then(() => model.refetch());
  const chooseEffort = (id: string) =>
    void act.run("effort", () => api.setAgentModel({ model: null, scope: "default", effort: id }), undefined).then(() => model.refetch());

  return (
    <div ref={ref} className="pointer-events-auto relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="dialog"
        aria-expanded={open}
        title="Modell und Denkaufwand"
        className="flex h-8 items-center gap-1.5 rounded-full border border-seam px-3 text-xs text-ink-dim transition-colors hover:border-seam-hi hover:text-ink"
      >
        <span className="text-ink">{modelLabel}</span>
        <span className="text-ink-faint" aria-hidden>·</span>
        <span>{effortShort}</span>
        {showQuota && u?.known && (
          <span className={`type-data ${pressure >= 0.95 ? "text-alarm" : pressure >= 0.8 ? "text-ember" : "text-ink-faint"}`}>{Math.round(pressure * 100)} %</span>
        )}
        <ChevronDown className={`size-3 transition-transform ${open ? "rotate-180" : ""}`} aria-hidden />
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="Modell und Denkaufwand"
          className={`holo-panel absolute z-30 flex w-[min(19rem,calc(100vw-2rem))] flex-col gap-4 p-4 ${placement === "up" ? "bottom-10 left-0" : "right-0 top-10"}`}
        >
          <div className="flex flex-col gap-1.5">
            <div className={SECTION}>Modell</div>
            <div role="radiogroup" aria-label="Sprachmodell" className="flex flex-col gap-0.5">
              {MODELS.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  role="radio"
                  aria-checked={current === c.id}
                  disabled={act.busy !== null || !st}
                  onClick={() => chooseModel(c.id)}
                  className={`flex items-center gap-3 rounded-lg px-2.5 py-1.5 text-left transition-colors ${current === c.id ? "bg-arc-deep text-arc" : "text-ink-dim hover:bg-plate-hi hover:text-ink"}`}
                >
                  <span className={`size-2 shrink-0 rounded-full border ${current === c.id ? "border-arc bg-arc" : "border-seam"}`} />
                  <span className="flex flex-col">
                    <span className="text-sm">{c.label}</span>
                    <span className="text-[11px] text-ink-faint">{c.hint}</span>
                  </span>
                </button>
              ))}
            </div>
          </div>
          <div className="flex flex-col gap-1.5 border-t border-seam pt-3">
            <div className={`${SECTION} flex items-center gap-1.5`}>
              <Brain className="size-3" aria-hidden /> Denkaufwand
            </div>
            <div role="radiogroup" aria-label="Denkaufwand" className="grid grid-cols-3 gap-1">
              {EFFORTS.map((e) => (
                <button
                  key={e.id}
                  type="button"
                  role="radio"
                  aria-checked={effort === e.id}
                  title={e.label}
                  disabled={act.busy !== null || !st}
                  onClick={() => chooseEffort(e.id)}
                  className={`rounded-md border px-1 py-1.5 text-xs transition-colors ${
                    effort === e.id ? "border-arc-dim bg-arc-deep text-arc" : "border-seam text-ink-dim hover:border-seam-hi hover:text-ink"
                  }`}
                >
                  {e.short}
                </button>
              ))}
            </div>
            <p className="text-[11px] text-ink-faint">Höher = gründlicher, aber langsamer. Gilt ab der nächsten Antwort.</p>
          </div>
          {showQuota && (
            <div className="flex flex-col gap-3 border-t border-seam pt-3">
              <div className={`${SECTION} flex items-center justify-between`}>
                Kontingent
                <button
                  type="button"
                  aria-label="Kontingent aktualisieren"
                  title="Jetzt abfragen (ein kurzer Haiku-Aufruf)"
                  disabled={act.busy !== null}
                  onClick={() => void act.run("usage", () => api.refreshAgentUsage(), undefined).then(() => usage.refetch())}
                  className="rounded p-1 hover:bg-plate-hi hover:text-ink"
                >
                  <RefreshCw className={`size-3.5 ${act.busy === "usage" ? "animate-spin" : ""}`} />
                </button>
              </div>
              {u?.known ? (
                <>
                  <Bar label="5 Stunden" w={u.windows.five_hour} />
                  <Bar label="7 Tage" w={u.windows.seven_day} />
                </>
              ) : (
                <p className="text-xs text-ink-faint">Noch keine Daten – erscheinen nach der nächsten Antwort.</p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
