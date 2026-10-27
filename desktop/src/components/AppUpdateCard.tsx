import { Hammer, Link2, RefreshCw, Send } from "lucide-react";
import { useState } from "react";
import { useAction, useQuery } from "../hooks/useQuery";
import {
  buildActive,
  buildLabel,
  buildTone,
  deviceHint,
  deviceLine,
  deviceTone,
  shortFingerprint,
  summary,
} from "../lib/appUpdate";
import { copyText } from "../lib/clipboard";
import { formatBytes, formatRelative } from "../lib/format";
import type { AppInstallLink, AppUpdateRelease } from "../lib/types";
import { api } from "../store/app";
import { Button, CodeBlock, Dialog, Dot, Section, StatusText, Tag } from "./ui";

/** Android app updates: build state, what each phone runs, releases (withdraw / rebuild) and the first install. */
export function AppUpdateCard() {
  const q = useQuery(() => api.appUpdate.status(), "app-update", { topics: ["system"] });
  const act = useAction();
  const [link, setLink] = useState<AppInstallLink | null>(null);
  const s = q.data;
  const b = s?.build;
  const active = buildActive(b);
  const latestCode = s?.latest?.version_code ?? null;

  const run = (label: string, fn: () => Promise<unknown>, ok?: string) =>
    void act.run(label, fn, ok).then(() => q.refetch());

  const withdraw = (r: AppUpdateRelease) =>
    run(`w${r.version_code}`, () => api.appUpdate.withdraw(r.version_code, !r.withdrawn),
      r.withdrawn ? "Release wieder freigegeben" : "Release zurückgezogen");

  if (q.error && !s) {
    return (
      <Section title="Handy-App-Updates">
        <div className="p-4 text-sm text-ink-dim">{q.error}</div>
      </Section>
    );
  }

  return (
    <Section
      title="Handy-App-Updates"
      meta={s ? `${s.branch} · ${s.auto_build ? "automatisch bei App-Änderungen" : "nur manuell"}` : undefined}
      actions={
        s?.enabled && (
          <>
            <Button size="sm" variant="ghost" icon={<RefreshCw className="size-3.5" />} busy={act.busy === "check"}
                    disabled={active} onClick={() => run("check", () => api.appUpdate.check())}>
              Prüfen
            </Button>
            <Button size="sm" variant="ghost" icon={<Hammer className="size-3.5" />} busy={act.busy === "build"}
                    disabled={active} onClick={() => run("build", () => api.appUpdate.build(), "Build gestartet")}>
              {b?.state === "failed" ? "Wiederholen" : "Jetzt bauen"}
            </Button>
            <Button size="sm" variant="ghost" icon={<Send className="size-3.5" />} busy={act.busy === "push"}
                    disabled={!s.latest} onClick={() => run("push", () => api.appUpdate.distribute(), "An die Handys gemeldet")}>
              Verteilen
            </Button>
            <Button size="sm" variant="ghost" icon={<Link2 className="size-3.5" />} busy={act.busy === "link"} disabled={!s.latest}
                    onClick={() => void act.run("link", () => api.appUpdate.installLink()).then((l) => l && setLink(l))}>
              Installationslink
            </Button>
          </>
        )
      }
    >
      <div className="flex flex-col gap-4 p-4 text-sm">
        {!s || !b ? (
          <span className="text-ink-dim">Lädt …</span>
        ) : (
          <>
            <div className="flex flex-col gap-1.5">
              <div className="flex items-center gap-2">
                <StatusText tone={buildTone(b)} pulse={active}>{buildLabel(b)}</StatusText>
                <span className="min-w-0 flex-1 truncate text-ink">{summary(s)}</span>
                {b.finished_at && !active && <span className="text-xs text-ink-faint">{formatRelative(b.finished_at)}</span>}
              </div>
              {s.last_check && !active && (
                <div className="text-xs text-ink-faint">
                  Letzte Prüfung {formatRelative(s.last_check.at)}: {s.last_check.reason}
                </div>
              )}
              {s.toolchain.ok === false && (
                <div className="text-xs text-ember">Werkzeuge: {s.toolchain.error}</div>
              )}
              {b.log_tail && (active || b.state === "failed") && <CodeBlock value={b.log_tail} className="max-h-40 text-2xs" />}
            </div>

            <div>
              <div className="mb-1.5 text-xs uppercase tracking-[0.14em] text-ink-faint">Handys</div>
              {s.devices.length === 0 ? (
                <p className="text-xs text-ink-dim">
                  Noch kein Handy hat sich gemeldet. Die erste Version mit Updater einmal per „Installationslink“ aufs Handy
                  bringen – danach meldet sich die App selbst.
                </p>
              ) : (
                <ul className="divide-y divide-seam">
                  {s.devices.map((d) => {
                    const hint = deviceHint(d);
                    return (
                      <li key={d.device_id} className="flex flex-col gap-1 py-2">
                        <div className="flex items-center gap-2">
                          <Dot tone={deviceTone(d, latestCode)} pulse={d.state === "downloading" || d.state === "installing"} />
                          <span className="text-ink">{d.name}</span>
                          <span className="type-data text-xs text-ink-dim">{d.version_name || d.version_code}</span>
                          <span className="flex-1 text-right text-xs text-ink-dim">{deviceLine(d, latestCode)}</span>
                          <span className="text-2xs text-ink-faint">{formatRelative(d.updated_at)}</span>
                        </div>
                        {d.state === "downloading" && d.progress != null && (
                          <div className="h-1 overflow-hidden rounded-full bg-seam">
                            <div className="h-full bg-arc transition-[width] duration-700" style={{ width: `${d.progress * 100}%` }} />
                          </div>
                        )}
                        {hint && <span className="text-xs text-ink-faint">{hint}</span>}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>

            <div>
              <div className="mb-1.5 text-xs uppercase tracking-[0.14em] text-ink-faint">
                Releases <span className="normal-case tracking-normal">(die letzten {s.keep_releases} bleiben)</span>
              </div>
              {s.releases.length === 0 ? (
                <p className="text-xs text-ink-dim">Noch kein Release.</p>
              ) : (
                <ul className="divide-y divide-seam">
                  {s.releases.map((r) => (
                    <li key={r.version_code} className="flex items-center gap-2 py-1.5">
                      <span className="type-data text-ink">{r.version_name}</span>
                      {r.version_code === latestCode && <Tag tone="ok">aktiv</Tag>}
                      {r.withdrawn && <Tag tone="bad">zurückgezogen</Tag>}
                      {r.rebuild_of != null && <Tag tone="info">Wiederherstellung von {r.rebuild_of}</Tag>}
                      <span className="min-w-0 flex-1 truncate text-xs text-ink-dim" title={r.changelog.map((c) => c.subject).join("\n")}>
                        {r.changelog[0]?.subject ?? ""}
                      </span>
                      <span className="text-xs text-ink-faint">{formatBytes(r.size)} · {formatRelative(r.created_at)}</span>
                      <Button size="sm" variant="quiet" busy={act.busy === `w${r.version_code}`} onClick={() => withdraw(r)}>
                        {r.withdrawn ? "Freigeben" : "Zurückziehen"}
                      </Button>
                      {r.version_code !== latestCode && (
                        <Button size="sm" variant="quiet" busy={act.busy === `r${r.version_code}`} disabled={active}
                                title="Baut diesen Stand neu mit höherer Versionsnummer (Android erlaubt kein Downgrade)"
                                onClick={() => run(`r${r.version_code}`, () => api.appUpdate.rebuild(r.version_code), "Wiederherstellung gestartet")}>
                          Wiederherstellen
                        </Button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="type-data text-xs text-ink-faint">
              Signatur {s.signing.exists ? shortFingerprint(s.signing.fingerprint) : "noch nicht erzeugt (beim ersten Build)"} · Log {s.log_file}
            </div>
          </>
        )}
      </div>

      <Dialog open={link !== null} onClose={() => setLink(null)} title="J.A.R.V.I.S. aufs Handy installieren">
        {link && (
          <div className="flex flex-col gap-3">
            <p>
              Einmalig für die erste Version mit Updater (oder ein neues Handy): QR-Code mit der Handy-Kamera scannen oder den
              Link öffnen – im WLAN oder unterwegs über Tailscale. Danach aktualisiert sich die App selbst.
            </p>
            {link.svg && (
              <img alt="QR-Code des Installationslinks" className="mx-auto size-48 rounded bg-white p-1"
                   src={`data:image/svg+xml;utf8,${encodeURIComponent(link.svg)}`} />
            )}
            <ul className="flex flex-col gap-1">
              {link.urls.map((u) => (
                <li key={u} className="flex items-center gap-2">
                  <span className="type-data min-w-0 flex-1 truncate text-xs">{u}</span>
                  <Button size="sm" variant="quiet" onClick={() => void copyText(u)}>Kopieren</Button>
                </li>
              ))}
            </ul>
            <p className="text-xs text-ink-faint">
              Version {link.release.version_name} · gültig bis {new Date(link.expires_at).toLocaleString("de-DE")}.
              {link.certificate_fingerprint && (
                <> Der Browser warnt vor dem eigenen Zertifikat von Jarvis – Fingerabdruck {shortFingerprint(link.certificate_fingerprint)} … dann „Weiter“.</>
              )}
            </p>
          </div>
        )}
      </Dialog>
    </Section>
  );
}
