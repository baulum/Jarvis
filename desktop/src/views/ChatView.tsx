import { AnimatePresence, motion } from "framer-motion";
import { Check, CircleStop, Loader2, Plus, SendHorizontal, TriangleAlert, Wrench, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApprovalCard } from "../components/ApprovalCard";
import { Button, EmptyState, ErrorState, Skeleton } from "../components/ui";
import { useQuery } from "../hooks/useQuery";
import { formatMs, formatRelative } from "../lib/format";
import { emptyThread, threadFromHistory, type ToolChip, type UiMessage } from "../lib/reducers";
import { Markdown } from "../components/Markdown";
import { ModelPicker } from "../components/ModelPicker";
import { useSlash } from "../hooks/useSlash";
import { api, useApp } from "../store/app";

export function ChatView() {
  const activeSessionId = useApp((s) => s.activeSessionId);
  const setActive = useApp((s) => s.setActiveSession);
  const sessions = useQuery(() => api.sessions(50), "sessions", { topics: ["session"] });

  return (
    <div className="flex h-full">
      <aside className="flex w-[264px] shrink-0 flex-col border-r border-seam">
        <div className="flex items-center justify-between px-4 py-3">
          <span className="text-xs text-ink-dim">Gespräche</span>
          <Button size="sm" variant="ghost" icon={<Plus className="size-3.5" />} onClick={() => setActive(null)}>
            Neues Gespräch
          </Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
          {sessions.loading && <div className="px-2"><Skeleton rows={6} /></div>}
          {sessions.error && <div className="px-2"><ErrorState message={sessions.error} onRetry={sessions.refetch} /></div>}
          {sessions.data && sessions.data.length === 0 && (
            <p className="px-2 py-4 text-xs text-ink-faint">Noch keine Gespräche. Schreib unten die erste Nachricht.</p>
          )}
          <ul className="flex flex-col gap-px">
            {sessions.data?.map((s) => {
              const active = s.id === activeSessionId;
              return (
                <li key={s.id}>
                  <button
                    type="button"
                    onClick={() => setActive(s.id)}
                    className={`w-full rounded-md px-2.5 py-2 text-left transition-colors ${active ? "bg-plate-hi" : "hover:bg-plate"}`}
                  >
                    <span className={`line-clamp-2 text-[0.8125rem] ${active ? "text-ink" : "text-ink-dim"}`}>
                      {s.title || "Ohne Titel"}
                    </span>
                    <span className="mt-1 flex items-center gap-2 text-2xs text-ink-faint">
                      <span>{formatRelative(s.last_activity_at)}</span>
                      <span className="type-data">{s.channel}</span>
                      {s.tainted && (
                        <span className="text-ember" title="Enthält Inhalte aus nicht vertrauenswürdigen Quellen (Web, E-Mail)">
                          extern
                        </span>
                      )}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      </aside>
      <Conversation key={activeSessionId ?? "new"} sessionId={activeSessionId} />
    </div>
  );
}

function Conversation({ sessionId }: { sessionId: string | null }) {
  const thread = useApp((s) => (sessionId ? s.threads[sessionId] : undefined));
  const setThread = useApp((s) => s.setThread);
  const setActive = useApp((s) => s.setActiveSession);
  const greeting = useApp((s) => s.greeting);
  const pending = useApp((s) => s.pendingApprovals);
  const approvalSessions = useApp((s) => s.approvalSessions);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [interrupting, setInterrupting] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const pushToast = useApp((s) => s.pushToast);

  // load history unless a live thread for this session already exists
  useEffect(() => {
    if (!sessionId) return;
    const existing = useApp.getState().threads[sessionId];
    if (existing && existing.messages.length > 0) return;
    let cancelled = false;
    setLoading(true);
    api
      .sessionMessages(sessionId)
      .then((rows) => {
        if (cancelled) return;
        const live = useApp.getState().threads[sessionId];
        if (live && live.running) return;
        setThread(threadFromHistory(sessionId, rows));
        setLoadError(null);
      })
      .catch((e: Error) => !cancelled && setLoadError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [sessionId, setThread]);

  const slash = useSlash({ draft, setDraft, sessionId, sendText, onNew: () => setActive(null) });
  const messages = useMemo(() => thread?.messages ?? [], [thread?.messages]);
  const running = Boolean(thread?.running);
  const sessionApprovals = useMemo(
    () => pending.filter((a) => sessionId && approvalSessions[a.id] === sessionId),
    [pending, approvalSessions, sessionId],
  );

  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages, sessionApprovals.length]);

  async function send() {
    const text = draft.trim();
    if (!text || sending) return;
    if (await slash.tryRun(text)) return;
    setDraft("");
    await sendText(text);
  }

  async function sendText(text: string) {
    setSending(true);
    stick.current = true;
    try {
      const res = await api.sendChat(text, sessionId);
      const st = useApp.getState();
      const t = st.threads[res.session_id] ?? emptyThread(res.session_id);
      const echoed = t.messages.some((m) => m.role === "user" && m.text === text && !m.pending);
      if (!echoed) {
        st.setThread({
          ...t,
          running: true,
          messages: [
            ...t.messages,
            { id: `local-${Date.now()}`, role: "user", text, tools: [], streaming: false, pending: true, ts: new Date().toISOString() },
          ],
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

  async function interrupt() {
    setInterrupting(true);
    try {
      const r = await api.interruptChat();
      if (!r.interrupted) pushToast({ tone: "info", title: "Es läuft gerade keine Antwort." });
    } catch (e) {
      pushToast({ tone: "bad", title: "Unterbrechen fehlgeschlagen", body: (e as Error).message });
    } finally {
      setInterrupting(false);
    }
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <div
        ref={scroller}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        }}
        className="selectable min-h-0 flex-1 overflow-y-auto"
      >
        <div className="mx-auto flex w-full max-w-[52rem] flex-col gap-7 px-8 py-8">
          {loading && <Skeleton rows={5} />}
          {loadError && <ErrorState message={loadError} />}
          {!sessionId && messages.length === 0 && (
            <div className="pt-[12vh]">
              <p className="type-display text-[2rem] text-ink">{greeting ?? "Bereit."}</p>
              <p className="mt-3 max-w-[52ch] text-sm text-ink-dim">
                Neues Gespräch. Anfragen mit Außenwirkung landen als Freigabe hier im Verlauf und unter Freigaben.
              </p>
            </div>
          )}
          {sessionId && !loading && !loadError && messages.length === 0 && (
            <EmptyState title="Dieses Gespräch ist leer." />
          )}
          {messages.map((m) => (
            <MessageRow key={m.id} m={m} />
          ))}
          <AnimatePresence>
            {sessionApprovals.map((a) => (
              <motion.div key={a.id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
                <ApprovalCard approval={a} compact />
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      </div>
      <div className="border-t border-seam bg-hull/60 px-8 py-4">
        <div className="mx-auto mb-2 flex w-full max-w-[52rem]"><ModelPicker placement="up" /></div>
        <form
          className="relative mx-auto flex w-full max-w-[52rem] items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void send();
          }}
        >
          {slash.menu}
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (slash.onKeyDown(e)) return;
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void send();
              }
            }}
            rows={Math.min(6, Math.max(1, draft.split("\n").length))}
            placeholder="Nachricht an J.A.R.V.I.S. – Enter sendet, Shift+Enter neue Zeile, „/“ für Befehle"
            aria-label="Nachricht"
            className="field min-h-[2.4rem] flex-1 resize-none py-2 text-sm leading-relaxed"
          />
          {running && (
            <Button
              variant="danger"
              onClick={() => void interrupt()}
              busy={interrupting}
              icon={<CircleStop className="size-4" />}
              title="Laufende Antwort abbrechen"
            >
              Unterbrechen
            </Button>
          )}
          <Button type="submit" variant="primary" busy={sending} disabled={!draft.trim()} icon={<SendHorizontal className="size-4" />}>
            Senden
          </Button>
        </form>
      </div>
      {slash.dialog}
    </div>
  );
}

function MessageRow({ m }: { m: UiMessage }) {
  if (m.role === "user") {
    return (
      <div className="flex flex-col items-end">
        <div className={`max-w-[80%] rounded-lg border border-seam bg-plate px-3.5 py-2 text-sm whitespace-pre-wrap text-ink ${m.pending ? "opacity-70" : ""}`}>
          {m.text}
        </div>
      </div>
    );
  }
  const empty = !m.text && m.streaming;
  return (
    <div className="flex flex-col gap-2">
      {m.tools.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {m.tools.map((t) => (
            <ToolChipView key={t.key} t={t} />
          ))}
        </div>
      )}
      <div className="max-w-[72ch] text-[0.9375rem] leading-[1.7] text-ink">
        {empty ? <span className="text-ink-faint">Denkt nach</span> : <Markdown text={m.text} streaming={m.streaming} />}
      </div>
      {(m.error || m.interrupted) && (
        <p className="flex items-center gap-1.5 text-xs text-ember">
          <TriangleAlert className="size-3.5" />
          {m.interrupted || m.error === "cancelled" ? "Antwort wurde unterbrochen." : `Fehler: ${m.error}`}
        </p>
      )}
    </div>
  );
}

function ToolChipView({ t }: { t: ToolChip }) {
  const icon =
    t.status === "running" ? (
      <Loader2 className="size-3 spin-slow text-arc" />
    ) : t.status === "failed" ? (
      <X className="size-3 text-alarm" />
    ) : (
      <Check className="size-3 text-arc-dim" />
    );
  const title = [t.args && `Argumente: ${t.args}`, t.preview, t.error && `Fehler: ${t.error}`].filter(Boolean).join("\n");
  return (
    <span
      title={title || undefined}
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 ${t.status === "failed" ? "border-alarm/40" : t.status === "running" ? "border-arc-dim/60" : "border-seam-hi"}`}
    >
      <Wrench className="size-3 text-ink-faint" />
      <span className="type-data text-ink-dim">{t.name}</span>
      {t.durationMs !== undefined && <span className="type-data text-ink-faint">{formatMs(t.durationMs)}</span>}
      {icon}
    </span>
  );
}

