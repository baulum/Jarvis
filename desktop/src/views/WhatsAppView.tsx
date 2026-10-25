// WhatsApp: the central connection on the Jarvis server (every client sees the same chats). Login, chats,
// received files, suggestions, delivery status and the connector settings in one place.
import { Check, LogOut, PlugZap, RefreshCw, Search, Trash2, Users, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Conversation } from "../components/whatsapp/Conversation";
import { LoginPanel } from "../components/whatsapp/LoginPanel";
import { SaveDialog } from "../components/whatsapp/SaveDialog";
import { KindIcon } from "../components/whatsapp/WaMedia";
import { Button, Dialog, EmptyState, ErrorState, Field, Section, Skeleton, StatusText, Tabs, Tag, Toggle } from "../components/ui";
import { useAction, useQuery } from "../hooks/useQuery";
import { formatBytes, formatDateTime, formatRelative } from "../lib/format";
import { saveBytes } from "../lib/native";
import type { Connector } from "../lib/types";
import {
  needsLogin,
  parseList,
  previewText,
  sendStatusLabel,
  sendStatusTone,
  stateLabel,
  stateTone,
  suggestionLabel,
  timeLabel,
  type WaChat,
  type WaFile,
  type WaStatus,
} from "../lib/whatsapp";
import { api, useApp } from "../store/app";

type Tab = "chats" | "files" | "suggestions" | "sends" | "settings";

export function WhatsAppView() {
  const status = useQuery(() => api.whatsapp.status(), "wa-status", { topics: ["whatsapp", "connector"], pollMs: 15_000 });
  const connectors = useQuery(() => api.connectors(), "connectors", { topics: ["connector"] });
  const connector = connectors.data?.connectors.find((c) => c.id === "whatsapp");
  const [tab, setTab] = useState<Tab>("chats");
  const act = useAction();
  const st = status.data;

  if (status.loading || connectors.loading) return <div className="p-8"><Skeleton rows={6} /></div>;
  if (status.error) return <div className="p-8"><ErrorState message={status.error} onRetry={status.refetch} /></div>;

  if (!st?.enabled) {
    return (
      <div className="mx-auto max-w-[44rem] px-8 py-10">
        <EmptyState
          icon={<PlugZap className="size-6" />}
          title="WhatsApp ist noch nicht aktiviert."
          action={
            <Button variant="primary" busy={act.busy === "enable"}
                    onClick={() => void act.run("enable", () => api.configureConnector("whatsapp", { enabled: true }), "WhatsApp aktiviert").then(() => { void status.refetch(); void connectors.refetch(); })}>
              Aktivieren
            </Button>
          }
        >
          Nach dem Aktivieren startet Jarvis den WhatsApp-Dienst auf dem Server. Anschließend verknüpfst du dein Konto per QR-Code
          oder Kopplungscode. Jede Nachricht, die Jarvis senden soll, wird dir vorher zur Bestätigung gezeigt.
        </EmptyState>
      </div>
    );
  }

  const login = needsLogin(st);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <StatusBar status={st} onChanged={() => void status.refetch()} />
      {login ? (
        <div className="min-h-0 flex-1"><LoginPanel status={st} onChanged={() => void status.refetch()} /></div>
      ) : (
        <>
          <div className="flex h-11 shrink-0 items-center border-b border-seam px-5">
            <Tabs
              value={tab}
              onChange={setTab}
              items={[
                { id: "chats", label: "Chats", count: st.counts?.unread_chats || undefined },
                { id: "files", label: "Dateien", count: st.counts?.files },
                { id: "suggestions", label: "Vorschläge", count: st.counts?.pending_suggestions || undefined },
                { id: "sends", label: "Versand" },
                { id: "settings", label: "Einstellungen" },
              ]}
            />
          </div>
          <div className="min-h-0 flex-1">
            {tab === "chats" && <ChatsTab />}
            {tab === "files" && <FilesTab />}
            {tab === "suggestions" && <SuggestionsTab />}
            {tab === "sends" && <SendsTab />}
            {tab === "settings" && connector && <SettingsTab connector={connector} status={st} onSaved={() => { void connectors.refetch(); void status.refetch(); }} />}
          </div>
        </>
      )}
    </div>
  );
}

function StatusBar({ status, onChanged }: { status: WaStatus; onChanged: () => void }) {
  const act = useAction();
  const tone = stateTone(status.state);
  const retry = status.state === "reconnecting" && status.retry_in_ms ? ` – neuer Versuch in ${Math.round(status.retry_in_ms / 1000)} s` : "";
  return (
    <div className="flex min-h-12 shrink-0 flex-wrap items-center gap-x-4 gap-y-1 border-b border-seam px-5 py-2">
      <StatusText tone={tone} pulse={tone === "warn"}>{stateLabel(status.state)}{retry}</StatusText>
      {status.me && (status.me.name || status.me.phone) && (
        <span className="text-xs text-ink-dim">
          {status.me.name}
          {status.me.phone && <span className="type-data ml-2 text-ink-faint">{status.me.phone}</span>}
        </span>
      )}
      {status.history && !status.history.latest && status.history.progress != null && (
        <span className="text-2xs text-ink-faint">Verlauf wird übernommen: {Math.round(status.history.progress)} %</span>
      )}
      {status.error && status.state !== "open" && <span className="max-w-[40rem] truncate text-2xs text-ember" title={status.error}>{status.error}</span>}
      <span className="flex-1" />
      {status.has_session && status.state !== "open" && (
        <Button size="sm" variant="ghost" icon={<RefreshCw className="size-3.5" />} busy={act.busy === "reconnect"}
                onClick={() => void act.run("reconnect", () => api.whatsapp.connect("qr"), "Verbindung wird aufgebaut").then(onChanged)}>
          Neu verbinden
        </Button>
      )}
      {status.state === "open" && (
        <Button size="sm" variant="ghost" busy={act.busy === "disconnect"}
                onClick={() => void act.run("disconnect", () => api.whatsapp.disconnect(), "Getrennt – die Sitzung bleibt gespeichert").then(onChanged)}>
          Trennen
        </Button>
      )}
      {status.sidecar?.baileys && <span className="type-data text-2xs text-ink-faint" title="Baileys-Version des WhatsApp-Dienstes">Baileys {status.sidecar.baileys}</span>}
    </div>
  );
}

// ------------------------------------------------------------------ chats
function ChatsTab() {
  const [q, setQ] = useState("");
  const chats = useQuery(() => api.whatsapp.chats(), "wa-chats", { topics: ["whatsapp"] });
  const [selected, setSelected] = useState<string | null>(null);
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (chats.data ?? []).filter((c) => !needle || c.name.toLowerCase().includes(needle) || c.jid.includes(needle));
  }, [chats.data, q]);

  return (
    <div className="grid h-full min-h-0 grid-cols-[minmax(15rem,19rem)_minmax(0,1fr)]">
      <aside className="flex min-h-0 flex-col border-r border-seam">
        <div className="relative p-3">
          <Search className="pointer-events-none absolute top-1/2 left-5.5 size-3.5 -translate-y-1/2 text-ink-faint" aria-hidden />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Chats durchsuchen" aria-label="Chats durchsuchen" className="field pl-8" />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {chats.loading && <div className="px-4"><Skeleton rows={8} /></div>}
          {chats.error && <div className="p-3"><ErrorState message={chats.error} onRetry={chats.refetch} /></div>}
          {chats.data && shown.length === 0 && (
            <p className="px-4 py-6 text-xs text-ink-faint">{chats.data.length ? "Kein Chat passt zur Suche." : "Noch keine Chats – der Verlauf wird nach der Anmeldung übernommen."}</p>
          )}
          <ul>
            {shown.map((c) => <ChatRow key={c.jid} chat={c} active={c.jid === selected} onOpen={() => setSelected(c.jid)} />)}
          </ul>
        </div>
      </aside>
      <div className="min-h-0">
        {selected ? <Conversation key={selected} jid={selected} onClose={() => setSelected(null)} /> : (
          <div className="flex h-full items-center justify-center px-8 text-center text-sm text-ink-faint">
            Einen Chat auswählen. Jarvis kann hier dasselbe lesen und senden wie im Gespräch – „Was hat Jonathan geschickt?“
          </div>
        )}
      </div>
    </div>
  );
}

function ChatRow({ chat, active, onOpen }: { chat: WaChat; active: boolean; onOpen: () => void }) {
  return (
    <li>
      <button type="button" onClick={onOpen} aria-current={active ? "true" : undefined}
              className={`flex w-full items-start gap-3 px-4 py-2.5 text-left ${active ? "bg-plate-hi" : "hover:bg-plate"}`}>
        <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full border border-seam-hi text-xs text-ink-dim" aria-hidden>
          {chat.is_group ? <Users className="size-3.5" /> : (chat.name || "?").slice(0, 1).toUpperCase()}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-2">
            <span className={`min-w-0 flex-1 truncate text-[0.8125rem] ${chat.unread ? "text-ink" : "text-ink-dim"}`}>{chat.name || chat.jid}</span>
            <span className="type-data shrink-0 text-ink-faint">{timeLabel(chat.last_message_at)}</span>
          </span>
          <span className="flex items-center gap-2">
            <span className="min-w-0 flex-1 truncate text-xs text-ink-faint">{previewText(chat.last_message)}</span>
            {chat.muted && <span className="text-2xs text-ink-faint">stumm</span>}
            {chat.unread > 0 && <span className="type-data rounded-full bg-arc-deep px-1.5 text-arc">{chat.unread}</span>}
          </span>
        </span>
      </button>
    </li>
  );
}

// ------------------------------------------------------------------ files
function FilesTab() {
  const [q, setQ] = useState("");
  const [type, setType] = useState<string>("");
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 250);
    return () => clearTimeout(t);
  }, [q]);
  const files = useQuery(() => api.whatsapp.files({ q: debounced, type: type || undefined, limit: 150 }), `wa-files:${debounced}:${type}`, { topics: ["whatsapp"] });
  const [saving, setSaving] = useState<WaFile | null>(null);
  const act = useAction();
  const toThisDevice = async (f: WaFile) => {
    const blob = await api.whatsapp.fileContent(f.file_id, true);
    const ext = f.name.includes(".") ? f.name.split(".").pop()! : "";
    return saveBytes(f.name, new Uint8Array(await blob.arrayBuffer()), { name: f.type, extensions: ext ? [ext] : [], mime: f.mime });
  };
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-[64rem] flex-col gap-4 px-6 py-5">
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative min-w-[16rem] flex-1">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-ink-faint" aria-hidden />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Nach Name, Chat, Absender oder Inhalt suchen" aria-label="Dateien durchsuchen" className="field pl-8" />
          </div>
          <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Dateityp" className="field w-40">
            <option value="">Alle Typen</option>
            <option value="document">Dokumente</option>
            <option value="image">Bilder</option>
            <option value="video">Videos</option>
            <option value="audio">Audio</option>
            <option value="archive">Archive</option>
          </select>
        </div>
        {files.error && <ErrorState message={files.error} onRetry={files.refetch} />}
        {files.loading && <Skeleton rows={6} />}
        {files.data && files.data.length === 0 && (
          <EmptyState title={debounced || type ? "Keine gespeicherte Datei passt." : "Noch keine Dateien gespeichert."}>
            Empfangene Anhänge landen hier, sobald du sie herunterlädst oder Jarvis sie liest. Den automatischen Download stellst du unter Einstellungen ein.
          </EmptyState>
        )}
        <ul className="divide-y divide-seam rounded-lg border border-seam">
          {files.data?.map((f) => (
            <li key={f.file_id} className="flex items-center gap-3 px-4 py-2.5">
              <KindIcon kind={f.type} className="size-4 shrink-0 text-ink-faint" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-[0.8125rem] text-ink">{f.name}</p>
                <p className="truncate text-2xs text-ink-faint">
                  {f.chat_name}{f.sender_name && f.sender_name !== f.chat_name ? `, von ${f.sender_name}` : ""}, {formatRelative(f.stored_at)}
                  {f.location !== "server" && <>, {f.location.replace("device:", "auf ")}</>}
                </p>
              </div>
              <span className="type-data shrink-0 text-ink-faint">{f.size_label ?? formatBytes(f.size)}</span>
              {f.location === "server" && (
                <>
                  <Button size="sm" variant="ghost" busy={act.busy === f.file_id} onClick={() => void act.run(f.file_id, () => toThisDevice(f))}>Auf dieses Gerät</Button>
                  <Button size="sm" variant="ghost" onClick={() => setSaving(f)}>Speichern unter …</Button>
                </>
              )}
            </li>
          ))}
        </ul>
      </div>
      {saving && <SaveDialog file={saving} onClose={() => setSaving(null)} />}
    </div>
  );
}

// ------------------------------------------------------------------ suggestions
function SuggestionsTab() {
  const list = useQuery(() => api.whatsapp.suggestions("pending"), "wa-suggestions", { topics: ["whatsapp"] });
  const act = useAction();
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-[52rem] flex-col gap-3 px-6 py-5">
        <p className="text-xs text-ink-dim">
          Was Jarvis in Chats erkannt hat. Erst wenn du übernimmst, landet etwas im Gedächtnis oder als Erinnerung – private
          Nachrichten werden nie von selbst gespeichert.
        </p>
        {list.loading && <Skeleton rows={4} />}
        {list.error && <ErrorState message={list.error} onRetry={list.refetch} />}
        {list.data && list.data.length === 0 && <EmptyState title="Keine offenen Vorschläge." />}
        {list.data?.map((s) => (
          <article key={s.id} className="plate flex flex-col gap-2 px-4 py-3">
            <header className="flex items-center gap-2">
              <Tag tone={s.kind === "invoice" || s.kind === "appointment" ? "warn" : "info"}>{suggestionLabel(s.kind)}</Tag>
              <span className="min-w-0 flex-1 truncate text-xs text-ink-faint">{s.chat_name || s.chat_jid}</span>
              {s.due_at && <span className="type-data text-ink-dim">{formatDateTime(s.due_at)}</span>}
            </header>
            <p className="type-title text-ink">{s.title}</p>
            {s.detail && <p className="selectable text-sm text-ink-dim">{s.detail}</p>}
            <div className="flex gap-2">
              <Button size="sm" variant="primary" icon={<Check className="size-3.5" />} busy={act.busy === `a:${s.id}`}
                      onClick={() => void act.run(`a:${s.id}`, () => api.whatsapp.decideSuggestion(s.id, "accept"), "Übernommen").then(list.refetch)}>
                Übernehmen
              </Button>
              <Button size="sm" variant="ghost" icon={<X className="size-3.5" />} busy={act.busy === `d:${s.id}`}
                      onClick={() => void act.run(`d:${s.id}`, () => api.whatsapp.decideSuggestion(s.id, "dismiss")).then(list.refetch)}>
                Verwerfen
              </Button>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ delivery status
function SendsTab() {
  const sends = useQuery(() => api.whatsapp.sends(60), "wa-sends", { topics: ["whatsapp"] });
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[60rem] px-6 py-5">
        {sends.loading && <Skeleton rows={5} />}
        {sends.error && <ErrorState message={sends.error} onRetry={sends.refetch} />}
        {sends.data && sends.data.length === 0 && <EmptyState title="Noch nichts gesendet.">Freigegebene Sendungen erscheinen hier mit ihrem Zustellstatus.</EmptyState>}
        <ul className="divide-y divide-seam rounded-lg border border-seam">
          {sends.data?.map((s) => (
            <li key={s.send_key} className="flex items-center gap-3 px-4 py-2.5">
              <div className="min-w-0 flex-1">
                <p className="truncate text-[0.8125rem] text-ink">
                  {s.chat_name || "Empfänger"}
                  <span className="ml-2 text-xs text-ink-faint">
                    {s.files.length ? s.files.map((f) => f.name).join(", ") : `Text, ${s.text_len} Zeichen`}
                  </span>
                </p>
                {s.error && <p className="truncate text-2xs text-alarm">{s.error}</p>}
              </div>
              <StatusText tone={sendStatusTone(s.status)}>{sendStatusLabel(s.status)}</StatusText>
              <span className="type-data w-20 shrink-0 text-right text-ink-faint">{formatRelative(s.created_at)}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ settings
const MEDIA_TYPES: { id: string; label: string }[] = [
  { id: "image", label: "Fotos" },
  { id: "voice", label: "Sprachnachrichten" },
  { id: "document", label: "Dokumente" },
  { id: "audio", label: "Audio" },
  { id: "video", label: "Videos" },
];

function SettingsTab({ connector, status, onSaved }: { connector: Connector; status: WaStatus; onSaved: () => void }) {
  const s = connector.settings as Record<string, unknown>;
  const bool = (k: string, d: boolean) => (typeof s[k] === "boolean" ? (s[k] as boolean) : d);
  const [form, setForm] = useState(() => ({
    download_dir: String(s.download_dir ?? ""),
    allowed_dirs: ((s.allowed_dirs as string[] | undefined) ?? []).join("\n"),
    excluded_chats: ((s.excluded_chats as string[] | undefined) ?? []).join("\n"),
    auto_download: bool("auto_download", false),
    auto_download_types: (s.auto_download_types as string[] | undefined) ?? ["image", "voice", "document"],
    max_download_mb: Number(s.max_download_mb ?? 64),
    max_upload_mb: Number(s.max_upload_mb ?? 64),
    notify_incoming: bool("notify_incoming", true),
    notify_groups: bool("notify_groups", false),
    notify_preview: bool("notify_preview", true),
    send_receipts_notify: bool("send_receipts_notify", true),
    history_sync: bool("history_sync", true),
    auto_suggestions: bool("auto_suggestions", false),
    retention_days: Number(s.retention_days ?? 0),
  }));
  const [confirm, setConfirm] = useState<"logout" | "purge" | null>(null);
  const act = useAction();
  const set = <K extends keyof typeof form>(k: K, v: (typeof form)[K]) => setForm((f) => ({ ...f, [k]: v }));

  const save = () =>
    void act
      .run(
        "save",
        () =>
          api.configureConnector("whatsapp", {
            settings: {
              ...form,
              allowed_dirs: parseList(form.allowed_dirs),
              excluded_chats: parseList(form.excluded_chats),
              max_download_mb: Math.max(1, Math.min(2048, Math.round(form.max_download_mb))),
              max_upload_mb: Math.max(1, Math.min(2048, Math.round(form.max_upload_mb))),
              retention_days: Math.max(0, Math.round(form.retention_days)),
            },
          }),
        "Einstellungen gespeichert",
      )
      .then(onSaved);

  const row = (label: string, hint: string, checked: boolean, onChange: (v: boolean) => void) => (
    <label className="flex items-start gap-3 py-2">
      <Toggle checked={checked} onChange={onChange} label={label} />
      <span>
        <span className="block text-[0.8125rem] text-ink">{label}</span>
        <span className="block text-2xs text-ink-faint">{hint}</span>
      </span>
    </label>
  );

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto grid max-w-[64rem] gap-5 px-6 py-5 lg:grid-cols-2">
        <Section title="Speicherorte">
          <div className="flex flex-col gap-4 p-4">
            <Field label="Download-Ordner" hint={`Leer = ${status.download_dir ?? "Jarvis-Datenordner/whatsapp/downloads"}. Je Chat ein Unterordner, Namen bleiben erhalten.`}>
              {(id) => <input id={id} className="field" value={form.download_dir} onChange={(e) => set("download_dir", e.target.value)} placeholder="z. B. ~/Documents/WhatsApp" />}
            </Field>
            <Field label="Weitere freigegebene Ordner" hint="Einer je Zeile. Standard: Schreibtisch, Dokumente, Downloads, Bilder, Musik, Filme/Videos und der Projektordner.">
              {(id) => <textarea id={id} rows={3} className="field resize-y" value={form.allowed_dirs} onChange={(e) => set("allowed_dirs", e.target.value)} />}
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Download-Limit (MB)">
                {(id) => <input id={id} type="number" min={1} max={2048} className="field" value={form.max_download_mb} onChange={(e) => set("max_download_mb", Number(e.target.value))} />}
              </Field>
              <Field label="Versand-Limit (MB)">
                {(id) => <input id={id} type="number" min={1} max={2048} className="field" value={form.max_upload_mb} onChange={(e) => set("max_upload_mb", Number(e.target.value))} />}
              </Field>
            </div>
          </div>
        </Section>

        <Section title="Automatischer Download">
          <div className="flex flex-col gap-2 p-4">
            {row("Erlaubte Typen automatisch laden", "Nur bis zum Download-Limit; Programmdateien werden nie geladen.", form.auto_download, (v) => set("auto_download", v))}
            <div className="flex flex-wrap gap-2 pl-11" role="group" aria-label="Typen für den automatischen Download">
              {MEDIA_TYPES.map((t) => {
                const on = form.auto_download_types.includes(t.id);
                return (
                  <button key={t.id} type="button" aria-pressed={on} disabled={!form.auto_download}
                          onClick={() => set("auto_download_types", on ? form.auto_download_types.filter((x) => x !== t.id) : [...form.auto_download_types, t.id])}
                          className={`h-7 rounded-md border px-2.5 text-xs disabled:opacity-40 ${on ? "border-arc-dim bg-arc-deep text-arc" : "border-seam-hi text-ink-dim"}`}>
                    {t.label}
                  </button>
                );
              })}
            </div>
          </div>
        </Section>

        <Section title="Meldungen">
          <div className="flex flex-col p-4">
            {row("Neue Nachrichten melden", "Desktop-Benachrichtigung, stumme Chats ausgenommen.", form.notify_incoming, (v) => set("notify_incoming", v))}
            {row("Auch Gruppen", "Gruppennachrichten ebenfalls melden.", form.notify_groups, (v) => set("notify_groups", v))}
            {row("Text in der Meldung zeigen", "Aus: nur „Neue Nachricht“ und der Name.", form.notify_preview, (v) => set("notify_preview", v))}
            {row("Fehlgeschlagene Zustellung melden", "Wenn WhatsApp eine Sendung nicht zustellen kann.", form.send_receipts_notify, (v) => set("send_receipts_notify", v))}
          </div>
        </Section>

        <Section title="Berechtigungen und Versand">
          <div className="flex flex-col gap-3 p-4">
            <p className="text-xs text-ink-dim">
              Jede Sendung – Text oder Datei – wird dir vor dem Versand mit Empfänger, Dateien und Text zur Bestätigung gezeigt.
              Das gilt in jedem Freigabemodus und lässt sich nicht abschalten.
            </p>
            {row("Chatverlauf bei der Anmeldung übernehmen", "Wirkt bei der nächsten Verbindung.", form.history_sync, (v) => set("history_sync", v))}
            {row("Vorschläge automatisch erkennen", "Einzelchats auf Aufgaben, Termine und Rechnungen prüfen – nur als Vorschlag.", form.auto_suggestions, (v) => set("auto_suggestions", v))}
            <Field label="Gesperrte Chats" hint="Chat-IDs (eine je Zeile), die Jarvis weder speichert noch liest, z. B. 4917…@s.whatsapp.net.">
              {(id) => <textarea id={id} rows={3} className="field type-data resize-y" value={form.excluded_chats} onChange={(e) => set("excluded_chats", e.target.value)} />}
            </Field>
          </div>
        </Section>

        <Section title="Aufbewahrung" className="lg:col-span-2">
          <div className="flex flex-wrap items-end gap-4 p-4">
            <Field label="Nachrichten und Downloads löschen nach (Tagen)" hint="0 = behalten. Kopien in deinen eigenen Ordnern bleiben unberührt.">
              {(id) => <input id={id} type="number" min={0} max={3650} className="field w-40" value={form.retention_days} onChange={(e) => set("retention_days", Number(e.target.value))} />}
            </Field>
            <span className="flex-1" />
            <Button variant="danger" size="sm" icon={<Trash2 className="size-3.5" />} onClick={() => setConfirm("purge")}>Gespeicherte Daten löschen</Button>
            <Button variant="danger" size="sm" icon={<LogOut className="size-3.5" />} onClick={() => setConfirm("logout")}>Abmelden</Button>
          </div>
        </Section>

        <div className="flex justify-end lg:col-span-2">
          <Button variant="primary" busy={act.busy === "save"} onClick={save}>Einstellungen speichern</Button>
        </div>
      </div>

      <Dialog open={confirm === "logout"} tone="danger" onClose={() => setConfirm(null)} title="Von WhatsApp abmelden?"
              footer={<><Button variant="ghost" onClick={() => setConfirm(null)}>Abbrechen</Button>
                <Button variant="danger" busy={act.busy === "logout"} onClick={() => void act.run("logout", () => api.whatsapp.logout(), "Abgemeldet").then(() => { setConfirm(null); onSaved(); })}>Abmelden</Button></>}>
        Jarvis wird als verknüpftes Gerät entfernt und die gespeicherte Sitzung gelöscht. Gespeicherte Nachrichten und Dateien bleiben, bis du sie löschst. Zum erneuten Verbinden ist ein neuer QR-Scan nötig.
      </Dialog>
      <Dialog open={confirm === "purge"} tone="danger" onClose={() => setConfirm(null)} title="Gespeicherte WhatsApp-Daten löschen?"
              footer={<><Button variant="ghost" onClick={() => setConfirm(null)}>Abbrechen</Button>
                <Button variant="danger" busy={act.busy === "purge"} onClick={() => void act.run("purge", () => api.whatsapp.purge(0), "Gelöscht").then(() => { setConfirm(null); onSaved(); useApp.getState().pushToast({ tone: "info", title: "WhatsApp-Daten auf dem Server gelöscht" }); })}>Alles löschen</Button></>}>
        Alle von Jarvis gespeicherten Nachrichten, Vorschläge und Downloads im WhatsApp-Ordner werden vom Server gelöscht. In WhatsApp selbst bleibt alles erhalten.
      </Dialog>
    </div>
  );
}
