import { useEffect } from "react";
import { ErrorState } from "./components/ui";
import { loadCoreToken, nativeLog, onServices, onTrayAction } from "./lib/native";
import { HoloApp } from "./holo/HoloApp";
import { api, startRealtime, useApp } from "./store/app";

export function App() {
  const token = useApp((s) => s.token);
  const tokenError = useApp((s) => s.tokenError);
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

  return <HoloApp />;
}
