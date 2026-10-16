// Bottom: the conversation. Collapsed it is one line of input plus the last exchange; it grows with the
// conversation and when Sir focuses it, and shrinks again when the brain is in use.
import { AnimatePresence, motion } from "framer-motion";
import { ArrowUp, CircleStop, Loader2, Mic, MicOff } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApprovalCard } from "../components/ApprovalCard";
import { emptyThread, threadFromHistory, type UiMessage } from "../lib/reducers";
import { api, useApp } from "../store/app";
import { useBrain } from "./store";

function Waveform({ active, source }: { active: boolean; source: "input" | "output" }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    let raf = 0;
    const hist = new Array<number>(48).fill(0);
    let smooth = 0;
    const draw = () => {
      raf = requestAnimationFrame(draw);
      const el = canvas.current;
      const ctx = el?.getContext("2d");
      if (!el || !ctx) return;
      const lv = useBrain.getState().level;
      const fresh = performance.now() - lv.ts < 500;
      const target = active && fresh ? Math.min(1, (source === "input" ? lv.input * 3 : lv.output * 2.2)) : 0;
      smooth += (target - smooth) * 0.25;
      hist.push(smooth);
      hist.shift();
      const w = el.width;
      const h = el.height;
      ctx.clearRect(0, 0, w, h);
      const bw = w / hist.length;
      ctx.fillStyle = "#6ad3e4";
      hist.forEach((v, i) => {
        const bh = Math.max(2, v * h * 0.9);
        ctx.globalAlpha = 0.25 + (i / hist.length) * 0.75;
        ctx.fillRect(i * bw + bw * 0.25, (h - bh) / 2, bw * 0.5, bh);
      });
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [active, source]);
  return <canvas ref={canvas} width={192} height={28} className="h-7 w-24" aria-hidden />;
}

function Message({ m }: { m: UiMessage }) {
  const select = useBrain((s) => s.select);
  if (m.role === "user") {
    return <p className="selectable text-right text-[0.8125rem] text-ink-dim">{m.text}</p>;
  }
  return (
    <div className="selectable text-[0.875rem] leading-relaxed text-ink">
      {m.tools.length > 0 && (
        <div className="mb-1.5 flex flex-wrap gap-1.5">
          {m.tools.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => select(`tool:${t.name}`)}
              className={`type-data rounded border px-1.5 py-px text-[0.62rem] ${
                t.status === "running" ? "border-arc-dim text-arc" : t.status === "failed" ? "border-alarm/50 text-alarm" : "border-seam-hi text-ink-faint"
              }`}
              title={t.error ?? t.args ?? t.name}
            >
              {t.name}
            </button>
          ))}
        </div>
      )}
      <span className={m.streaming ? "caret" : ""}>{m.text}</span>
      {m.interrupted && <span className="ml-2 text-2xs text-ink-faint">unterbrochen</span>}
      {m.error && m.error !== "cancelled" && <span className="ml-2 text-2xs text-alarm">{m.error}</span>}
    </div>
  );
}

export function ChatDock() {
  const sessionId = useApp((s) => s.activeSessionId);
  const setActive = useApp((s) => s.setActiveSession);
  const thread = useApp((s) => (sessionId ? s.threads[sessionId] : undefined));
  const setThread = useApp((s) => s.setThread);
  const pushToast = useApp((s) => s.pushToast);
  const voice = useApp((s) => s.voice);
  const pending = useApp((s) => s.pendingApprovals);
  const approvalSessions = useApp((s) => s.approvalSessions);
  const greeting = useApp((s) => s.greeting);
  const [draft, setDraft] = useState("");
  const [focused, setFocused] = useState(false);
  const [sending, setSending] = useState(false);
  const [ptt, setPtt] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!sessionId || useApp.getState().threads[sessionId]?.messages.length) return;
    let cancelled = false;
    api
      .sessionMessages(sessionId)
      .then((rows) => !cancelled && !useApp.getState().threads[sessionId]?.running && setThread(threadFromHistory(sessionId, rows)))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [sessionId, setThread]);

  const messages = useMemo(() => thread?.messages ?? [], [thread?.messages]);
  const running = Boolean(thread?.running);
  const expanded = focused || messages.length > 2;
  const shown = expanded ? messages.slice(-24) : messages.slice(-2);
  const sessionApprovals = pending.filter((a) => sessionId && approvalSessions[a.id] === sessionId);

  const lastText = messages[messages.length - 1]?.text;
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [shown.length, lastText, expanded]);

  // Enter focuses the input from anywhere (unless typing elsewhere)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
      if (!typing && e.key === "Enter") {
        e.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  async function send() {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    setDraft("");
    try {
      const res = await api.sendChat(text, sessionId);
      const st = useApp.getState();
      const t = st.threads[res.session_id] ?? emptyThread(res.session_id);
      if (!t.messages.some((m) => m.role === "user" && m.text === text && !m.pending)) {
        st.setThread({
          ...t,
          running: true,
          messages: [...t.messages, { id: `local-${Date.now()}`, role: "user", text, tools: [], streaming: false, pending: true, ts: new Date().toISOString() }],
        });
      }
      if (res.session_id !== sessionId) setActive(res.session_id);
    } catch (e) {
      setDraft(text);
      pushToast({ tone: "bad", title: "Nachricht nicht gesendet", body: (e as Error).message });
    } finally {
      setSending(false);
    }
  }

  async function pushToTalk(on: boolean) {
    setPtt(on);
    try {
      await api.voiceCommand(on ? "ptt_start" : "ptt_stop");
    } catch (e) {
      setPtt(false);
      pushToast({ tone: "bad", title: "Sprachsteuerung nicht erreichbar", body: (e as Error).message });
    }
  }

  const listening = voice.state === "listening" || voice.state === "recording" || ptt;
  const speaking = voice.state === "speaking";
  const voiceOk = Boolean(voice.connected) && !voice.muted;

  return (
    <motion.section
      layout
      transition={{ type: "spring", stiffness: 380, damping: 38 }}
      className="holo-dock pointer-events-auto mx-auto flex w-[min(44rem,calc(100vw-2rem))] flex-col"
      aria-label="Gespräch mit J.A.R.V.I.S."
      onFocus={() => setFocused(true)}
      onBlur={(e) => !e.currentTarget.contains(e.relatedTarget as Node) && setFocused(false)}
    >
      <AnimatePresence initial={false}>
        {(shown.length > 0 || sessionApprovals.length > 0 || (!sessionId && greeting && focused)) && (
          <motion.div
            key="log"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden"
          >
            <div ref={scroller} className={`flex flex-col gap-3 overflow-y-auto px-5 pt-4 pb-2 ${expanded ? "max-h-[38vh]" : "max-h-[8.5rem]"}`}>
              {!sessionId && greeting && shown.length === 0 && <p className="text-[0.875rem] text-ink-dim">{greeting}</p>}
              {shown.map((m) => (
                <Message key={m.id} m={m} />
              ))}
              {sessionApprovals.map((a) => (
                <ApprovalCard key={a.id} approval={a} compact />
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <form
        className="flex items-end gap-2 px-3 py-2.5"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <button
          type="button"
          disabled={!voiceOk}
          onPointerDown={() => void pushToTalk(true)}
          onPointerUp={() => ptt && void pushToTalk(false)}
          onPointerLeave={() => ptt && void pushToTalk(false)}
          aria-label={voiceOk ? "Gedrückt halten zum Sprechen" : "Sprachsteuerung nicht verfügbar"}
          title={voiceOk ? "Gedrückt halten zum Sprechen – oder einfach „Hey Jarvis“" : voice.muted ? "Mikrofon stumm" : "Sprachdienst nicht verbunden"}
          className={`flex size-9 shrink-0 items-center justify-center rounded-full border transition-colors ${
            listening ? "border-arc bg-arc-deep text-arc" : "border-seam-hi text-ink-dim hover:text-ink disabled:opacity-40"
          }`}
        >
          {voiceOk ? <Mic className="size-4" strokeWidth={1.7} /> : <MicOff className="size-4" strokeWidth={1.7} />}
        </button>
        <div className="relative min-w-0 flex-1">
          <textarea
            ref={input}
            rows={1}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
              if (e.key === "Escape") (e.target as HTMLTextAreaElement).blur();
            }}
            placeholder={listening ? "Ich höre zu …" : speaking ? "J.A.R.V.I.S. spricht …" : "Frag J.A.R.V.I.S. – oder sag „Hey Jarvis“"}
            aria-label="Nachricht an J.A.R.V.I.S."
            className="max-h-32 min-h-9 w-full resize-none bg-transparent py-2 text-[0.875rem] text-ink placeholder:text-ink-faint focus:outline-none"
          />
        </div>
        {(listening || speaking) && <Waveform active source={speaking ? "output" : "input"} />}
        {running ? (
          <button
            type="button"
            onClick={() => void api.interruptChat().catch(() => undefined)}
            aria-label="Antwort abbrechen"
            className="flex size-9 shrink-0 items-center justify-center rounded-full border border-seam-hi text-ink-dim hover:text-ink"
          >
            <CircleStop className="size-4" strokeWidth={1.7} />
          </button>
        ) : (
          <button
            type="submit"
            disabled={!draft.trim() || sending}
            aria-label="Senden"
            className="flex size-9 shrink-0 items-center justify-center rounded-full bg-arc text-void transition-opacity disabled:opacity-25"
          >
            {sending ? <Loader2 className="spin-slow size-4" /> : <ArrowUp className="size-4" strokeWidth={2} />}
          </button>
        )}
      </form>
    </motion.section>
  );
}
