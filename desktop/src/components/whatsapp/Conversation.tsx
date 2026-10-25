// One WhatsApp chat: history with media, attachment actions, and a composer whose send goes through Sir's
// approval like every send of Jarvis (recipient, files and text are shown before anything leaves).
import { CheckCheck, Download, FolderDown, History, Lightbulb, Loader2, MonitorDown, Paperclip, Reply, Send, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useAction, useQuery } from "../../hooks/useQuery";
import { formatBytes } from "../../lib/format";
import { saveBytes } from "../../lib/native";
import {
  MEDIA_KINDS,
  groupByDay,
  kindLabel,
  receiptLabel,
  timeLabel,
  uploadWithProgress,
  type WaFile,
  type WaMessage,
  type WaUpload,
} from "../../lib/whatsapp";
import { api, useApp } from "../../store/app";
import { ApprovalCard } from "../ApprovalCard";
import { Button, EmptyState, ErrorState, IconButton, Skeleton, Tag, Toggle } from "../ui";
import { SaveDialog } from "./SaveDialog";
import { KindIcon, MediaPreview } from "./WaMedia";

interface Draft {
  key: string;
  name: string;
  size: number;
  progress: number;
  upload?: WaUpload;
  error?: string;
}

/** Mounted with `key={jid}`: switching chats starts with a fresh composer and history. */
export function Conversation({ jid, onClose }: { jid: string; onClose?: () => void }) {
  const q = useQuery(() => api.whatsapp.messages(jid), `wa-chat:${jid}`, { topics: ["whatsapp"] });
  const [older, setOlder] = useState<WaMessage[]>([]);
  const [replyTo, setReplyTo] = useState<WaMessage | null>(null);
  const [saving, setSaving] = useState<WaFile | null>(null);
  const act = useAction();
  const bottom = useRef<HTMLDivElement>(null);
  const messages = useMemo(() => {
    const seen = new Set((q.data?.messages ?? []).map((m) => m.id));
    return [...older.filter((m) => !seen.has(m.id)), ...(q.data?.messages ?? [])];
  }, [older, q.data]);
  const lastId = q.data?.messages.at(-1)?.id;

  useEffect(() => bottom.current?.scrollIntoView({ block: "end" }), [lastId]);

  const loadOlder = () =>
    void act.run("older", async () => {
      const first = messages[0];
      if (!first) return;
      const res = await api.whatsapp.messages(jid, first.ts);
      setOlder((prev) => [...res.messages, ...prev]);
    });

  if (q.loading) return <div className="p-6"><Skeleton rows={8} /></div>;
  if (q.error) return <div className="p-6"><ErrorState message={q.error} onRetry={q.refetch} /></div>;
  const chat = q.data!.chat;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-seam px-4">
        <div className="min-w-0 flex-1">
          <h3 className="type-title truncate text-[0.9rem] text-ink">{chat.name || jid}</h3>
          <p className="text-2xs text-ink-faint">{chat.is_group ? "Gruppe" : "Einzelchat"}</p>
        </div>
        <Button size="sm" variant="ghost" icon={<CheckCheck className="size-3.5" />} busy={act.busy === "read"} disabled={!chat.unread}
                onClick={() => void act.run("read", () => api.whatsapp.markRead(jid), "Als gelesen markiert").then(q.refetch)}>
          Gelesen
        </Button>
        <Button size="sm" variant="ghost" icon={<Lightbulb className="size-3.5" />} busy={act.busy === "suggest"}
                onClick={() => void act.run("suggest", async () => {
                  const r = await api.whatsapp.suggest(jid);
                  useApp.getState().pushToast({ tone: "info", title: r.created.length ? `${r.created.length} Vorschläge erkannt` : "Keine Vorschläge erkannt", action: r.created.length ? { label: "Ansehen", view: "whatsapp" } : undefined });
                })}>
          Vorschläge erkennen
        </Button>
        {onClose && <IconButton label="Chat schließen" onClick={onClose}><X className="size-4" /></IconButton>}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        <div className="mb-3 flex justify-center gap-2">
          {q.data!.has_more && <Button size="sm" variant="ghost" busy={act.busy === "older"} onClick={loadOlder}>Ältere Nachrichten</Button>}
          <Button size="sm" variant="ghost" icon={<History className="size-3.5" />} busy={act.busy === "history"}
                  onClick={() => void act.run("history", () => api.whatsapp.loadHistory(jid), "Ältere Nachrichten beim Handy angefordert")}>
            Vom Handy nachladen
          </Button>
        </div>
        {messages.length === 0 && <EmptyState title="Noch keine Nachrichten gespeichert." />}
        {groupByDay(messages).map((g) => (
          <section key={g.day} aria-label={g.day}>
            <p className="my-3 text-center text-2xs text-ink-faint">{g.day}</p>
            <ul className="flex flex-col gap-1.5">
              {g.messages.map((m) => (
                <MessageRow key={m.id} m={m} group={chat.is_group} onReply={() => setReplyTo(m)} onSave={setSaving} onChanged={q.refetch} />
              ))}
            </ul>
          </section>
        ))}
        <div ref={bottom} />
      </div>

      <Composer jid={jid} replyTo={replyTo} onClearReply={() => setReplyTo(null)} />
      {saving && <SaveDialog file={saving} onClose={() => setSaving(null)} />}
    </div>
  );
}

function MessageRow({ m, group, onReply, onSave, onChanged }: {
  m: WaMessage;
  group: boolean;
  onReply: () => void;
  onSave: (f: WaFile) => void;
  onChanged: () => void;
}) {
  const act = useAction();
  const file = m.files?.find((f) => f.location === "server");
  const media = MEDIA_KINDS.has(m.kind);
  if (m.kind === "system") {
    return <li className="text-center text-2xs text-ink-faint">Hinweis im Chat</li>;
  }
  const toThisDevice = async (f: WaFile) => {
    const blob = await api.whatsapp.fileContent(f.file_id, true);
    const ext = f.name.includes(".") ? f.name.split(".").pop()! : "";
    return saveBytes(f.name, new Uint8Array(await blob.arrayBuffer()), { name: f.type, extensions: ext ? [ext] : [], mime: f.mime });
  };
  return (
    <li className={`group flex ${m.from_me ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[min(34rem,78%)] rounded-lg border px-3 py-2 ${m.from_me ? "border-arc-deep bg-arc-deep/45" : "border-seam bg-plate-hi"}`}>
        {group && !m.from_me && <p className="mb-0.5 text-2xs text-arc-dim">{m.sender_name || "Teilnehmer"}</p>}
        {m.forwarded && <p className="mb-0.5 text-2xs text-ink-faint">Weitergeleitet</p>}
        {m.deleted ? (
          <p className="text-sm text-ink-faint">Diese Nachricht wurde gelöscht.</p>
        ) : (
          <>
            {media && (
              <div className="mb-1.5 flex flex-col gap-1.5">
                <MediaPreview chatJid={m.chat_jid} msgId={m.id} hasThumb={!!m.has_thumbnail} fileId={file?.file_id} category={file?.type}
                              alt={m.media?.fileName ?? kindLabel(m.kind)} />
                <div className="flex items-center gap-2 text-xs text-ink-dim">
                  <KindIcon kind={m.kind} className="size-3.5 shrink-0" />
                  <span className="min-w-0 truncate">{m.media?.fileName ?? kindLabel(m.kind)}</span>
                  {m.media?.fileLength ? <span className="type-data text-ink-faint">{formatBytes(m.media.fileLength)}</span> : null}
                  {m.media?.seconds ? <span className="type-data text-ink-faint">{m.media.seconds} s</span> : null}
                </div>
                <div className="flex flex-wrap gap-1">
                  {!file ? (
                    <Button size="sm" variant="ghost" icon={<Download className="size-3.5" />} busy={act.busy === "dl"}
                            onClick={() => void act.run("dl", () => api.whatsapp.download(m.chat_jid, m.id), "Auf dem Jarvis-Server gespeichert").then(onChanged)}>
                      Herunterladen
                    </Button>
                  ) : (
                    <>
                      <Button size="sm" variant="ghost" icon={<MonitorDown className="size-3.5" />} busy={act.busy === "local"}
                              onClick={() => void act.run("local", () => toThisDevice(file))}>Auf dieses Gerät</Button>
                      <Button size="sm" variant="ghost" icon={<FolderDown className="size-3.5" />} onClick={() => onSave(file)}>Speichern unter …</Button>
                    </>
                  )}
                </div>
              </div>
            )}
            {m.text && <p className="selectable text-sm break-words whitespace-pre-wrap text-ink">{m.text}</p>}
            {m.location && <p className="text-sm text-ink">Standort {m.location.name ?? `${m.location.latitude.toFixed(4)}, ${m.location.longitude.toFixed(4)}`}</p>}
            {m.poll && <p className="text-sm text-ink">Umfrage: {m.poll.name} ({m.poll.options.join(", ")})</p>}
            {m.contact && <p className="text-sm text-ink">Kontaktkarte: {m.contact.displayName}</p>}
            {m.kind === "unsupported" && !m.text && <p className="text-xs text-ink-faint">Dieser Nachrichtentyp wird nicht angezeigt.</p>}
          </>
        )}
        <div className="mt-1 flex items-center justify-end gap-2 text-2xs text-ink-faint">
          {m.reactions?.length ? <span>{m.reactions.map((r) => r.emoji).join(" ")}</span> : null}
          {m.edited && <span>bearbeitet</span>}
          <span className="type-data">{timeLabel(m.ts)}</span>
          {m.from_me && m.status && <span className={m.status === "error" ? "text-alarm" : m.status === "read" ? "text-arc" : ""}>{receiptLabel(m.status)}</span>}
          {!m.deleted && (
            <button type="button" aria-label="Antworten" title="Antworten" onClick={onReply} className="opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100 hover:text-ink">
              <Reply className="size-3.5" />
            </button>
          )}
        </div>
      </div>
    </li>
  );
}

function Composer({ jid, replyTo, onClearReply }: { jid: string; replyTo: WaMessage | null; onClearReply: () => void }) {
  const [text, setText] = useState("");
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [asDocument, setAsDocument] = useState(false);
  const [approvalId, setApprovalId] = useState<string | null>(null);
  const [summary, setSummary] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const pushToast = useApp((s) => s.pushToast);
  const approval = useApp((s) => s.pendingApprovals.find((a) => a.id === approvalId));
  const uploading = drafts.some((d) => !d.upload && !d.error);
  const ready = drafts.filter((d) => d.upload);

  const addFiles = (files: FileList | null) => {
    for (const f of Array.from(files ?? []).slice(0, 10)) {
      const key = `${f.name}-${f.size}-${Math.random().toString(36).slice(2, 7)}`;
      setDrafts((d) => [...d, { key, name: f.name, size: f.size, progress: 0 }]);
      const { url, token } = api.whatsapp.uploadTarget(f.name);
      uploadWithProgress(url, token ?? "", f, (p) => setDrafts((d) => d.map((x) => (x.key === key ? { ...x, progress: p } : x))))
        .then((upload) => setDrafts((d) => d.map((x) => (x.key === key ? { ...x, upload, progress: 1 } : x))))
        .catch((e: Error) => setDrafts((d) => d.map((x) => (x.key === key ? { ...x, error: e.message } : x))));
    }
  };

  const send = async () => {
    setBusy(true);
    try {
      const res = await api.whatsapp.send({
        chat: jid,
        text: text.trim(),
        files: ready.map((d) => d.upload!.ref),
        as_document: asDocument,
        ...(replyTo ? { reply_to: replyTo.id } : {}),
      });
      if (res.status === "approval_required" && res.approval_id) {
        setApprovalId(res.approval_id);
        setSummary(res.summary ?? null);
        void useApp.getState().refreshApprovals();
      } else if (res.status === "duplicate") {
        pushToast({ tone: "info", title: "Bereits gesendet", body: res.message });
      }
      setText("");
      setDrafts([]);
      onClearReply();
    } catch (e) {
      pushToast({ tone: "bad", title: "Nicht gesendet", body: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="shrink-0 border-t border-seam bg-hull px-4 py-3">
      {approvalId && (
        <div className="mb-3">
          {approval ? (
            <ApprovalCard approval={approval} compact />
          ) : summary ? (
            <p className="rounded-md border border-seam px-3 py-2 text-xs text-ink-dim">
              <span className="text-ink">Zur Freigabe vorgelegt:</span> {summary}
            </p>
          ) : null}
          {!approval && (
            <button type="button" className="mt-1 text-2xs text-ink-faint hover:text-ink" onClick={() => setApprovalId(null)}>Hinweis schließen</button>
          )}
        </div>
      )}
      {replyTo && (
        <div className="mb-2 flex items-center gap-2 rounded-md border-l-2 border-arc-dim bg-plate px-3 py-1.5 text-xs">
          <span className="min-w-0 flex-1 truncate text-ink-dim">Antwort auf {replyTo.from_me ? "dich" : replyTo.sender_name || "Kontakt"}: {replyTo.text || kindLabel(replyTo.kind)}</span>
          <IconButton label="Antwort verwerfen" onClick={onClearReply}><X className="size-3.5" /></IconButton>
        </div>
      )}
      {drafts.length > 0 && (
        <ul className="mb-2 flex flex-wrap gap-2" aria-label="Anhänge">
          {drafts.map((d) => (
            <li key={d.key} className={`relative flex items-center gap-2 overflow-hidden rounded-md border px-2.5 py-1.5 text-xs ${d.error ? "border-alarm/50" : "border-seam-hi"}`}>
              {!d.upload && !d.error && <Loader2 className="size-3.5 spin-slow text-ink-faint" aria-hidden />}
              <span className="max-w-[14rem] truncate text-ink">{d.name}</span>
              <span className="type-data text-ink-faint">{d.error ? d.error : d.upload ? d.upload.type : `${Math.round(d.progress * 100)} %`}</span>
              <IconButton label={`${d.name} entfernen`} className="size-5" onClick={() => setDrafts((x) => x.filter((y) => y.key !== d.key))}><X className="size-3" /></IconButton>
              {!d.upload && !d.error && <span className="absolute bottom-0 left-0 h-0.5 bg-arc" style={{ width: `${d.progress * 100}%` }} aria-hidden />}
            </li>
          ))}
        </ul>
      )}
      <div className="flex items-end gap-2">
        <input ref={input} type="file" multiple hidden onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
        <IconButton label="Datei anhängen" onClick={() => input.current?.click()}><Paperclip className="size-4" /></IconButton>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !busy && !uploading && (text.trim() || ready.length)) {
              e.preventDefault();
              void send();
            }
          }}
          rows={Math.min(5, Math.max(1, text.split("\n").length))}
          placeholder="Nachricht – vor dem Senden wird sie dir zur Bestätigung gezeigt"
          aria-label="Nachricht"
          className="field min-h-8.5 flex-1 resize-none"
        />
        <Button variant="primary" icon={<Send className="size-4" />} busy={busy} disabled={uploading || (!text.trim() && !ready.length)} onClick={() => void send()}>
          Senden
        </Button>
      </div>
      {ready.length > 0 && (
        <label className="mt-2 flex items-center gap-2 text-2xs text-ink-dim">
          <Toggle checked={asDocument} onChange={setAsDocument} label="Als Dokument senden" />
          Als Dokument senden (Originalqualität, Dateiname bleibt)
          <Tag>{ready.length} Datei{ready.length === 1 ? "" : "en"}</Tag>
        </label>
      )}
    </div>
  );
}
