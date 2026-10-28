// Electrical impulses travelling along real paths (request → J.A.R.V.I.S., J.A.R.V.I.S. → tool, tool → memory hit …).
// Positions are recomputed on the CPU each frame from the live node positions, so an impulse follows its nodes
// through a view change. Every impulse was queued by a real activity step (fx.ts).
import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo } from "react";
import * as THREE from "three";
import { impulseFragment, impulseVertex } from "./anatomyShaders";
import { fx, TONE_COLOR } from "./fx";
import { anchorPosition, brainHandle, sceneUniforms } from "./scene";

const TRAIL = 6;
const MAX = 160 * TRAIL;

const ease = (t: number) => t * t * (3 - 2 * t);

export function Impulses() {
  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(MAX * 3), 3).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute("aColor", new THREE.BufferAttribute(new Float32Array(MAX * 4), 4).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute("aSize", new THREE.BufferAttribute(new Float32Array(MAX), 1).setUsage(THREE.DynamicDrawUsage));
    g.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 5000);
    g.setDrawRange(0, 0);
    return g;
  }, []);
  useEffect(() => () => geom.dispose(), [geom]);
  const mat = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader: impulseVertex,
        fragmentShader: impulseFragment,
        uniforms: { uPixelRatio: sceneUniforms.uPixelRatio },
        transparent: true,
        depthWrite: false,
        depthTest: false, // a signal inside the brain stays visible through the cortex
        blending: THREE.AdditiveBlending,
      }),
    [],
  );
  useEffect(() => () => mat.dispose(), [mat]);

  const a = useMemo(() => new THREE.Vector3(), []);
  const b = useMemo(() => new THREE.Vector3(), []);
  const c = useMemo(() => new THREE.Vector3(), []);
  const p = useMemo(() => new THREE.Vector3(), []);

  useFrame(() => {
    const now = performance.now() / 1000;
    const pos = geom.getAttribute("position") as THREE.BufferAttribute;
    const col = geom.getAttribute("aColor") as THREE.BufferAttribute;
    const size = geom.getAttribute("aSize") as THREE.BufferAttribute;
    const P = pos.array as Float32Array;
    const C = col.array as Float32Array;
    const S = size.array as Float32Array;
    let n = 0;
    const brain = brainHandle.brainness;
    for (const imp of fx.impulses) {
      const age = now - imp.born;
      if (age < 0) continue;
      const segs = imp.path.length - 1;
      const u = age / imp.segment;
      const [r, g, bl] = TONE_COLOR[imp.tone];
      for (let k = 0; k < TRAIL && n < MAX; k += 1) {
        const uk = u - k * 0.05;
        if (uk < 0) break;
        let seg = Math.floor(uk);
        let t = uk - seg;
        let fade = 1;
        if (seg >= segs) {
          // arrived: a short flash at the destination
          seg = segs - 1;
          t = 1;
          fade = Math.max(0, 1 - (uk - segs) / 0.4);
          if (k > 0) break;
        }
        anchorPosition(imp.path[seg]!, a);
        anchorPosition(imp.path[seg + 1]!, b);
        // curved route: in the brain view through the depth (like a white-matter tract), else a gentle arc
        c.addVectors(a, b).multiplyScalar(0.5);
        const len = a.distanceTo(b);
        c.multiplyScalar(1 - 0.35 * brain);
        c.y += len * 0.12 * (1 - brain);
        const e = ease(t);
        const i1 = 1 - e;
        p.set(
          i1 * i1 * a.x + 2 * i1 * e * c.x + e * e * b.x,
          i1 * i1 * a.y + 2 * i1 * e * c.y + e * e * b.y,
          i1 * i1 * a.z + 2 * i1 * e * c.z + e * e * b.z,
        );
        P[n * 3] = p.x;
        P[n * 3 + 1] = p.y;
        P[n * 3 + 2] = p.z;
        const tail = 1 - k / TRAIL;
        C[n * 4] = r;
        C[n * 4 + 1] = g;
        C[n * 4 + 2] = bl;
        C[n * 4 + 3] = fade * tail * (k === 0 ? 1 : 0.55);
        S[n] = (k === 0 ? 5.2 : 3.6 * tail) * (seg >= segs - 1 && t === 1 ? 1 + (1 - fade) * 1.5 : 1);
        n += 1;
      }
    }
    geom.setDrawRange(0, n);
    if (n) {
      pos.needsUpdate = true;
      col.needsUpdate = true;
      size.needsUpdate = true;
    }
  });
  return <points geometry={geom} material={mat} frustumCulled={false} renderOrder={4} />;
}
