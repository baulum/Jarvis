/** Which model choice is active, from `/api/agent/model` – the conversation override wins over the default. */
export function currentChoice(st: { default: string; auto_downgrade: boolean; session_override: string | null } | undefined): string {
  if (!st) return "auto";
  return st.session_override ?? (st.default !== "sonnet" ? st.default : st.auto_downgrade ? "auto" : "sonnet");
}

export function resetLabel(s: number | null): string {
  if (s === null) return "";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `Reset in ${h} h ${m} min` : `Reset in ${m} min`;
}
