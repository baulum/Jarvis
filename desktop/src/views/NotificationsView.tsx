import { Bell, CheckCheck } from "lucide-react";
import { useState } from "react";
import { Button, Dot, EmptyState, ErrorState, Skeleton, Tabs } from "../components/ui";
import { useAction, useQuery } from "../hooks/useQuery";
import { formatRelative } from "../lib/format";
import { notificationTarget } from "../lib/targets";
import { api, useApp } from "../store/app";

const LEVEL_TONE = { info: "info", warning: "warn", error: "bad", critical: "bad" } as const;

export function NotificationsView() {
  const [tab, setTab] = useState<"unread" | "all">("unread");
  const q = useQuery(() => api.notifications(tab === "unread"), `notifications-${tab}`, { topics: ["notification"] });
  const refreshUnread = useApp((s) => s.refreshUnread);
  const openTarget = useApp((s) => s.openTarget);
  const act = useAction();
  const markAll = () => void act.run("read", () => api.markNotificationsRead()).then(() => { void q.refetch(); void refreshUnread(); });

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-4 border-b border-seam px-8 py-2.5">
        <Tabs value={tab} onChange={setTab} items={[{ id: "unread", label: "Ungelesen" }, { id: "all", label: "Alle" }]} />
        <Button size="sm" variant="ghost" className="ml-auto" icon={<CheckCheck className="size-3.5" />} onClick={markAll}>Alle gelesen</Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-[52rem] flex-col gap-2 px-8 py-6">
          {q.loading && <Skeleton rows={6} />}
          {q.error && <ErrorState message={q.error} onRetry={q.refetch} />}
          {q.data && q.data.length === 0 && <EmptyState icon={<Bell className="size-6" />} title={tab === "unread" ? "Alles gelesen." : "Noch keine Benachrichtigungen."} />}
          {q.data?.map((n) => {
            const target = notificationTarget(n.event, n.data);
            return (
              <article key={n.id} className={`plate flex gap-3 p-3.5 ${n.read ? "opacity-70" : ""}`}>
                <Dot tone={LEVEL_TONE[n.level as keyof typeof LEVEL_TONE] ?? "info"} className="mt-1.5" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-3"><h3 className="text-sm text-ink">{n.title}</h3><span className="ml-auto shrink-0 text-xs text-ink-faint">{formatRelative(n.created_at)}</span></div>
                  {n.body && <p className="mt-1 whitespace-pre-wrap text-sm text-ink-dim">{n.body}</p>}
                  <p className="mt-1.5 flex items-baseline gap-3 text-xs text-ink-faint">
                    <span>{n.event}{n.delivered.length > 0 && ` · zugestellt: ${n.delivered.join(", ")}`}</span>
                    {target && (
                      <button type="button" className="ml-auto text-arc hover:underline" onClick={() => openTarget(target)}>
                        Öffnen
                      </button>
                    )}
                  </p>
                </div>
              </article>
            );
          })}
        </div>
      </div>
    </div>
  );
}
