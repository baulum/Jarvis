// Global emergency stop. Engaging is one click (it only ever makes the system safer);
// releasing needs an explicit confirmation.
import { AnimatePresence, motion } from "framer-motion";
import { OctagonX } from "lucide-react";
import { useState } from "react";
import { api, useApp } from "../store/app";
import { Button, Dialog } from "./ui";

export function KillSwitch() {
  const ks = useApp((s) => s.status?.kill_switch);
  const refresh = useApp((s) => s.refreshStatus);
  const pushToast = useApp((s) => s.pushToast);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const engaged = Boolean(ks?.engaged);

  async function engage() {
    setBusy(true);
    try {
      await api.setKillSwitch(true, "Not-Aus über die Desktop-App");
      await refresh();
    } catch (e) {
      pushToast({ tone: "bad", title: "Not-Aus konnte nicht ausgelöst werden", body: String((e as Error).message ?? e) });
    } finally {
      setBusy(false);
    }
  }

  async function release() {
    setBusy(true);
    try {
      await api.setKillSwitch(false);
      setConfirm(false);
      await refresh();
    } catch (e) {
      pushToast({ tone: "bad", title: "Not-Aus konnte nicht aufgehoben werden", body: String((e as Error).message ?? e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => (engaged ? setConfirm(true) : void engage())}
        disabled={busy}
        aria-pressed={engaged}
        title={engaged ? "Not-Aus ist aktiv – klicken zum Aufheben" : "Alle Aktionen sofort stoppen"}
        className={`group flex h-8 items-center gap-2 rounded-md border pr-3 pl-2 text-[0.8125rem] font-medium transition-colors disabled:opacity-50 ${
          engaged
            ? "border-alarm bg-alarm text-void hover:bg-[#f06a61]"
            : "border-alarm/45 text-alarm hover:border-alarm hover:bg-alarm-deep"
        }`}
      >
        <OctagonX className="size-4" strokeWidth={1.8} />
        {engaged ? "Not-Aus aktiv" : "Not-Aus"}
      </button>
      <Dialog
        open={confirm}
        onClose={() => setConfirm(false)}
        title="Not-Aus aufheben?"
        tone="danger"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirm(false)} data-autofocus>
              Aktiv lassen
            </Button>
            <Button variant="danger" busy={busy} onClick={() => void release()}>
              Not-Aus aufheben
            </Button>
          </>
        }
      >
        <p>
          J.A.R.V.I.S. darf danach wieder Werkzeuge ausführen, Zeitpläne starten und Aufgaben fortsetzen.
        </p>
        {ks?.reason && (
          <p className="mt-3 text-xs">
            Ausgelöst mit Grund: <span className="text-ink">{ks.reason}</span>
          </p>
        )}
      </Dialog>
    </>
  );
}

export function KillSwitchBanner() {
  const ks = useApp((s) => s.status?.kill_switch);
  return (
    <AnimatePresence initial={false}>
      {ks?.engaged && (
        <motion.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: "auto", opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          className="overflow-hidden border-b border-alarm/40 bg-alarm-deep"
          role="status"
        >
          <div className="flex items-center gap-3 px-6 py-2 text-[0.8125rem]">
            <OctagonX className="size-4 text-alarm" />
            <span className="text-ink">Not-Aus aktiv. Es werden keine Aktionen ausgeführt.</span>
            {ks.reason && <span className="truncate text-ink-dim">Grund: {ks.reason}</span>}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
