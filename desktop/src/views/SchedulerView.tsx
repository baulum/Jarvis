import { CalendarClock, Pencil, Play, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Button, Dialog, EmptyState, ErrorState, Field, Skeleton, Tag, Toggle } from "../components/ui";
import { useAction, useQuery } from "../hooks/useQuery";
import { CRON_EXAMPLES, describeCron, timeOf, validateCron, withTime } from "../lib/cron";
import { formatDateTime, formatRelative } from "../lib/format";
import type { Schedule, ScheduleUpdate } from "../lib/types";
import { api } from "../store/app";

function CreateDialog({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [cron, setCron] = useState("0 8 * * 1-5");
  const [prompt, setPrompt] = useState("");
  const act = useAction();
  const cronError = validateCron(cron);
  const valid = name.trim() && prompt.trim() && !cronError;
  return (
    <Dialog open onClose={onClose} title="Neuer Zeitplan"
            footer={<><Button variant="ghost" onClick={onClose}>Abbrechen</Button>
              <Button variant="primary" disabled={!valid} busy={act.busy === "create"}
                      onClick={() => void act.run("create", () => api.createSchedule({ name, cron, action: { type: "prompt", prompt } }), "Zeitplan angelegt").then((r) => { if (r) { onCreated(); onClose(); } })}>Anlegen</Button></>}>
      <div className="flex flex-col gap-4 text-sm">
        <Field label="Name">{(id) => <input id={id} className="field" value={name} onChange={(e) => setName(e.target.value)} placeholder="Morgen-Briefing" />}</Field>
        <Field label="Cron (Minute Stunde Tag Monat Wochentag, Europe/Berlin)" hint={cronError ?? describeCron(cron)}>
          {(id) => <input id={id} className="field type-data" value={cron} onChange={(e) => setCron(e.target.value)} />}
        </Field>
        <div className="flex flex-wrap gap-1.5">
          {CRON_EXAMPLES.map((ex) => <button key={ex.expr} className="rounded border border-seam px-2 py-1 text-xs text-ink-dim hover:border-arc-dim hover:text-arc" onClick={() => setCron(ex.expr)}>{ex.label}</button>)}
        </div>
        <Field label="Anweisung an J.A.R.V.I.S.">{(id) => <textarea id={id} className="field min-h-24" value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Fasse meine Termine für heute zusammen und schick sie mir per Telegram." />}</Field>
      </div>
    </Dialog>
  );
}

function EditDialog({ schedule, onClose, onSaved }: { schedule: Schedule; onClose: () => void; onSaved: () => void }) {
  const isPrompt = schedule.action.type === "prompt";
  const [name, setName] = useState(schedule.name);
  const [cron, setCron] = useState(schedule.cron);
  const [prompt, setPrompt] = useState(String(schedule.action.prompt ?? ""));
  const [enabled, setEnabled] = useState(schedule.enabled);
  const act = useAction();
  const cronError = validateCron(cron);
  const time = timeOf(cron);
  const valid = Boolean(name.trim()) && !cronError && (!isPrompt || Boolean(prompt.trim()));
  const save = () => {
    const body: ScheduleUpdate = { name: name.trim(), cron: cron.trim(), enabled };
    if (isPrompt) body.prompt = prompt.trim();
    return act.run("save", () => api.updateSchedule(schedule.id, body), "Zeitplan gespeichert").then((r) => {
      if (r) {
        onSaved();
        onClose();
      }
    });
  };
  return (
    <Dialog open onClose={onClose} title="Zeitplan bearbeiten"
            footer={<><Button variant="ghost" onClick={onClose}>Abbrechen</Button>
              <Button variant="primary" disabled={!valid} busy={act.busy === "save"} onClick={() => void save()}>Speichern</Button></>}>
      <div className="flex flex-col gap-4 text-sm">
        <Field label="Name">{(id) => <input id={id} className="field" value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        <div className="grid gap-4 sm:grid-cols-[10rem_1fr]">
          <Field label="Uhrzeit (Berlin)" hint={time === null ? "Nur bei fester Uhrzeit – sonst den Cron-Ausdruck bearbeiten." : "Ändert Minute und Stunde im Cron-Ausdruck."}>
            {(id) => <input id={id} type="time" className="field type-data" value={time ?? ""} disabled={time === null}
                            onChange={(e) => { if (e.target.value) setCron(withTime(cron, e.target.value)); }} />}
          </Field>
          <Field label="Cron (Minute Stunde Tag Monat Wochentag)" hint={cronError ?? describeCron(cron)}>
            {(id) => <input id={id} className="field type-data" value={cron} onChange={(e) => setCron(e.target.value)} />}
          </Field>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {CRON_EXAMPLES.map((ex) => <button key={ex.expr} className="rounded border border-seam px-2 py-1 text-xs text-ink-dim hover:border-arc-dim hover:text-arc" onClick={() => setCron(ex.expr)}>{ex.label}</button>)}
        </div>
        {isPrompt ? (
          <Field label="Anweisung an J.A.R.V.I.S.">{(id) => <textarea id={id} className="field min-h-24" value={prompt} onChange={(e) => setPrompt(e.target.value)} />}</Field>
        ) : (
          <p className="text-xs text-ink-dim">
            Dieser Zeitplan führt „{String(schedule.action.type ?? "unbekannt")}“ aus. Die Anweisung lässt sich nur bei Prompt-Zeitplänen ändern.
          </p>
        )}
        <Toggle label="Aktiv" checked={enabled} onChange={setEnabled} />
      </div>
    </Dialog>
  );
}

export function SchedulerView() {
  const q = useQuery(() => api.schedules(), "schedules", { topics: ["schedule"] });
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Schedule | null>(null);
  const act = useAction();
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-[60rem] flex-col gap-3 px-8 py-6">
        <div className="flex justify-end"><Button variant="primary" icon={<Plus className="size-3.5" />} onClick={() => setCreating(true)}>Zeitplan</Button></div>
        {q.loading && <Skeleton rows={5} />}
        {q.error && <ErrorState message={q.error} onRetry={q.refetch} />}
        {q.data && q.data.length === 0 && (
          <EmptyState icon={<CalendarClock className="size-6" />} title="Keine geplanten Aufträge.">
            Wiederkehrende Aufträge wie ein Morgen-Briefing laufen hier nach Cron-Zeitplan. Du kannst sie auch im Chat anlegen lassen.
          </EmptyState>
        )}
        {q.data?.map((s) => (
          <article key={s.id} className={`plate flex items-start gap-4 p-4 ${s.enabled ? "" : "opacity-60"}`}>
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline gap-3"><h3 className="type-title text-ink">{s.name}</h3><span className="type-data text-xs text-arc-dim">{s.cron}</span></div>
              <p className="mt-0.5 text-xs text-ink-dim">{describeCron(s.cron)}</p>
              <p className="mt-2 text-sm text-ink">{String((s.action as { prompt?: string }).prompt ?? JSON.stringify(s.action))}</p>
              <p className="mt-2 text-xs text-ink-faint">
                Nächster Lauf {s.enabled ? formatDateTime(s.next_run_at) : "–"} · zuletzt {s.last_run_at ? formatRelative(s.last_run_at) : "nie"}
                {s.last_status && <> · <Tag tone={s.last_status === "ok" ? "ok" : "bad"}>{s.last_status}</Tag></>}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              <Toggle label="Aktiv" checked={s.enabled} onChange={(v) => void act.run("toggle", () => api.setScheduleEnabled(s.id, v)).then(q.refetch)} />
              <Button size="sm" variant="ghost" icon={<Pencil className="size-3.5" />} onClick={() => setEditing(s)}>Bearbeiten</Button>
              <Button size="sm" variant="ghost" icon={<Play className="size-3.5" />} onClick={() => void act.run("run", () => api.runSchedule(s.id), "Gestartet")}>Jetzt</Button>
              <Button size="sm" variant="ghost" icon={<Trash2 className="size-3.5" />} onClick={() => void act.run("del", () => api.deleteSchedule(s.id), "Gelöscht").then(q.refetch)} aria-label="Löschen" />
            </div>
          </article>
        ))}
        {creating && <CreateDialog onClose={() => setCreating(false)} onCreated={() => void q.refetch()} />}
        {editing && <EditDialog key={editing.id} schedule={editing} onClose={() => setEditing(null)} onSaved={() => void q.refetch()} />}
      </div>
    </div>
  );
}
