import { Check, X } from "lucide-react";
import { useState } from "react";
import { compactJson, formatRelative, statusLabel, statusTone } from "../lib/format";
import type { Approval } from "../lib/types";
import { api, useApp } from "../store/app";
import { Button, CodeBlock, RiskBadge, StatusText } from "./ui";

/** One approval request with approve/reject. Used in the approvals view and inline in chat. */
export function ApprovalCard({ approval: a, compact = false }: { approval: Approval; compact?: boolean }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const pushToast = useApp((s) => s.pushToast);
  const refreshApprovals = useApp((s) => s.refreshApprovals);
  const pending = a.status === "pending";
  const hasArgs = a.args && Object.keys(a.args).length > 0;

  async function decide(approve: boolean) {
    setBusy(approve ? "approve" : "reject");
    try {
      const r = await api.decideApproval(a.id, approve, note.trim() || undefined);
      pushToast({
        tone: r.status === "failed" ? "bad" : "ok",
        title: approve ? (r.status === "executed" ? "Freigegeben und ausgeführt" : `Freigabe: ${statusLabel(r.status)}`) : "Abgelehnt",
        body: a.summary,
      });
      await refreshApprovals();
    } catch (e) {
      pushToast({ tone: "bad", title: "Entscheidung fehlgeschlagen", body: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  return (
    <article
      className={`rounded-lg border bg-plate ${pending ? (a.risk === "critical" ? "border-alarm/50" : "border-ember/40") : "border-seam"}`}
    >
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-seam px-4 py-2.5">
        <RiskBadge risk={a.risk} />
        <span className="type-data text-ink-dim">{a.tool}</span>
        <span className="flex-1" />
        {!pending && <StatusText tone={statusTone(a.status)}>{statusLabel(a.status)}</StatusText>}
        <span className="text-2xs text-ink-faint" title={a.created_at}>
          {formatRelative(a.created_at)}
        </span>
      </header>
      <div className="flex flex-col gap-3 px-4 py-3">
        <p className="text-[0.9375rem] text-ink">{a.summary}</p>
        {a.reason && <p className="text-xs text-ink-dim">Grund für die Rückfrage: {a.reason}</p>}
        {hasArgs && (compact ? (
          <details className="text-xs text-ink-dim">
            <summary className="cursor-default select-none hover:text-ink">Argumente anzeigen</summary>
            <CodeBlock value={compactJson(a.args, 4000)} className="mt-2" />
          </details>
        ) : (
          <CodeBlock value={compactJson(a.args, 4000)} />
        ))}
        {!compact && (
          <p className="text-2xs text-ink-faint">
            Angefragt von <span className="type-data">{a.requested_by}</span> über {a.channel}
            {pending && <> – läuft ab {formatRelative(a.expires_at)}</>}
            {a.decided_by && <> – entschieden von <span className="type-data">{a.decided_by}</span></>}
          </p>
        )}
        {!pending && a.result && Object.keys(a.result).length > 0 && !compact && (
          <details className="text-xs text-ink-dim">
            <summary className="cursor-default select-none hover:text-ink">Ergebnis</summary>
            <CodeBlock value={compactJson(a.result, 4000)} className="mt-2" />
          </details>
        )}
        {pending && (
          <div className="flex flex-wrap items-center gap-2 pt-1">
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Notiz (optional)"
              aria-label="Notiz zur Entscheidung"
              className="field h-8.5 max-w-xs flex-1 py-1"
            />
            <Button variant="ghost" busy={busy === "reject"} disabled={busy !== null} onClick={() => void decide(false)} icon={<X className="size-4" />}>
              Ablehnen
            </Button>
            <Button variant="primary" busy={busy === "approve"} disabled={busy !== null} onClick={() => void decide(true)} icon={<Check className="size-4" />}>
              Freigeben
            </Button>
          </div>
        )}
      </div>
    </article>
  );
}
