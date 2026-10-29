// Every existing function stays reachable: the former views open in a panel over the brain.
import { AnimatePresence, motion } from "framer-motion";
import { X } from "lucide-react";
import { useEffect, type ComponentType } from "react";
import { AgentsView } from "../views/AgentsView";
import { ApprovalsView } from "../views/ApprovalsView";
import { ChatView } from "../views/ChatView";
import { ConnectorsView } from "../views/ConnectorsView";
import { DevicesView } from "../views/DevicesView";
import { LogsView } from "../views/LogsView";
import { MemoryView } from "../views/MemoryView";
import { NotificationsView } from "../views/NotificationsView";
import { PermissionsView } from "../views/PermissionsView";
import { SchedulerView } from "../views/SchedulerView";
import { SettingsView } from "../views/SettingsView";
import { SkillsView } from "../views/SkillsView";
import { SocialView } from "../views/SocialView";
import { SystemView } from "../views/SystemView";
import { TasksView } from "../views/TasksView";
import { UptikView } from "../views/UptikView";
import { VoiceView } from "../views/VoiceView";
import { WhatsAppView } from "../views/WhatsAppView";
import { useApp } from "../store/app";
import { useBrain } from "./store";

export const SYSTEM_VIEWS: { id: string; label: string; keywords: string[]; view: ComponentType }[] = [
  { id: "social", label: "Social Media", keywords: ["video", "reels", "tiktok", "instagram", "youtube", "pje", "werbung", "captions"], view: SocialView },
  { id: "uptik", label: "UpTik Autopost", keywords: ["tiktok", "instagram", "reels", "shorts", "veröffentlichen", "autopost", "warteschlange"], view: UptikView },
  { id: "tasks", label: "Aufgaben", keywords: ["tasks", "playbook", "ablauf"], view: TasksView },
  { id: "approvals", label: "Freigaben", keywords: ["approval", "freigeben"], view: ApprovalsView },
  { id: "chat", label: "Gespräche", keywords: ["chat", "verlauf", "sessions"], view: ChatView },
  { id: "agents", label: "Agent-Monitor", keywords: ["coding", "claude", "werkzeugaufrufe"], view: AgentsView },
  { id: "scheduler", label: "Zeitplaner", keywords: ["cron", "termin", "wiederkehrend"], view: SchedulerView },
  { id: "memory", label: "Gedächtnis als Liste", keywords: ["vault", "obsidian", "notizen", "export"], view: MemoryView },
  { id: "whatsapp", label: "WhatsApp", keywords: ["chats", "nachrichten", "dateien", "qr", "messenger"], view: WhatsAppView },
  { id: "connectors", label: "Connectoren", keywords: ["lexware", "google", "telegram", "fish", "twilio", "mcp"], view: ConnectorsView },
  { id: "skills", label: "Skills", keywords: ["claude skills"], view: SkillsView },
  { id: "voice", label: "Stimme", keywords: ["mikrofon", "push-to-talk", "voice"], view: VoiceView },
  { id: "notifications", label: "Benachrichtigungen", keywords: ["meldungen"], view: NotificationsView },
  { id: "logs", label: "Live-Logs", keywords: ["protokoll", "log"], view: LogsView },
  { id: "system", label: "Systemstatus", keywords: ["ressourcen", "status", "cpu"], view: SystemView },
  { id: "permissions", label: "Berechtigungen", keywords: ["rollen", "risiko", "macos"], view: PermissionsView },
  { id: "devices", label: "Geräte & Plattform", keywords: ["windows", "linux", "device agent", "tailscale", "verbindung", "fähigkeiten"], view: DevicesView },
  { id: "settings", label: "Einstellungen", keywords: ["geräte", "config"], view: SettingsView },
];

export function SystemPanel() {
  const overlay = useBrain((s) => s.overlay);
  const viewId = useBrain((s) => s.systemView);
  const open = useBrain((s) => s.openOverlay);
  const pending = useApp((s) => s.pendingApprovals.length);
  const unread = useApp((s) => s.unread);
  const visible = overlay === "system";
  const current = SYSTEM_VIEWS.find((v) => v.id === viewId) ?? SYSTEM_VIEWS[0]!;
  const View = current.view;

  useEffect(() => {
    if (!visible) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && open(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [visible, open]);

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          key="system"
          className="pointer-events-auto absolute inset-0 z-20 flex bg-void/55"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onMouseDown={(e) => e.target === e.currentTarget && open(null)}
        >
          <motion.div
            initial={{ x: 40, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: 40, opacity: 0 }}
            transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
            className="ml-auto flex h-full w-[min(72rem,calc(100vw-3rem))] border-l border-seam bg-hull"
          >
            <nav className="flex w-48 shrink-0 flex-col gap-px border-r border-seam px-2 pt-14 pb-4" aria-label="Systemansichten">
              {SYSTEM_VIEWS.map((v) => {
                const badge = v.id === "approvals" ? pending : v.id === "notifications" ? unread : 0;
                return (
                  <button
                    key={v.id}
                    type="button"
                    onClick={() => open("system", v.id)}
                    aria-current={v.id === current.id ? "page" : undefined}
                    className={`flex h-8 items-center rounded-md px-2.5 text-left text-[0.8125rem] ${v.id === current.id ? "bg-plate-hi text-ink" : "text-ink-dim hover:bg-plate hover:text-ink"}`}
                  >
                    <span className="flex-1 truncate">{v.label}</span>
                    {badge > 0 && <span className={`type-data rounded px-1.5 ${v.id === "approvals" ? "bg-ember-deep text-ember" : "bg-plate-hi text-ink-dim"}`}>{badge}</span>}
                  </button>
                );
              })}
            </nav>
            <div className="relative flex min-w-0 flex-1 flex-col">
              <header className="flex h-12 shrink-0 items-center border-b border-seam px-6">
                <h2 className="type-display text-[1.1rem] text-ink">{current.label}</h2>
                <button type="button" onClick={() => open(null)} aria-label="Schließen" className="ml-auto rounded p-1.5 text-ink-faint hover:text-ink">
                  <X className="size-4" />
                </button>
              </header>
              <div className="relative min-h-0 flex-1">
                <View />
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
