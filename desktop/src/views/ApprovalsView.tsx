import { ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { ApprovalCard } from "../components/ApprovalCard";
import { ApprovalModePanel } from "../components/ApprovalModePanel";
import { EmptyState, ErrorState, Skeleton, Tabs } from "../components/ui";
import { useQuery } from "../hooks/useQuery";
import { api, useApp } from "../store/app";

export function ApprovalsView() {
  const [tab, setTab] = useState<"pending" | "history">("pending");
  const pending = useApp((s) => s.pendingApprovals);
  const refresh = useApp((s) => s.refreshApprovals);
  const all = useQuery(() => api.approvals("all"), "approvals-all", { topics: ["approval"], enabled: tab === "history" });
  const history = (all.data ?? []).filter((a) => a.status !== "pending");

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-4 border-b border-seam px-8 py-2.5">
        <Tabs
          value={tab}
          onChange={setTab}
          items={[
            { id: "pending", label: "Offen", count: pending.length },
            { id: "history", label: "Verlauf" },
          ]}
        />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-[56rem] flex-col gap-3 px-8 py-6">
          {tab === "pending" && <ApprovalModePanel />}
          {tab === "pending" &&
            (pending.length === 0 ? (
              <EmptyState icon={<ShieldCheck className="size-6" />} title="Nichts wartet auf deine Freigabe.">
                Aktionen, die laut Freigabe-Modus deine Zustimmung brauchen, erscheinen hier, bevor J.A.R.V.I.S. sie ausführt.
              </EmptyState>
            ) : (
              pending.map((a) => <ApprovalCard key={a.id} approval={a} />)
            ))}
          {tab === "history" && (
            <>
              {all.loading && <Skeleton rows={6} />}
              {all.error && <ErrorState message={all.error} onRetry={all.refetch} />}
              {all.data && history.length === 0 && <EmptyState title="Noch keine entschiedenen Freigaben." />}
              {history.map((a) => (
                <ApprovalCard key={a.id} approval={a} />
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
