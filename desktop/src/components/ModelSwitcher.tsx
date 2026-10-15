// Model selection for the Claude agent: automatic (Sonnet, simple requests → Haiku) or a fixed model.
import { Cpu } from "lucide-react";
import { useAction, useQuery } from "../hooks/useQuery";
import { api, useApp } from "../store/app";

const CHOICES = [
  { id: "auto", label: "Auto" },
  { id: "haiku", label: "Haiku" },
  { id: "sonnet", label: "Sonnet" },
  { id: "opus", label: "Opus" },
] as const;

export function ModelSwitcher({ sessionId }: { sessionId: string | null }) {
  const status = useQuery(() => api.agentModel(sessionId), `agent-model-${sessionId ?? "none"}`, { topics: ["system"] });
  const last = useApp((s) => (sessionId ? s.turnModels[sessionId] : undefined));
  const act = useAction();
  const st = status.data;
  const current = st?.session_override ?? (st && st.default !== "sonnet" ? st.default : st?.auto_downgrade ? "auto" : "sonnet");

  const choose = (id: string) => {
    const model = id === "auto" ? null : id;
    const scope = sessionId ? "session" : "default";
    void act.run("model", () => api.setAgentModel({ model, scope, session_id: sessionId }), undefined).then(() => status.refetch());
  };

  return (
    <div className="flex items-center gap-3 text-xs text-ink-faint">
      <Cpu className="size-3.5" aria-hidden />
      <div role="radiogroup" aria-label="Sprachmodell" className="flex overflow-hidden rounded-md border border-seam">
        {CHOICES.map((c) => (
          <button
            key={c.id}
            role="radio"
            aria-checked={current === c.id}
            disabled={act.busy !== null || !st}
            onClick={() => choose(c.id)}
            className={`px-2.5 py-1 transition-colors ${current === c.id ? "bg-arc-deep text-arc" : "text-ink-dim hover:bg-plate-hi hover:text-ink"}`}
          >
            {c.label}
          </button>
        ))}
      </div>
      <span>
        {current === "auto" ? "Sonnet 5.5, einfache Anfragen Haiku 5.5" : `${st?.models.find((m) => m.alias === current)?.label ?? current} fest`}
        {sessionId ? " · dieses Gespräch" : " · Standard"}
      </span>
      {last && <span className="ml-auto type-data text-ink-dim" title={last.reason}>zuletzt: {last.model}</span>}
    </div>
  );
}
