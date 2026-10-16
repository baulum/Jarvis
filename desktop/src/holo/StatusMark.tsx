// Top-left: who is talking to whom. The reactor glyph, one word for the state, and the last real action.
import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useState } from "react";
import { Reactor, reactorMode } from "../components/Reactor";
import { useApp } from "../store/app";
import { deriveJarvisState, STATE_LABEL, type JarvisState } from "./graph/live";
import { useBrain } from "./store";

function useNow(intervalMs = 500): number {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    const id = setInterval(() => setNow(performance.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

function useJarvisState(now: number): JarvisState {
  const voice = useApp((s) => s.voice.state ?? null);
  const connection = useApp((s) => s.connection);
  const pending = useApp((s) => s.pendingApprovals.length);
  const running = useApp((s) => Object.values(s.threads).some((t) => t.running));
  const live = useBrain((s) => s.live);
  return deriveJarvisState({ voice, connection, pendingApprovals: pending, agentRunning: running, live, now });
}

const TONE: Record<JarvisState, string> = {
  idle: "text-ink-dim",
  listening: "text-arc",
  thinking: "text-arc",
  retrieving: "text-arc",
  tools: "text-arc",
  coding: "text-arc",
  speaking: "text-arc",
  approval: "text-ember",
  error: "text-alarm",
};

export function StatusMark() {
  const now = useNow();
  const state = useJarvisState(now);
  const voice = useApp((s) => s.voice);
  const connection = useApp((s) => s.connection);
  const lines = useBrain((s) => s.live.lines);
  const select = useBrain((s) => s.select);
  const last = lines[lines.length - 1];
  const fresh = last && now - last.ts < 12_000;
  return (
    <div className="pointer-events-auto flex items-start gap-3" data-tauri-drag-region>
      <Reactor mode={reactorMode(voice.state, Boolean(voice.connected), Boolean(voice.muted))} level={voice.level ?? 0} size={34} label="J.A.R.V.I.S." />
      <div className="min-w-0 leading-tight" data-tauri-drag-region>
        <div className="flex items-baseline gap-2">
          <span className="type-display text-[0.95rem] tracking-[0.08em] text-ink">J.A.R.V.I.S.</span>
          {connection !== "open" && (
            <span className="type-data text-[0.62rem] text-ember">{connection === "unauthorized" ? "Token abgelehnt" : "verbindet …"}</span>
          )}
        </div>
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={state}
            initial={{ opacity: 0, y: 3 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.25 }}
            className={`mt-0.5 text-xs ${TONE[state]}`}
            role="status"
            aria-live="polite"
          >
            {STATE_LABEL[state]}
          </motion.div>
        </AnimatePresence>
        <AnimatePresence>
          {fresh && last && (
            <motion.button
              key={last.ts}
              type="button"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => last.node && select(last.node)}
              className={`mt-1 block max-w-[22rem] truncate text-left text-2xs ${last.tone === "bad" ? "text-alarm" : last.tone === "warn" ? "text-ember" : "text-ink-faint hover:text-ink-dim"}`}
              title={last.text}
            >
              {last.text}
            </motion.button>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
