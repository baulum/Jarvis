// Floating labels: only for what matters right now (hover, selection and its neighbours, live activity,
// the most important nodes close to the camera). Positions are written straight to the DOM each frame.
import { useEffect, useRef } from "react";
import * as THREE from "three";
import { FLOW_COLUMNS, GROUP_LABEL } from "./graph/anatomy";
import { fx, GROUPS, heldGlow } from "./graph/fx";
import { brainHandle, GLOW_SOURCE } from "./graph/scene";
import { KIND_LABEL } from "./graph/types";
import { nodeVisible, useBrain } from "./store";

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
        if (index === undefined || !nodeVisible(b, m.nodes[index]!)) return;
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

/**
 * Names in space: the brain regions while they are active (or hovered in the legend) in the brain view, the
 * columns of the live view. Positions follow the camera every frame, written straight to the DOM.
 */
export function SpaceLabels() {
  const regionEls = useRef<HTMLDivElement[]>([]);
  const columnEls = useRef<HTMLDivElement[]>([]);
  useEffect(() => {
    let raf = 0;
    let heldRev = -1;
    let held = heldGlow(useBrain.getState().live);
    let colTop = FLOW_COLUMNS.map(() => 260);
    let lastTop = 0;
    const v = new THREE.Vector3();
    const place = (el: HTMLDivElement | undefined, x: number, y: number, z: number, opacity: number) => {
      const cam = brainHandle.camera;
      if (!el || !cam) return;
      v.set(x, y, z).project(cam);
      if (opacity < 0.02 || v.z > 1) {
        el.style.opacity = "0";
        return;
      }
      const { width, height } = brainHandle.size;
      el.style.transform = `translate(${((v.x * 0.5 + 0.5) * width).toFixed(1)}px, ${((-v.y * 0.5 + 0.5) * height).toFixed(1)}px) translate(-50%, -100%)`;
      el.style.opacity = opacity.toFixed(2);
    };
    const frame = (t: number) => {
      raf = requestAnimationFrame(frame);
      const b = useBrain.getState();
      if (b.live.rev !== heldRev) {
        heldRev = b.live.rev;
        held = heldGlow(b.live);
      }
      const brain = brainHandle.brainness;
      GROUPS.forEach((g, i) => {
        const level = Math.max(held[g], fx.glow[g] * 0.8, b.regionHover === g ? 1 : 0);
        const [x, y, z, r] = GLOW_SOURCE[g];
        const el = regionEls.current[i];
        if (el) el.dataset.active = level > 0.3 ? "1" : "0";
        place(el, x, y + r * 0.22, z, Math.min(1, level * 1.6) * brain);
      });
      // live view: column names above the highest node of each column
      if (t - lastTop > 500 && b.model && brainHandle.target.length === b.model.nodes.length * 3) {
        lastTop = t;
        colTop = FLOW_COLUMNS.map((c) => {
          let top = -Infinity;
          for (let i = 0; i < b.model!.nodes.length; i += 1) {
            if (Math.abs(brainHandle.target[i * 3]! - c.x) < 260 && brainHandle.target[i * 3 + 1]! > top) top = brainHandle.target[i * 3 + 1]!;
          }
          return Number.isFinite(top) ? top + 70 : 160;
        });
      }
      const live = b.view === "activity" ? 1 - brain : 0;
      FLOW_COLUMNS.forEach((c, i) => place(columnEls.current[i], c.x, colTop[i]!, 0, live * 0.9));
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, []);
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
      {GROUPS.map((g, i) => (
        <div
          key={g}
          ref={(el) => {
            if (el) regionEls.current[i] = el;
          }}
          className="holo-space-label absolute top-0 left-0 opacity-0"
        >
          {GROUP_LABEL[g]}
        </div>
      ))}
      {FLOW_COLUMNS.map((c, i) => (
        <div
          key={c.id}
          ref={(el) => {
            if (el) columnEls.current[i] = el;
          }}
          className="holo-space-label absolute top-0 left-0 opacity-0"
        >
          {c.label}
        </div>
      ))}
    </div>
  );
}
