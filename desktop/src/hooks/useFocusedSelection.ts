import { useState } from "react";
import { useApp } from "../store/app";

/**
 * Selection in a list view that follows notification targets: a newer `focus` (opened from a notification)
 * wins over the last click, a click after that wins again. Derived from the store, so no effect is needed.
 */
export function useFocusedSelection(kind: "job" | "task"): [string | null, (id: string) => void] {
  const focus = useApp((s) => s.focus);
  const [pick, setPick] = useState<{ id: string | null; seq: number }>({ id: null, seq: -1 });
  const focused = focus[kind];
  const selected = pick.seq >= focus.seq || !focused ? pick.id : focused;
  return [selected, (id) => setPick({ id, seq: focus.seq })];
}
