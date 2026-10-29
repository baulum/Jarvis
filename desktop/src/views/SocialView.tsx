// Social Media: the daily PJE Systems video. Dashboard (next run, running production, last video, errors),
// library (player over the API, download, captions to copy, regenerate, e-mail, publish), automation switches
// and the platform connectors. Everything with an outside effect goes through the Core's approvals.
import { Check, Clapperboard, Copy, Download, ExternalLink, Link2, Mail, PlugZap, RefreshCw, Send, Sparkles, Unplug } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Button, Dialog, EmptyState, ErrorState, Field, KeyValue, Section, Skeleton, StatusText, Tabs, Tag, Toggle } from "../components/ui";
import { useBlobUrl } from "../hooks/useBlobUrl";
import { useAction, useQuery } from "../hooks/useQuery";
import { copyText } from "../lib/clipboard";
import { formatBytes, formatDateTime, formatRelative } from "../lib/format";
import { openExternal, saveBytes } from "../lib/native";
import {
  CATEGORY_LABEL,
  MANUAL_UPLOAD,
  PLATFORM_LABEL,
  PLATFORMS,
  PUBLICATION_LABEL,
  STAGE_LABEL,
  captionOverLimit,
  captionText,
  dependencyTone,
  publicationTone,
  safeFileName,
  validTime,
  videoStatusLabel,
  videoTone,
  type Platform,
  type SocialSettings,
  type SocialVideo,
  type SocialVideoDetail,
} from "../lib/social";
import { api, useApp } from "../store/app";

type Tab = "overview" | "library" | "automation" | "connector";

export function SocialView() {
  const status = useQuery(() => api.social.status(), "social-status", { topics: ["social", "task", "schedule", "connector"], pollMs: 10_000 });
  const [tab, setTab] = useState<Tab>("overview");
  const [selected, setSelected] = useState<string | null>(null);
  const [producing, setProducing] = useState(false);
  const st = status.data;

  const open = (id: string) => {
    setSelected(id);
    setTab("library");
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-seam px-6 py-2.5">
        <Tabs<Tab>
          value={tab}
          onChange={setTab}
          items={[
            { id: "overview", label: "Übersicht" },
            { id: "library", label: "Bibliothek", count: st ? (st.counts.ready ?? 0) : undefined },
            { id: "automation", label: "Automatisierung" },
            { id: "connector", label: "Connector" },
          ]}
        />
        <div className="flex-1" />
        <Button variant="primary" icon={<Sparkles className="size-3.5" />} onClick={() => setProducing(true)} disabled={!st?.dependencies.ok}>
          Jetzt produzieren
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {status.loading && <div className="p-8"><Skeleton rows={6} /></div>}
        {status.error && <div className="p-8"><ErrorState message={status.error} onRetry={status.refetch} /></div>}
        {st && tab === "overview" && <Overview onOpen={open} />}
        {st && tab === "library" && <Library selected={selected} onSelect={setSelected} />}
        {st && tab === "automation" && <Automation />}
        {st && tab === "connector" && <Connector />}
      </div>
      {producing && <ProduceDialog onClose={() => setProducing(false)} />}
    </div>
  );
}

function ProduceDialog({ onClose, parent }: { onClose: () => void; parent?: SocialVideo }) {
  const [hint, setHint] = useState("");
  const act = useAction();
  const go = () =>
    void act
      .run("produce", () => (parent ? api.social.regenerate(parent.id, hint) : api.social.produce(hint)), "Produktion gestartet – siehe Übersicht")
      .then((r) => r && onClose());
  return (
    <Dialog
      open
      onClose={onClose}
      title={parent ? "Erneut generieren" : "Neues Video produzieren"}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Abbrechen</Button>
          <Button variant="primary" busy={act.busy === "produce"} onClick={go}>Starten</Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p>
          {parent
            ? `Jarvis entwickelt eine neue Idee als Ersatz für „${parent.title}“. Das alte Video bleibt in der Bibliothek.`
            : "Jarvis entwickelt sofort eine neue Idee, rendert, prüft und speichert das Video. Dauert einige Minuten."}
        </p>
        <Field label="Wunsch (optional)" hint="z. B. „etwas mit einem Countdown“ oder „Thema Festpreis“. Die Regeln gegen Wiederholung gelten trotzdem.">
          {(id) => <textarea id={id} className="field min-h-20" maxLength={500} value={hint} onChange={(e) => setHint(e.target.value)} />}
        </Field>
      </div>
    </Dialog>
  );
}

// ------------------------------------------------------------------ overview
function Overview({ onOpen }: { onOpen: (id: string) => void }) {
  const q = useQuery(() => api.social.status(), "social-overview", { topics: ["social", "task", "schedule"], pollMs: 5_000 });
  const act = useAction();
  const last = q.data?.last_video;
  const detail = useQuery(() => (last ? api.social.video(last.id) : Promise.resolve(null)), `social-last-${last?.id ?? "none"}`, { topics: ["social"] });
  if (!q.data) return <div className="p-8"><Skeleton rows={5} /></div>;
  const st = q.data;
  const sched = st.schedule;
  return (
    <div className="mx-auto grid max-w-[68rem] grid-cols-1 gap-4 px-6 py-5 lg:grid-cols-[1fr_22rem]">
      <div className="flex min-w-0 flex-col gap-4">
        {!st.dependencies.ok && st.dependencies.ok !== null && (
          <div role="alert" className="plate border-alarm/40 p-4">
            <div className="flex items-start gap-3">
              <StatusText tone="bad">Video-Produktion nicht möglich</StatusText>
              <div className="flex-1" />
              <Button size="sm" variant="ghost" icon={<RefreshCw className="size-3.5" />} busy={act.busy === "deps"}
                      onClick={() => void act.run("deps", () => api.social.checkDependencies()).then(() => q.refetch())}>Erneut prüfen</Button>
            </div>
            <ul className="mt-2 list-disc pl-5 text-sm text-ink">
              {st.dependencies.problems.map((p) => <li key={p}>{p}</li>)}
            </ul>
            <p className="mt-2 text-xs text-ink-dim">Einrichtung: SETUP.md › Social Media (Node.js, <span className="type-data">npm ci</span> und <span className="type-data">npm run browser</span> in <span className="type-data">social/</span>, FFmpeg).</p>
          </div>
        )}
        {st.producing.map((v) => (
          <article key={v.id} className="plate p-4">
            <div className="flex items-center gap-3">
              <StatusText tone="info" pulse>{videoStatusLabel(v)}</StatusText>
              <span className="text-xs text-ink-faint">gestartet {formatRelative(v.created_at)}</span>
            </div>
            <p className="mt-1 type-title text-ink">{v.title || "Neue Idee entsteht …"}</p>
            <Steps stage={v.stage} percent={st.progress.video_id === v.id ? st.progress.percent : undefined} />
          </article>
        ))}
        <Section title="Letztes Video" meta={last ? formatDateTime(last.created_at) : undefined}
                 actions={last && <Button size="sm" variant="ghost" onClick={() => onOpen(last.id)}>In der Bibliothek</Button>}>
          {!last && (
            <EmptyState icon={<Clapperboard className="size-6" />} title="Noch kein Video.">
              Das erste Video entsteht zur geplanten Zeit – oder sofort mit „Jetzt produzieren“.
            </EmptyState>
          )}
          {last && (
            <div className="grid grid-cols-[minmax(0,12rem)_1fr] gap-4 p-4">
              <Player video={last} />
              <div className="flex min-w-0 flex-col gap-2 text-sm">
                <p className="type-title text-[0.95rem] text-ink">{last.title}</p>
                <div className="flex flex-wrap gap-1.5">
                  <Tag tone="info">{last.category} · {CATEGORY_LABEL[last.category] ?? ""}</Tag>
                  <Tag>{last.tone}</Tag>
                  <Tag tone={last.email.status === "sent" ? "ok" : "idle"}>{last.email.status === "sent" ? "E-Mail gesendet" : "E-Mail offen"}</Tag>
                </div>
                <p className="text-ink-dim">{last.summary}</p>
                {detail.data?.log && (
                  <ol className="mt-1 flex flex-col gap-0.5 text-xs text-ink-faint">
                    {detail.data.log.slice(-6).map((l, i) => (
                      <li key={i} className={l.level === "error" ? "text-alarm" : l.level === "warning" ? "text-ember" : ""}>
                        <span className="type-data mr-2">{l.ts.slice(11, 16)}</span>{l.msg}
                      </li>
                    ))}
                  </ol>
                )}
              </div>
            </div>
          )}
        </Section>
      </div>
      <div className="flex flex-col gap-4">
        <Section title="Aufgabe" meta="PJE Systems Daily Social Video">
          <div className="p-4">
            <KeyValue
              rows={[
                ["Status", <StatusText key="s" tone={sched.enabled ? "ok" : "idle"}>{sched.enabled ? "täglich aktiv" : "pausiert"}</StatusText>],
                ["Zeit", <span key="t" className="type-data">{sched.time} ({sched.timezone})</span>],
                ["Nächstes Video", sched.enabled ? formatDateTime(sched.next_run_at) : "–"],
                ["Letzter Lauf", sched.last_run_at ? `${formatRelative(sched.last_run_at)}${sched.last_status ? ` · ${sched.last_status}` : ""}` : "nie"],
                ["Abhängigkeiten", <StatusText key="d" tone={dependencyTone(st.dependencies)}>{st.dependencies.ok ? "bereit" : st.dependencies.ok === null ? "wird geprüft" : "fehlen"}</StatusText>],
                ["E-Mail", st.settings.email_enabled ? `an ${st.settings.email_recipient}` : "aus"],
                ["Autopost", st.settings.autopost_enabled ? "an (nur verbundene Plattformen)" : "aus"],
              ]}
            />
          </div>
        </Section>
        <Section title="Veröffentlichungen">
          <div className="flex flex-col gap-1.5 p-4 text-sm">
            {st.recent_publications.length === 0 && <p className="text-ink-faint">Noch nichts veröffentlicht. Autopost ist standardmäßig aus.</p>}
            {st.recent_publications.map((p) => (
              <div key={p.id} className="flex items-center gap-2">
                <span className="w-28 shrink-0 text-ink-dim">{PLATFORM_LABEL[p.platform]}</span>
                <StatusText tone={publicationTone(p.status)}>{PUBLICATION_LABEL[p.status]}</StatusText>
                {p.post_url && <button className="ml-auto text-xs text-arc hover:underline" onClick={() => void openExternal(p.post_url!)}>Post öffnen</button>}
              </div>
            ))}
          </div>
        </Section>
        {st.recent_errors.length > 0 && (
          <Section title="Fehler">
            <div className="flex flex-col gap-2 p-4 text-sm">
              {st.recent_errors.map((e) => (
                <button key={e.id} className="text-left" onClick={() => onOpen(e.id)}>
                  <p className="text-alarm">{e.title || "Produktion"} · {formatRelative(e.at)}</p>
                  <p className="line-clamp-3 text-xs text-ink-dim">{e.error}</p>
                </button>
              ))}
            </div>
          </Section>
        )}
      </div>
    </div>
  );
}

const STEPS = ["concept", "render", "qa", "captions", "email"] as const;

function Steps({ stage, percent }: { stage: string; percent?: number }) {
  const at = Math.max(0, STEPS.indexOf(stage as (typeof STEPS)[number]));
  return (
    <ol className="mt-3 flex gap-1.5" aria-label="Fortschritt">
      {STEPS.map((s, i) => (
        <li key={s} className="flex-1">
          <div className={`h-1 rounded-full ${i < at ? "bg-arc" : i === at ? "bg-arc-dim" : "bg-plate-hi"}`}>
            {i === at && percent !== undefined && <div className="h-1 rounded-full bg-arc" style={{ width: `${percent}%` }} />}
          </div>
          <span className={`mt-1 block text-2xs ${i <= at ? "text-ink-dim" : "text-ink-faint"}`}>{STAGE_LABEL[s]}{i === at && percent !== undefined ? ` ${percent} %` : ""}</span>
        </li>
      ))}
    </ol>
  );
}

// ------------------------------------------------------------------ player
function Player({ video }: { video: Pick<SocialVideo, "id" | "status" | "file_name"> }) {
  const ready = video.status === "ready" && !!video.file_name;
  const url = useBlobUrl(ready ? `social/${video.id}` : null, () => api.social.file(video.id));
  if (!ready) return <div className="aspect-[9/16] w-full rounded-lg border border-seam bg-void" />;
  return url ? (
    <video src={url} controls playsInline loop className="aspect-[9/16] w-full rounded-lg border border-seam bg-black" aria-label="Video abspielen" />
  ) : (
    <div className="flex aspect-[9/16] w-full items-center justify-center rounded-lg border border-seam bg-void text-xs text-ink-faint">Lädt …</div>
  );
}

// ------------------------------------------------------------------ library
function Library({ selected, onSelect }: { selected: string | null; onSelect: (id: string) => void }) {
  const list = useQuery(() => api.social.videos(), "social-videos", { topics: ["social"] });
  const videos = useMemo(() => list.data?.videos ?? [], [list.data]);
  const current = selected ?? videos[0]?.id ?? null;
  if (list.loading) return <div className="p-8"><Skeleton rows={6} /></div>;
  if (list.error) return <div className="p-8"><ErrorState message={list.error} onRetry={list.refetch} /></div>;
  if (!videos.length) {
    return (
      <div className="mx-auto max-w-[44rem] px-8 py-10">
        <EmptyState icon={<Clapperboard className="size-6" />} title="Die Bibliothek ist leer.">
          Jedes Video landet hier mit Konzept, Captions und Prüfbericht – automatisch jeden Tag oder per „Jetzt produzieren“.
        </EmptyState>
      </div>
    );
  }
  return (
    <div className="grid h-full grid-cols-[18rem_1fr]">
      <ul className="flex flex-col gap-px overflow-y-auto border-r border-seam p-2" aria-label="Videos">
        {videos.map((v) => (
          <li key={v.id}>
            <button
              onClick={() => onSelect(v.id)}
              aria-current={v.id === current}
              className={`flex w-full flex-col gap-1 rounded-md px-3 py-2 text-left transition-colors ${v.id === current ? "bg-plate-hi" : "hover:bg-plate"}`}
            >
              <span className="line-clamp-2 text-sm text-ink">{v.title || "Ohne Titel"}</span>
              <span className="flex items-center gap-2 text-2xs text-ink-faint">
                <StatusText tone={videoTone(v)}>{v.status === "ready" ? formatDateTime(v.created_at) : videoStatusLabel(v)}</StatusText>
                {v.category && <span className="type-data">{v.category}</span>}
                {v.trigger === "schedule" && <span>täglich</span>}
              </span>
            </button>
          </li>
        ))}
      </ul>
      <div className="min-w-0 overflow-y-auto">{current && <VideoDetail key={current} id={current} onSelect={onSelect} />}</div>
    </div>
  );
}

function VideoDetail({ id, onSelect }: { id: string; onSelect: (id: string) => void }) {
  const q = useQuery(() => api.social.video(id), `social-video-${id}`, { topics: ["social"] });
  const platforms = useQuery(() => api.social.platforms(), "social-platforms", { topics: ["connector", "social"] });
  const act = useAction();
  const pushToast = useApp((s) => s.pushToast);
  const [regen, setRegen] = useState(false);
  const [manual, setManual] = useState<Platform | null>(null);
  const v = q.data;
  if (q.loading) return <div className="p-8"><Skeleton rows={6} /></div>;
  if (q.error || !v) return <div className="p-8"><ErrorState message={q.error ?? "Nicht gefunden"} onRetry={q.refetch} /></div>;
  const pubs = new Map(v.publications.map((p) => [p.platform, p]));
  const ready = v.status === "ready";

  const download = () =>
    void act.run("dl", async () => {
      const blob = await api.social.file(v.id, true);
      const saved = await saveBytes(safeFileName(v), new Uint8Array(await blob.arrayBuffer()), { name: "MP4-Video", extensions: ["mp4"], mime: "video/mp4" });
      if (saved) pushToast({ tone: "ok", title: "Gespeichert", body: saved });
    });
  const copy = (text: string, what: string) => void copyText(text).then((ok) => pushToast({ tone: ok ? "ok" : "bad", title: ok ? `${what} kopiert` : "Kopieren fehlgeschlagen" }));

  return (
    <div className="mx-auto grid max-w-[64rem] grid-cols-1 gap-5 px-6 py-5 xl:grid-cols-[17rem_1fr]">
      <div className="flex flex-col gap-3">
        <Player video={v} />
        {ready && (
          <div className="grid grid-cols-2 gap-1.5">
            <Button size="sm" icon={<Download className="size-3.5" />} busy={act.busy === "dl"} onClick={download}>MP4</Button>
            <Button size="sm" icon={<RefreshCw className="size-3.5" />} onClick={() => setRegen(true)}>Neu generieren</Button>
            <Button size="sm" icon={<Mail className="size-3.5" />} busy={act.busy === "mail"}
                    onClick={() => void act.run("mail", () => api.social.email(v.id), "E-Mail an Sir und Bagy gesendet").then(q.refetch)}>E-Mail</Button>
            <Button size="sm" icon={<Link2 className="size-3.5" />} busy={act.busy === "link"}
                    onClick={() => void act.run("link", () => api.social.link(v.id, 24)).then((r) => r && (r.url ? copy(r.url, "Download-Link (24 h)") : pushToast({ tone: "warn", title: "Kein öffentlicher Link", body: "server.public_base_url bzw. social_media.public_base_url ist nicht gesetzt." })))}>
              Link
            </Button>
          </div>
        )}
        <KeyValue
          rows={[
            ["Status", <StatusText key="s" tone={videoTone(v)}>{videoStatusLabel(v)}</StatusText>],
            ["Erstellt", formatDateTime(v.created_at)],
            ["Kategorie", `${v.category} · ${CATEGORY_LABEL[v.category] ?? ""}`],
            ["Ton / Farbe", `${v.tone} · ${v.palette}`],
            ["Länge", v.duration_s ? `${v.duration_s.toFixed(1)} s · ${formatBytes(v.size)}` : "–"],
            ["E-Mail", v.email.status === "sent" ? `an ${v.email.to}, ${formatRelative(v.email.sent_at)}` : "nicht gesendet"],
          ]}
        />
      </div>
      <div className="flex min-w-0 flex-col gap-4">
        <div>
          <h2 className="type-title text-[1.05rem] text-ink">{v.title || "Ohne Titel"}</h2>
          {v.hook && <p className="mt-1 text-sm text-ink-dim">Hook: „{v.hook}“</p>}
          {v.concept.summary && <p className="mt-2 text-sm text-ink">{v.concept.summary}</p>}
          {v.error && <p className="mt-2 text-sm text-alarm">{v.error}</p>}
          {v.parent_id && <button className="mt-1 text-xs text-arc hover:underline" onClick={() => onSelect(v.parent_id!)}>Ersetzt ein früheres Video</button>}
        </div>

        {ready && (
          <Section title="Captions & Hashtags">
            <div className="flex flex-col divide-y divide-seam">
              {PLATFORMS.map((pf) => {
                const c = v.captions[pf];
                const text = captionText(pf, c);
                const pub = pubs.get(pf);
                const plat = platforms.data?.find((p) => p.platform === pf);
                return (
                  <div key={pf} className="flex flex-col gap-2 px-4 py-3">
                    <div className="flex items-center gap-2">
                      <span className="text-sm text-ink">{PLATFORM_LABEL[pf]}</span>
                      {pub && <StatusText tone={publicationTone(pub.status)}>{PUBLICATION_LABEL[pub.status]}</StatusText>}
                      {captionOverLimit(pf, c) && <Tag tone="bad">zu lang</Tag>}
                      <div className="flex-1" />
                      <Button size="sm" variant="ghost" icon={<Copy className="size-3.5" />} onClick={() => copy(text, "Caption")}>Kopieren</Button>
                      {plat?.connected && plat.enabled ? (
                        <Button size="sm" variant="ghost" icon={<Send className="size-3.5" />} busy={act.busy === `pub-${pf}`}
                                onClick={() => void act.run(`pub-${pf}`, () => api.social.publish(v.id, pf)).then((r) => {
                                  if (r?.status === "approval_required") pushToast({ tone: "info", title: "Freigabe nötig", body: r.summary, action: { label: "Freigaben", view: "approvals" } });
                                  void q.refetch();
                                })}>Veröffentlichen</Button>
                      ) : (
                        <Button size="sm" variant="ghost" icon={<ExternalLink className="size-3.5" />} onClick={() => { copy(text, "Caption"); setManual(pf); }}>Manuell</Button>
                      )}
                    </div>
                    {pf === "youtube" && c?.title && <p className="text-sm text-ink">Titel: {c.title}</p>}
                    <p className="whitespace-pre-wrap text-sm text-ink-dim">{text || "–"}</p>
                    {pub?.post_url && <button className="self-start text-xs text-arc hover:underline" onClick={() => void openExternal(pub.post_url!)}>{pub.post_url}</button>}
                    {pub?.error && <p className="text-xs text-alarm">{pub.error}</p>}
                  </div>
                );
              })}
            </div>
          </Section>
        )}

        <QaSection v={v} />

        <Section title="Konzept & Storyboard">
          <div className="flex flex-col gap-3 p-4 text-sm">
            {v.concept.idea && <p className="text-ink">{String(v.concept.idea)}</p>}
            {v.script && <p className="whitespace-pre-wrap text-ink-dim">{v.script}</p>}
            <ol className="flex flex-wrap gap-1.5">
              {(v.storyboard.scenes ?? []).map((s) => (
                <li key={s.id}><Tag>{s.component} · {s.duration.toFixed(1)} s{s.transition && s.transition !== "cut" ? ` · ${s.transition}` : ""}</Tag></li>
              ))}
            </ol>
            <p className="text-xs text-ink-faint">Sounds: {v.sounds.join(", ") || "–"}{v.concept.inspiration?.length ? ` · Inspiration: ${v.concept.inspiration.join(", ")}` : ""}</p>
          </div>
        </Section>

        <Section title="Protokoll">
          <ol className="flex flex-col gap-1 p-4 text-xs">
            {v.log.map((l, i) => (
              <li key={i} className={l.level === "error" ? "text-alarm" : l.level === "warning" ? "text-ember" : "text-ink-dim"}>
                <span className="type-data mr-2 text-ink-faint">{formatDateTime(l.ts)}</span>{l.msg}
              </li>
            ))}
          </ol>
        </Section>
      </div>
      {regen && <ProduceDialog parent={v} onClose={() => setRegen(false)} />}
      {manual && <ManualDialog video={v} platform={manual} onClose={() => setManual(null)} onDone={() => void q.refetch()} />}
    </div>
  );
}

function QaSection({ v }: { v: SocialVideoDetail }) {
  const checks = v.qa.checks ?? [];
  if (!checks.length) return null;
  const probe = v.qa.probe as { width?: number; height?: number; fps?: number; video_codec?: string; audio_codec?: string; loudness?: { integrated_lufs?: number; true_peak_db?: number } } | undefined;
  return (
    <Section title="Qualitätskontrolle" meta={v.qa.passed ? "bestanden" : "nicht bestanden"}>
      <div className="flex flex-col gap-2 p-4">
        {probe && (
          <p className="type-data text-xs text-ink-faint">
            {probe.width}×{probe.height} · {probe.fps} fps · {probe.video_codec}/{probe.audio_codec}
            {probe.loudness ? ` · ${probe.loudness.integrated_lufs} LUFS · ${probe.loudness.true_peak_db} dBTP` : ""}
          </p>
        )}
        <ul className="grid grid-cols-1 gap-x-6 gap-y-1 text-xs md:grid-cols-2">
          {checks.map((c) => (
            <li key={c.id} className="flex items-start gap-1.5">
              {c.ok ? <Check className="mt-px size-3.5 shrink-0 text-arc" aria-label="ok" /> : <span className="mt-px text-alarm" aria-label="Fehler">✕</span>}
              <span className={c.ok ? "text-ink-dim" : "text-ink"} title={c.detail}>{c.label}{!c.ok && c.detail ? ` – ${c.detail}` : ""}</span>
            </li>
          ))}
        </ul>
      </div>
    </Section>
  );
}

function ManualDialog({ video, platform, onClose, onDone }: { video: SocialVideoDetail; platform: Platform; onClose: () => void; onDone: () => void }) {
  const [url, setUrl] = useState("");
  const act = useAction();
  return (
    <Dialog
      open
      onClose={onClose}
      title={`${PLATFORM_LABEL[platform]} – manuell posten`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Schließen</Button>
          <Button variant="primary" busy={act.busy === "mark"} disabled={!!url && !url.startsWith("https://")}
                  onClick={() => void act.run("mark", () => api.social.markManual(video.id, platform, url || null), "Gespeichert").then((r) => { if (r) { onDone(); onClose(); } })}>
            {url ? "Als veröffentlicht speichern" : "Als manuell markieren"}
          </Button>
        </>
      }
    >
      <ol className="flex list-decimal flex-col gap-2 pl-5">
        <li>Die Caption ist in der Zwischenablage.</li>
        <li>MP4 herunterladen (links unter dem Video) oder am Handy unter Social Media öffnen.</li>
        <li>
          <button className="text-arc hover:underline" onClick={() => void openExternal(MANUAL_UPLOAD[platform])}>{PLATFORM_LABEL[platform]} öffnen</button>, Video hochladen, Caption einfügen.
        </li>
        <li>Optional den Link zum Post hier eintragen – er erscheint in der Bibliothek und in künftigen Mails.</li>
      </ol>
      <div className="mt-3">
        <Field label="Link zum Post (optional)">{(id) => <input id={id} className="field" value={url} placeholder="https://…" onChange={(e) => setUrl(e.target.value.trim())} />}</Field>
      </div>
    </Dialog>
  );
}

// ------------------------------------------------------------------ automation
function Automation() {
  const q = useQuery(() => api.social.settings(), "social-settings", { topics: ["social", "schedule"] });
  const s = q.data;
  if (q.loading) return <div className="p-8"><Skeleton rows={6} /></div>;
  if (q.error || !s) return <div className="p-8"><ErrorState message={q.error ?? "Fehler"} onRetry={q.refetch} /></div>;
  // the form starts from the saved values; a change on the Core (another device) remounts it
  const key = [s.schedule.time, s.publish_time, s.email_recipient, s.hint].join("|");
  return <AutomationForm key={key} s={s} onSaved={(r) => q.setData(() => r)} />;
}

function AutomationForm({ s, onSaved }: { s: SocialSettings; onSaved: (s: SocialSettings) => void }) {
  const act = useAction();
  const [time, setTime] = useState(s.schedule.time);
  const [publishTime, setPublishTime] = useState(s.publish_time ?? "");
  const [recipient, setRecipient] = useState(s.email_recipient);
  const [hint, setHint] = useState(s.hint);
  const save = (body: Parameters<typeof api.social.saveSettings>[0], msg = "Gespeichert") =>
    void act.run("save", () => api.social.saveSettings(body), msg).then((r) => r && onSaved(r));
  const thirdParty = recipient.trim().toLowerCase() !== s.owner_email.toLowerCase();

  return (
    <div className="mx-auto flex max-w-[44rem] flex-col gap-4 px-6 py-5">
      <Section title="Tägliche Produktion" meta="Zeitplan „PJE Systems Daily Social Video“">
        <div className="flex flex-col gap-4 p-4 text-sm">
          <Row label="Täglich automatisch produzieren" hint="Ein Video pro Tag. Verpasste Läufe holt Jarvis nach, aber nur am selben Tag.">
            <Toggle label="Tägliche Produktion" checked={s.schedule.enabled} onChange={(v) => save({ daily_enabled: v }, v ? "Tägliche Produktion an" : "Tägliche Produktion pausiert")} />
          </Row>
          <Row label="Uhrzeit" hint={`Zeitzone ${s.schedule.timezone}. Nächstes Video: ${s.schedule.enabled ? formatDateTime(s.schedule.next_run_at) : "–"}`}>
            <div className="flex gap-1.5">
              <input type="time" aria-label="Produktionszeit" className="field w-28 type-data" value={time} onChange={(e) => setTime(e.target.value)} />
              <Button size="sm" disabled={!validTime(time) || time === s.schedule.time} onClick={() => save({ production_time: time })}>Übernehmen</Button>
            </div>
          </Row>
          <Row label="Voiceover (lokale Jarvis-Stimme)" hint="Standard aus. Nutzt die vorhandene Sprachausgabe von Jarvis.">
            <Toggle label="Voiceover" checked={s.voiceover} onChange={(v) => save({ voiceover: v })} />
          </Row>
          <Field label="Kreativer Wunsch für die nächsten Videos (optional)" hint="Wird als Hinweis an die Ideenfindung gegeben; Regeln gegen Wiederholung gelten weiter.">
            {(id) => (
              <div className="flex gap-1.5">
                <input id={id} className="field flex-1" maxLength={500} value={hint} onChange={(e) => setHint(e.target.value)} placeholder="z. B. mehr Formate mit echtem Ablauf" />
                <Button size="sm" disabled={hint === s.hint} onClick={() => save({ hint })}>Speichern</Button>
              </div>
            )}
          </Field>
        </div>
      </Section>
      <Section title="E-Mail">
        <div className="flex flex-col gap-4 p-4 text-sm">
          <Row label="Video täglich per E-Mail schicken" hint="Über die Gmail-Anbindung (Connectoren › Google). MP4 im Anhang, sonst ein Download-Link.">
            <Toggle label="E-Mail-Versand" checked={s.email_enabled} onChange={(v) => save({ email_enabled: v })} />
          </Row>
          <Field label="Empfänger" hint={thirdParty ? "Nicht Sirs Adresse: jeder Versand braucht dann eine Freigabe." : "Sirs eigene Adresse – Versand ohne tägliche Freigabe."}>
            {(id) => (
              <div className="flex gap-1.5">
                <input id={id} type="email" className="field flex-1" value={recipient} onChange={(e) => setRecipient(e.target.value)} />
                <Button size="sm" disabled={recipient === s.email_recipient} onClick={() => save({ email_recipient: recipient })}>Speichern</Button>
              </div>
            )}
          </Field>
        </div>
      </Section>
      <Section title="Veröffentlichung">
        <div className="flex flex-col gap-4 p-4 text-sm">
          <Row label="Autopost" hint="Standard aus. Veröffentlicht wird nur auf verbundenen und einzeln aktivierten Plattformen – und jede Veröffentlichung läuft über die Freigaben.">
            <Toggle label="Autopost" checked={s.autopost_enabled} onChange={(v) => save({ autopost_enabled: v })} />
          </Row>
          <Row label="Veröffentlichungszeit (optional)" hint="Leer = direkt nach der Produktion.">
            <div className="flex gap-1.5">
              <input type="time" aria-label="Veröffentlichungszeit" className="field w-28 type-data" value={publishTime} onChange={(e) => setPublishTime(e.target.value)} />
              <Button size="sm" disabled={(publishTime || null) === s.publish_time || (!!publishTime && !validTime(publishTime))} onClick={() => save({ publish_time: publishTime })}>Übernehmen</Button>
            </div>
          </Row>
        </div>
      </Section>
    </div>
  );
}

function Row({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex items-start gap-4">
      <div className="min-w-0 flex-1">
        <p className="text-ink">{label}</p>
        {hint && <p className="mt-0.5 text-xs text-ink-faint">{hint}</p>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

// ------------------------------------------------------------------ connector
const PLATFORM_NOTE: Record<Platform, string> = {
  instagram: "Instagram-Business- oder Creator-Konto, verknüpft mit einer Facebook-Seite; Meta-App mit instagram_content_publish (App-Review für den Livebetrieb).",
  facebook: "Facebook-Seite von PJE Systems; dieselbe Meta-App und Anmeldung wie Instagram (pages_manage_posts).",
  tiktok: "TikTok-Developer-App mit Content Posting API. Ohne Audit durch TikTok nur private Posts (SELF_ONLY).",
  youtube: "YouTube Data API v3 im Google-Cloud-Projekt aktivieren. Ungeprüfte Projekte laden nur privat hoch; 1 600 Quota-Einheiten je Upload.",
};

function Connector() {
  const q = useQuery(() => api.social.platforms(), "social-platforms", { topics: ["connector", "social"] });
  const settings = useQuery(() => api.social.settings(), "social-settings", { topics: ["social"] });
  const act = useAction();
  const setView = useApp((s) => s.setView);
  if (q.loading || settings.loading) return <div className="p-8"><Skeleton rows={6} /></div>;
  if (q.error) return <div className="p-8"><ErrorState message={q.error} onRetry={q.refetch} /></div>;
  const s = settings.data;
  return (
    <div className="mx-auto flex max-w-[48rem] flex-col gap-4 px-6 py-5">
      <div className="plate flex items-start gap-4 p-4 text-sm">
        <div className="min-w-0 flex-1">
          <p className="text-ink">Autopost global</p>
          <p className="mt-0.5 text-xs text-ink-faint">Aus: Videos werden nur gespeichert und gemailt. An: nur Plattformen, die verbunden UND einzeln aktiviert sind; jede Veröffentlichung braucht eine Freigabe (oder eine eigene Regel unter Freigaben).</p>
        </div>
        {s && <Toggle label="Autopost global" checked={s.autopost_enabled} onChange={(v) => void act.run("auto", () => api.social.saveSettings({ autopost_enabled: v })).then(() => { void settings.refetch(); void q.refetch(); })} />}
      </div>
      {q.data?.map((p) => (
        <article key={p.platform} className="plate flex flex-col gap-3 p-4 text-sm">
          <div className="flex items-center gap-3">
            <h3 className="type-title text-ink">{PLATFORM_LABEL[p.platform]}</h3>
            <StatusText tone={p.connected && p.enabled ? "ok" : p.connected ? "warn" : "idle"}>
              {p.connected ? (p.enabled ? "verbunden" : "verbunden, deaktiviert") : "nicht verbunden"}
            </StatusText>
            {p.will_autopost && <Tag tone="ok">postet automatisch</Tag>}
            <div className="flex-1" />
            {p.connected ? (
              <Button size="sm" variant="ghost" icon={<Unplug className="size-3.5" />} busy={act.busy === `dc-${p.platform}`}
                      onClick={() => void act.run(`dc-${p.platform}`, () => api.social.disconnect(p.platform), "Getrennt").then(q.refetch)}>Trennen</Button>
            ) : (
              <Button size="sm" icon={<PlugZap className="size-3.5" />} busy={act.busy === `oa-${p.platform}`} disabled={!p.app_configured}
                      title={p.app_configured ? undefined : "Erst die App-Zugangsdaten unter Connectoren eintragen"}
                      onClick={() => void act.run(`oa-${p.platform}`, async () => { const r = await api.social.oauthStart(p.platform); await openExternal(r.auth_url); }, "Anmeldung im Browser geöffnet")}>
                Verbinden
              </Button>
            )}
          </div>
          <p className="text-xs text-ink-dim">{PLATFORM_NOTE[p.platform]}</p>
          {p.health?.message && <p className={`text-xs ${p.health.status === "ok" ? "text-ink-faint" : "text-ember"}`}>{p.health.message}</p>}
          <div className="flex items-center gap-4 border-t border-seam pt-3">
            <label className="flex items-center gap-2 text-xs text-ink-dim">
              <Toggle label={`Autopost ${PLATFORM_LABEL[p.platform]}`} checked={p.autopost}
                      onChange={(v) => void act.run("pf", () => api.social.saveSettings({ platforms: { [p.platform]: { autopost: v } } })).then(q.refetch)} />
              Automatisch veröffentlichen
            </label>
            <label className="flex items-center gap-2 text-xs text-ink-dim">
              <Toggle label={`Connector ${PLATFORM_LABEL[p.platform]} aktiv`} checked={p.enabled} disabled={!p.connected}
                      onChange={(v) => void act.run("en", () => api.configureConnector(p.connector ?? `social_${p.platform}`, { enabled: v })).then(q.refetch)} />
              Connector aktiv
            </label>
            <div className="flex-1" />
            {!p.app_configured && <Button size="sm" variant="quiet" onClick={() => setView("connectors")}>App-Zugangsdaten eintragen</Button>}
          </div>
        </article>
      ))}
    </div>
  );
}
