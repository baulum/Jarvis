// State of the holographic interface. The 3D layer reads it imperatively (getState in useFrame);
// React components subscribe to the few slices they display.
import { create } from "zustand";
import type { BusEvent } from "../lib/types";
import { classOf, type NodeClass, type RegionGroup } from "./graph/anatomy";
import { pushEffects } from "./graph/fx";
import { applySnapshot, applyStep, emptyLive, expireLive, setSync, stepFromEvent, type ActivitySnapshot, type LiveState } from "./graph/live";
import { buildModel, type GNode, type GraphModel } from "./graph/model";
import type { Category, GraphDTO } from "./graph/types";

export type FocusRequest =
  | { kind: "node"; id: string; seq: number }
  | { kind: "nodes"; ids: string[]; seq: number }
  | { kind: "overview"; seq: number };

export type Overlay = null | "search" | "system";

/** The four ways to look at Jarvis; switching animates every node to its place in the new view. */
export type ViewMode = "brain" | "network" | "activity" | "explorer";

export const VIEW_LABEL: Record<ViewMode, string> = {
  brain: "Gehirn",
  network: "Netzwerk",
  activity: "Live-Aktivität",
  explorer: "Erinnerungen",
};

interface BrainState {
  dto: GraphDTO | null;
  model: GraphModel | null;
  loadError: string | null;
  loading: boolean;
  selected: string | null;
  hovered: string | null;
  hidden: Set<Category>;
  hiddenClasses: Set<NodeClass>;
  /** Memory explorer: ids matching search/filters (null = no filter). */
  highlight: Set<string> | null;
  /** Bumped whenever anything that decides node visibility changes (the 3D layer re-uploads sizes). */
  visRev: number;
  view: ViewMode;
  regionHover: RegionGroup | null;
  streamOpen: boolean;
  focus: FocusRequest | null;
  live: LiveState;
  overlay: Overlay;
  systemView: string;
  lastInteraction: number;
  /** Pixels at the bottom of the window covered by the chat dock – the brain is framed in the space above. */
  occludedBottom: number;
  /** Pixels at the left covered by the view's side panel (live view, explorer) – the scene is centred right of it. */
  occludedLeft: number;
  /** Audio levels from the voice daemon (voice.level, ~12 Hz). */
  level: { input: number; output: number; ts: number };
  setGraph: (dto: GraphDTO) => void;
  setLoad: (loading: boolean, error?: string | null) => void;
  select: (id: string | null, focus?: boolean) => void;
  hover: (id: string | null) => void;
  toggleCategory: (c: Category) => void;
  toggleClass: (c: NodeClass) => void;
  showOnly: (c: Category | null) => void;
  setHighlight: (ids: Set<string> | null) => void;
  setView: (v: ViewMode) => void;
  setRegionHover: (g: RegionGroup | null) => void;
  setStreamOpen: (open: boolean) => void;
  focusNodes: (ids: string[]) => void;
  overview: () => void;
  openOverlay: (o: Overlay, systemView?: string) => void;
  touch: () => void;
  setOccludedBottom: (px: number) => void;
  setOccludedLeft: (px: number) => void;
  onEvent: (ev: BusEvent) => void;
  /** Connection state of the event socket: offline pauses the live view, open resynchronises it. */
  setConnection: (state: string) => void;
  /** Fetches what was missed (running work, steps since the last seq) and folds it in. */
  resync: () => Promise<void>;
  tick: () => void;
}

let seq = 0;
const now = () => performance.now();

/** Loader for the activity snapshot – set by the app (keeps this store free of the API module for tests). */
let fetchSnapshot: ((since: number, boot: string | null) => Promise<ActivitySnapshot>) | null = null;
export function setSnapshotLoader(fn: typeof fetchSnapshot): void {
  fetchSnapshot = fn;
}
let resyncing: Promise<void> | null = null;
let resyncAgain = false;
let connected = false;
/** The snapshot endpoint failed while connected (e.g. an older core): live steps are applied as they come. */
let snapshotBroken = false;

/** Whether a node is drawn in the current view with the current filters. */
export function nodeVisible(
  b: Pick<BrainState, "hidden" | "hiddenClasses" | "view" | "highlight" | "selected">,
  n: Pick<GNode, "id" | "kind" | "category">,
): boolean {
  if (n.id === b.selected) return true;
  if (b.hidden.has(n.category)) return false;
  const cls = classOf(n);
  if (b.hiddenClasses.has(cls)) return false;
  if (b.view === "explorer") {
    if (cls !== "longterm") return false;
    if (b.highlight && !b.highlight.has(n.id)) return false;
  }
  return true;
}

export const useBrain = create<BrainState>((set, get) => ({
  dto: null,
  model: null,
  loadError: null,
  loading: false,
  selected: null,
  hovered: null,
  hidden: new Set(),
  hiddenClasses: new Set(),
  highlight: null,
  visRev: 0,
  view: "brain",
  regionHover: null,
  streamOpen: typeof window === "undefined" || window.innerWidth >= 760, // phones: headline only
  focus: null,
  live: emptyLive(),
  overlay: null,
  systemView: "tasks",
  lastInteraction: 0,
  occludedBottom: 0,
  occludedLeft: 0,
  level: { input: 0, output: 0, ts: 0 },

  setGraph: (dto) => {
    const prev = get().model;
    const model = buildModel(dto, prev, now() / 1000);
    const selected = get().selected;
    set({ dto, model, loadError: null, selected: selected && model.byId.has(selected) ? selected : null });
  },
  setLoad: (loading, error = null) => set({ loading, loadError: error }),
  select: (id, focus = true) => {
    set((s) => ({ selected: id, lastInteraction: now(), visRev: s.visRev + 1 }));
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
      return { hidden, visRev: s.visRev + 1 };
    }),
  toggleClass: (c) =>
    set((s) => {
      const hiddenClasses = new Set(s.hiddenClasses);
      if (hiddenClasses.has(c)) hiddenClasses.delete(c);
      else hiddenClasses.add(c);
      return { hiddenClasses, visRev: s.visRev + 1 };
    }),
  showOnly: (c) =>
    set((s) => {
      if (c === null) return { hidden: new Set(), hiddenClasses: new Set(), visRev: s.visRev + 1 };
      const all = new Set(s.model?.nodes.map((n) => n.category) ?? []);
      all.delete(c);
      return { hidden: all, visRev: s.visRev + 1 };
    }),
  setHighlight: (ids) => set((s) => ({ highlight: ids, visRev: s.visRev + 1 })),
  setView: (view) => {
    if (get().view === view) return;
    set((s) => ({ view, visRev: s.visRev + 1, lastInteraction: 0, focus: { kind: "overview", seq: ++seq } }));
  },
  setRegionHover: (regionHover) => set({ regionHover }),
  setStreamOpen: (streamOpen) => set({ streamOpen }),
  focusNodes: (ids) => set({ focus: { kind: "nodes", ids, seq: ++seq }, lastInteraction: now() }),
  overview: () => set((s) => ({ selected: null, focus: { kind: "overview", seq: ++seq }, visRev: s.visRev + 1 })),
  openOverlay: (overlay, systemView) => set((s) => ({ overlay, systemView: systemView ?? s.systemView })),
  touch: () => set({ lastInteraction: now() }),
  setOccludedBottom: (px) => {
    const v = Math.max(0, Math.round(px));
    if (get().occludedBottom !== v) set({ occludedBottom: v });
  },
  setOccludedLeft: (px) => {
    const v = Math.max(0, Math.round(px));
    if (get().occludedLeft !== v) set({ occludedLeft: v });
  },
  onEvent: (ev) => {
    if (ev.type === "voice.level") {
      const d = ev.data as { level?: number; output?: number };
      set({ level: { input: Number(d.level ?? 0), output: Number(d.output ?? 0), ts: now() } });
      return;
    }
    const step = stepFromEvent(ev);
    if (!step) return;
    const live = get().live;
    // a gap in the sequence (dropped frames) or a restarted core: fetch what is missing instead of guessing –
    // the snapshot also contains this step
    if (live.boot && fetchSnapshot && !snapshotBroken && (step.boot !== live.boot || step.seq > live.seq + 1)) {
      void get().resync();
      return;
    }
    const r = applyStep(live, step, now());
    if (r.state !== live) set({ live: r.state });
    if (r.effects.length) pushEffects(r.effects);
  },
  setConnection: (state) => {
    connected = state === "open";
    if (state === "open") {
      void get().resync();
      return;
    }
    if (state === "reconnecting" || state === "closed" || state === "unauthorized") set((s) => ({ live: setSync(s.live, "offline") }));
  },
  resync: async () => {
    if (!fetchSnapshot) return;
    if (resyncing) {
      resyncAgain = true;
      return resyncing;
    }
    const load = fetchSnapshot;
    resyncing = (async () => {
      try {
        do {
          resyncAgain = false;
          const before = get().live;
          if (before.sync === "offline") set({ live: setSync(before, "resyncing") });
          const snap = await load(before.seq, before.boot);
          set({ live: applySnapshot(get().live, snap, now()) });
          snapshotBroken = false;
        } while (resyncAgain);
      } catch {
        snapshotBroken = connected;
        // core not reachable (yet): stays offline/paused; the next (re)connect tries again
        set((s) => ({ live: setSync(s.live, connected ? "live" : "offline") }));
      } finally {
        resyncing = null;
      }
    })();
    return resyncing;
  },
  tick: () => {
    const live = expireLive(get().live, now());
    if (live !== get().live) set({ live });
  },
}));
