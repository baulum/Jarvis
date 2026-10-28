// The 3D brain: an anatomical cortex with neurons (brain view) and the real knowledge graph – memories, tools,
// agents – drawn with one instanced draw for nodes and one line draw for relations. Nodes ease to their place
// in the current view (brain, network, live activity, memory explorer); impulses travel along real activity.
// All hot-path work reads the brain store imperatively – React does not re-render per frame.
import { CameraControls, PerformanceMonitor } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { useApp } from "../../store/app";
import { nodeVisible, useBrain, type ViewMode } from "../store";
import { brainPlacement, flowPlacement } from "./anatomy";
import { Anatomy, FxDriver } from "./BrainTissue";
import { Bloom } from "./Bloom";
import { boundingSphere, fitBoxDistance, fitDistance, focusPose, freeFraction, pickNearest, viewShift, type Vec3 } from "./camera";
import { fx } from "./fx";
import { buildIconAtlas, iconIndex } from "./icons";
import { Impulses } from "./Impulses";
import { deriveJarvisState, type JarvisState, type LiveState } from "./live";
import type { LayoutRequest, LayoutResponse } from "./layout";
import { layoutGroup, nodeSize, rand01, type GraphModel } from "./model";
import { deviceInfo, detectTier, QUALITY, stepTier, type Tier } from "./quality";
import { brainHandle } from "./scene";
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

// ------------------------------------------------------------------ view targets
/** Node ids that take part in live activity right now (live view brings them to the front). */
function activeIds(live: LiveState): Set<string> {
  const out = new Set<string>();
  for (const [id, a] of Object.entries(live.nodes)) if (a.status !== "pulse") out.add(id);
  for (const p of Object.values(live.processes)) if (p.until === undefined) for (const id of p.path) out.add(id);
  return out;
}

/** Where the nodes are heading: anatomical places, the force layout, or the live flow. */
function useViewTargets(model: GraphModel, network: React.RefObject<Float32Array>) {
  const brain = useMemo(() => brainPlacement(model), [model]);
  const flow = useRef({ rev: -1, at: 0, pos: flowPlacement(model, new Set()) });
  useEffect(() => {
    flow.current = { rev: -1, at: 0, pos: flowPlacement(model, new Set()) };
  }, [model]);
  return (view: ViewMode, live: LiveState, t: number): Float32Array => {
    if (view === "brain") return brain;
    if (view === "activity") {
      const f = flow.current;
      if (f.rev !== live.rev && t - f.at > 0.35) flow.current = { rev: live.rev, at: t, pos: flowPlacement(model, activeIds(live)) };
      return flow.current.pos;
    }
    const net = network.current;
    return net && net.length === model.nodes.length * 3 ? net : model.positions;
  };
}

// ------------------------------------------------------------------ nodes
function Nodes({ model, atlas, target }: { model: GraphModel; atlas: THREE.Texture | null; target: React.RefObject<Float32Array> }) {
  const count = model.nodes.length;
  const targetFor = useViewTargets(model, target);
  const geom = useMemo(() => {
    const g = new THREE.InstancedBufferGeometry();
    const quad = new THREE.PlaneGeometry(1, 1);
    g.index = quad.index;
    g.setAttribute("position", quad.getAttribute("position"));
    g.setAttribute("uv", quad.getAttribute("uv"));
    // start where the nodes are drawn right now (a graph refresh must not make them jump)
    const offset = new Float32Array(count * 3);
    const prev = brainHandle.model;
    for (const n of model.nodes) {
      const j = prev?.byId.get(n.id);
      if (prev && j !== undefined && brainHandle.positions.length >= (j + 1) * 3) offset.set(brainHandle.positions.subarray(j * 3, j * 3 + 3), n.index * 3);
      else offset.set(model.positions.subarray(n.index * 3, n.index * 3 + 3), n.index * 3);
    }
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
    brainHandle.positions = offset;
    brainHandle.model = model;
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
          uDim: { value: 0 },
        },
      }),
    [],
  );
  useEffect(() => {
    mat.uniforms.uAtlas!.value = atlas;
  }, [atlas, mat]);
  useEffect(() => () => mat.dispose(), [mat]);

  const seen = useRef({ liveRev: -1, visRev: -1, hovered: null as string | null });
  useFrame((_, dt) => {
    const t = performance.now() / 1000;
    const b = useBrain.getState();
    mat.uniforms.uTime!.value = t;
    const tint = STATE_TINT[currentState()];
    (mat.uniforms.uStateTint!.value as THREE.Color).setRGB(tint[0], tint[1], tint[2]);
    // in the brain, knowledge nodes read as bright neurons among the substrate – a little smaller
    mat.uniforms.uScale!.value = 0.72 * (1 - 0.32 * brainHandle.brainness);
    const dim = b.view === "activity" ? 1 : 0;
    mat.uniforms.uDim!.value += (dim - mat.uniforms.uDim!.value) * Math.min(1, dt * 3);
    // ease displayed positions towards the target of the current view
    const off = geom.getAttribute("aOffset") as THREE.InstancedBufferAttribute;
    const cur = off.array as Float32Array;
    brainHandle.positions = cur; // edges, labels, impulses and picking read exactly what is drawn
    const tgt = targetFor(b.view, b.live, t);
    brainHandle.target = tgt;
    if (tgt.length === cur.length) {
      const k = Math.min(1, dt * 2.6);
      let moved = 0;
      for (let i = 0; i < cur.length; i += 1) {
        const d = (tgt[i] ?? 0) - (cur[i] ?? 0);
        if (d > 0.01 || d < -0.01) {
          cur[i] = (cur[i] ?? 0) + d * k;
          moved += 1;
        }
      }
      moving = moved > 0;
      if (moved) off.needsUpdate = true;
    }
    // activity / filter / selection → aAct and aSize (only when something changed)
    const s = seen.current;
    if (b.live.rev !== s.liveRev || b.visRev !== s.visRev || b.hovered !== s.hovered) {
      s.liveRev = b.live.rev;
      s.visRev = b.visRev;
      s.hovered = b.hovered;
      const act = geom.getAttribute("aAct") as THREE.InstancedBufferAttribute;
      const size = geom.getAttribute("aSize") as THREE.InstancedBufferAttribute;
      const a = act.array as Float32Array;
      const sz = size.array as Float32Array;
      const sel = b.selected ? model.byId.get(b.selected) : undefined;
      const neigh = new Set(sel !== undefined ? model.neighbors[sel] : []);
      const hov = b.hovered ? model.byId.get(b.hovered) : undefined;
      const inFlight = b.view === "activity" ? activeIds(b.live) : null;
      for (const n of model.nodes) {
        const i = n.index;
        const live = b.live.nodes[n.id];
        const code = live ? STATUS_CODE[live.status] : STATUS_CODE.none;
        let emph = live ? (live.status === "ok" ? 0.5 : 1) : 0;
        if (inFlight?.has(n.id)) emph = Math.max(emph, 0.6);
        if (i === sel || i === hov) emph = 1;
        else if (neigh.has(i)) emph = Math.max(emph, 0.55);
        else if (sel !== undefined) emph = Math.min(emph, 0.15);
        const prevCode = a[i * 4 + 2] ?? 0;
        a[i * 4] = emph;
        if (live && (prevCode !== code || (a[i * 4 + 1] ?? 0) < live.since / 1000 - 0.05)) a[i * 4 + 1] = live.since / 1000;
        a[i * 4 + 2] = code;
        sz[i] = nodeVisible(b, n) ? nodeSize(n) : 0;
      }
      act.needsUpdate = true;
      size.needsUpdate = true;
    }
  });

  return <mesh geometry={geom} material={mat} frustumCulled={false} renderOrder={2} />;
}

/** Set by Nodes each frame: whether any node is still on its way (edges only rebuild their curves then). */
let moving = true;

// ------------------------------------------------------------------ edges
const SEG = 4; // each relation is a short curved polyline – through the depth in the brain view
/** Relations of the previous graph – a relation that was not in it grows from its source when it appears. */
let previousEdges: Set<string> | null = null;
const grownGeometries = new WeakSet<THREE.BufferGeometry>();
const edgeKey = (a: string, b: string, rel: string) => (a < b ? `${a}|${b}|${rel}` : `${b}|${a}|${rel}`);

function Edges({ model }: { model: GraphModel }) {
  const n = model.edges.length;
  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    const verts = n * SEG * 2;
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(verts * 3), 3).setUsage(THREE.DynamicDrawUsage));
    const along = new Float32Array(verts);
    const base = new Float32Array(verts);
    const usage = (i: number) => {
      const c = model.nodes[i]?.meta.access_count;
      return typeof c === "number" ? Math.min(1, Math.log1p(c) / 3) : 0;
    };
    model.edges.forEach((e, i) => {
      // frequently used memories are more strongly wired
      const b0 = e.relation === "ähnlich" ? 0.035 : e.relation === "gehört zu" || e.relation === "kann" ? 0.03 : 0.07 + 0.05 * Math.min(1, e.weight);
      const b = b0 + 0.05 * Math.max(usage(e.source), usage(e.target));
      for (let k = 0; k < SEG; k += 1) {
        const v = (i * SEG + k) * 2;
        along[v] = k / SEG;
        along[v + 1] = (k + 1) / SEG;
        base[v] = b;
        base[v + 1] = b;
      }
    });
    g.setAttribute("aAlong", new THREE.BufferAttribute(along, 1));
    g.setAttribute("aBase", new THREE.BufferAttribute(base, 1));
    g.setAttribute("aBorn", new THREE.BufferAttribute(new Float32Array(verts), 1));
    g.setAttribute("aEdge", new THREE.BufferAttribute(new Float32Array(verts * 2), 2).setUsage(THREE.DynamicDrawUsage));
    g.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 4000);
    return g;
  }, [model, n]);
  useEffect(() => () => geom.dispose(), [geom]);
  // new relations (a new memory, a new link) grow from their source: compared with the previous graph
  useEffect(() => {
    if (grownGeometries.has(geom)) return;
    grownGeometries.add(geom);
    const now = performance.now() / 1000;
    const keys = new Set<string>();
    const born = geom.getAttribute("aBorn") as THREE.BufferAttribute;
    const arr = born.array as Float32Array;
    model.edges.forEach((e, i) => {
      const src = model.nodes[e.source]!;
      const tgt = model.nodes[e.target]!;
      const key = edgeKey(src.id, tgt.id, e.relation);
      keys.add(key);
      if (previousEdges === null || previousEdges.has(key)) return;
      const t = Math.max(src.bornAt, tgt.bornAt, fx.grow.get(src.id) ?? 0, fx.grow.get(tgt.id) ?? 0, now - 0.2);
      arr.fill(t, i * SEG * 2, (i + 1) * SEG * 2);
    });
    previousEdges = keys;
    born.needsUpdate = true;
  }, [geom, model]);
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
          uDim: { value: 1 },
        },
      }),
    [],
  );
  useEffect(() => () => mat.dispose(), [mat]);
  const seen = useRef({ liveRev: -1, visRev: -1, hovered: null as string | null, brain: -1, frames: 0 });
  useFrame(() => {
    mat.uniforms.uTime!.value = performance.now() / 1000;
    const brain = brainHandle.brainness;
    // relations stay quiet in the brain (anatomy first) and in the live view (only active paths matter)
    const quiet = useBrain.getState().view === "activity" ? 0.85 : 0;
    mat.uniforms.uDim!.value += (Math.max(brain, quiet) - mat.uniforms.uDim!.value) * 0.08;
    const s = seen.current;
    const pos = geom.getAttribute("position") as THREE.BufferAttribute;
    const arr = pos.array as Float32Array;
    const p = brainHandle.positions;
    if (p.length >= model.nodes.length * 3 && (moving || brain !== s.brain || s.frames < 3)) {
      s.brain = brain;
      s.frames += 1;
      const pull = 1 - 0.32 * brain; // control point pulled towards the centre: the curve runs through the depth
      for (let i = 0; i < n; i += 1) {
        const e = model.edges[i]!;
        const ax = p[e.source * 3]!;
        const ay = p[e.source * 3 + 1]!;
        const az = p[e.source * 3 + 2]!;
        const bx = p[e.target * 3]!;
        const by = p[e.target * 3 + 1]!;
        const bz = p[e.target * 3 + 2]!;
        const cx = ((ax + bx) / 2) * pull;
        const cy = ((ay + by) / 2) * pull;
        const cz = ((az + bz) / 2) * pull;
        let px = ax;
        let py = ay;
        let pz = az;
        for (let k = 1; k <= SEG; k += 1) {
          const t = k / SEG;
          const u = 1 - t;
          const qx = u * u * ax + 2 * u * t * cx + t * t * bx;
          const qy = u * u * ay + 2 * u * t * cy + t * t * by;
          const qz = u * u * az + 2 * u * t * cz + t * t * bz;
          const v = (i * SEG + k - 1) * 6;
          arr[v] = px;
          arr[v + 1] = py;
          arr[v + 2] = pz;
          arr[v + 3] = qx;
          arr[v + 4] = qy;
          arr[v + 5] = qz;
          px = qx;
          py = qy;
          pz = qz;
        }
      }
      pos.needsUpdate = true;
    }
    const b = useBrain.getState();
    if (b.live.rev === s.liveRev && b.visRev === s.visRev && b.hovered === s.hovered) return;
    s.liveRev = b.live.rev;
    s.visRev = b.visRev;
    s.hovered = b.hovered;
    // edges along the data paths of running work carry a moving signal; edges of the selection are emphasised
    const flow = new Map<string, number>();
    const mark = (a: number, c: number, v: number) => {
      const key = a < c ? `${a}-${c}` : `${c}-${a}`;
      flow.set(key, Math.max(flow.get(key) ?? 0, v));
    };
    for (const pr of Object.values(b.live.processes)) {
      const ids = pr.path.map((id) => model.byId.get(id)).filter((x): x is number => x !== undefined);
      const strength = pr.until === undefined ? 1 : 0.55;
      for (let k = 0; k + 1 < ids.length; k += 1) mark(ids[k]!, ids[k + 1]!, strength);
    }
    const sel = b.selected ? model.byId.get(b.selected) : undefined;
    const hov = b.hovered ? model.byId.get(b.hovered) : undefined;
    const ed = geom.getAttribute("aEdge") as THREE.BufferAttribute;
    const ea = ed.array as Float32Array;
    for (let i = 0; i < n; i += 1) {
      const e = model.edges[i]!;
      const hiddenEdge = !nodeVisible(b, model.nodes[e.source]!) || !nodeVisible(b, model.nodes[e.target]!);
      const key = e.source < e.target ? `${e.source}-${e.target}` : `${e.target}-${e.source}`;
      const f = flow.get(key) ?? 0;
      let emph = f > 0 ? 0.8 : 0;
      if (e.source === sel || e.target === sel || e.source === hov || e.target === hov) emph = Math.max(emph, 0.7);
      if (hiddenEdge) emph = -1;
      for (let k = 0; k < SEG * 2; k += 1) {
        const v = (i * SEG * 2 + k) * 2;
        ea[v] = emph;
        ea[v + 1] = hiddenEdge ? 0 : f;
      }
    }
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
      const b = useBrain.getState();
      ref.current.visible = ji !== undefined && nodeVisible(b, model!.nodes[ji]!);
      if (ji !== undefined && p.length > ji * 3 + 2) ref.current.position.set(p[ji * 3]!, p[ji * 3 + 1]!, p[ji * 3 + 2]!);
      ref.current.quaternion.copy(camera.quaternion);
      ref.current.scale.setScalar(1 - 0.5 * brainHandle.brainness); // deep in the brain it sits in the thalamus

    }
  });
  return (
    <mesh ref={ref} material={mat} renderOrder={3}>
      <planeGeometry args={[78, 78]} />
    </mesh>
  );
}

// ------------------------------------------------------------------ camera: focus requests, idle drift, picking
/** Viewing direction of the overview per view: the brain from front-left (language areas and lobes readable),
 * the live flow straight on (it reads left → right). */
const VIEW_DIR: Record<ViewMode, Vec3> = {
  brain: [-0.9, 0.24, 0.36],
  network: [0, 0.18, 1],
  activity: [0, 0.05, 1],
  explorer: [0.12, 0.2, 1],
};

/** Distance that frames the current view in the part of the window the chat dock leaves free. */
function overviewDistance(model: GraphModel, camera: THREE.Camera, height: number) {
  const b = useBrain.getState();
  const tgt = brainHandle.target.length === model.nodes.length * 3 ? brainHandle.target : brainHandle.positions;
  const visible = model.nodes.filter((n) => nodeVisible(b, n)).map((n) => n.index);
  const free = freeFraction(b.occludedBottom, height);
  const cam = camera as THREE.PerspectiveCamera;
  if (b.view === "activity") {
    // the flow is wide: fit its box, not a sphere around it
    const lo = [Infinity, Infinity];
    const hi = [-Infinity, -Infinity];
    for (const i of visible) {
      lo[0] = Math.min(lo[0]!, tgt[i * 3]!);
      hi[0] = Math.max(hi[0]!, tgt[i * 3]!);
      lo[1] = Math.min(lo[1]!, tgt[i * 3 + 1]!);
      hi[1] = Math.max(hi[1]!, tgt[i * 3 + 1]!);
    }
    if (!visible.length) return { sphere: { center: [0, 0, 0] as Vec3, radius: 300 }, distance: 1000 };
    const center: Vec3 = [(lo[0]! + hi[0]!) / 2, (lo[1]! + hi[1]!) / 2 + 20, 0];
    const freeW = 1 - Math.min(b.occludedLeft, brainHandle.size.width * 0.45) / Math.max(1, brainHandle.size.width);
    const distance = fitBoxDistance((hi[0]! - lo[0]!) / 2 + 60, (hi[1]! - lo[1]!) / 2 + 70, cam.fov, cam.aspect * freeW, 0.92 * free);
    return { sphere: { center, radius: (hi[0]! - lo[0]!) / 2 }, distance };
  }
  const sphere = b.view === "brain" ? { center: [0, -14, 0] as Vec3, radius: 168 } : boundingSphere(tgt, visible.length ? visible : model.nodes.map((n) => n.index), 0.85);
  // fit height and width: on a portrait phone screen the width decides
  const freeW = 1 - Math.min(b.occludedLeft, brainHandle.size.width * 0.45) / Math.max(1, brainHandle.size.width);
  const distance = Math.max(
    fitDistance(sphere.radius, cam.fov, 1.05 * free),
    fitBoxDistance(sphere.radius * 1.05, sphere.radius * 0.8, cam.fov, cam.aspect * freeW, 0.98),
  );
  return { sphere, distance };
}

function CameraRig({ model, panelOpen }: { model: GraphModel; panelOpen: boolean }) {
  const controls = useRef<CameraControls>(null);
  const { camera, size, gl } = useThree();
  const lastFocus = useRef(0);
  const lastRecall = useRef(0);
  /** when the last overview was framed – a later touch means Sir navigates himself and the zoom stays his */
  const overviewAt = useRef(-1);
  brainHandle.camera = camera as THREE.PerspectiveCamera;
  brainHandle.size = { width: size.width, height: size.height };
  const height = size.height;

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
        const frame = () => {
          const { sphere, distance: d } = overviewDistance(model, camera, height);
          overviewAt.current = performance.now();
          const [dx, dy, dz] = VIEW_DIR[useBrain.getState().view];
          const l = Math.hypot(dx, dy, dz);
          void c.setLookAt(sphere.center[0] + (dx / l) * d, sphere.center[1] + (dy / l) * d, sphere.center[2] + (dz / l) * d, ...sphere.center, true);
        };
        // after a view switch the new targets are set by the next frame – frame those, not the old view
        if (s.view !== prev.view) setTimeout(frame, 80);
        else frame();
        return;
      }
      overviewAt.current = -1;
      const ids = s.focus.kind === "node" ? [s.focus.id] : s.focus.ids;
      const idx = ids.map((id) => model.byId.get(id)).filter((x): x is number => x !== undefined);
      if (!idx.length) return;
      const sphere = boundingSphere(p, idx);
      const single = idx.length === 1;
      const n = single ? model.nodes[idx[0]!] : undefined;
      const free = freeFraction(s.occludedBottom, height);
      const dist = single ? Math.max(90, nodeSize(n!) * 16) : fitDistance(sphere.radius, fov, 0.75 * free);
      const cam: Vec3 = [camera.position.x, camera.position.y, camera.position.z];
      const pose = focusPose(cam, sphere.center, dist, panelOpen ? 0.22 : 0);
      void c.setLookAt(...pose.position, ...pose.lookAt, true);
    });
  }, [camera, model, panelOpen, height]);

  // the chat dock grew or shrank: while the overview is untouched, re-fit its zoom to the new free space
  // (direction and idle rotation stay – only the distance changes)
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const unsub = useBrain.subscribe((s, prev) => {
      if (s.occludedBottom === prev.occludedBottom) return;
      clearTimeout(timer);
      timer = setTimeout(() => {
        const c = controls.current;
        const b = useBrain.getState();
        if (!c || b.selected || overviewAt.current < 0 || b.lastInteraction > overviewAt.current) return;
        const { distance } = overviewDistance(model, camera, height);
        void c.dollyTo(distance * Math.hypot(1, 0.18), true);
      }, 250);
    });
    return () => {
      clearTimeout(timer);
      unsub();
    };
  }, [camera, model, height]);

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
        (k) => nodeVisible(b, model.nodes[k]!),
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
    // the live flow and the explorer stay still – they are read, not admired
    const idle = now - b.lastInteraction > 12_000 && !b.selected && (b.view === "brain" || b.view === "network");
    // calm idle rotation; stops the moment Sir touches the brain
    if (idle && !c.active) void c.rotate(dt * (b.view === "brain" ? 0.035 : 0.025), 0, false);
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

// ------------------------------------------------------------------ safe area above the chat dock
/**
 * The canvas always fills the window; the chat dock floats over its lower part. Instead of resizing the canvas
 * (re-allocating the drawing buffer while the dock animates), the projection is shifted up by half the covered
 * height – no distortion, and labels/picking follow because they use the same projection matrix. Eased, so the
 * brain glides with the dock.
 */
function SafeArea() {
  const shift = useRef({ x: 0, y: 0 });
  const applied = useRef({ w: 0, h: 0, x: 0, y: 0 });
  useFrame(({ camera, size }, dt) => {
    const b = useBrain.getState();
    const ty = viewShift(b.occludedBottom, size.height);
    const tx = Math.min(b.occludedLeft, size.width * 0.45) / 2; // a side panel moves the centre to the right
    const s = shift.current;
    const k = Math.min(1, dt * 6);
    s.y += (ty - s.y) * k;
    s.x += (tx - s.x) * k;
    if (Math.abs(ty - s.y) < 0.5) s.y = ty;
    if (Math.abs(tx - s.x) < 0.5) s.x = tx;
    const y = Math.round(s.y * 2) / 2;
    const x = Math.round(s.x * 2) / 2;
    const a = applied.current;
    if (a.y === y && a.x === x && a.w === size.width && a.h === size.height) return;
    applied.current = { w: size.width, h: size.height, x, y };
    const cam = camera as THREE.PerspectiveCamera;
    if (y === 0 && x === 0) cam.clearViewOffset();
    else cam.setViewOffset(size.width, size.height, -x, y, size.width, size.height);
  });
  return null;
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
  // adaptive quality: start at what the device can do, step down when frames drop, back up when they recover
  const maxTier = useMemo<Tier>(() => detectTier(deviceInfo()), []);
  const [tier, setTier] = useState<Tier>(maxTier);
  const quality = QUALITY[tier];
  const recording = useApp((s) => s.voice.state === "recording");
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
      flat
      linear
      dpr={Math.min(quality.dprMax, window.devicePixelRatio || 1)}
      camera={{ position: [-760, 260, 380], fov: 42, near: 1, far: 6000 }}
      gl={{ antialias: true, alpha: false, powerPreference: "high-performance" }}
      onCreated={({ gl }) => gl.setClearColor(new THREE.Color("#030608"), 1)}
      aria-label="Jarvis-Gehirn"
    >
      <PerformanceMonitor
        flipflops={4}
        onDecline={() => setTier((t) => stepTier(t, -1, maxTier))}
        onIncline={() => setTier((t) => stepTier(t, 1, maxTier))}
        onFallback={() => setTier(0)}
      />
      <FrameLimiter />
      <SafeArea />
      <FxDriver />
      <Dust count={quality.dust} />
      <Anatomy quality={quality} />
      {model && (
        <>
          <Nodes key={`n-${model.version}-${model.nodes.length}`} model={model} atlas={atlas} target={target} />
          <Edges key={`e-${model.version}-${model.edges.length}`} model={model} />
          <CameraRig model={model} panelOpen={panelOpen} />
        </>
      )}
      <Impulses />
      <Core />
      {quality.bloom && !recording && <Bloom />}
    </Canvas>
  );
}
