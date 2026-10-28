// Camera framing math (pure): where to put the camera to look at a node or a whole cluster.

export type Vec3 = [number, number, number];

export interface Sphere {
  center: Vec3;
  radius: number;
}

/**
 * Bounding sphere of a set of positions (centroid + distance). ``quantile`` < 1 ignores outliers, so a few
 * far-away nodes do not shrink the whole brain to a dot.
 */
export function boundingSphere(positions: Float32Array, indices: number[], quantile = 1): Sphere {
  if (!indices.length) return { center: [0, 0, 0], radius: 100 };
  let cx = 0;
  let cy = 0;
  let cz = 0;
  for (const i of indices) {
    cx += positions[i * 3] ?? 0;
    cy += positions[i * 3 + 1] ?? 0;
    cz += positions[i * 3 + 2] ?? 0;
  }
  cx /= indices.length;
  cy /= indices.length;
  cz /= indices.length;
  const dists = indices.map((i) =>
    Math.hypot((positions[i * 3] ?? 0) - cx, (positions[i * 3 + 1] ?? 0) - cy, (positions[i * 3 + 2] ?? 0) - cz),
  );
  dists.sort((a, b) => a - b);
  const r = dists[Math.min(dists.length - 1, Math.floor((dists.length - 1) * quantile))] ?? 0;
  return { center: [cx, cy, cz], radius: Math.max(r, 20) };
}

/** Distance at which a sphere of ``radius`` fills ``fill`` of the vertical field of view. */
export function fitDistance(radius: number, fovDeg: number, fill = 0.7): number {
  const half = (fovDeg * Math.PI) / 360;
  return radius / Math.sin(half) / fill;
}

/** At most this share of the window counts as covered – the brain never gets squeezed into a sliver. */
const MAX_OCCLUDED = 0.6;

/** Share of the window height left free for the brain when ``occluded`` px at the bottom are covered (chat dock). */
export function freeFraction(occluded: number, height: number): number {
  if (height <= 0) return 1;
  return 1 - Math.min(Math.max(occluded, 0), height * MAX_OCCLUDED) / height;
}

/**
 * Vertical projection offset (px, for ``camera.setViewOffset``) that moves the centre of the view into the middle
 * of the free part above the covered strip: the scene shifts up by half the covered height.
 */
export function viewShift(occluded: number, height: number): number {
  return ((1 - freeFraction(occluded, height)) * Math.max(height, 0)) / 2;
}

/**
 * Camera position for focusing ``target``: keep the current viewing direction (no disorienting jumps),
 * move to ``distance`` and shift the look-at point sideways so the target sits left of a detail panel.
 */
export function focusPose(
  camera: Vec3,
  target: Vec3,
  distance: number,
  panelShift = 0,
): { position: Vec3; lookAt: Vec3 } {
  let dx = camera[0] - target[0];
  let dy = camera[1] - target[1];
  let dz = camera[2] - target[2];
  const len = Math.hypot(dx, dy, dz) || 1;
  dx /= len;
  dy /= len;
  dz /= len;
  // right vector (world up × view direction)
  const rx = dz;
  const rz = -dx;
  const rl = Math.hypot(rx, rz) || 1;
  const sx = (rx / rl) * panelShift * distance;
  const sz = (rz / rl) * panelShift * distance;
  const lookAt: Vec3 = [target[0] + sx, target[1], target[2] + sz];
  return { position: [lookAt[0] + dx * distance, lookAt[1] + dy * distance, lookAt[2] + dz * distance], lookAt };
}

/** Projects world points to screen pixels and returns the nearest one under the cursor (CPU picking). */
export function pickNearest(
  positions: Float32Array,
  count: number,
  viewProj: ArrayLike<number>,
  width: number,
  height: number,
  mx: number,
  my: number,
  radiusPx: (i: number, w: number) => number,
  visible: (i: number) => boolean = () => true,
): number {
  let best = -1;
  let bestScore = Infinity;
  const m = viewProj;
  for (let i = 0; i < count; i += 1) {
    if (!visible(i)) continue;
    const x = positions[i * 3] ?? 0;
    const y = positions[i * 3 + 1] ?? 0;
    const z = positions[i * 3 + 2] ?? 0;
    const w = (m[3] ?? 0) * x + (m[7] ?? 0) * y + (m[11] ?? 0) * z + (m[15] ?? 1);
    if (w <= 0.01) continue; // behind the camera
    const sx = (((m[0] ?? 0) * x + (m[4] ?? 0) * y + (m[8] ?? 0) * z + (m[12] ?? 0)) / w + 1) * 0.5 * width;
    const sy = (1 - (((m[1] ?? 0) * x + (m[5] ?? 0) * y + (m[9] ?? 0) * z + (m[13] ?? 0)) / w + 1) * 0.5) * height;
    if (sx < -50 || sy < -50 || sx > width + 50 || sy > height + 50) continue; // frustum
    const d = Math.hypot(sx - mx, sy - my);
    const r = Math.max(6, radiusPx(i, w));
    if (d > r) continue;
    const score = d / r + w * 0.0005; // prefer centred and closer nodes
    if (score < bestScore) {
      bestScore = score;
      best = i;
    }
  }
  return best;
}

/**
 * Distance at which an axis-aligned box (half extents ``hw`` × ``hh`` facing the camera) fits the view – for wide
 * layouts such as the live flow, where a bounding sphere would leave most of the screen empty.
 */
export function fitBoxDistance(hw: number, hh: number, fovDeg: number, aspect: number, fill = 0.9): number {
  const t = Math.tan((fovDeg * Math.PI) / 360);
  return Math.max(hh / t, hw / (t * Math.max(aspect, 0.1))) / fill;
}
