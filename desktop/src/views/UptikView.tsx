// UpTik Autopost: accounts (login in a browser window, autopost switch, circuit breaker), publish form (video, caption,
// hashtags, platforms, time), queue, history and settings. Publishing, scheduling and connecting end in the Core's
// approvals; resuming a stopped account is only possible here, after Sir checked it on the platform.
import { AlertTriangle, ExternalLink, Plus, RefreshCw, Send, ShieldCheck, Trash2, Unplug, Upload, UserRoundCheck } from "lucide-react";
import { useMemo, useRef, useState, type ReactNode } from "react";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Button, Dialog, EmptyState, ErrorState, Field, KeyValue, Section, Skeleton, StatusText, Tabs, Tag, Toggle } from "../components/ui";
import { useAction, useQuery } from "../hooks/useQuery";
import { formatBytes, formatDateTime, formatRelative } from "../lib/format";
import { openExternal } from "../lib/native";
import {
  accountState,
  buildPostBody,
  overLimit,
  PLATFORM_LABEL,
  postStatusLabel,
  postText,
  postTone,
  type UptikAccount,
  type UptikPlatform,
  type UptikPost,
  type UptikPostBody,
  type UptikSettings,
  type UptikStatus,
} from "../lib/uptik";
import { api, useApp } from "../store/app";

type Tab = "accounts" | "publish" | "queue" | "history" | "settings";

export function UptikView() {
  const status = useQuery(() => api.uptik.status(), "uptik-status", { topics: ["uptik", "connector"], pollMs: 15_000 });
  const [tab, setTab] = useState<Tab>("accounts");
  const act = useAction();
  const st = status.data;
  const open = (st?.counts.queued ?? 0) + (st?.counts.retry_wait ?? 0) + (st?.counts.running ?? 0) + (st?.counts.blocked ?? 0);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-seam px-6 py-2.5">
        <Tabs<Tab>
          value={tab}
          onChange={setTab}
          items={[
            { id: "accounts", label: "Konten", count: st?.accounts.length },
            { id: "publish", label: "Veröffentlichen" },
            { id: "queue", label: "Warteschlange", count: st ? open : undefined },
            { id: "history", label: "Historie" },
            { id: "settings", label: "Einstellungen" },
          ]}
        />
        <div className="flex-1" />
        {st && (
          <label className="flex items-center gap-2 text-xs text-ink-dim">
            <Toggle label="UpTik-Connector aktiv" checked={st.enabled} disabled={act.busy !== null}
                    onChange={(v) => void act.run("toggle", () => api.uptik.configure({ enabled: v }), v ? "UpTik aktiviert" : "UpTik deaktiviert").then(status.refetch)} />
            Connector aktiv
          </label>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {status.loading && <div className="p-8"><Skeleton rows={6} /></div>}
        {status.error && <div className="p-8"><ErrorState message={status.error} onRetry={status.refetch} /></div>}
        {st && !st.enabled && <Disabled />}
        {st?.enabled && (
          <>
            <Banners st={st} />
            {tab === "accounts" && <Accounts st={st} refetch={status.refetch} />}
            {tab === "publish" && <Publish st={st} onDone={() => setTab("queue")} />}
            {tab === "queue" && <Queue />}
            {tab === "history" && <History />}
            {tab === "settings" && <Settings settings={st.settings} refetch={status.refetch} />}
          </>
        )}
      </div>
    </div>
  );
}

function Disabled() {
  return (
    <div className="mx-auto max-w-[48rem] px-6 py-6">
      <EmptyState icon={<Send className="size-6" />} title="UpTik ist ausgeschaltet.">
        <p>
          Veröffentlicht Videos auf TikTok, Instagram Reels und optional YouTube Shorts über deren Webseiten – in einem echten
          Chrome mit deiner eigenen Anmeldung, ohne Developer-App. Jede Veröffentlichung braucht deine Freigabe; bei Captcha,
          Sicherheitsprüfung oder Ratenlimit stoppt das Konto sofort.
        </p>
        <p className="mt-2 text-ember">Browser-Automatisierung verstößt gegen die Nutzungsbedingungen der Plattformen und kann Konten gefährden.</p>
      </EmptyState>
    </div>
  );
}

function Banners({ st }: { st: UptikStatus }) {
  const tripped = st.accounts.filter((a) => a.circuit_state === "tripped");
  return (
    <div className="mx-auto flex max-w-[60rem] flex-col gap-2 px-6 pt-5 empty:hidden">
      {st.kill_switch && <p role="alert" className="rounded-md border border-alarm/40 bg-alarm-deep/40 px-3 py-2 text-sm text-alarm">Not-Aus ist aktiv – die Warteschlange ruht.</p>}
      {tripped.map((a) => (
        <p key={a.id} role="alert" className="flex items-center gap-2 rounded-md border border-alarm/40 bg-alarm-deep/40 px-3 py-2 text-sm text-ink">
          <AlertTriangle className="size-4 shrink-0 text-alarm" aria-hidden />
          {a.platform_label} „{a.label}“ ist angehalten: {a.circuit_reason}. Bitte das Konto selbst im Browser prüfen, dann unter Konten freigeben.
        </p>
      ))}
      {!st.local_runner.ok && st.accounts.some((a) => a.runner === "local") && (
        <p className="rounded-md border border-ember/40 px-3 py-2 text-xs text-ember">Browser auf diesem Rechner nicht verfügbar: {st.local_runner.reason}</p>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ accounts
function Accounts({ st, refetch }: { st: UptikStatus; refetch: () => Promise<void> }) {
  const [adding, setAdding] = useState(false);
  return (
    <div className="mx-auto flex max-w-[60rem] flex-col gap-4 px-6 py-5">
      {st.accounts.length === 0 && (
        <EmptyState icon={<UserRoundCheck className="size-6" />} title="Noch kein Konto."
                    action={<Button variant="primary" icon={<Plus className="size-3.5" />} onClick={() => setAdding(true)}>Konto hinzufügen</Button>}>
          Lege je Plattform ein Konto an und melde dich im sich öffnenden Browserfenster selbst an. Jarvis sieht und speichert kein Passwort;
          nur die Sitzung wird verschlüsselt abgelegt.
        </EmptyState>
      )}
      {st.accounts.map((a) => <AccountCard key={a.id} a={a} refetch={refetch} />)}
      {st.accounts.length > 0 && (
        <div><Button icon={<Plus className="size-3.5" />} onClick={() => setAdding(true)}>Konto hinzufügen</Button></div>
      )}
      {adding && <AddAccountDialog st={st} onClose={() => setAdding(false)} onSaved={refetch} />}
    </div>
  );
}

function AccountCard({ a, refetch }: { a: UptikAccount; refetch: () => Promise<void> }) {
  const act = useAction();
  const [confirm, setConfirm] = useState<"autopost" | "remove" | "resume" | null>(null);
  const [requeue, setRequeue] = useState(false);
  const state = accountState(a);
  const pushToast = useApp((s) => s.pushToast);
  const run = (label: string, fn: () => Promise<unknown>, ok?: string) => void act.run(label, fn, ok).then(() => refetch());

  const connect = () =>
    void act.run("connect", () => api.uptik.connect(a.id)).then((r) => {
      if (r?.status === "approval_required") pushToast({ tone: "info", title: "Anmeldung wartet auf Freigabe", body: r.summary, action: { label: "Freigaben", view: "approvals" } });
      else if (r) pushToast({ tone: "ok", title: "Anmeldefenster geöffnet", body: "Bitte im Browserfenster selbst anmelden." });
      void refetch();
    });

  return (
    <article className="plate flex flex-col gap-3 p-4 text-sm">
      <header className="flex flex-wrap items-center gap-3">
        <h3 className="type-title text-ink">{a.label}</h3>
        <Tag>{a.platform_label}</Tag>
        {a.handle && <span className="type-data text-xs text-ink-dim">@{a.handle}</span>}
        <StatusText tone={state.tone} pulse={state.tone === "info"}>{state.label}</StatusText>
        <div className="flex-1" />
        <label className="flex items-center gap-2 text-xs text-ink-dim">
          <Toggle label={`Autopost ${a.label}`} checked={a.autopost} disabled={a.status !== "connected" || act.busy !== null}
                  onChange={(v) => (v ? setConfirm("autopost") : run("auto", () => api.uptik.updateAccount(a.id, { autopost: false }), "Autopost aus"))} />
          Autopost
        </label>
      </header>
      <KeyValue rows={[
        ["Browser", a.runner === "local" ? "Auf dem Core-Rechner" : `Device Agent ${a.device || "(Standardgerät)"}`],
        ["Sitzung", a.session_saved ? `verschlüsselt gespeichert ${formatRelative(a.session_saved_at)}` : "keine"],
        ["Zuletzt veröffentlicht", a.last_used_at ? formatDateTime(a.last_used_at) : "–"],
      ]} />
      <footer className="flex flex-wrap gap-2 border-t border-seam pt-3">
        {a.circuit_state === "tripped" && (
          <Button size="sm" variant="primary" icon={<ShieldCheck className="size-3.5" />} onClick={() => setConfirm("resume")}>Konto freigeben</Button>
        )}
        <Button size="sm" variant={a.status === "connected" ? "ghost" : "primary"} busy={act.busy === "connect"} disabled={a.login_running}
                icon={<UserRoundCheck className="size-3.5" />} onClick={connect}>
          {a.status === "connected" ? "Neu anmelden" : "Anmelden"}
        </Button>
        <Button size="sm" variant="ghost" busy={act.busy === "check"} disabled={!a.session_saved} icon={<RefreshCw className="size-3.5" />}
                onClick={() => run("check", () => api.uptik.check(a.id), "Verbindung geprüft")}>Prüfen</Button>
        <Button size="sm" variant="ghost" busy={act.busy === "dc"} disabled={!a.session_saved} icon={<Unplug className="size-3.5" />}
                onClick={() => run("dc", () => api.uptik.disconnect(a.id), "Sitzung gelöscht")}>Abmelden</Button>
        <div className="flex-1" />
        <Button size="sm" variant="quiet" icon={<Trash2 className="size-3.5" />} onClick={() => setConfirm("remove")}>Entfernen</Button>
      </footer>
      <ConfirmDialog open={confirm === "autopost"} title="Autopost einschalten?" confirmLabel="Einschalten" busy={act.busy === "auto"}
                     onCancel={() => setConfirm(null)}
                     onConfirm={() => { setConfirm(null); run("auto", () => api.uptik.updateAccount(a.id, { autopost: true }), "Autopost an"); }}>
        Fertige Videos der Videoproduktion werden für „{a.label}“ automatisch zur Veröffentlichung vorbereitet (nur wenn unter
        Einstellungen „Videos übernehmen“ an ist). Jede Veröffentlichung geht weiterhin über deine Freigaben.
      </ConfirmDialog>
      <ConfirmDialog open={confirm === "remove"} title="Konto entfernen?" tone="danger" confirmLabel="Entfernen" busy={act.busy === "rm"}
                     onCancel={() => setConfirm(null)}
                     onConfirm={() => { setConfirm(null); run("rm", () => api.uptik.deleteAccount(a.id), "Konto entfernt"); }}>
        Die gespeicherte Sitzung wird gelöscht, wartende Beiträge dieses Kontos werden abgebrochen. Auf der Plattform selbst ändert sich nichts.
      </ConfirmDialog>
      <ConfirmDialog open={confirm === "resume"} title="Konto wieder freigeben?" confirmLabel="Freigeben" busy={act.busy === "resume"}
                     onCancel={() => setConfirm(null)}
                     onConfirm={() => { setConfirm(null); run("resume", () => api.uptik.resume(a.id, requeue), "Konto freigegeben"); }}>
        <p>Grund des Stopps: {a.circuit_reason}. Freigeben erst, wenn du das Konto selbst im Browser geprüft hast (Captcha gelöst,
          Bestätigung erledigt, keine Sperre). Jarvis versucht nichts davon zu umgehen.</p>
        <label className="mt-3 flex items-center gap-2 text-xs text-ink">
          <Toggle label="Angehaltene Beiträge wieder einreihen" checked={requeue} onChange={setRequeue} />
          Angehaltene Beiträge wieder einreihen
        </label>
      </ConfirmDialog>
    </article>
  );
}

function AddAccountDialog({ st, onClose, onSaved }: { st: UptikStatus; onClose: () => void; onSaved: () => Promise<void> }) {
  const [platform, setPlatform] = useState<UptikPlatform>(st.platforms[0]?.id ?? "tiktok");
  const [label, setLabel] = useState("");
  const [runner, setRunner] = useState<"local" | "device">(st.settings.runner);
  const [device, setDevice] = useState(st.settings.device);
  const act = useAction();
  const save = () =>
    void act.run("add", () => api.uptik.createAccount(platform, label, runner, runner === "device" ? device : undefined), "Konto angelegt – jetzt anmelden")
      .then((r) => { if (r) { void onSaved(); onClose(); } });
  return (
    <Dialog open onClose={onClose} title="Konto hinzufügen"
            footer={<><Button variant="ghost" onClick={onClose}>Abbrechen</Button><Button variant="primary" busy={act.busy === "add"} disabled={!label.trim()} onClick={save}>Anlegen</Button></>}>
      <div className="flex flex-col gap-4">
        <Field label="Plattform">
          {(id) => (
            <select id={id} className="field" value={platform} onChange={(e) => setPlatform(e.target.value as UptikPlatform)}>
              {st.platforms.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select>
          )}
        </Field>
        <Field label="Name" hint="Nur zur Unterscheidung in Jarvis, z. B. „PJE Systems“.">
          {(id) => <input id={id} className="field" maxLength={80} value={label} onChange={(e) => setLabel(e.target.value)} />}
        </Field>
        <Field label="Browser läuft auf" hint="Auf deinem Rechner (Device Agent), wenn der Core auf einem Server läuft.">
          {(id) => (
            <select id={id} className="field" value={runner} onChange={(e) => setRunner(e.target.value as "local" | "device")}>
              <option value="local">Core-Rechner</option>
              <option value="device">Gerät mit Device Agent</option>
            </select>
          )}
        </Field>
        {runner === "device" && (
          <Field label="Gerät" hint="Name des Device Agents; leer = das einzige verbundene Gerät.">
            {(id) => <input id={id} className="field" maxLength={120} value={device} onChange={(e) => setDevice(e.target.value)} />}
          </Field>
        )}
      </div>
    </Dialog>
  );
}

// ------------------------------------------------------------------ publish form
function Publish({ st, onDone }: { st: UptikStatus; onDone: () => void }) {
  const inbox = useQuery(() => api.uptik.inbox(), "uptik-inbox", { topics: ["uptik"] });
  const usable = st.accounts.filter((a) => a.status === "connected" && a.circuit_state === "ok");
  const [video, setVideo] = useState("");
  const [caption, setCaption] = useState("");
  const [hashtags, setHashtags] = useState("");
  const [title, setTitle] = useState("");
  const [accounts, setAccounts] = useState<string[]>([]);
  const [when, setWhen] = useState("");
  const [tiktokVisibility, setTiktokVisibility] = useState("public");
  const [ytVisibility, setYtVisibility] = useState("public");
  const [comments, setComments] = useState(true);
  const [check, setCheck] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const act = useAction();
  const pushToast = useApp((s) => s.pushToast);

  const chosen = usable.filter((a) => accounts.includes(a.id));
  const platforms = [...new Set(chosen.map((a) => a.platform))];
  const tags = useMemo(() => hashtags.split(/[\s,;]+/).filter(Boolean).map((t) => `#${t.replace(/^#+/, "")}`), [hashtags]);
  const tooLong = overLimit(caption, tags, platforms);
  const length = postText(caption, tags).length;

  const body = (): UptikPostBody => {
    const options: UptikPostBody["options"] = {};
    if (platforms.includes("tiktok")) options.tiktok = { visibility: tiktokVisibility, allow_comments: comments };
    if (platforms.includes("instagram_reels")) options.instagram_reels = { allow_comments: comments };
    if (platforms.includes("youtube_shorts")) options.youtube_shorts = { visibility: ytVisibility };
    return buildPostBody({ video, caption, hashtags, title, accounts, when, options });
  };

  const pick = (iid: string) => {
    setVideo(iid ? `inbox:${iid}` : "");
    const item = inbox.data?.find((i) => i.id === iid);
    const c = item?.captions.tiktok ?? item?.captions.instagram_reels;
    if (c && !caption) setCaption(c.caption ?? "");
    if (c?.hashtags && !hashtags) setHashtags(c.hashtags.join(" "));
    if (item && !title) setTitle(item.captions.youtube_shorts?.title ?? item.title);
  };

  const upload = (f: File | undefined) => {
    if (!f) return;
    void act.run("upload", () => api.uptik.upload(f, f.name), "Video hochgeladen").then((item) => {
      if (item) { void inbox.refetch(); setVideo(`inbox:${item.id}`); }
    });
  };

  const validate = () =>
    void act.run("validate", () => api.uptik.validate(body())).then((r) => {
      if (r) setCheck(r.ok ? `Bereit: ${r.targets?.map((t) => `${PLATFORM_LABEL[t.platform]} (${t.label})`).join(", ")}${r.publish_at_label ? ` am ${r.publish_at_label}` : " – sofort nach Freigabe"}` : r.problems ?? "Nicht veröffentlichbar");
    });

  const send = () =>
    void act.run("publish", () => api.uptik.publish(body())).then((r) => {
      if (!r) return;
      if (r.status === "approval_required") pushToast({ tone: "info", title: "Wartet auf deine Freigabe", body: r.summary, action: { label: "Freigaben", view: "approvals" } });
      else pushToast({ tone: "ok", title: "Eingereiht" });
      onDone();
    });

  if (usable.length === 0) {
    return <div className="mx-auto max-w-[48rem] px-6 py-6"><EmptyState title="Kein verbundenes Konto.">Lege unter „Konten“ ein Konto an und melde dich an.</EmptyState></div>;
  }
  return (
    <div className="mx-auto flex max-w-[48rem] flex-col gap-4 px-6 py-5 text-sm">
      <Section title="Video" actions={<Button size="sm" icon={<Upload className="size-3.5" />} busy={act.busy === "upload"} onClick={() => fileRef.current?.click()}>Hochladen</Button>}>
        <div className="flex flex-col gap-2 p-4">
          <input ref={fileRef} type="file" accept="video/mp4,video/quicktime,video/webm" className="hidden" onChange={(e) => upload(e.target.files?.[0])} />
          {inbox.loading ? <Skeleton rows={2} /> : (
            <Field label="Aus dem Eingang (Videoproduktion und Uploads)">
              {(id) => (
                <select id={id} className="field" value={video.replace(/^inbox:/, "")} onChange={(e) => pick(e.target.value)}>
                  <option value="">– Video wählen –</option>
                  {inbox.data?.map((i) => <option key={i.id} value={i.id}>{i.title || i.name} · {formatBytes(i.size)} · {formatRelative(i.created_at)}</option>)}
                </select>
              )}
            </Field>
          )}
        </div>
      </Section>
      <Section title="Konten" meta="Veröffentlicht wird auf jedem gewählten Konto (Fan-out)">
        <ul className="divide-y divide-seam">
          {usable.map((a) => (
            <li key={a.id} className="flex items-center gap-3 px-4 py-2">
              <Toggle label={`${a.platform_label} ${a.label}`} checked={accounts.includes(a.id)}
                      onChange={(v) => setAccounts(v ? [...accounts, a.id] : accounts.filter((x) => x !== a.id))} />
              <span className="text-ink">{a.label}</span>
              <Tag>{a.platform_label}</Tag>
            </li>
          ))}
        </ul>
      </Section>
      <Field label={`Text (${length} Zeichen)`} hint={tooLong.length ? `Zu lang für ${tooLong.map((p) => PLATFORM_LABEL[p]).join(", ")}` : "Die Hashtags werden mit einer Leerzeile angehängt."}>
        {(id) => <textarea id={id} className="field min-h-28" maxLength={5000} value={caption} onChange={(e) => setCaption(e.target.value)} />}
      </Field>
      <Field label="Hashtags" hint="Mit Leerzeichen oder Komma getrennt, # optional">
        {(id) => <input id={id} className="field" value={hashtags} onChange={(e) => setHashtags(e.target.value)} />}
      </Field>
      {platforms.includes("youtube_shorts") && (
        <Field label="Titel (YouTube, max. 100 Zeichen)">
          {(id) => <input id={id} className="field" maxLength={100} value={title} onChange={(e) => setTitle(e.target.value)} />}
        </Field>
      )}
      {platforms.length > 0 && (
        <div className="plate flex flex-wrap items-end gap-4 p-4">
          {platforms.includes("tiktok") && (
            <Field label="TikTok: Wer darf es sehen">
              {(id) => (
                <select id={id} className="field" value={tiktokVisibility} onChange={(e) => setTiktokVisibility(e.target.value)}>
                  <option value="public">Alle</option><option value="friends">Freunde</option><option value="private">Nur ich</option>
                </select>
              )}
            </Field>
          )}
          {platforms.includes("youtube_shorts") && (
            <Field label="YouTube: Sichtbarkeit">
              {(id) => (
                <select id={id} className="field" value={ytVisibility} onChange={(e) => setYtVisibility(e.target.value)}>
                  <option value="public">Öffentlich</option><option value="unlisted">Nicht gelistet</option><option value="private">Privat</option>
                </select>
              )}
            </Field>
          )}
          {(platforms.includes("tiktok") || platforms.includes("instagram_reels")) && (
            <label className="flex items-center gap-2 pb-2 text-xs text-ink-dim">
              <Toggle label="Kommentare erlauben" checked={comments} onChange={setComments} />
              Kommentare erlauben
            </label>
          )}
        </div>
      )}
      <Field label="Zeitpunkt (Berliner Zeit)" hint="Leer = sofort nach deiner Freigabe. Zum Zeitpunkt muss der Rechner mit dem Browser laufen.">
        {(id) => <input id={id} type="datetime-local" className="field" value={when} onChange={(e) => setWhen(e.target.value)} />}
      </Field>
      {check && <p className="rounded-md border border-seam-hi px-3 py-2 text-xs text-ink-dim">{check}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" busy={act.busy === "validate"} disabled={!video || accounts.length === 0} onClick={validate}>Prüfen</Button>
        <Button variant="primary" icon={<Send className="size-3.5" />} busy={act.busy === "publish"} disabled={!video || accounts.length === 0 || tooLong.length > 0} onClick={send}>
          {when ? "Planen (Freigabe)" : "Veröffentlichen (Freigabe)"}
        </Button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ queue and history
function PostRow({ p, accounts, actions }: { p: UptikPost; accounts: Map<string, UptikAccount>; actions?: ReactNode }) {
  const a = accounts.get(p.account_id);
  return (
    <li className="flex flex-col gap-1 px-4 py-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <StatusText tone={postTone(p.status)} pulse={p.status === "running"}>{postStatusLabel(p.status)}</StatusText>
        <Tag>{PLATFORM_LABEL[p.platform]}</Tag>
        <span className="text-ink">{a?.label ?? "entferntes Konto"}</span>
        <span className="truncate text-xs text-ink-dim">{p.video_name}</span>
        <div className="flex-1" />
        {actions}
      </div>
      <p className="text-xs text-ink-faint">
        {p.finished_at ? `beendet ${formatDateTime(p.finished_at)}` : `${p.scheduled ? "geplant" : "eingereiht"} für ${formatDateTime(p.run_at)}`}
        {p.attempts > 0 && ` · Versuch ${p.attempts}/${p.max_attempts}`}
        {p.next_attempt_at && p.status === "retry_wait" && ` · nächster ${formatDateTime(p.next_attempt_at)}`}
        {p.source !== "manual" && ` · Quelle ${p.source}`}
      </p>
      {p.last_error && <p className="text-xs text-ember">{p.last_error}</p>}
    </li>
  );
}

function useAccountMap() {
  const q = useQuery(() => api.uptik.accounts(), "uptik-accounts", { topics: ["uptik"] });
  return useMemo(() => new Map((q.data ?? []).map((a) => [a.id, a])), [q.data]);
}

function Queue() {
  const q = useQuery(() => api.uptik.queue(), "uptik-queue", { topics: ["uptik"], pollMs: 20_000 });
  const accounts = useAccountMap();
  const act = useAction();
  const [cancel, setCancel] = useState<string | null>(null);
  if (q.loading) return <div className="p-8"><Skeleton rows={4} /></div>;
  if (q.error) return <div className="p-8"><ErrorState message={q.error} onRetry={q.refetch} /></div>;
  return (
    <div className="mx-auto max-w-[60rem] px-6 py-5">
      <Section title="Warteschlange" meta="wartend, laufend, neuer Versuch, angehalten">
        {q.data?.length === 0 ? <p className="px-4 py-6 text-sm text-ink-dim">Nichts geplant.</p> : (
          <ul className="divide-y divide-seam">
            {q.data?.map((p) => (
              <PostRow key={p.id} p={p} accounts={accounts}
                       actions={p.status !== "running" && <Button size="sm" variant="quiet" onClick={() => setCancel(p.id)}>Abbrechen</Button>} />
            ))}
          </ul>
        )}
      </Section>
      <ConfirmDialog open={cancel !== null} title="Beitrag abbrechen?" tone="danger" confirmLabel="Abbrechen" busy={act.busy === "cancel"}
                     onCancel={() => setCancel(null)}
                     onConfirm={() => { const pid = cancel; setCancel(null); if (pid) void act.run("cancel", () => api.uptik.cancel(pid), "Abgebrochen").then(q.refetch); }}>
        Der Beitrag wird nicht veröffentlicht. Er bleibt in der Historie sichtbar.
      </ConfirmDialog>
    </div>
  );
}

function History() {
  const q = useQuery(() => api.uptik.history(), "uptik-history", { topics: ["uptik"] });
  const accounts = useAccountMap();
  const act = useAction();
  if (q.loading) return <div className="p-8"><Skeleton rows={4} /></div>;
  if (q.error) return <div className="p-8"><ErrorState message={q.error} onRetry={q.refetch} /></div>;
  return (
    <div className="mx-auto max-w-[60rem] px-6 py-5">
      <Section title="Historie">
        {q.data?.length === 0 ? <p className="px-4 py-6 text-sm text-ink-dim">Noch nichts veröffentlicht.</p> : (
          <ul className="divide-y divide-seam">
            {q.data?.map((p) => (
              <PostRow key={p.id} p={p} accounts={accounts} actions={
                <>
                  {p.result_url && <Button size="sm" variant="quiet" icon={<ExternalLink className="size-3.5" />} onClick={() => void act.run("open", () => openExternal(p.result_url ?? ""))}>Öffnen</Button>}
                  {p.status === "needs_review" && (
                    <>
                      <Button size="sm" variant="ghost" onClick={() => void act.run("rv", () => api.uptik.review(p.id, "published"), "Als veröffentlicht markiert").then(q.refetch)}>Ist online</Button>
                      <Button size="sm" variant="ghost" onClick={() => void act.run("rv", () => api.uptik.review(p.id, "failed"), "Als nicht veröffentlicht markiert").then(q.refetch)}>Nicht online</Button>
                    </>
                  )}
                </>
              } />
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}

// ------------------------------------------------------------------ settings
function Settings({ settings, refetch }: { settings: UptikSettings; refetch: () => Promise<void> }) {
  const [s, setS] = useState<UptikSettings>(settings);
  const act = useAction();
  const set = <K extends keyof UptikSettings>(k: K, v: UptikSettings[K]) => setS({ ...s, [k]: v });
  const save = () => void act.run("save", () => api.uptik.configure({ settings: s }), "Einstellungen gespeichert").then(refetch);
  const toggle = (k: "headless" | "handoff_enabled" | "youtube_shorts_enabled", label: string, hint: string) => (
    <div className="flex items-start gap-3">
      <Toggle label={label} checked={s[k]} onChange={(v) => set(k, v)} />
      <div><p className="text-ink">{label}</p><p className="text-xs text-ink-faint">{hint}</p></div>
    </div>
  );
  return (
    <div className="mx-auto flex max-w-[48rem] flex-col gap-4 px-6 py-5 text-sm">
      <div className="plate flex flex-col gap-4 p-4">
        {toggle("handoff_enabled", "Videos der Videoproduktion übernehmen", "Fertige Clips landen im Eingang; für Konten mit Autopost wird eine Veröffentlichung zur Freigabe vorgelegt.")}
        {toggle("youtube_shorts_enabled", "YouTube Shorts anbieten (experimentell)", "Upload über YouTube Studio im Browser; ohne Developer-App.")}
        {toggle("headless", "Ohne sichtbares Fenster veröffentlichen", "Die Anmeldung läuft immer in einem sichtbaren Fenster.")}
      </div>
      <div className="plate grid gap-4 p-4 md:grid-cols-2">
        <Field label="Uhrzeit für übernommene Videos (Berlin)" hint="Leer = sofort nach der Freigabe">
          {(id) => <input id={id} type="time" className="field" value={s.handoff_publish_time} onChange={(e) => set("handoff_publish_time", e.target.value)} />}
        </Field>
        <Field label="Standard-Browser für neue Konten">
          {(id) => (
            <select id={id} className="field" value={s.runner} onChange={(e) => set("runner", e.target.value as "local" | "device")}>
              <option value="local">Core-Rechner</option><option value="device">Gerät mit Device Agent</option>
            </select>
          )}
        </Field>
        <Field label="Gerät (Device Agent)">
          {(id) => <input id={id} className="field" maxLength={120} value={s.device} onChange={(e) => set("device", e.target.value)} />}
        </Field>
        <Field label="Versuche je Beitrag" hint="Nur bei vorübergehenden Fehlern; nie nach Captcha, Prüfung oder Ratenlimit">
          {(id) => <input id={id} type="number" min={1} max={6} className="field" value={s.max_attempts} onChange={(e) => set("max_attempts", Number(e.target.value))} />}
        </Field>
        <Field label="Erste Wartezeit (Sekunden, verdoppelt sich)">
          {(id) => <input id={id} type="number" min={30} max={3600} className="field" value={s.retry_base_s} onChange={(e) => set("retry_base_s", Number(e.target.value))} />}
        </Field>
        <Field label="Konten gleichzeitig">
          {(id) => <input id={id} type="number" min={1} max={3} className="field" value={s.max_parallel} onChange={(e) => set("max_parallel", Number(e.target.value))} />}
        </Field>
      </div>
      <div className="flex justify-end"><Button variant="primary" busy={act.busy === "save"} onClick={save}>Speichern</Button></div>
    </div>
  );
}
