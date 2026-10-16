// GLSL for the brain. Kept deliberately cheap: one instanced draw for all nodes, one line draw for all edges,
// additive blending, no post-processing (voice recognition shares the GPU on an M1).

/** Status codes written into aAct.z (must match STATUS_CODE in BrainCanvas). */
export const STATUS_CODE = { none: 0, active: 1, ok: 2, error: 3, recalled: 4, new: 5, approval: 6, pulse: 7 } as const;

export const nodeVertex = /* glsl */ `
uniform float uTime;
uniform float uIconDist;
uniform float uScale;
attribute vec3 aOffset;
attribute float aSize;
attribute vec3 aColor;
attribute float aIcon;
attribute vec4 aAct;      // x: emphasis 0..1, y: pulse start (s), z: status code, w: born at (s)
varying vec2 vUv;
varying vec3 vColor;
varying float vIcon;
varying float vIconAlpha;
varying vec4 vAct;
varying float vFog;
void main() {
  vec4 mv = modelViewMatrix * vec4(aOffset, 1.0);
  float dist = -mv.z;
  float born = aAct.w > 0.0 ? clamp((uTime - aAct.w) / 1.4, 0.0, 1.0) : 1.0;
  float grow = born * born * (3.0 - 2.0 * born);
  float s = aSize * uScale * (0.2 + 0.8 * grow) * (1.0 + 0.28 * aAct.x);
  s = max(s, dist * 0.0055) * step(0.001, aSize);   // minimum on-screen size; size 0 = filtered out
  mv.xy += position.xy * s * 2.6;                     // quad leaves room for the pulse ring
  gl_Position = projectionMatrix * mv;
  vUv = uv;
  vColor = aColor;
  vIcon = aIcon;
  vAct = aAct;
  vIconAlpha = smoothstep(uIconDist * 1.7, uIconDist * 0.75, dist);
  vFog = smoothstep(2400.0, 260.0, dist);
}
`;

export const nodeFragment = /* glsl */ `
uniform sampler2D uAtlas;
uniform float uTime;
uniform vec3 uStateTint;
varying vec2 vUv;
varying vec3 vColor;
varying float vIcon;
varying float vIconAlpha;
varying vec4 vAct;
varying float vFog;

vec3 statusColor(float code, vec3 base) {
  if (code > 5.5 && code < 6.5) return vec3(0.886, 0.631, 0.298);   // approval: amber
  if (code > 2.5 && code < 3.5) return vec3(0.898, 0.345, 0.31);    // error
  if (code > 1.5 && code < 2.5) return mix(base, vec3(0.62, 0.95, 0.85), 0.6);  // ok
  if (code > 0.5) return mix(base, vec3(0.92, 0.98, 1.0), 0.65);   // active / recalled / new
  return base;
}

void main() {
  vec2 p = vUv * 2.0 - 1.0;
  float r = length(p) * 1.3;                 // node body lives in r <= 1
  vec3 col = statusColor(vAct.z, vColor);
  float emph = vAct.x;

  // far LOD: a soft point of light; near LOD: thin ring + glyph
  float core = exp(-r * r * 5.5);
  float ring = smoothstep(0.06, 0.0, abs(r - 0.92)) * 0.42;
  vec2 cell = vec2(mod(vIcon, 4.0), floor(vIcon / 4.0));
  vec2 g = vec2(p.x, -p.y) * 1.3 * 0.5;      // [-0.5,0.5] across the body
  float inside = step(abs(g.x), 0.5) * step(abs(g.y), 0.5);
  vec2 iuv = (cell + vec2(0.5) + g * 0.75) / 4.0;
  float glyph = texture2D(uAtlas, vec2(iuv.x, 1.0 - iuv.y)).a * inside;
  float disc = smoothstep(0.98, 0.9, r) * 0.08;   // faint fill so the glyph reads against edges

  float far = core * (0.8 + 0.4 * emph);
  float near = glyph * 0.72 + ring + disc;
  float a = mix(far, near, vIconAlpha);

  // pulse: a ring that travels outwards once per event (memory access, tool call, new node)
  float t = uTime - vAct.y;
  float pulse = 0.0;
  if (vAct.y > 0.0 && t >= 0.0 && t < 1.6) {
    float pr = 0.9 + t * 0.9;
    pulse = smoothstep(0.08, 0.0, abs(r - pr)) * (1.0 - t / 1.6);
  }
  // waiting for approval: slow breathing, so it stays noticeable without flashing
  if (vAct.z > 5.5 && vAct.z < 6.5) a *= 0.75 + 0.25 * sin(uTime * 2.4);

  float alpha = (a * (0.45 + 0.55 * max(emph, 0.35)) + pulse) * vFog;
  if (alpha < 0.004) discard;
  gl_FragColor = vec4(col * (1.0 + emph * 0.4) + uStateTint * 0.04, alpha);
}
`;

export const edgeVertex = /* glsl */ `
attribute float aAlong;    // 0 at source, 1 at target
attribute vec2 aEdge;      // x: emphasis 0..1 (selection/activity), y: flow (1 = signal travelling)
attribute float aBase;     // resting opacity (relation weight)
varying float vAlong;
varying vec2 vEdge;
varying float vBase;
varying float vFog;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_Position = projectionMatrix * mv;
  vAlong = aAlong;
  vEdge = aEdge;
  vBase = aBase;
  vFog = smoothstep(2400.0, 200.0, -mv.z);
}
`;

export const edgeFragment = /* glsl */ `
uniform float uTime;
uniform vec3 uColor;
uniform vec3 uFlowColor;
varying float vAlong;
varying vec2 vEdge;
varying float vBase;
varying float vFog;
void main() {
  float a = vBase + vEdge.x * 0.42;
  float flow = 0.0;
  if (vEdge.y > 0.0) {
    float head = fract(uTime * 0.85);
    float d = vAlong - head;
    flow = exp(-d * d * 90.0) * vEdge.y;   // a packet moving source → target
  }
  vec3 col = mix(uColor, uFlowColor, clamp(vEdge.x + flow, 0.0, 1.0));
  float alpha = (a + flow * 0.9) * vFog;
  if (alpha < 0.003) discard;
  gl_FragColor = vec4(col, alpha);
}
`;

export const dustVertex = /* glsl */ `
uniform float uTime;
uniform float uPixelRatio;
attribute float aSeed;
varying float vA;
void main() {
  vec3 p = position;
  p.y += sin(uTime * 0.05 + aSeed * 6.28) * 6.0;
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  gl_Position = projectionMatrix * mv;
  gl_PointSize = (1.2 + aSeed * 1.4) * uPixelRatio * (300.0 / max(80.0, -mv.z));
  vA = 0.12 + 0.18 * aSeed;
}
`;

export const dustFragment = /* glsl */ `
varying float vA;
void main() {
  vec2 c = gl_PointCoord * 2.0 - 1.0;
  float a = exp(-dot(c, c) * 3.0) * vA;
  gl_FragColor = vec4(0.62, 0.82, 0.9, a);
}
`;

export const coreVertex = /* glsl */ `
varying vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

/** J.A.R.V.I.S. at the centre: rings that follow the microphone / voice output level and the state. */
export const coreFragment = /* glsl */ `
uniform float uTime;
uniform float uIn;
uniform float uOut;
uniform float uSpin;
uniform vec3 uTint;
varying vec2 vUv;
void main() {
  vec2 p = vUv * 2.0 - 1.0;
  float r = length(p);
  float ang = atan(p.y, p.x);
  float level = max(uIn, uOut);
  float r1 = 0.52 + level * 0.12;
  float ring1 = smoothstep(0.02, 0.0, abs(r - r1));
  // segmented outer ring rotates with activity (thinking / tools), stands still when idle
  float seg = step(0.5, fract((ang + uTime * uSpin) * 12.0 / 6.2831));
  float ring2 = smoothstep(0.012, 0.0, abs(r - 0.7)) * seg * 0.6;
  // voice: a waveform-like modulation of the inner ring while listening or speaking
  float wave = smoothstep(0.03, 0.0, abs(r - (0.4 + 0.05 * level * sin(ang * 9.0 + uTime * 7.0)))) * level;
  float a = (ring1 * 0.55 + ring2 + wave * 0.9) * smoothstep(1.0, 0.85, r);
  if (a < 0.004) discard;
  gl_FragColor = vec4(uTint, a);
}
`;
