// The 3D knowledge graph: one instanced draw for nodes, one for edges, a faint dust field for depth, the
// J.A.R.V.I.S. core at the centre. Positions come from the layout worker and are eased every frame.
// All hot-path work reads the brain store imperatively – React does not re-render per frame.
import { CameraControls, PerformanceMonitor } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { useApp } from "../../store/app";
import { useBrain } from "../store";
import { boundingSphere, fitDistance, focusPose, pickNearest, type Vec3 } from "./camera";
import { buildIconAtlas, iconIndex } from "./icons";
import { deriveJarvisState, type JarvisState } from "./live";
import type { LayoutRequest, LayoutResponse } from "./layout";
import { layoutGroup, nodeSize, rand01, type GraphModel } from "./model";
import {
  coreFragment,
  coreVertex,
  dustFragment,
  dustVertex,
  edgeFragment,
  edgeVertex,
  nodeFragment,
  nodeVertex,
  STATUS_CODE,
} from "./shaders";
import { CATEGORY_COLOR } from "./types";

const STATE_TINT: Record<JarvisState, [number, number, number]> = {
  idle: [0.42, 0.83, 0.9],
  listening: [0.75, 0.95, 1.0],
  thinking: [0.55, 0.88, 0.95],
  retrieving: [0.7, 0.92, 1.0],
  tools: [0.62, 0.9, 0.95],
  coding: [0.6, 0.92, 0.85],
  speaking: [0.85, 0.97, 1.0],
  approval: [0.886, 0.631, 0.298],
  error: [0.898, 0.345, 0.31],
};
const STATE_SPIN: Record<JarvisState, number> = {
  idle: 0.0,
  listening: 0.08,
  thinking: 0.45,
  retrieving: 0.7,
  tools: 0.55,
  coding: 0.35,
  speaking: 0.12,
  approval: 0.0,
  error: 0.0,
};

export interface BrainHandle {
  /** current eased positions (xyz per node) – used by labels and picking */
  positions: Float32Array;
  camera: THREE.PerspectiveCamera | null;
  size: { width: number; height: number };
}

export const brainHandle: BrainHandle = { positions: new Float32Array(0), camera: null, size: { width: 1, height: 1 } };

function currentState(): JarvisState {
  const app = useApp.getState();
  const brain = useBrain.getState();
  const running = Object.values(app.threads).some((t) => t.running);
  return deriveJarvisState({
    voice: app.voice.state ?? null,
    connection: app.connection,
    pendingApprovals: app.pendingApprovals.length,
    agentRunning: running,
    live: brain.live,
    now: performance.now(),
  });
}

// ------------------------------------------------------------------ layout worker bridge
function useLayout(model: GraphModel | null) {
  const target = useRef<Float32Array>(new Float32Array(0));
  useEffect(() => {
    if (!model) return;
    target.current = new Float32Array(model.positions);
    const worker = new Worker(new URL("./layout.worker.ts", import.meta.url), { type: "module" });
    const fresh = !brainHandle.positions.length;
    const msg: LayoutRequest = {
      type: "init",
      nodes: model.nodes.map((n) => ({ id: n.id, group: layoutGroup(n), degree: n.degree, pinned: n.id === "agent:jarvis" })),
      links: model.edges.map((e) => ({ source: e.source, target: e.target, weight: e.weight, relation: e.relation })),
      positions: model.positions,
      alpha: fresh ? 1 : 0.35, // an update reorganises gently instead of exploding
    };
    worker.onmessage = (e: MessageEvent<LayoutResponse>) => {
      if (e.data.type === "tick") {
        target.current = e.data.positions;
        model.positions.set(e.data.positions);
      }
    };
    worker.postMessage(msg);
    return () => worker.terminate();
  }, [model]);
  return target;
}

// ------------------------------------------------------------------ nodes
function Nodes({ model, atlas, target }: { model: GraphModel; atlas: THREE.Texture | null; target: React.RefObject<Float32Array> }) {
  const count = model.nodes.length;
  const geom = useMemo(() => {
    const g = new THREE.InstancedBufferGeometry();
    const quad = new THREE.PlaneGeometry(1, 1);
    g.index = quad.index;
    g.setAttribute("position", quad.getAttribute("position"));
    g.setAttribute("uv", quad.getAttribute("uv"));
    const offset = new Float32Array(model.positions);
    g.setAttribute("aOffset", new THREE.InstancedBufferAttribute(offset, 3).setUsage(THREE.DynamicDrawUsage));
    const size = new Float32Array(count);
    const color = new Float32Array(count * 3);
    const icon = new Float32Array(count);
    const act = new Float32Array(count * 4);
    model.nodes.forEach((n, i) => {
      size[i] = nodeSize(n);
      color.set(CATEGORY_COLOR[n.category], i * 3);
      icon[i] = iconIndex(n.category);
      act[i * 4 + 3] = n.bornAt;
    });
    g.setAttribute("aSize", new THREE.InstancedBufferAttribute(size, 1));
    g.setAttribute("aColor", new THREE.InstancedBufferAttribute(color, 3));
    g.setAttribute("aIcon", new THREE.InstancedBufferAttribute(icon, 1));
    g.setAttribute("aAct", new THREE.InstancedBufferAttribute(act, 4).setUsage(THREE.DynamicDrawUsage));
    g.instanceCount = count;
    g.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 4000);
    return g;
  }, [model, count]);
  useEffect(() => () => geom.dispose(), [geom]);

  const mat = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader: nodeVertex,
        fragmentShader: nodeFragment,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        uniforms: {
          uTime: { value: 0 },
          uIconDist: { value: 260 },
          uScale: { value: 0.72 },
          uAtlas: { value: null },
          uStateTint: { value: new THREE.Color() },
        },
      }),
    [],
  );
  useEffect(() => {
    mat.uniforms.uAtlas!.value = atlas;
  }, [atlas, mat]);

  const seen = useRef({ liveRev: -1, hidden: null as unknown, selected: null as string | null, hovered: null as string | null });
  useFrame((_, dt) => {
    const t = performance.now() / 1000;
    mat.uniforms.uTime!.value = t;
    const tint = STATE_TINT[currentState()];
    (mat.uniforms.uStateTint!.value as THREE.Color).setRGB(tint[0], tint[1], tint[2]);
    // ease displayed positions towards the layout target
    const off = geom.getAttribute("aOffset") as THREE.InstancedBufferAttribute;
    const cur = off.array as Float32Array;
    brainHandle.positions = cur; // edges, labels and picking read exactly what is drawn
    const tgt = target.current;
    if (tgt && tgt.length === cur.length) {
      const k = Math.min(1, dt * 3.2);
      let moved = 0;
      for (let i = 0; i < cur.length; i += 1) {
        const d = (tgt[i] ?? 0) - (cur[i] ?? 0);
        if (d > 0.01 || d < -0.01) {
          cur[i] = (cur[i] ?? 0) + d * k;
          moved += 1;
        }
      }
      if (moved) off.needsUpdate = true;
    }
    // activity / filter / selection → aAct and aSize (only when something changed)
    const b = useBrain.getState();
    const s = seen.current;
    if (b.live.rev !== s.liveRev || b.hidden !== s.hidden || b.selected !== s.selected || b.hovered !== s.hovered) {
      s.liveRev = b.live.rev;
      s.hidden = b.hidden;
      s.selected = b.selected;
      s.hovered = b.hovered;
      const act = geom.getAttribute("aAct") as THREE.InstancedBufferAttribute;
      const size = geom.getAttribute("aSize") as THREE.InstancedBufferAttribute;
      const a = act.array as Float32Array;
      const sz = size.array as Float32Array;
      const sel = b.selected ? model.byId.get(b.selected) : undefined;
      const neigh = new Set(sel !== undefined ? model.neighbors[sel] : []);
      const hov = b.hovered ? model.byId.get(b.hovered) : undefined;
      for (const n of model.nodes) {
        const i = n.index;
        const live = b.live.nodes[n.id];
        const code = live ? STATUS_CODE[live.status] : STATUS_CODE.none;
        let emph = live ? (live.status === "ok" ? 0.5 : 1) : 0;
        if (i === sel || i === hov) emph = 1;
        else if (neigh.has(i)) emph = Math.max(emph, 0.55);
        else if (sel !== undefined) emph = Math.min(emph, 0.15);
        const prevCode = a[i * 4 + 2] ?? 0;
        a[i * 4] = emph;
        if (live && (prevCode !== code || (a[i * 4 + 1] ?? 0) < live.since / 1000 - 0.05)) a[i * 4 + 1] = live.since / 1000;
        a[i * 4 + 2] = code;
        sz[i] = b.hidden.has(n.category) && i !== sel ? 0 : nodeSize(n);
      }
      act.needsUpdate = true;
      size.needsUpdate = true;
    }
  });

  return <mesh geometry={geom} material={mat} frustumCulled={false} renderOrder={2} />;
}

// ------------------------------------------------------------------ edges
function Edges({ model }: { model: GraphModel }) {
  const n = model.edges.length;
  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(n * 6), 3).setUsage(THREE.DynamicDrawUsage));
    const along = new Float32Array(n * 2);
    const base = new Float32Array(n * 2);
    model.edges.forEach((e, i) => {
      along[i * 2 + 1] = 1;
      const b = e.relation === "ähnlich" ? 0.035 : e.relation === "gehört zu" || e.relation === "kann" ? 0.03 : 0.07 + 0.05 * Math.min(1, e.weight);
      base[i * 2] = b;
      base[i * 2 + 1] = b;
    });
    g.setAttribute("aAlong", new THREE.BufferAttribute(along, 1));
    g.setAttribute("aBase", new THREE.BufferAttribute(base, 1));
    g.setAttribute("aEdge", new THREE.BufferAttribute(new Float32Array(n * 4), 2).setUsage(THREE.DynamicDrawUsage));
    g.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 4000);
    return g;
  }, [model, n]);
  useEffect(() => () => geom.dispose(), [geom]);
  const mat = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader: edgeVertex,
        fragmentShader: edgeFragment,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        uniforms: {
          uTime: { value: 0 },
          uColor: { value: new THREE.Color(0.42, 0.7, 0.8) },
          uFlowColor: { value: new THREE.Color(0.9, 0.98, 1.0) },
        },
      }),
    [],
  );
  const seen = useRef({ liveRev: -1, selected: null as string | null, hovered: null as string | null, hidden: null as unknown });
  useFrame(() => {
    mat.uniforms.uTime!.value = performance.now() / 1000;
    const pos = geom.getAttribute("position") as THREE.BufferAttribute;
    const arr = pos.array as Float32Array;
    const p = brainHandle.positions;
    if (p.length >= model.nodes.length * 3) {
      model.edges.forEach((e, i) => {
        arr[i * 6] = p[e.source * 3] ?? 0;
        arr[i * 6 + 1] = p[e.source * 3 + 1] ?? 0;
        arr[i * 6 + 2] = p[e.source * 3 + 2] ?? 0;
        arr[i * 6 + 3] = p[e.target * 3] ?? 0;
        arr[i * 6 + 4] = p[e.target * 3 + 1] ?? 0;
        arr[i * 6 + 5] = p[e.target * 3 + 2] ?? 0;
      });
      pos.needsUpdate = true;
    }
    const b = useBrain.getState();
    const s = seen.current;
    if (b.live.rev === s.liveRev && b.selected === s.selected && b.hovered === s.hovered && b.hidden === s.hidden) return;
    s.liveRev = b.live.rev;
    s.selected = b.selected;
    s.hovered = b.hovered;
    s.hidden = b.hidden;
    // edges on active streams carry a moving signal (source → target); edges of the selection are emphasised
    const flow = new Map<string, number>();
    const mark = (a: number, c: number, v: number) => {
      const key = a < c ? `${a}-${c}` : `${c}-${a}`;
      flow.set(key, Math.max(flow.get(key) ?? 0, v));
    };
    for (const st of Object.values(b.live.streams)) {
      const ids = st.path.map((id) => model.byId.get(id)).filter((x): x is number => x !== undefined);
      const strength = st.status === "running" ? 1 : 0.55;
      const chain = ids.filter((i) => !model.nodes[i]?.id.startsWith("memory:"));
      for (let k = 0; k + 1 < chain.length; k += 1) mark(chain[k]!, chain[k + 1]!, strength);
      // memory hits hang off the last element of the chain (agent or the memory tool)
      const hub = chain[chain.length - 1];
      if (hub !== undefined) for (const i of ids) if (model.nodes[i]?.id.startsWith("memory:")) mark(hub, i, strength);
    }
    const sel = b.selected ? model.byId.get(b.selected) : undefined;
    const hov = b.hovered ? model.byId.get(b.hovered) : undefined;
    const ed = geom.getAttribute("aEdge") as THREE.BufferAttribute;
    const ea = ed.array as Float32Array;
    model.edges.forEach((e, i) => {
      const hiddenEdge = b.hidden.has(model.nodes[e.source]!.category) || b.hidden.has(model.nodes[e.target]!.category);
      const key = e.source < e.target ? `${e.source}-${e.target}` : `${e.target}-${e.source}`;
      const f = flow.get(key) ?? 0;
      let emph = f > 0 ? 0.8 : 0;
      if (e.source === sel || e.target === sel || e.source === hov || e.target === hov) emph = Math.max(emph, 0.7);
      if (hiddenEdge) emph = -0.3;
      ea[i * 4] = emph;
      ea[i * 4 + 1] = f;
      ea[i * 4 + 2] = emph;
      ea[i * 4 + 3] = f;
    });
    ed.needsUpdate = true;
  });
  return <lineSegments geometry={geom} material={mat} frustumCulled={false} renderOrder={1} />;
}

// ------------------------------------------------------------------ dust (depth cue), adaptive count
function Dust({ count }: { count: number }) {
  const ref = useRef<THREE.Points>(null);
  const { geom, mat } = useMemo(() => {
    const g = new THREE.BufferGeometry();
    const p = new Float32Array(count * 3);
    const seed = new Float32Array(count);
    for (let i = 0; i < count; i += 1) {
      const r = 300 + rand01(`d${i}`, 1) * 1400;
      const th = rand01(`d${i}`, 2) * Math.PI * 2;
      const ph = Math.acos(2 * rand01(`d${i}`, 3) - 1);
      p[i * 3] = r * Math.sin(ph) * Math.cos(th);
      p[i * 3 + 1] = r * Math.cos(ph) * 0.6;
      p[i * 3 + 2] = r * Math.sin(ph) * Math.sin(th);
      seed[i] = rand01(`d${i}`, 4);
    }
    g.setAttribute("position", new THREE.BufferAttribute(p, 3));
    g.setAttribute("aSeed", new THREE.BufferAttribute(seed, 1));
    const m = new THREE.ShaderMaterial({
      vertexShader: dustVertex,
      fragmentShader: dustFragment,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      uniforms: { uTime: { value: 0 }, uPixelRatio: { value: 1 } },
    });
    return { geom: g, mat: m };
  }, [count]);
  useEffect(() => () => geom.dispose(), [geom]);
  useFrame((state, dt) => {
    mat.uniforms.uTime!.value = performance.now() / 1000;
    mat.uniforms.uPixelRatio!.value = state.gl.getPixelRatio();
    if (ref.current) ref.current.rotation.y += dt * 0.004;
  });
  return <points ref={ref} geometry={geom} material={mat} frustumCulled={false} />;
}

// ------------------------------------------------------------------ the J.A.R.V.I.S. core
function Core() {
  const ref = useRef<THREE.Mesh>(null);
  const mat = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader: coreVertex,
        fragmentShader: coreFragment,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        uniforms: { uTime: { value: 0 }, uIn: { value: 0 }, uOut: { value: 0 }, uSpin: { value: 0 }, uTint: { value: new THREE.Color() } },
      }),
    [],
  );
  const smooth = useRef({ inp: 0, out: 0, spin: 0 });
  useFrame(({ camera }, dt) => {
    const st = currentState();
    const lv = useBrain.getState().level;
    const fresh = performance.now() - lv.ts < 600;
    const s = smooth.current;
    const k = Math.min(1, dt * 10);
    s.inp += ((st === "listening" && fresh ? lv.input * 3 : 0) - s.inp) * k;
    s.out += ((st === "speaking" && fresh ? lv.output * 2.2 : 0) - s.out) * k;
    s.spin += (STATE_SPIN[st] - s.spin) * Math.min(1, dt * 2);
    mat.uniforms.uTime!.value = performance.now() / 1000;
    mat.uniforms.uIn!.value = Math.min(1, s.inp);
    mat.uniforms.uOut!.value = Math.min(1, s.out);
    mat.uniforms.uSpin!.value = s.spin;
    const tint = STATE_TINT[st];
    (mat.uniforms.uTint!.value as THREE.Color).setRGB(tint[0], tint[1], tint[2]);
    const p = brainHandle.positions;
    const model = useBrain.getState().model;
    const ji = model?.byId.get("agent:jarvis");
    if (ref.current) {
      if (ji !== undefined && p.length > ji * 3 + 2) ref.current.position.set(p[ji * 3]!, p[ji * 3 + 1]!, p[ji * 3 + 2]!);
      ref.current.quaternion.copy(camera.quaternion);
    }
  });
  return (
    <mesh ref={ref} material={mat} renderOrder={3}>
      <planeGeometry args={[78, 78]} />
    </mesh>
  );
}

// ------------------------------------------------------------------ camera: focus requests, idle drift, picking
function CameraRig({ model, panelOpen }: { model: GraphModel; panelOpen: boolean }) {
  const controls = useRef<CameraControls>(null);
  const { camera, size, gl } = useThree();
  const lastFocus = useRef(0);
  const lastRecall = useRef(0);
  brainHandle.camera = camera as THREE.PerspectiveCamera;
  brainHandle.size = { width: size.width, height: size.height };

  // frame the whole brain once the first layout has settled a little
  useEffect(() => {
    const t = setTimeout(() => {
      if (!useBrain.getState().selected && useBrain.getState().lastInteraction === 0) useBrain.getState().overview();
    }, 2500);
    return () => clearTimeout(t);
  }, []);

  // focus requests from the store (node click, search, cluster, overview)
  useEffect(() => {
    return useBrain.subscribe((s, prev) => {
      const c = controls.current;
      if (!c || !s.focus || s.focus === prev.focus || s.focus.seq === lastFocus.current) return;
      lastFocus.current = s.focus.seq;
      const fov = (camera as THREE.PerspectiveCamera).fov;
      const p = brainHandle.positions;
      if (s.focus.kind === "overview") {
        const all = model.nodes.map((n) => n.index);
        const sphere = boundingSphere(p, all, 0.85);
        const d = fitDistance(sphere.radius, fov, 1.05);
        void c.setLookAt(sphere.center[0], sphere.center[1] + d * 0.18, sphere.center[2] + d, ...sphere.center, true);
        return;
      }
      const ids = s.focus.kind === "node" ? [s.focus.id] : s.focus.ids;
      const idx = ids.map((id) => model.byId.get(id)).filter((x): x is number => x !== undefined);
      if (!idx.length) return;
      const sphere = boundingSphere(p, idx);
      const single = idx.length === 1;
      const n = single ? model.nodes[idx[0]!] : undefined;
      const dist = single ? Math.max(90, nodeSize(n!) * 16) : fitDistance(sphere.radius, fov, 0.75);
      const cam: Vec3 = [camera.position.x, camera.position.y, camera.position.z];
      const pose = focusPose(cam, sphere.center, dist, panelOpen ? 0.22 : 0);
      void c.setLookAt(...pose.position, ...pose.lookAt, true);
    });
  }, [camera, model, panelOpen]);

  // pointer: hover + click picking on the CPU (cheap for thousands of nodes, no raycast geometry needed)
  useEffect(() => {
    const el = gl.domElement;
    let down: { x: number; y: number; t: number } | null = null;
    let raf = 0;
    let last: PointerEvent | null = null;
    const vp = new THREE.Matrix4();
    const pick = (ev: PointerEvent): string | null => {
      const r = el.getBoundingClientRect();
      vp.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
      const b = useBrain.getState();
      const proj = (camera as THREE.PerspectiveCamera).projectionMatrix.elements[5] ?? 1;
      const i = pickNearest(
        brainHandle.positions,
        model.nodes.length,
        vp.elements,
        r.width,
        r.height,
        ev.clientX - r.left,
        ev.clientY - r.top,
        (k, w) => (nodeSize(model.nodes[k]!) * proj * r.height) / (2 * w) * 1.3,
        (k) => !b.hidden.has(model.nodes[k]!.category),
      );
      return i >= 0 ? model.nodes[i]!.id : null;
    };
    const onMove = (ev: PointerEvent) => {
      last = ev;
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        if (last && !down) {
          const id = pick(last);
          useBrain.getState().hover(id);
          el.style.cursor = id ? "pointer" : "grab";
        }
      });
    };
    const onDown = (ev: PointerEvent) => {
      down = { x: ev.clientX, y: ev.clientY, t: performance.now() };
      useBrain.getState().touch();
    };
    const onUp = (ev: PointerEvent) => {
      const d = down;
      down = null;
      if (!d || Math.hypot(ev.clientX - d.x, ev.clientY - d.y) > 5) return; // it was a drag (rotate/pan)
      const id = pick(ev);
      if (id) useBrain.getState().select(id);
    };
    const onDbl = (ev: MouseEvent) => {
      if (!pick(ev as PointerEvent)) useBrain.getState().overview();
    };
    const onLeave = () => useBrain.getState().hover(null);
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("dblclick", onDbl);
    el.addEventListener("pointerleave", onLeave);
    return () => {
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("dblclick", onDbl);
      el.removeEventListener("pointerleave", onLeave);
      cancelAnimationFrame(raf);
    };
  }, [camera, gl, model]);

  useFrame((_, dt) => {
    const c = controls.current;
    if (!c) return;
    const b = useBrain.getState();
    const now = performance.now();
    const idle = now - b.lastInteraction > 12_000 && !b.selected;
    // calm idle rotation; stops the moment Sir touches the brain
    if (idle && !c.active) void c.rotate(dt * 0.025, 0, false);
    // Jarvis recalls something while Sir is not navigating: drift the view subtly towards it
    if (idle && b.live.lastRecall > lastRecall.current) {
      lastRecall.current = b.live.lastRecall;
      const hits = Object.entries(b.live.nodes)
        .filter(([, v]) => v.status === "recalled")
        .map(([id]) => model.byId.get(id))
        .filter((x): x is number => x !== undefined);
      if (hits.length) {
        const sph = boundingSphere(brainHandle.positions, hits);
        const t = new THREE.Vector3();
        c.getTarget(t);
        void c.setTarget(t.x + (sph.center[0] - t.x) * 0.25, t.y + (sph.center[1] - t.y) * 0.25, t.z + (sph.center[2] - t.z) * 0.25, true);
      }
    }
  });

  return (
    <CameraControls
      ref={controls}
      makeDefault
      smoothTime={0.55}
      draggingSmoothTime={0.12}
      minDistance={30}
      maxDistance={2600}
      dollySpeed={0.6}
      truckSpeed={1.4}
      onStart={() => useBrain.getState().touch()}
    />
  );
}

// ------------------------------------------------------------------ frame budget
/** Renders on demand at a rate that leaves the GPU to speech recognition when it matters. */
function FrameLimiter() {
  const { invalidate } = useThree();
  useEffect(() => {
    let id = 0;
    let last = 0;
    const loop = (t: number) => {
      id = requestAnimationFrame(loop);
      const voice = useApp.getState().voice.state;
      const fps = document.hidden ? 0 : voice === "recording" || voice === "thinking" ? 30 : 60;
      if (!fps) return;
      if (t - last >= 1000 / fps - 2) {
        last = t;
        invalidate();
      }
    };
    id = requestAnimationFrame(loop);
    const expire = setInterval(() => useBrain.getState().tick(), 400);
    return () => {
      cancelAnimationFrame(id);
      clearInterval(expire);
    };
  }, [invalidate]);
  return null;
}

export function BrainCanvas({ panelOpen }: { panelOpen: boolean }) {
  const model = useBrain((s) => s.model);
  const target = useLayout(model);
  const [atlas, setAtlas] = useState<THREE.Texture | null>(null);
  const [dpr, setDpr] = useState(Math.min(1.5, window.devicePixelRatio || 1));
  const [dust, setDust] = useState(1400);
  useEffect(() => {
    let alive = true;
    void buildIconAtlas().then((canvas) => {
      if (!alive) return;
      const tex = new THREE.CanvasTexture(canvas);
      tex.colorSpace = THREE.NoColorSpace;
      tex.generateMipmaps = true;
      tex.minFilter = THREE.LinearMipmapLinearFilter;
      tex.needsUpdate = true;
      setAtlas(tex);
    });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <Canvas
      frameloop="demand"
      dpr={dpr}
      camera={{ position: [0, 160, 900], fov: 42, near: 1, far: 6000 }}
      gl={{ antialias: true, alpha: false, powerPreference: "high-performance" }}
      onCreated={({ gl }) => gl.setClearColor(new THREE.Color("#030608"), 1)}
      aria-label="Wissensgraph"
    >
      <PerformanceMonitor
        onDecline={() => {
          setDpr(1);
          setDust(500);
        }}
        onIncline={() => {
          setDpr(Math.min(1.5, window.devicePixelRatio || 1));
          setDust(1400);
        }}
      />
      <FrameLimiter />
      <Dust count={dust} />
      {model && (
        <>
          <Edges key={`e-${model.version}-${model.edges.length}`} model={model} />
          <Nodes key={`n-${model.version}-${model.nodes.length}`} model={model} atlas={atlas} target={target} />
          <CameraRig model={model} panelOpen={panelOpen} />
        </>
      )}
      <Core />
    </Canvas>
  );
}
