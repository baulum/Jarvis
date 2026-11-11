// Einstellungen → System-Prompt: the editable part of the system prompt (persona, tone, style, personal instructions)
// of the active personality. The fixed core instructions (approvals, untrusted content, secrets, tool rules, the rule
// to finish tasks) and the loaded behaviour rules are shown read-only – they cannot be changed here.
// Order of the final prompt: Kernanweisungen → this text → Verhaltensregeln (global, then project) → context knowledge.
import { RotateCcw } from "lucide-react";
import { useState } from "react";
import { Button, Section, Skeleton, Tag } from "./ui";
import { useAction, useQuery } from "../hooks/useQuery";
import { formatDateTime } from "../lib/format";
import { api } from "../store/app";

export function SystemPromptCard() {
  const q = useQuery(() => api.systemPrompt(), "system-prompt", { topics: ["system"] });
  const act = useAction();
  // local edits per persona; without one the saved text is shown (a switch of persona or a reset brings a new text)
  const [edit, setEdit] = useState<{ persona: string; text: string } | null>(null);
  const [showCore, setShowCore] = useState(false);
  const view = q.data;
  const text = view ? (edit && edit.persona === view.persona ? edit.text : view.text) : "";
  const setText = (t: string) => view && setEdit({ persona: view.persona, text: t });

  const dirty = !!view && text !== view.text;
  const tooLong = !!view && text.length > view.max_chars;

  const save = async () => {
    const r = await act.run("save", () => api.setSystemPrompt(text), "System-Prompt gespeichert – gilt ab der nächsten Antwort");
    if (r) {
      setEdit(null);
      q.setData(() => r);
    }
  };
  const reset = async () => {
    const r = await act.run("reset", () => api.resetSystemPrompt(), "Standard wiederhergestellt");
    if (r) {
      setEdit(null);
      q.setData(() => r);
    }
  };

  return (
    <Section
      title="System-Prompt"
      meta={view ? `${view.persona_label} · ${view.customized ? "angepasst" : "Standard"}` : undefined}
    >
      {q.loading && <div className="p-4"><Skeleton rows={4} /></div>}
      {q.error && !view && <p role="alert" className="p-4 text-sm text-alarm">{q.error}</p>}
      {view && (
        <div className="flex flex-col gap-3 p-4">
          <p className="text-xs text-ink-dim">
            Persönlichkeit, Ton, Humor und eigene Anweisungen. Wirkt bei neuen Antworten ohne Neustart. Die Kernanweisungen
            (Freigaben, fremde Inhalte, Zugangsdaten, Werkzeugnutzung, Aufgaben vollständig erledigen) stehen darüber und
            bleiben immer gültig.
          </p>
          <textarea
            aria-label="System-Prompt"
            className="field selectable min-h-72 font-mono text-xs"
            value={text}
            spellCheck={false}
            onChange={(e) => setText(e.target.value)}
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" size="sm" disabled={!dirty || tooLong || text.trim().length === 0} busy={act.busy === "save"} onClick={() => void save()}>
              Speichern
            </Button>
            <Button size="sm" variant="ghost" disabled={!view.customized && !dirty} busy={act.busy === "reset"} icon={<RotateCcw className="size-3.5" />} onClick={() => void reset()}>
              Standard wiederherstellen
            </Button>
            <span className={`ml-auto text-xs ${tooLong ? "text-alarm" : "text-ink-faint"}`}>
              {text.length} / {view.max_chars}
            </span>
            {view.updated_at && <span className="text-xs text-ink-faint">Zuletzt {formatDateTime(view.updated_at)}</span>}
          </div>
          {view.rules_warnings.length > 0 && (
            <p role="status" className="text-xs text-ember">{view.rules_warnings.join(" · ")}</p>
          )}
          <button type="button" className="self-start text-xs text-ink-dim hover:text-arc" aria-expanded={showCore} onClick={() => setShowCore((v) => !v)}>
            {showCore ? "Feste Anteile ausblenden" : "Feste Anteile anzeigen (nicht änderbar)"}
          </button>
          {showCore && (
            <div className="flex flex-col gap-2">
              <div><Tag tone="warn">Kernanweisungen – fest im Code</Tag></div>
              <pre aria-label="Kernanweisungen" className="selectable max-h-72 overflow-y-auto whitespace-pre-wrap rounded border border-seam p-3 text-xs text-ink-dim">{view.core}</pre>
              <div><Tag tone="info">Geladene Verhaltensregeln</Tag></div>
              <pre aria-label="Geladene Verhaltensregeln" className="selectable max-h-48 overflow-y-auto whitespace-pre-wrap rounded border border-seam p-3 text-xs text-ink-dim">{view.rules || "Keine aktiven globalen Regeln."}</pre>
            </div>
          )}
        </div>
      )}
    </Section>
  );
}
