// The main surface: the brain fills the window; status, conversation, details and controls float above it
// and appear only when they have something to say.
import { AnimatePresence, motion } from "framer-motion";
import { Focus, LayoutGrid, Mic, MicOff, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ApprovalCard } from "../components/ApprovalCard";
import { KillSwitch, KillSwitchBanner } from "../components/KillSwitch";
import { Toasts } from "../components/Shell";
import { api, useApp } from "../store/app";
import { BrainCanvas } from "./graph/BrainCanvas";
import { ICON_OF } from "./graph/icons";
import { mergeDTO } from "./graph/model";
import { CATEGORY_LABEL, type Category } from "./graph/types";
import { ChatDock } from "./ChatDock";
import { CommandBar } from "./CommandBar";
import { LabelLayer } from "./LabelLayer";
import { ModelDock } from "./ModelDock";
import { NodeDetail } from "./NodeDetail";
import { StatusMark } from "./StatusMark";
import { SystemPanel } from "./SystemPanel";
import { useBrain } from "./store";

/** Loads /api/graph and refetches (debounced) when memory, tasks, coding jobs or sessions change. */
function useGraphSync() {
  const v = useApp((s) => s.versions);
  const key = `${v.memory}-${v.task}-${v.coding}-${v.session}-${v.connector}-${v.schedule}`;
  const setGraph = useBrain((s) => s.setGraph);
  const setLoad = useBrain((s) => s.setLoad);
  useEffect(() => {
    let alive = true;
    const t = setTimeout(
      () => {
        setLoad(true);
        api
          .graph(1500)
          .then((dto) => alive && setGraph(dto))
          .catch((e: Error) => alive && setLoad(false, e.message))
          .finally(() => alive && setLoad(false, useBrain.getState().loadError));
      },
      useBrain.getState().dto ? 900 : 0,
    );
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [key, setGraph, setLoad]);
}

function FilterRail() {
  const model = useBrain((s) => s.model);
  const hidden = useBrain((s) => s.hidden);
  const toggle = useBrain((s) => s.toggleCategory);
  const showOnly = useBrain((s) => s.showOnly);
  const focusNodes = useBrain((s) => s.focusNodes);
  const dto = useBrain((s) => s.dto);
  const setGraph = useBrain((s) => s.setGraph);
  const [open, setOpen] = useState(false);
  const counts = useMemo(() => {
    const c = new Map<Category, number>();
    for (const n of model?.nodes ?? []) c.set(n.category, (c.get(n.category) ?? 0) + 1);
    return [...c.entries()].sort((a, b) => b[1] - a[1]);
  }, [model]);
  const truncated = Object.entries(model?.truncated ?? {});

  async function loadMore(kind: string) {
    if (!dto) return;
    const have = dto.nodes.filter((n) => n.kind === kind).length;
    const extra = await api.graphCluster(kind, have);
    setGraph({ ...mergeDTO(dto, extra), truncated: { ...dto.truncated, [kind]: Math.max(0, (dto.truncated[kind] ?? 0) - extra.nodes.length) } });
  }

  return (
    <div className="pointer-events-auto flex flex-col items-start gap-1" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <AnimatePresence>
        {open && (
          <motion.ul initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }} className="holo-panel mb-1 flex flex-col py-1.5" aria-label="Wissensarten">
            {counts.map(([cat, n]) => {
              const Icon = ICON_OF[cat];
              const off = hidden.has(cat);
              return (
                <li key={cat} className="flex items-center">
                  <button type="button" onClick={() => toggle(cat)} aria-pressed={!off} className={`flex flex-1 items-center gap-2.5 px-3 py-1.5 text-left text-xs ${off ? "text-ink-faint" : "text-ink-dim hover:text-ink"}`}>
                    <Icon className="size-3.5" strokeWidth={1.5} />
                    <span className="flex-1">{CATEGORY_LABEL[cat]}</span>
                    <span className="type-data text-ink-faint">{n}</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      showOnly(cat);
                      focusNodes((model?.nodes ?? []).filter((x) => x.category === cat).map((x) => x.id));
                    }}
                    aria-label={`Nur ${CATEGORY_LABEL[cat]} zeigen`}
                    title="Nur diese zeigen und hinfliegen"
                    className="px-2 text-ink-faint hover:text-arc"
                  >
                    <Focus className="size-3" />
                  </button>
                </li>
              );
            })}
            {hidden.size > 0 && (
              <li>
                <button type="button" onClick={() => showOnly(null)} className="w-full px-3 py-1.5 text-left text-xs text-arc">
                  Alle zeigen
                </button>
              </li>
            )}
            {truncated.map(([kind, n]) => (
              <li key={kind}>
                <button type="button" onClick={() => void loadMore(kind)} className="w-full px-3 py-1.5 text-left text-2xs text-ink-faint hover:text-ink">
                  + {n} weitere {kind} laden
                </button>
              </li>
            ))}
          </motion.ul>
        )}
      </AnimatePresence>
      <button type="button" className="type-data rounded px-1.5 py-1 text-[0.62rem] tracking-[0.12em] text-ink-faint uppercase hover:text-ink-dim">
        {model ? `${model.nodes.length} Knoten · ${model.edges.length} Verbindungen` : "Lade Gedächtnis …"}
      </button>
    </div>
  );
}

function ApprovalDock() {
  const pending = useApp((s) => s.pendingApprovals);
  const open = useBrain((s) => s.openOverlay);
  if (!pending.length) return null;
  return (
    <div className="pointer-events-auto flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2" aria-label="Wartende Freigaben">
      {pending.slice(0, 2).map((a) => (
        <div key={a.id} className="holo-panel border-ember/40 p-0.5">
          <ApprovalCard approval={a} compact />
        </div>
      ))}
      {pending.length > 2 && (
        <button type="button" onClick={() => open("system", "approvals")} className="self-end text-xs text-ember hover:underline">
          {pending.length - 2} weitere Freigaben
        </button>
      )}
    </div>
  );
}

function Controls() {
  const open = useBrain((s) => s.openOverlay);
  const overview = useBrain((s) => s.overview);
  const voice = useApp((s) => s.voice);
  const pushToast = useApp((s) => s.pushToast);
  const muted = Boolean(voice.muted);
  const btn = "flex size-8 items-center justify-center rounded-md text-ink-faint transition-colors hover:bg-plate hover:text-ink";
  return (
    <div className="holo-controls pointer-events-auto flex items-center gap-1">
      <button type="button" className={btn} onClick={() => open("search")} aria-label="Suchen (/)" title="Suchen  /  ⌘K">
        <Search className="size-4" strokeWidth={1.6} />
      </button>
      <button type="button" className={btn} onClick={() => overview()} aria-label="Gesamtansicht" title="Gesamtansicht (Doppelklick ins Leere)">
        <Focus className="size-4" strokeWidth={1.6} />
      </button>
      <button
        type="button"
        className={btn}
        onClick={() => api.voiceCommand(muted ? "unmute" : "mute").catch((e: Error) => pushToast({ tone: "bad", title: "Stimme", body: e.message }))}
        aria-label={muted ? "Mikrofon einschalten" : "Mikrofon stummschalten"}
        title={muted ? "Mikrofon ist aus" : "Mikrofon stummschalten"}
      >
        {muted ? <MicOff className="size-4 text-ember" strokeWidth={1.6} /> : <Mic className="size-4" strokeWidth={1.6} />}
      </button>
      <button type="button" className={btn} onClick={() => open("system")} aria-label="Systemansichten" title="Aufgaben, Connectoren, Skills, Logs, Einstellungen …">
        <LayoutGrid className="size-4" strokeWidth={1.6} />
      </button>
    </div>
  );
}

export function HoloApp() {
  useGraphSync();
  const selected = useBrain((s) => s.selected);
  const loadError = useBrain((s) => s.loadError);
  const model = useBrain((s) => s.model);
  // follow the conversation that is happening right now (desktop or voice)
  useEffect(() => {
    return useApp.subscribe((s, prev) => {
      if (s.threads === prev.threads) return;
      for (const [sid, t] of Object.entries(s.threads)) {
        if (t.running && !prev.threads[sid]?.running && s.activeSessionId !== sid) {
          useApp.getState().setActiveSession(sid);
          break;
        }
      }
    });
  }, []);

  return (
    <div className="relative h-full overflow-hidden bg-[#030608]">
      <div className="absolute inset-0">
        <BrainCanvas panelOpen={Boolean(selected)} />
      </div>
      <LabelLayer />
      <div className="drag absolute inset-x-0 top-0 h-10" data-tauri-drag-region />
      <div className="pointer-events-none absolute inset-0 flex flex-col">
        <header className="flex items-start gap-4 px-5 pt-9">
          <StatusMark />
          <div className="ml-auto flex flex-col items-end gap-3">
            <div className="pointer-events-auto flex items-center gap-2">
              <ModelDock />
              <Controls />
              <KillSwitch />
            </div>
            <ApprovalDock />
          </div>
        </header>
        <div className="pointer-events-auto">
          <KillSwitchBanner />
        </div>
        <div className="flex-1" />
        {loadError && !model && (
          <p className="pointer-events-auto mx-auto mb-4 text-xs text-alarm">Gedächtnis nicht erreichbar: {loadError}</p>
        )}
        <footer className="relative flex items-end px-5 pb-5">
          <div className="absolute bottom-6 left-5">
            <FilterRail />
          </div>
          <div className="flex-1">
            <ChatDock />
          </div>
        </footer>
      </div>
      <NodeDetail />
      <CommandBar />
      <SystemPanel />
      <Toasts />
    </div>
  );
}
