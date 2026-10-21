import { AnimatePresence, motion } from "framer-motion";
import { ArrowDown, CircleStop, Plus, SendHorizontal } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ApprovalCard } from "../components/ApprovalCard";
import { ChatTimeline } from "../components/chat/ChatTimeline";
import { ModelPicker } from "../components/ModelPicker";
import { Button, EmptyState, ErrorState, Skeleton } from "../components/ui";
import { useQuery } from "../hooks/useQuery";
import { useSlash } from "../hooks/useSlash";
import { useStickToBottom } from "../hooks/useStickToBottom";
import { formatRelative } from "../lib/format";
import { emptyThread } from "../lib/timeline";
import { api, useApp } from "../store/app";

export function ChatView() {
  const activeSessionId = useApp((s) => s.activeSessionId);
  const setActive = useApp((s) => s.setActiveSession);
  const sessions = useQuery(() => api.sessions(50), "sessions", { topics: ["session"] });

  return (
    <div className="flex h-full">
      <aside className="flex w-[264px] shrink-0 flex-col border-r border-seam max-[900px]:hidden">
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
  const loadThread = useApp((s) => s.loadThread);
  const sendMessage = useApp((s) => s.sendMessage);
  const setActive = useApp((s) => s.setActiveSession);
  const greeting = useApp((s) => s.greeting);
  const pending = useApp((s) => s.pendingApprovals);
  const approvalSessions = useApp((s) => s.approvalSessions);
  const pushToast = useApp((s) => s.pushToast);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [interrupting, setInterrupting] = useState(false);
  const events = thread?.events;
  const { ref: scrollRef, onScroll, atBottom, toBottom, keepPosition } = useStickToBottom<HTMLDivElement>(events);
  const loading = Boolean(sessionId) && !thread?.loaded && !loadError;

  // the stored timeline is the source of truth; live events of a running answer merge into it by seq
  useEffect(() => {
    if (!sessionId || useApp.getState().threads[sessionId]?.loaded) return;
    let cancelled = false;
    loadThread(sessionId).catch((e: Error) => !cancelled && setLoadError(e.message));
    return () => {
      cancelled = true;
    };
  }, [sessionId, loadThread]);

  const slash = useSlash({ draft, setDraft, sessionId, sendText, onNew: () => setActive(null) });
  const running = Boolean(thread?.running);
  const empty = !events || events.length === 0;
  const sessionApprovals = useMemo(
    () => pending.filter((a) => sessionId && approvalSessions[a.id] === sessionId),
    [pending, approvalSessions, sessionId],
  );

  async function send() {
    const text = draft.trim();
    if (!text || sending) return;
    if (await slash.tryRun(text)) return;
    setDraft("");
    await sendText(text);
  }

  async function sendText(text: string) {
    setSending(true);
    toBottom(false);
    try {
      const sid = await sendMessage(text, sessionId);
      if (sid !== sessionId) setActive(sid);
    } catch (e) {
      setDraft(text);
      pushToast({ tone: "bad", title: "Nachricht nicht gesendet", body: (e as Error).message });
    } finally {
      setSending(false);
    }
  }

  async function loadOlder() {
    if (!sessionId) return;
    setLoadingOlder(true);
    keepPosition();
    try {
      await loadThread(sessionId, true);
    } catch (e) {
      pushToast({ tone: "bad", title: "Ältere Nachrichten nicht geladen", body: (e as Error).message });
    } finally {
      setLoadingOlder(false);
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
    <div className="relative flex min-w-0 flex-1 flex-col">
      <div ref={scrollRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto [overflow-anchor:none]">
        <div className="mx-auto flex w-full max-w-[52rem] flex-col gap-7 px-8 py-8 max-[700px]:px-4">
          {loading && <Skeleton rows={5} />}
          {loadError && (
            <ErrorState
              message={loadError}
              onRetry={() => {
                if (!sessionId) return;
                setLoadError(null);
                loadThread(sessionId).catch((e: Error) => setLoadError(e.message));
              }}
            />
          )}
          {!sessionId && empty && (
            <div className="pt-[12vh]">
              <p className="type-display text-[2rem] text-ink">{greeting ?? "Bereit."}</p>
              <p className="mt-3 max-w-[52ch] text-sm text-ink-dim">
                Neues Gespräch. Anfragen mit Außenwirkung landen als Freigabe hier im Verlauf und unter Freigaben.
              </p>
            </div>
          )}
          {sessionId && !loading && !loadError && empty && <EmptyState title="Dieses Gespräch ist leer." />}
          <ChatTimeline
            thread={thread ?? emptyThread(sessionId ?? "new")}
            onLoadOlder={() => void loadOlder()}
            loadingOlder={loadingOlder}
          />
          <AnimatePresence>
            {sessionApprovals.map((a) => (
              <motion.div key={a.id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
                <ApprovalCard approval={a} compact />
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      </div>
      {!atBottom && (
        <button
          type="button"
          onClick={() => toBottom()}
          className="absolute right-6 bottom-28 flex items-center gap-1.5 rounded-full border border-seam-hi bg-hull px-3 py-1.5 text-xs text-ink-dim shadow-lg hover:text-ink"
        >
          <ArrowDown className="size-3.5" />
          Zum Ende
        </button>
      )}
      <div className="border-t border-seam bg-hull/60 px-8 py-4 max-[700px]:px-4">
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
            placeholder="Nachricht an Jarvis – Enter sendet, Shift+Enter neue Zeile, „/“ für Befehle"
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
