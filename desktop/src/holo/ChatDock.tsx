// Bottom: the conversation. Collapsed it is one line of input plus the last exchange; it grows with the
// conversation and when Sir focuses it, and shrinks again when the brain is in use.
import { AnimatePresence, motion } from "framer-motion";
import { ArrowUp, CircleStop, Loader2, Mic, MicOff } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApprovalCard } from "../components/ApprovalCard";
import { AttachButton, AttachmentTray, DropOverlay } from "../components/chat/Attachments";
import { ChatTimeline } from "../components/chat/ChatTimeline";
import { ModelPicker } from "../components/ModelPicker";
import { useAttachmentDraft } from "../hooks/useAttachmentDraft";
import { useSlash } from "../hooks/useSlash";
import { useStickToBottom } from "../hooks/useStickToBottom";
import { buildRows } from "../lib/timeline";
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

export function ChatDock() {
  const sessionId = useApp((s) => s.activeSessionId);
  const setActive = useApp((s) => s.setActiveSession);
  const thread = useApp((s) => (sessionId ? s.threads[sessionId] : undefined));
  const loadThread = useApp((s) => s.loadThread);
  const sendMessage = useApp((s) => s.sendMessage);
  const pushToast = useApp((s) => s.pushToast);
  const voice = useApp((s) => s.voice);
  const pending = useApp((s) => s.pendingApprovals);
  const approvalSessions = useApp((s) => s.approvalSessions);
  const greeting = useApp((s) => s.greeting);
  const [draft, setDraft] = useState("");
  const [focused, setFocused] = useState(false);
  const [sending, setSending] = useState(false);
  const [ptt, setPtt] = useState(false);
  const [chatUsedAt, setChatUsedAt] = useState(0);
  const brainUsedAt = useBrain((s) => s.lastInteraction);
  const input = useRef<HTMLTextAreaElement>(null);
  const onAttachErrors = useCallback(
    (errors: string[]) => pushToast({ tone: "bad", title: errors.length > 1 ? "Dateien nicht angehängt" : "Datei nicht angehängt", body: errors.join(" ") }),
    [pushToast],
  );
  // the dock is the chat of the main window: files dropped anywhere on the window are attached here
  const attach = useAttachmentDraft({ onErrors: onAttachErrors, scope: "window" });

  useEffect(() => {
    if (!sessionId || useApp.getState().threads[sessionId]?.loaded) return;
    void loadThread(sessionId).catch(() => undefined);
  }, [sessionId, loadThread]);

  const slash = useSlash({ draft, setDraft, sessionId, sendText, onNew: () => setActive(null) });
  const rowCount = useMemo(() => (thread ? buildRows(thread.events).length : 0), [thread]);
  const running = Boolean(thread?.running);
  // a longer conversation stays open – until Sir turns to the brain; focusing the chat opens it again
  const expanded = focused || (rowCount > 2 && brainUsedAt <= chatUsedAt);
  const sessionApprovals = pending.filter((a) => sessionId && approvalSessions[a.id] === sessionId);
  // growing on expand is followed by the hook's ResizeObserver; new events by the signal
  const { ref: scrollRef, onScroll, toBottom } = useStickToBottom<HTMLDivElement>(thread?.events);

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
    const withFiles = attach.items.length > 0;
    if ((!text && !withFiles) || sending) return;
    if (!withFiles && (await slash.tryRun(text))) return;
    setDraft("");
    await sendText(text, withFiles);
  }

  async function sendText(text: string, withFiles = false) {
    setSending(true);
    toBottom(false);
    try {
      const files = withFiles ? await attach.upload() : [];
      const sid = await sendMessage(text, sessionId, files);
      if (withFiles) attach.clear();
      if (sid !== sessionId) setActive(sid);
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
      className="holo-dock pointer-events-auto relative mx-auto flex w-[min(44rem,calc(100vw-2rem))] flex-col"
      aria-label="Gespräch mit J.A.R.V.I.S."
      onFocus={() => {
        setFocused(true);
        setChatUsedAt(performance.now());
      }}
      onBlur={(e) => !e.currentTarget.contains(e.relatedTarget as Node) && setFocused(false)}
    >
      <AnimatePresence initial={false}>
        {(rowCount > 0 || sessionApprovals.length > 0 || (!sessionId && greeting && focused)) && (
          <motion.div
            key="log"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden"
          >
            <div
              ref={scrollRef}
              onScroll={onScroll}
              className={`overflow-y-auto px-5 pt-4 pb-2 [overflow-anchor:none] ${expanded ? "max-h-[38vh]" : "max-h-[8.5rem]"}`}
            >
              <div className="flex flex-col gap-3">
                {!sessionId && greeting && rowCount === 0 && <p className="text-[0.875rem] text-ink-dim">{greeting}</p>}
                {thread && <ChatTimeline thread={thread} compact lastRows={expanded ? 24 : 2} />}
                {sessionApprovals.map((a) => (
                  <ApprovalCard key={a.id} approval={a} compact />
                ))}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <DropOverlay active={attach.dragging} />
      <form
        className="relative flex flex-col gap-1 px-3 pb-2.5 pt-2"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        {slash.menu}
        {attach.items.length > 0 && (
          <div className="px-1 pt-1">
            <AttachmentTray items={attach.items} onRemove={attach.remove} disabled={sending} />
          </div>
        )}
        <textarea
          ref={input}
          rows={1}
          value={draft}
          onPaste={attach.onPaste}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (slash.onKeyDown(e)) return;
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void send();
            }
            if (e.key === "Escape") (e.target as HTMLTextAreaElement).blur();
          }}
          placeholder={listening ? "Ich höre zu …" : speaking ? "J.A.R.V.I.S. spricht …" : "Frag J.A.R.V.I.S. – „/“ für Befehle – oder sag „Hey Jarvis“"}
          aria-label="Nachricht an J.A.R.V.I.S."
          className="max-h-40 min-h-9 w-full resize-none bg-transparent px-2 py-2 text-[0.9rem] text-ink placeholder:text-ink-faint focus:outline-none"
        />
        <div className="flex items-center gap-2">
          <AttachButton onFiles={attach.add} disabled={sending} className="size-8" />
          <ModelPicker placement="up" />
          <div className="ml-auto flex items-center gap-2">
            {(listening || speaking) && <Waveform active source={speaking ? "output" : "input"} />}
            <button
              type="button"
              disabled={!voiceOk}
              onPointerDown={() => void pushToTalk(true)}
              onPointerUp={() => ptt && void pushToTalk(false)}
              onPointerLeave={() => ptt && void pushToTalk(false)}
              aria-label={voiceOk ? "Gedrückt halten zum Sprechen" : "Sprachsteuerung nicht verfügbar"}
              title={voiceOk ? "Gedrückt halten zum Sprechen – oder einfach „Hey Jarvis“" : voice.muted ? "Mikrofon stumm" : "Sprachdienst nicht verbunden"}
              className={`flex size-9 shrink-0 items-center justify-center rounded-full border transition-colors ${
                listening ? "border-arc bg-arc-deep text-arc" : "border-seam text-ink-dim hover:border-seam-hi hover:text-ink disabled:opacity-40"
              }`}
            >
              {voiceOk ? <Mic className="size-4" strokeWidth={1.7} /> : <MicOff className="size-4" strokeWidth={1.7} />}
            </button>
            {running || speaking ? (
              <button
                type="button"
                onClick={() => void api.interruptChat().catch(() => undefined)}
                aria-label={running ? "Antwort abbrechen" : "Sprachausgabe stoppen"}
                title={running ? "Antwort abbrechen (stoppt auch die Sprachausgabe)" : "Sprachausgabe stoppen"}
                className="flex size-9 shrink-0 items-center justify-center rounded-full border border-seam-hi text-ink-dim hover:text-ink"
              >
                <CircleStop className="size-4" strokeWidth={1.7} />
              </button>
            ) : (
              <button
                type="submit"
                disabled={(!draft.trim() && attach.items.length === 0) || sending}
                aria-label="Senden"
                className="flex size-9 shrink-0 items-center justify-center rounded-full bg-arc text-void transition-opacity disabled:opacity-25"
              >
                {sending ? <Loader2 className="spin-slow size-4" /> : <ArrowUp className="size-4" strokeWidth={2} />}
              </button>
            )}
          </div>
        </div>
      </form>
      {slash.dialog}
    </motion.section>
  );
}
