import { AnimatePresence, motion } from "framer-motion";
import { useEffect, type ComponentType } from "react";
import { Shell } from "./components/Shell";
import { ErrorState } from "./components/ui";
import { loadCoreToken, nativeLog, onServices, onTrayAction } from "./lib/native";
import { api, startRealtime, useApp, type ViewId } from "./store/app";
import { AgentsView } from "./views/AgentsView";
import { ApprovalsView } from "./views/ApprovalsView";
import { ChatView } from "./views/ChatView";
import { ConnectorsView } from "./views/ConnectorsView";
import { LogsView } from "./views/LogsView";
import { MemoryView } from "./views/MemoryView";
import { NotificationsView } from "./views/NotificationsView";
import { PermissionsView } from "./views/PermissionsView";
import { SchedulerView } from "./views/SchedulerView";
import { SettingsView } from "./views/SettingsView";
import { SkillsView } from "./views/SkillsView";
import { SystemView } from "./views/SystemView";
import { TasksView } from "./views/TasksView";
import { VoiceView } from "./views/VoiceView";

const VIEWS: Record<ViewId, ComponentType> = {
  chat: ChatView, voice: VoiceView, tasks: TasksView, approvals: ApprovalsView, agents: AgentsView,
  logs: LogsView, memory: MemoryView, connectors: ConnectorsView, skills: SkillsView, scheduler: SchedulerView,
  notifications: NotificationsView, settings: SettingsView, permissions: PermissionsView, system: SystemView,
};

export function App() {
  const token = useApp((s) => s.token);
  const tokenError = useApp((s) => s.tokenError);
  const view = useApp((s) => s.view);
  const setToken = useApp((s) => s.setToken);
  const pushToast = useApp((s) => s.pushToast);

  useEffect(() => {
    loadCoreToken()
      .then((t) => {
        setToken(t);
        nativeLog("core token loaded");
        startRealtime();
        void useApp.getState().refreshStatus();
        api.greeting().then((g) => useApp.setState({ greeting: g.text })).catch(() => undefined);
      })
      .catch((e: unknown) => setToken(null, e instanceof Error ? e.message : String(e)));
    const unlisten = onTrayAction((a) =>
      pushToast({ tone: a.ok ? "ok" : "bad", title: a.ok ? "Erledigt" : "Aktion fehlgeschlagen", body: a.detail || a.action }),
    );
    const unlistenServices = onServices((e) => {
      if (e.state === "error") pushToast({ tone: "bad", title: "Dienste konnten nicht starten", body: e.detail ?? "" });
      if (e.state === "running") void useApp.getState().refreshStatus();
    });
    return () => {
      void unlisten.then((u) => u());
      void unlistenServices.then((u) => u());
    };
  }, [setToken, pushToast]);

  if (tokenError) {
    return (
      <div className="flex h-full items-center justify-center p-10">
        <div className="max-w-xl">
          <ErrorState message={`Kein Zugang zum Core: ${tokenError}`} />
          <p className="mt-3 text-sm text-ink-dim">Einmalig einrichten: <span className="type-data">scripts/setup.sh</span> (legt Zugangsschlüssel an und installiert die App).</p>
        </div>
      </div>
    );
  }
  if (!token) return <div className="h-full bg-void" />;

  const View = VIEWS[view];
  return (
    <Shell>
      <AnimatePresence mode="wait">
        <motion.div key={view} className="h-full" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0 }} transition={{ duration: 0.18, ease: [0.2, 0.7, 0.2, 1] }}>
          <View />
        </motion.div>
      </AnimatePresence>
    </Shell>
  );
}
