import { DatabaseBackup } from "lucide-react";
import { Dot, Section, Tag } from "./ui";
import { useAction, useQuery } from "../hooks/useQuery";
import { formatBytes, formatRelative } from "../lib/format";
import { api } from "../store/app";

export function BackupCard() {
  const q = useQuery(() => api.backupStatus(), "backup-status", { topics: ["system"] });
  const act = useAction();
  const b = q.data;
  const last = b?.last;
  const failed = last?.ok === false;
  return (
    <Section
      title="Sicherung"
      meta={b ? `${b.count} Sicherungen · täglich ${b.at}` : undefined}
      actions={
        <button
          type="button"
          disabled={act.busy !== null || !b?.enabled}
          onClick={() => void act.run("backup", () => api.runBackup(), "Sicherung abgeschlossen").then(() => q.refetch())}
          className="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-ink-dim hover:bg-plate-hi hover:text-ink disabled:opacity-50"
        >
          <DatabaseBackup className="size-3.5" aria-hidden />
          {act.busy === "backup" ? "Sichere …" : "Jetzt sichern"}
        </button>
      }
    >
      <div className="flex flex-col gap-2 p-4 text-sm">
        {!b ? (
          <span className="text-ink-dim">Lädt …</span>
        ) : !b.enabled ? (
          <span className="text-ink-dim">Automatische Sicherung ist deaktiviert.</span>
        ) : (
          <>
            <div className="flex items-center gap-2">
              <Dot tone={failed ? "bad" : b.recent_enough ? "ok" : "warn"} />
              <span className="text-ink">
                {failed
                  ? `Fehlgeschlagen: ${last?.error ?? "unbekannt"}`
                  : last?.ok_at
                    ? `Zuletzt ${formatRelative(last.ok_at)} · ${formatBytes(last.size ?? 0)}`
                    : b.recent_enough
                      ? "Aktuelle Sicherung vorhanden"
                      : "Noch keine Sicherung"}
              </span>
              {b.encrypted && <Tag tone="ok">verschlüsselt</Tag>}
            </div>
            <div className="type-data text-xs text-ink-faint">{b.directory}</div>
            <p className="text-xs text-ink-dim">
              Datenbank, Gedächtnis (Vault) und Konfiguration. Schlüsselbund-Zugangsdaten sind nie enthalten. Schlüssel sichern:
              <code className="mx-1">jarvisctl backup key</code>
            </p>
          </>
        )}
      </div>
    </Section>
  );
}
