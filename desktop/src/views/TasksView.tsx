import { Ban, ListChecks, Plus, RotateCcw, Send, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Button, CodeBlock, EmptyState, ErrorState, KeyValue, Skeleton, StatusText } from "../components/ui";
import { useFocusedSelection } from "../hooks/useFocusedSelection";
import { useAction, useQuery } from "../hooks/useQuery";
import { compactJson, formatClock, formatDateTime, formatRelative, statusLabel, statusTone } from "../lib/format";
import { applyTaskEvent } from "../lib/reducers";
import type { JsonSchema, Playbook, Task } from "../lib/types";
import { api, useApp } from "../store/app";

const ACTIVE = new Set(["queued", "running", "waiting_approval", "waiting_input", "waiting_external"]);

export function TasksView() {
  const tasks = useQuery(() => api.tasks(), "tasks", { topics: ["task"] });
  const playbooks = useQuery(() => api.playbooks(), "playbooks");
  const [selected, setSelected] = useFocusedSelection("task");
  const [filter, setFilter] = useState<"active" | "all">("all");

  const list = useMemo(() => {
    const rows = tasks.data ?? [];
    return filter === "active" ? rows.filter((t) => ACTIVE.has(t.status)) : rows;
  }, [tasks.data, filter]);

  const current = list.find((t) => t.id === selected) ?? (tasks.data ?? []).find((t) => t.id === selected);

  return (
    <div className="flex h-full">
      <aside className="flex w-[340px] shrink-0 flex-col border-r border-seam">
        <div className="flex items-center gap-1 px-4 py-3">
          {(["all", "active"] as const).map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setFilter(f)}
              className={`h-7 rounded-md px-2.5 text-xs ${filter === f ? "bg-plate-hi text-ink" : "text-ink-dim hover:text-ink"}`}
            >
              {f === "all" ? "Alle" : "Aktiv"}
            </button>
          ))}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
          {tasks.loading && <div className="px-2"><Skeleton rows={6} /></div>}
          {tasks.error && <div className="px-2"><ErrorState message={tasks.error} onRetry={tasks.refetch} /></div>}
          {tasks.data && list.length === 0 && (
            <EmptyState icon={<ListChecks className="size-6" />} title={filter === "active" ? "Keine laufenden Aufgaben." : "Noch keine Aufgaben."}>
              Aufgaben entstehen, wenn J.A.R.V.I.S. ein Playbook startet – im Chat, per Zeitplan oder über Telegram.
            </EmptyState>
          )}
          <ul className="flex flex-col gap-px">
            {list.map((t) => (
              <li key={t.id}>
                <button
                  type="button"
                  onClick={() => setSelected(t.id)}
                  className={`w-full rounded-md px-2.5 py-2.5 text-left ${t.id === selected ? "bg-plate-hi" : "hover:bg-plate"}`}
                >
                  <div className="flex items-center gap-2">
                    <span className="flex-1 truncate text-[0.8125rem] text-ink">{t.title || t.kind}</span>
                    <StatusText tone={statusTone(t.status)} pulse={t.status === "running"}>
                      {statusLabel(t.status)}
                    </StatusText>
                  </div>
                  <div className="mt-1 flex items-center gap-3 text-2xs text-ink-faint">
                    <span className="type-data">{t.kind}</span>
                    <span>
                      {t.steps.filter((s) => s.status === "completed" || s.status === "skipped").length}/{t.steps.length} Schritte
                    </span>
                    <span>{formatRelative(t.updated_at)}</span>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </aside>
      <div className="min-w-0 flex-1 overflow-y-auto">
        {current ? (
          <TaskDetail key={current.id} task={current} playbooks={playbooks.data ?? []} onChanged={tasks.refetch} />
        ) : (
          <div className="px-8 py-8">
            <EmptyState title="Wähle links eine Aufgabe.">Details, Schrittverlauf und Eingaben erscheinen hier.</EmptyState>
          </div>
        )}
      </div>
    </div>
  );
}

function TaskDetail({ task: initial, playbooks, onChanged }: { task: Task; playbooks: Playbook[]; onChanged: () => Promise<void> }) {
  const detail = useQuery(() => api.task(initial.id), `task-${initial.id}`, { topics: ["task"] });
  const task = detail.data ?? initial;
  const { busy, run } = useAction();
  const canCancel = ACTIVE.has(task.status);
  const canRetry = task.status === "failed" || task.status === "cancelled";

  // fold live step events into the detail while the refetch is in flight
  const versions = useApp((s) => s.versions.task);
  useEffect(() => {
    detail.setData((prev) => (prev ? applyTaskEvent([prev], { id: "", type: "noop", ts: "", data: {} }).tasks[0] : prev));
  }, [versions, detail]);

  return (
    <div className="mx-auto flex max-w-[56rem] flex-col gap-6 px-8 py-7">
      <header className="flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <h2 className="type-display text-[1.5rem] text-ink">{task.title || task.kind}</h2>
          <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-ink-dim">
            <StatusText tone={statusTone(task.status)} pulse={task.status === "running"}>
              {statusLabel(task.status)}
            </StatusText>
            <span className="type-data">{task.kind}</span>
            <span>Versuch {task.attempts}</span>
            <span>erstellt {formatDateTime(task.created_at)}</span>
          </div>
        </div>
        <div className="flex gap-2">
          {canRetry && (
            <Button
              busy={busy === "retry"}
              icon={<RotateCcw className="size-4" />}
              onClick={() => void run("retry", () => api.taskRetry(task.id), "Aufgabe wird erneut versucht").then(onChanged)}
            >
              Erneut versuchen
            </Button>
          )}
          {canCancel && (
            <Button
              variant="danger"
              busy={busy === "cancel"}
              icon={<Ban className="size-4" />}
              onClick={() => void run("cancel", () => api.taskCancel(task.id), "Aufgabe abgebrochen").then(onChanged)}
            >
              Abbrechen
            </Button>
          )}
        </div>
      </header>

      {detail.error && <ErrorState message={detail.error} onRetry={detail.refetch} />}

      {task.status === "waiting_input" && (
        <InputForm task={task} playbook={playbooks.find((p) => p.name === task.kind)} onDone={() => void detail.refetch().then(onChanged)} />
      )}

      {task.error && (
        <div className="rounded-md border border-alarm/30 bg-alarm-deep/40 px-4 py-3 text-sm text-ink">
          <span className="text-alarm">Fehler: </span>
          {task.error}
        </div>
      )}

      <section>
        <h3 className="type-title mb-4 text-sm text-ink-dim">Schritte</h3>
        {task.steps.length === 0 ? (
          <p className="text-xs text-ink-faint">Diese Aufgabe hat keine Schritte.</p>
        ) : (
          <ol className="relative flex flex-col">
            {task.steps.map((s, i) => {
              const tone = statusTone(s.status);
              const last = i === task.steps.length - 1;
              return (
                <li key={s.idx} className="relative flex gap-4 pb-5">
                  {!last && <span className="absolute top-6 bottom-0 left-[11px] w-px bg-seam-hi" aria-hidden />}
                  <span
                    className={`type-data relative z-10 flex size-6 shrink-0 items-center justify-center rounded-full border text-2xs ${
                      tone === "ok"
                        ? "border-arc-dim bg-arc-deep text-arc"
                        : tone === "bad"
                          ? "border-alarm/60 bg-alarm-deep text-alarm"
                          : tone === "warn"
                            ? "border-ember/60 bg-ember-deep text-ember"
                            : tone === "info"
                              ? "border-arc text-arc"
                              : "border-seam-hi text-ink-faint"
                    }`}
                  >
                    {s.idx + 1}
                  </span>
                  <div className="min-w-0 flex-1 pt-0.5">
                    <div className="flex flex-wrap items-baseline gap-x-3">
                      <span className={`text-sm ${s.status === "pending" ? "text-ink-dim" : "text-ink"}`}>{s.name}</span>
                      <span className="text-xs text-ink-faint">{statusLabel(s.status)}</span>
                      {s.started_at && <span className="type-data text-ink-faint">{formatClock(s.started_at)}</span>}
                    </div>
                    {s.error && <p className="mt-1 text-xs text-ember">{s.error}</p>}
                    {s.output !== null && s.output !== undefined && s.status === "completed" && (
                      <details className="mt-1.5 text-xs text-ink-dim">
                        <summary className="cursor-default select-none hover:text-ink">Ausgabe</summary>
                        <CodeBlock value={compactJson(s.output, 3000)} className="mt-2" />
                      </details>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </section>

      <section className="grid gap-6 md:grid-cols-2">
        <div>
          <h3 className="type-title mb-3 text-sm text-ink-dim">Parameter</h3>
          {Object.keys(task.params ?? {}).length ? <CodeBlock value={compactJson(task.params, 4000)} /> : <p className="text-xs text-ink-faint">Keine.</p>}
        </div>
        <div>
          <h3 className="type-title mb-3 text-sm text-ink-dim">Ergebnis</h3>
          {task.result && Object.keys(task.result).length ? (
            <CodeBlock value={compactJson(task.result, 4000)} />
          ) : (
            <p className="text-xs text-ink-faint">{ACTIVE.has(task.status) ? "Noch keins – die Aufgabe läuft." : "Keins."}</p>
          )}
        </div>
      </section>
      <KeyValue rows={[["Zuletzt aktualisiert", formatDateTime(task.updated_at)], ["ID", <span key="id" className="type-data">{task.id}</span>]]} />
    </div>
  );
}

/** Fields the task asks for: from the live event if seen, else the playbook schema's missing required keys. */
function inputFieldsFor(task: Task, live: string[] | undefined, playbook: Playbook | undefined): string[] {
  if (live && live.length) return live;
  const schema: JsonSchema | undefined = playbook?.params_schema;
  const req = schema?.required ?? [];
  return req.filter((k) => task.params?.[k] === undefined || task.params?.[k] === "");
}

function InputForm({ task, playbook, onDone }: { task: Task; playbook: Playbook | undefined; onDone: () => void }) {
  const live = useApp((s) => s.taskInputFields[task.id]);
  const suggested = inputFieldsFor(task, live, playbook);
  const [rows, setRows] = useState<{ key: string; value: string }[]>(() =>
    suggested.length ? suggested.map((k) => ({ key: k, value: "" })) : [{ key: "", value: "" }],
  );
  const { busy, run } = useAction();
  const props = playbook?.params_schema?.properties ?? {};

  function coerce(key: string, value: string): unknown {
    const t = props[key]?.type;
    if (t === "number" || t === "integer") {
      const n = Number(value.replace(",", "."));
      return Number.isFinite(n) ? n : value;
    }
    if (t === "boolean") return value === "true" || value === "ja";
    return value;
  }

  async function submit() {
    const values: Record<string, unknown> = {};
    for (const r of rows) if (r.key.trim()) values[r.key.trim()] = coerce(r.key.trim(), r.value);
    const res = await run("input", () => api.taskInput(task.id, values), "Eingabe übermittelt – die Aufgabe läuft weiter");
    if (res) onDone();
  }

  return (
    <form
      className="flex flex-col gap-4 rounded-lg border border-ember/40 bg-ember-deep/30 px-5 py-4"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <div>
        <p className="text-sm text-ink">{task.question || "Die Aufgabe braucht weitere Angaben."}</p>
        {!live && suggested.length === 0 && (
          <p className="mt-1 text-xs text-ink-dim">Welche Felder fehlen, ist nicht bekannt – trage Schlüssel und Wert selbst ein.</p>
        )}
      </div>
      <div className="flex flex-col gap-2">
        {rows.map((r, i) => (
          <div key={i} className="flex items-center gap-2">
            <input
              value={r.key}
              onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)))}
              placeholder="Feld"
              aria-label="Feldname"
              className="field type-data w-48"
            />
            <input
              value={r.value}
              onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))}
              placeholder={props[r.key]?.description ?? (typeof props[r.key]?.type === "string" ? String(props[r.key]?.type) : "Wert")}
              aria-label={`Wert für ${r.key || "Feld"}`}
              className="field flex-1"
            />
            <button
              type="button"
              aria-label="Zeile entfernen"
              className="p-1.5 text-ink-faint hover:text-alarm"
              onClick={() => setRows(rows.filter((_, j) => j !== i))}
            >
              <Trash2 className="size-3.5" />
            </button>
          </div>
        ))}
      </div>
      <div className="flex gap-2">
        <Button size="sm" variant="ghost" icon={<Plus className="size-3.5" />} onClick={() => setRows([...rows, { key: "", value: "" }])}>
          Feld hinzufügen
        </Button>
        <span className="flex-1" />
        <Button type="submit" variant="primary" busy={busy === "input"} icon={<Send className="size-3.5" />}>
          Eingabe senden
        </Button>
      </div>
    </form>
  );
}
