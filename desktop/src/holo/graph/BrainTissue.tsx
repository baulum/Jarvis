// The anatomical layer of the brain view: translucent cortex, neurons and synapses, plus the driver that turns
// queued effects (fx.ts) into the shared glow/wave uniforms once per frame.
import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { buildSubstrate, buildSurface } from "./anatomy";
import {
  MAX_GLOW,
  MAX_WAVES,
  neuronFragment,
  neuronVertex,
  shellFragment,
  shellVertex,
  synapseFragment,
  synapseVertex,
} from "./anatomyShaders";
import { fx, GROUPS, groupColor, heldGlow, stepFx, TONE_COLOR } from "./fx";
import type { Quality } from "./quality";
import { anchorPosition, brainHandle, GLOW_SOURCE, refreshCentroids, sceneUniforms } from "./scene";
import { useBrain } from "../store";
import { rand01 } from "./model";

/** Fills the shared uniforms from the effect queue and the running processes. Runs before everything else. */
export function FxDriver() {
  const held = useRef({ rev: -1, value: heldGlow(useBrain.getState().live) });
  const lastCentroids = useRef(0);
  const tmp = useMemo(() => new THREE.Vector3(), []);
  useFrame(({ camera, gl }, dt) => {
    const t = performance.now() / 1000;
    const b = useBrain.getState();
    stepFx(t, dt);
    // view morph: anatomical (1) ↔ free layouts (0)
    const target = b.view === "brain" ? 1 : 0;
    brainHandle.brainness += (target - brainHandle.brainness) * Math.min(1, dt * 2.4);
    if (Math.abs(target - brainHandle.brainness) < 0.002) brainHandle.brainness = target;
    if (t - lastCentroids.current > 0.4) {
      lastCentroids.current = t;
      refreshCentroids();
    }
    const u = sceneUniforms;
    u.uTime.value = t;
    u.uOpacity.value = brainHandle.brainness;
    u.uPixelRatio.value = gl.getPixelRatio();
    u.uFocus.value = camera.position.length();
    const offline = b.live.sync === "offline" ? 1 : 0;
    u.uOffline.value += (offline - u.uOffline.value) * Math.min(1, dt * 3);
    if (held.current.rev !== b.live.rev) held.current = { rev: b.live.rev, value: heldGlow(b.live) };
    GROUPS.forEach((g, i) => {
      if (i >= MAX_GLOW) return;
      const breathe = 0.82 + 0.18 * Math.sin(t * 2.1 + i);
      const level = Math.max(fx.glow[g], held.current.value[g] * breathe) + (b.regionHover === g ? 0.4 : 0);
      const [x, y, z, r] = GLOW_SOURCE[g];
      const [cr, cg, cb] = groupColor(g, fx.glow[g] > held.current.value[g] ? fx.glowTone[g] : "signal");
      u.uGlowPos.value[i]!.set(x, y, z, r);
      u.uGlowCol.value[i]!.set(cr, cg, cb, Math.min(1.2, level));
    });
    for (let i = 0; i < MAX_WAVES; i += 1) {
      const w = fx.waves[i];
      if (!w) {
        u.uWaveCol.value[i]!.w = 0;
        continue;
      }
      anchorPosition(w.at, tmp);
      const c = TONE_COLOR[w.tone];
      u.uWavePos.value[i]!.set(tmp.x, tmp.y, tmp.z, w.born);
      u.uWaveCol.value[i]!.set(c[0], c[1], c[2], w.strength);
    }
  }, -1);
  return null;
}

function useMaterial(vertexShader: string, fragmentShader: string, extra: Partial<THREE.ShaderMaterialParameters> = {}) {
  const mat = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader,
        fragmentShader,
        uniforms: sceneUniforms,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        ...extra,
      }),
    [vertexShader, fragmentShader, extra],
  );
  useEffect(() => () => mat.dispose(), [mat]);
  return mat;
}

const SHELL_EXTRA = { side: THREE.FrontSide };
const NONE = {};

/** Cortex surface; hidden (and not drawn) outside the brain view. */
function Shell({ res }: { res: number }) {
  const geom = useMemo(() => {
    const s = buildSurface(res);
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(s.positions, 3));
    g.setAttribute("normal", new THREE.BufferAttribute(s.normals, 3));
    g.setIndex(new THREE.BufferAttribute(s.indices, 1));
    g.computeBoundingSphere();
    return g;
  }, [res]);
  useEffect(() => () => geom.dispose(), [geom]);
  const mat = useMaterial(shellVertex, shellFragment, SHELL_EXTRA);
  const ref = useRef<THREE.Mesh>(null);
  useFrame(() => {
    if (ref.current) ref.current.visible = brainHandle.brainness > 0.01;
  });
  return <mesh ref={ref} geometry={geom} material={mat} renderOrder={0} />;
}

function Substrate({ neurons, links }: { neurons: number; links: number }) {
  const { points, lines } = useMemo(() => {
    const s = buildSubstrate(neurons, 7, links);
    const n = s.depth.length;
    const seed = new Float32Array(n);
    for (let i = 0; i < n; i += 1) seed[i] = rand01(`n${i}`, 5);
    const p = new THREE.BufferGeometry();
    p.setAttribute("position", new THREE.BufferAttribute(s.positions, 3));
    p.setAttribute("aDepth", new THREE.BufferAttribute(s.depth, 1));
    p.setAttribute("aSeed", new THREE.BufferAttribute(seed, 1));
    p.computeBoundingSphere();
    const m = s.synapses.length;
    const lp = new Float32Array(m * 3);
    const ls = new Float32Array(m);
    for (let k = 0; k < m; k += 1) {
      const i = s.synapses[k]!;
      lp.set(s.positions.subarray(i * 3, i * 3 + 3), k * 3);
      ls[k] = seed[i]!;
    }
    const l = new THREE.BufferGeometry();
    l.setAttribute("position", new THREE.BufferAttribute(lp, 3));
    l.setAttribute("aSeed", new THREE.BufferAttribute(ls, 1));
    l.computeBoundingSphere();
    return { points: p, lines: l };
  }, [neurons, links]);
  useEffect(
    () => () => {
      points.dispose();
      lines.dispose();
    },
    [points, lines],
  );
  const pointMat = useMaterial(neuronVertex, neuronFragment, NONE);
  const lineMat = useMaterial(synapseVertex, synapseFragment, NONE);
  const group = useRef<THREE.Group>(null);
  useFrame(() => {
    if (group.current) group.current.visible = brainHandle.brainness > 0.01;
  });
  return (
    <group ref={group}>
      <lineSegments geometry={lines} material={lineMat} renderOrder={0} />
      <points geometry={points} material={pointMat} renderOrder={1} />
    </group>
  );
}

export function Anatomy({ quality }: { quality: Quality }) {
  return (
    <>
      <Shell res={quality.shellRes} />
      <Substrate neurons={quality.neurons} links={quality.synapseLinks} />
    </>
  );
}
