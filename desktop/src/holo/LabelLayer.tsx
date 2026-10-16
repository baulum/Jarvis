// Floating labels: only for what matters right now (hover, selection and its neighbours, live activity,
// the most important nodes close to the camera). Positions are written straight to the DOM each frame.
import { useEffect, useRef } from "react";
import * as THREE from "three";
import { brainHandle } from "./graph/BrainCanvas";
import { KIND_LABEL } from "./graph/types";
import { useBrain } from "./store";

const MAX = 22;

interface Slot {
  id: string;
  index: number;
  strong: boolean;
}

export function LabelLayer() {
  const root = useRef<HTMLDivElement>(null);
  const els = useRef<HTMLDivElement[]>([]);
  const slots = useRef<Slot[]>([]);

  useEffect(() => {
    let raf = 0;
    let lastPick = 0;
    const v = new THREE.Vector3();
    const choose = () => {
      const b = useBrain.getState();
      const m = b.model;
      const cam = brainHandle.camera;
      if (!m || !cam) return [];
      const out: Slot[] = [];
      const add = (id: string | null | undefined, strong: boolean) => {
        if (!id || out.length >= MAX || out.some((s) => s.id === id)) return;
        const index = m.byId.get(id);
        if (index === undefined || b.hidden.has(m.nodes[index]!.category)) return;
        out.push({ id, index, strong });
      };
      add(b.hovered, true);
      add(b.selected, true);
      const sel = b.selected ? m.byId.get(b.selected) : undefined;
      if (sel !== undefined) for (const j of (m.neighbors[sel] ?? []).slice(0, 10)) add(m.nodes[j]!.id, false);
      for (const [id, a] of Object.entries(b.live.nodes)) if (a.status !== "ok") add(id, a.status !== "pulse");
      add("agent:jarvis", false);
      // nearest important nodes when Sir flies close
      const p = brainHandle.positions;
      const near: [number, number][] = [];
      for (const n of m.nodes) {
        if (n.category === "tool" && n.kind === "tool") continue;
        const d = Math.hypot((p[n.index * 3] ?? 0) - cam.position.x, (p[n.index * 3 + 1] ?? 0) - cam.position.y, (p[n.index * 3 + 2] ?? 0) - cam.position.z);
        if (d < 340) near.push([d / (0.4 + n.weight), n.index]);
      }
      near.sort((a, c) => a[0] - c[0]);
      for (const [, i] of near.slice(0, 8)) add(m.nodes[i]!.id, false);
      return out;
    };
    const frame = (t: number) => {
      raf = requestAnimationFrame(frame);
      const cam = brainHandle.camera;
      const m = useBrain.getState().model;
      if (!cam || !m || !root.current) return;
      if (t - lastPick > 160) {
        lastPick = t;
        slots.current = choose();
        slots.current.forEach((s, i) => {
          const el = els.current[i];
          if (!el) return;
          const n = m.nodes[s.index]!;
          const nameEl = el.firstElementChild as HTMLElement;
          const kindEl = el.lastElementChild as HTMLElement;
          if (nameEl.textContent !== n.label) nameEl.textContent = n.label;
          const kind = KIND_LABEL[n.kind] ?? n.kind;
          if (kindEl.textContent !== kind) kindEl.textContent = kind;
          el.dataset.strong = s.strong ? "1" : "0";
        });
      }
      const { width, height } = brainHandle.size;
      const p = brainHandle.positions;
      for (let i = 0; i < MAX; i += 1) {
        const el = els.current[i];
        if (!el) continue;
        const s = slots.current[i];
        if (!s) {
          el.style.opacity = "0";
          continue;
        }
        v.set(p[s.index * 3] ?? 0, p[s.index * 3 + 1] ?? 0, p[s.index * 3 + 2] ?? 0).project(cam);
        if (v.z > 1 || v.x < -1.1 || v.x > 1.1 || v.y < -1.1 || v.y > 1.1) {
          el.style.opacity = "0";
          continue;
        }
        const x = (v.x * 0.5 + 0.5) * width;
        const y = (-v.y * 0.5 + 0.5) * height;
        el.style.transform = `translate(${x.toFixed(1)}px, ${(y + 12).toFixed(1)}px) translateX(-50%)`;
        el.style.opacity = s.strong ? "1" : "0.62";
      }
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <div ref={root} className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
      {Array.from({ length: MAX }, (_, i) => (
        <div
          key={i}
          ref={(el) => {
            if (el) els.current[i] = el;
          }}
          className="holo-label absolute top-0 left-0 opacity-0 transition-opacity duration-300"
        >
          <span className="block max-w-[16rem] truncate" />
          <span className="type-data block text-center text-[0.6rem] tracking-[0.12em] text-ink-faint uppercase" />
        </div>
      ))}
    </div>
  );
}
