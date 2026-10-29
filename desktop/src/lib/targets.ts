// Where a notification leads: the approval, the coding job or the task it is about – never just the list.

export interface Target {
  view: "approvals" | "agents" | "tasks" | "whatsapp" | "social" | "uptik";
  job?: string;
  task?: string;
}

const id = (v: unknown): string | undefined => (typeof v === "string" && v ? v : undefined);

/** `event` and `data` of a notification (bus event `notification` or a row of /api/notifications). */
export function notificationTarget(event: string, data: Record<string, unknown> | null | undefined): Target | null {
  const d = data ?? {};
  if (event === "approval.requested" || id(d.approval_id)) return { view: "approvals" };
  if (event.startsWith("social.")) return { view: "social" }; // new video, failed production
  if (event.startsWith("uptik.")) return { view: "uptik" }; // published, failed, account stopped
  const job = id(d.job_id);
  if (job) return { view: "agents", job }; // a coding job has its own monitor, preferred over its task
  const task = id(d.task_id);
  if (task) return { view: "tasks", task };
  if (event.startsWith("whatsapp.")) return { view: "whatsapp" }; // messages, reminders, failed sends
  if (event.startsWith("coding.")) return { view: "agents" };
  if (event.startsWith("task.")) return { view: "tasks" };
  return null;
}
