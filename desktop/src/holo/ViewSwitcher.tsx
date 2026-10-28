// Top centre: the four views. Switching animates every node to its place in the new view (BrainCanvas).
// Keys 1–4 switch while no text field has focus.
import { motion } from "framer-motion";
import { Activity, Brain, Library, Waypoints, type LucideIcon } from "lucide-react";
import { useEffect } from "react";
import { useBrain, VIEW_LABEL, type ViewMode } from "./store";

const VIEWS: { id: ViewMode; icon: LucideIcon; hint: string }[] = [
  { id: "brain", icon: Brain, hint: "Anatomisches Gehirn: Regionen, Neuronen und Impulse aus echter Aktivität" },
  { id: "network", icon: Waypoints, hint: "Freies Netzwerk aus Erinnerungen, Werkzeugen und Agenten mit ihren echten Beziehungen" },
  { id: "activity", icon: Activity, hint: "Laufende Prozesse, Werkzeugaufrufe und Datenströme von links nach rechts" },
  { id: "explorer", icon: Library, hint: "Erinnerungen durchsuchen, nach Projekt, Thema und Zeitraum filtern" },
];

export function ViewSwitcher() {
  const view = useBrain((s) => s.view);
  const setView = useBrain((s) => s.setView);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      if (e.metaKey || e.ctrlKey || e.altKey || useBrain.getState().overlay) return;
      const i = ["1", "2", "3", "4"].indexOf(e.key);
      if (i >= 0) setView(VIEWS[i]!.id);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setView]);
  return (
    <nav className="holo-panel pointer-events-auto flex items-center gap-0.5 p-1" aria-label="Ansicht">
      {VIEWS.map((v, i) => {
        const Icon = v.icon;
        const active = v.id === view;
        return (
          <button
            key={v.id}
            type="button"
            onClick={() => setView(v.id)}
            aria-pressed={active}
            title={`${v.hint}  (${i + 1})`}
            className={`relative flex h-7 items-center gap-1.5 rounded-md px-2.5 text-xs transition-colors ${active ? "text-ink" : "text-ink-faint hover:text-ink-dim"}`}
          >
            {active && (
              <motion.span
                layoutId="view-indicator"
                className="absolute inset-0 rounded-md bg-plate-hi"
                transition={{ type: "spring", stiffness: 520, damping: 42 }}
              />
            )}
            <Icon className="relative size-3.5" strokeWidth={1.6} />
            <span className="relative hidden lg:inline">{VIEW_LABEL[v.id]}</span>
          </button>
        );
      })}
    </nav>
  );
}
