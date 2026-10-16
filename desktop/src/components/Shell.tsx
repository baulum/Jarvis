// Window chrome: navigation rail, top bar with the always-visible kill switch, toasts.
import { AnimatePresence, motion } from "framer-motion";
import {
  Activity,
  AudioLines,
  Bell,
  BookOpenText,
  CalendarClock,
  Cpu,
  KeyRound,
  ListChecks,
  MessagesSquare,
  Plug,
  ScrollText,
  Settings2,
  ShieldCheck,
  Waypoints,
  X,
} from "lucide-react";
import type { ComponentType, ReactNode } from "react";
import { useApp, type ViewId } from "../store/app";
import { KillSwitch, KillSwitchBanner } from "./KillSwitch";
import { Reactor, reactorMode } from "./Reactor";
import { Dot } from "./ui";

interface NavItem {
  id: ViewId;
  label: string;
  icon: ComponentType<{ className?: string; strokeWidth?: number }>;
  hint: string;
}

export const NAV_GROUPS: NavItem[][] = [
  [
    { id: "chat", label: "Chat", icon: MessagesSquare, hint: "Gespräche mit J.A.R.V.I.S." },
    { id: "voice", label: "Stimme", icon: AudioLines, hint: "Sprach-Pipeline und Push-to-Talk" },
  ],
  [
    { id: "tasks", label: "Aufgaben", icon: ListChecks, hint: "Mehrstufige Abläufe und ihre Schritte" },
    { id: "approvals", label: "Freigaben", icon: ShieldCheck, hint: "Aktionen, die deine Zustimmung brauchen" },
    { id: "agents", label: "Agent-Monitor", icon: Activity, hint: "Werkzeugaufrufe, Coding-Jobs, Hermes" },
    { id: "scheduler", label: "Zeitplaner", icon: CalendarClock, hint: "Wiederkehrende Aufträge" },
  ],
  [
    { id: "memory", label: "Wissensgraph", icon: Waypoints, hint: "Gedächtnis, Notizen und Verknüpfungen" },
    { id: "skills", label: "Skills", icon: BookOpenText, hint: "Hermes-Skills, Playbooks und Werkzeuge" },
    { id: "connectors", label: "Connectoren", icon: Plug, hint: "Dienste verbinden und prüfen" },
  ],
  [
    { id: "notifications", label: "Benachrichtigungen", icon: Bell, hint: "Meldungen des Systems" },
    { id: "logs", label: "Live-Logs", icon: ScrollText, hint: "Protokoll des Cores in Echtzeit" },
    { id: "system", label: "Systemstatus", icon: Cpu, hint: "Komponenten, Ressourcen, Verbrauch" },
    { id: "permissions", label: "Berechtigungen", icon: KeyRound, hint: "Rollen, Risikostufen, macOS-Rechte" },
    { id: "settings", label: "Einstellungen", icon: Settings2, hint: "Konfiguration und Geräte" },
  ],
];

export const NAV_ITEMS = NAV_GROUPS.flat();

function Rail() {
  const view = useApp((s) => s.view);
  const setView = useApp((s) => s.setView);
  const pending = useApp((s) => s.pendingApprovals.length);
  const openTasks = useApp((s) => s.status?.open_tasks ?? 0);
  const unread = useApp((s) => s.unread);
  const voice = useApp((s) => s.voice);
  const connection = useApp((s) => s.connection);

  const counts: Partial<Record<ViewId, { n: number; tone: "warn" | "info" }>> = {
    approvals: { n: pending, tone: "warn" },
    tasks: { n: openTasks, tone: "info" },
    notifications: { n: unread, tone: "info" },
  };

  return (
    <nav aria-label="Hauptnavigation" className="flex w-[212px] shrink-0 flex-col border-r border-seam bg-hull">
      <div className="drag h-[52px] shrink-0" data-tauri-drag-region />
      <div className="flex items-center gap-2.5 px-4 pb-5">
        <Reactor
          mode={reactorMode(voice.state, Boolean(voice.connected), Boolean(voice.muted))}
          level={voice.level ?? 0}
          size={30}
          label="J.A.R.V.I.S."
        />
        <div className="leading-none">
          <div className="type-display text-[1.05rem] tracking-[0.06em] text-ink">J.A.R.V.I.S.</div>
          <div className="mt-1 text-2xs text-ink-faint">Kommandozentrale</div>
        </div>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-2.5 pb-3">
        {NAV_GROUPS.map((group, gi) => (
          <ul key={gi} className={`flex flex-col gap-px ${gi > 0 ? "border-t border-seam pt-3" : ""}`}>
            {group.map((item) => {
              const active = item.id === view;
              const Icon = item.icon;
              const c = counts[item.id];
              return (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => setView(item.id)}
                    aria-current={active ? "page" : undefined}
                    title={item.hint}
                    className={`group relative flex h-8 w-full items-center gap-2.5 rounded-md px-2.5 text-[0.8125rem] transition-colors ${
                      active ? "bg-plate-hi text-ink" : "text-ink-dim hover:bg-plate hover:text-ink"
                    }`}
                  >
                    {active && (
                      <motion.span
                        layoutId="rail-active"
                        className="absolute top-1.5 bottom-1.5 left-0 w-[2px] rounded-full bg-arc"
                        transition={{ type: "spring", stiffness: 500, damping: 40 }}
                      />
                    )}
                    <Icon className={`size-[15px] ${active ? "text-arc" : ""}`} strokeWidth={1.6} />
                    <span className="flex-1 truncate text-left">{item.label}</span>
                    {c && c.n > 0 && (
                      <span
                        className={`type-data rounded px-1.5 ${c.tone === "warn" ? "bg-ember-deep text-ember" : "bg-plate-hi text-ink-dim"}`}
                      >
                        {c.n}
                      </span>
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        ))}
      </div>
      <ConnectionFooter connection={connection} />
    </nav>
  );
}

function ConnectionFooter({ connection }: { connection: string }) {
  const version = useApp((s) => s.status?.version);
  const map: Record<string, { tone: "ok" | "warn" | "bad" | "idle"; text: string }> = {
    open: { tone: "ok", text: "Live verbunden" },
    connecting: { tone: "warn", text: "Verbinde …" },
    reconnecting: { tone: "warn", text: "Verbindung wird wiederhergestellt" },
    unauthorized: { tone: "bad", text: "Token abgelehnt" },
    closed: { tone: "idle", text: "Getrennt" },
    idle: { tone: "idle", text: "Nicht verbunden" },
  };
  const c = map[connection] ?? { tone: "idle" as const, text: connection };
  return (
    <div className="flex items-center gap-2 border-t border-seam px-4 py-3 text-2xs text-ink-dim">
      <Dot tone={c.tone} pulse={connection === "reconnecting"} />
      <span className="flex-1 truncate">{c.text}</span>
      {version && <span className="type-data text-ink-faint">v{version}</span>}
    </div>
  );
}

function TopBar() {
  const view = useApp((s) => s.view);
  const item = NAV_ITEMS.find((n) => n.id === view);
  return (
    <header className="drag flex h-[52px] shrink-0 items-center gap-4 border-b border-seam pr-3 pl-6" data-tauri-drag-region>
      <div className="pointer-events-none flex min-w-0 flex-1 items-baseline gap-3" data-tauri-drag-region>
        <h1 className="type-display text-[1.2rem] text-ink">{item?.label}</h1>
        <p className="truncate text-xs text-ink-faint">{item?.hint}</p>
      </div>
      <KillSwitch />
    </header>
  );
}

export function Toasts() {
  const toasts = useApp((s) => s.toasts);
  const dismiss = useApp((s) => s.dismissToast);
  const setView = useApp((s) => s.setView);
  const bar: Record<string, string> = { ok: "bg-arc", info: "bg-arc-dim", warn: "bg-ember", bad: "bg-alarm" };
  return (
    <div aria-live="polite" className="pointer-events-none fixed right-4 bottom-4 z-40 flex w-[22rem] flex-col gap-2">
      <AnimatePresence initial={false}>
        {toasts.map((t) => (
          <motion.div
            key={t.id}
            layout
            initial={{ opacity: 0, x: 24 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 24, transition: { duration: 0.15 } }}
            transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
            className="pointer-events-auto flex overflow-hidden rounded-lg border border-seam-hi bg-hull shadow-xl shadow-black/50"
          >
            <span className={`w-[3px] shrink-0 ${bar[t.tone]}`} />
            <div className="min-w-0 flex-1 px-3.5 py-2.5">
              <p className="text-[0.8125rem] text-ink">{t.title}</p>
              {t.body && <p className="mt-0.5 line-clamp-3 text-xs text-ink-dim">{t.body}</p>}
              {t.action && (
                <button
                  type="button"
                  className="mt-1.5 text-xs text-arc hover:underline"
                  onClick={() => {
                    setView(t.action!.view);
                    dismiss(t.id);
                  }}
                >
                  {t.action.label}
                </button>
              )}
            </div>
            <button
              type="button"
              aria-label="Meldung schließen"
              onClick={() => dismiss(t.id)}
              className="self-start p-2 text-ink-faint hover:text-ink"
            >
              <X className="size-3.5" />
            </button>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const view = useApp((s) => s.view);
  return (
    <div className="flex h-full bg-void">
      <Rail />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <KillSwitchBanner />
        <main className="relative min-h-0 flex-1">
          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={view}
              className="absolute inset-0 overflow-hidden"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.12 }}
            >
              {children}
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
      <Toasts />
    </div>
  );
}

