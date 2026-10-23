import { useCallback, useEffect, useState } from "react";
import { RemoteCoreForm, SignIn } from "./components/ConnectionForms";
import { ErrorState } from "./components/ui";
import { loadConnection, NeedsSignIn } from "./lib/connection";
import { inTauri, isMac, nativeLog, onServices, onTrayAction, onTrayRequest } from "./lib/native";
import { HoloApp } from "./holo/HoloApp";
import { api, startRealtime, useApp } from "./store/app";

export function App() {
  const token = useApp((s) => s.token);
  const tokenError = useApp((s) => s.tokenError);
  const setToken = useApp((s) => s.setToken);
  const setConnection = useApp((s) => s.setConnection);
  const pushToast = useApp((s) => s.pushToast);
  const [signIn, setSignIn] = useState(false);

  const connect = useCallback(() => {
    loadConnection()
      .then((c) => {
        setSignIn(false);
        setConnection(c);
        nativeLog(c.remote ? "remote core connection loaded" : "core token loaded");
        startRealtime();
        void useApp.getState().refreshStatus();
        api.greeting().then((g) => useApp.setState({ greeting: g.text })).catch(() => undefined);
      })
      .catch((e: unknown) => {
        if (e instanceof NeedsSignIn) {
          setSignIn(true);
          return;
        }
        setToken(null, e instanceof Error ? e.message : String(e));
      });
  }, [setConnection, setToken]);

  useEffect(() => {
    connect();
    const unlisten = onTrayAction((a) =>
      pushToast({ tone: a.ok ? "ok" : "bad", title: a.ok ? "Erledigt" : "Aktion fehlgeschlagen", body: a.detail || a.action }),
    );
    // client of a remote Core: tray actions arrive here and go out over HTTPS
    const unlistenRequests = onTrayRequest((r) => {
      const run = r.path === "/api/system/killswitch" ? api.setKillSwitch(true, "Not-Aus über die Menüleiste")
        : api.voiceCommand(r.body?.action === "ptt_stop" ? "ptt_stop" : "ptt_start");
      run.then(() => pushToast({ tone: "ok", title: "Erledigt", body: r.action }))
        .catch((e: unknown) => pushToast({ tone: "bad", title: "Aktion fehlgeschlagen", body: e instanceof Error ? e.message : String(e) }));
    });
    const unlistenServices = onServices((e) => {
      if (e.state === "error") pushToast({ tone: "bad", title: "Dienste konnten nicht starten", body: e.detail ?? "" });
      if (e.state === "running") void useApp.getState().refreshStatus();
    });
    return () => {
      void unlisten.then((u) => u());
      void unlistenRequests.then((u) => u());
      void unlistenServices.then((u) => u());
    };
  }, [connect, pushToast]);

  if (signIn) return <SignIn onDone={connect} />;
  if (tokenError) {
    return (
      <div className="flex h-full items-center justify-center p-10">
        <div className="flex max-w-xl flex-col gap-6">
          <ErrorState message={`Kein Zugang zum Core: ${tokenError}`} />
          {inTauri() && !isMac() ? (
            <RemoteCoreForm onSaved={connect} />
          ) : (
            <p className="text-sm text-ink-dim">Einmalig einrichten: <span className="type-data">scripts/setup.sh</span> (legt Zugangsschlüssel an und installiert die App).</p>
          )}
        </div>
      </div>
    );
  }
  if (!token) return <div className="h-full bg-void" />;

  return <HoloApp />;
}
