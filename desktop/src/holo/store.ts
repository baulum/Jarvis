// State of the holographic interface. The 3D layer reads it imperatively (getState in useFrame);
// React components subscribe to the few slices they display.
import { create } from "zustand";
import type { BusEvent } from "../lib/types";
import { applyLiveEvent, emptyLive, expireLive, type LiveState } from "./graph/live";
import { buildModel, type GraphModel } from "./graph/model";
import type { Category, GraphDTO } from "./graph/types";

export type FocusRequest =
  | { kind: "node"; id: string; seq: number }
  | { kind: "nodes"; ids: string[]; seq: number }
  | { kind: "overview"; seq: number };

export type Overlay = null | "search" | "system";

interface BrainState {
  dto: GraphDTO | null;
  model: GraphModel | null;
  loadError: string | null;
  loading: boolean;
  selected: string | null;
  hovered: string | null;
  hidden: Set<Category>;
  focus: FocusRequest | null;
  live: LiveState;
  overlay: Overlay;
  systemView: string;
  lastInteraction: number;
  /** Pixels at the bottom of the window covered by the chat dock – the brain is framed in the space above. */
  occludedBottom: number;
  /** Audio levels from the voice daemon (voice.level, ~12 Hz). */
  level: { input: number; output: number; ts: number };
  setGraph: (dto: GraphDTO) => void;
  setLoad: (loading: boolean, error?: string | null) => void;
  select: (id: string | null, focus?: boolean) => void;
  hover: (id: string | null) => void;
  toggleCategory: (c: Category) => void;
  showOnly: (c: Category | null) => void;
  focusNodes: (ids: string[]) => void;
  overview: () => void;
  openOverlay: (o: Overlay, systemView?: string) => void;
  touch: () => void;
  setOccludedBottom: (px: number) => void;
  onEvent: (ev: BusEvent) => void;
  tick: () => void;
}

let seq = 0;
const now = () => performance.now();

export const useBrain = create<BrainState>((set, get) => ({
  dto: null,
  model: null,
  loadError: null,
  loading: false,
  selected: null,
  hovered: null,
  hidden: new Set(),
  focus: null,
  live: emptyLive(),
  overlay: null,
  systemView: "tasks",
  lastInteraction: 0,
  occludedBottom: 0,
  level: { input: 0, output: 0, ts: 0 },

  setGraph: (dto) => {
    const prev = get().model;
    const model = buildModel(dto, prev, now() / 1000);
    const selected = get().selected;
    set({ dto, model, loadError: null, selected: selected && model.byId.has(selected) ? selected : null });
  },
  setLoad: (loading, error = null) => set({ loading, loadError: error }),
  select: (id, focus = true) => {
    set({ selected: id, lastInteraction: now() });
    if (id && focus) set({ focus: { kind: "node", id, seq: ++seq } });
  },
  hover: (id) => {
    if (get().hovered !== id) set({ hovered: id });
  },
  toggleCategory: (c) =>
    set((s) => {
      const hidden = new Set(s.hidden);
      if (hidden.has(c)) hidden.delete(c);
      else hidden.add(c);
      return { hidden };
    }),
  showOnly: (c) =>
    set((s) => {
      if (c === null) return { hidden: new Set() };
      const all = new Set(s.model?.nodes.map((n) => n.category) ?? []);
      all.delete(c);
      return { hidden: all };
    }),
  focusNodes: (ids) => set({ focus: { kind: "nodes", ids, seq: ++seq }, lastInteraction: now() }),
  overview: () => set({ selected: null, focus: { kind: "overview", seq: ++seq } }),
  openOverlay: (overlay, systemView) => set((s) => ({ overlay, systemView: systemView ?? s.systemView })),
  touch: () => set({ lastInteraction: now() }),
  setOccludedBottom: (px) => {
    const v = Math.max(0, Math.round(px));
    if (get().occludedBottom !== v) set({ occludedBottom: v });
  },
  onEvent: (ev) => {
    if (ev.type === "voice.level") {
      const d = ev.data as { level?: number; output?: number };
      set({ level: { input: Number(d.level ?? 0), output: Number(d.output ?? 0), ts: now() } });
      return;
    }
    const live = applyLiveEvent(get().live, ev, now());
    if (live !== get().live) set({ live });
  },
  tick: () => {
    const live = expireLive(get().live, now());
    if (live !== get().live) set({ live });
  },
}));
