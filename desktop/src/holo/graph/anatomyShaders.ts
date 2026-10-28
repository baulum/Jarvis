// GLSL for the anatomical brain: translucent cortex with gyri and sulci, neurons, synapses, impulses.
// Activity reaches these shaders only through the glow/wave uniforms, which the render loop fills from real
// activity steps (see fx.ts) – the substrate itself never lights up on its own.

export const MAX_GLOW = 6;
export const MAX_WAVES = 6;

/** 3D simplex noise (Ashima Arts / Stefan Gustavson, MIT). */
const snoise = /* glsl */ `
vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 permute(vec4 x) { return mod289(((x * 34.0) + 1.0) * x); }
vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }
float snoise(vec3 v) {
  const vec2 C = vec2(1.0 / 6.0, 1.0 / 3.0);
  const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
  vec3 i = floor(v + dot(v, C.yyy));
  vec3 x0 = v - i + dot(i, C.xxx);
  vec3 g = step(x0.yzx, x0.xyz);
  vec3 l = 1.0 - g;
  vec3 i1 = min(g.xyz, l.zxy);
  vec3 i2 = max(g.xyz, l.zxy);
  vec3 x1 = x0 - i1 + C.xxx;
  vec3 x2 = x0 - i2 + C.yyy;
  vec3 x3 = x0 - D.yyy;
  i = mod289(i);
  vec4 p = permute(permute(permute(i.z + vec4(0.0, i1.z, i2.z, 1.0)) + i.y + vec4(0.0, i1.y, i2.y, 1.0)) + i.x + vec4(0.0, i1.x, i2.x, 1.0));
  float n_ = 0.142857142857;
  vec3 ns = n_ * D.wyz - D.xzx;
  vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
  vec4 x_ = floor(j * ns.z);
  vec4 y_ = floor(j - 7.0 * x_);
  vec4 x = x_ * ns.x + ns.yyyy;
  vec4 y = y_ * ns.x + ns.yyyy;
  vec4 h = 1.0 - abs(x) - abs(y);
  vec4 b0 = vec4(x.xy, y.xy);
  vec4 b1 = vec4(x.zw, y.zw);
  vec4 s0 = floor(b0) * 2.0 + 1.0;
  vec4 s1 = floor(b1) * 2.0 + 1.0;
  vec4 sh = -step(h, vec4(0.0));
  vec4 a0 = b0.xzyw + s0.xzyw * sh.xxyy;
  vec4 a1 = b1.xzyw + s1.xzyw * sh.zzww;
  vec3 p0 = vec3(a0.xy, h.x);
  vec3 p1 = vec3(a0.zw, h.y);
  vec3 p2 = vec3(a1.xy, h.z);
  vec3 p3 = vec3(a1.zw, h.w);
  vec4 norm = taylorInvSqrt(vec4(dot(p0, p0), dot(p1, p1), dot(p2, p2), dot(p3, p3)));
  p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
  vec4 m = max(0.6 - vec4(dot(x0, x0), dot(x1, x1), dot(x2, x2), dot(x3, x3)), 0.0);
  m = m * m;
  return 42.0 * dot(m * m, vec4(dot(p0, x0), dot(p1, x1), dot(p2, x2), dot(p3, x3)));
}
`;

/** Activity at a world position: region glow (gaussian) plus travelling wave fronts. */
const activity = /* glsl */ `
uniform vec4 uGlowPos[${MAX_GLOW}];   // xyz, radius
uniform vec4 uGlowCol[${MAX_GLOW}];   // rgb, intensity
uniform vec4 uWavePos[${MAX_WAVES}];  // xyz, start time (s)
uniform vec4 uWaveCol[${MAX_WAVES}];  // rgb, strength
vec3 activityAt(vec3 p, float t) {
  vec3 acc = vec3(0.0);
  for (int i = 0; i < ${MAX_GLOW}; i++) {
    float w = uGlowCol[i].w;
    if (w <= 0.001) continue;
    float d = distance(p, uGlowPos[i].xyz) / uGlowPos[i].w;
    acc += uGlowCol[i].rgb * w * exp(-d * d * 2.2);
  }
  for (int i = 0; i < ${MAX_WAVES}; i++) {
    float age = t - uWavePos[i].w;
    if (age < 0.0 || age > 1.6 || uWaveCol[i].w <= 0.001) continue;
    float r = age * 230.0;
    float d = distance(p, uWavePos[i].xyz) - r;
    acc += uWaveCol[i].rgb * uWaveCol[i].w * exp(-d * d / 260.0) * (1.0 - age / 1.6);
  }
  return acc;
}
`;

/** Height of the cortex folds at an (undisplaced) surface point: 1 on the crown of a gyrus, 0 deep in a sulcus.
 * The zero set of 3D noise on a surface draws the winding, branching lines typical of sulci. */
const folds = /* glsl */ `
float foldNoise(vec3 p) {
  return snoise(p * 0.047) + 0.42 * snoise(p * 0.104 + 5.0);
}
float foldHeight(float n) {
  return smoothstep(0.0, 0.13, abs(n));
}
`;

export const shellVertex = /* glsl */ `
uniform float uTime;
varying vec3 vWorld;
varying vec3 vObj;
varying vec3 vNormal;
${snoise}
${folds}
void main() {
  vec3 p = position;
  float h = foldHeight(foldNoise(p));
  p -= normal * (1.0 - h) * 6.5;                        // sulci sink in, the silhouette shows the folds
  p += normal * sin(uTime * 0.5 + p.z * 0.015) * 0.45;  // very slow breathing of the tissue
  vec4 w = modelMatrix * vec4(p, 1.0);
  vWorld = w.xyz;
  vObj = position;
  vNormal = normalize(mat3(modelMatrix) * normal);
  gl_Position = projectionMatrix * viewMatrix * w;
}
`;

export const shellFragment = /* glsl */ `
uniform float uTime;
uniform float uOpacity;
uniform float uOffline;
uniform vec3 uTint;
varying vec3 vWorld;
varying vec3 vObj;
varying vec3 vNormal;
${snoise}
${folds}
${activity}
// bump mapping from the fold height (Mikkelsen, surface gradient via screen-space derivatives)
vec3 perturb(vec3 N, vec3 p, float h, float scale) {
  vec3 dpdx = dFdx(p);
  vec3 dpdy = dFdy(p);
  vec3 r1 = cross(dpdy, N);
  vec3 r2 = cross(N, dpdx);
  float det = dot(dpdx, r1);
  vec3 grad = sign(det) * (dFdx(h) * scale * r1 + dFdy(h) * scale * r2);
  return normalize(abs(det) * N - grad);
}
void main() {
  vec3 N = normalize(vNormal);
  vec3 V = normalize(cameraPosition - vWorld);
  if (dot(N, V) < 0.0) N = -N;
  float h = foldHeight(foldNoise(vObj));
  vec3 B = perturb(N, vWorld, h, 16.0);
  float facing = max(dot(N, V), 0.0);
  float fres = pow(1.0 - facing, 3.0);
  // cinematic key light from above front-left, a cool rim from behind
  vec3 L = normalize(vec3(-0.45, 0.8, 0.4));
  float key = max(dot(B, L), 0.0);
  float spec = pow(max(dot(reflect(-L, B), V), 0.0), 18.0);
  float sulcus = 1.0 - h;
  vec3 col = uTint * (0.3 + 0.6 * fres) + vec3(0.78, 0.9, 1.0) * (key * 0.45 + spec * 0.4) * h;
  float a = 0.028 + 0.12 * fres + (0.07 * key + 0.07 * spec) * h;
  a *= 1.0 - 0.9 * sulcus;
  // the flat medial walls of the longitudinal fissure stay quiet – seen through the cortex they read as a second outline
  a *= 1.0 - 0.85 * smoothstep(16.0, 5.0, abs(vObj.x)) * abs(N.x);
  vec3 act = activityAt(vWorld, uTime);
  float e = length(act);
  col += act * (0.45 + 0.6 * h);
  a += min(0.32, e * (0.05 + 0.12 * fres + 0.08 * h));
  col = mix(col, vec3(dot(col, vec3(0.333))), uOffline * 0.8);
  a *= uOpacity * (1.0 - uOffline * 0.45);
  if (a < 0.002) discard;
  gl_FragColor = vec4(col, a);
}
`;

export const neuronVertex = /* glsl */ `
uniform float uTime;
uniform float uPixelRatio;
uniform float uOpacity;
uniform float uFocus;
uniform float uOffline;
uniform vec3 uTint;
attribute float aDepth;
attribute float aSeed;
varying float vA;
varying vec3 vCol;
${activity}
void main() {
  // organic micro-motion, a few units at most
  vec3 p = position + vec3(sin(uTime * 0.31 + aSeed * 41.0), cos(uTime * 0.27 + aSeed * 29.0), sin(uTime * 0.23 + aSeed * 17.0)) * 1.1;
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  float dist = -mv.z;
  vec3 act = activityAt(p, uTime);
  float e = min(1.4, length(act));
  // subtle depth of field: away from the focus distance a neuron gets softer and dimmer
  float blur = clamp(abs(dist - uFocus) / max(uFocus, 1.0) - 0.15, 0.0, 1.0);
  gl_PointSize = (1.5 + 2.4 * e + blur * 2.2) * uPixelRatio * (520.0 / max(60.0, dist));
  vA = (0.09 + 0.08 * (1.0 - aDepth) + 0.85 * e) * (1.0 - blur * 0.6) * uOpacity * (1.0 - uOffline * 0.5);
  vCol = mix(uTint, vec3(0.92, 0.98, 1.0), 0.2) * (1.0 - uOffline * 0.4) + act;
  gl_Position = projectionMatrix * mv;
}
`;

export const neuronFragment = /* glsl */ `
varying float vA;
varying vec3 vCol;
void main() {
  vec2 c = gl_PointCoord * 2.0 - 1.0;
  float r = dot(c, c);
  float a = (exp(-r * 5.0) + 0.25 * exp(-r * 1.4)) * vA;
  if (a < 0.003) discard;
  gl_FragColor = vec4(vCol, a);
}
`;

export const synapseVertex = /* glsl */ `
uniform float uTime;
uniform float uOpacity;
uniform float uOffline;
uniform vec3 uTint;
attribute float aSeed;
varying float vA;
varying vec3 vCol;
${activity}
void main() {
  vec3 p = position + vec3(sin(uTime * 0.31 + aSeed * 41.0), cos(uTime * 0.27 + aSeed * 29.0), sin(uTime * 0.23 + aSeed * 17.0)) * 1.1;
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  vec3 act = activityAt(p, uTime);
  vA = (0.03 + min(0.4, length(act) * 0.3)) * uOpacity * smoothstep(2200.0, 200.0, -mv.z) * (1.0 - uOffline * 0.5);
  vCol = uTint * 0.8 + act;
  gl_Position = projectionMatrix * mv;
}
`;

export const synapseFragment = /* glsl */ `
varying float vA;
varying vec3 vCol;
void main() {
  if (vA < 0.002) discard;
  gl_FragColor = vec4(vCol, vA);
}
`;

/** Impulses: bright points with a fading tail, positions written by the CPU each frame. */
export const impulseVertex = /* glsl */ `
uniform float uPixelRatio;
attribute vec4 aColor;   // rgb, alpha
attribute float aSize;
varying vec4 vColor;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_PointSize = aSize * uPixelRatio * (600.0 / max(50.0, -mv.z));
  vColor = aColor;
  gl_Position = projectionMatrix * mv;
}
`;

export const impulseFragment = /* glsl */ `
varying vec4 vColor;
void main() {
  vec2 c = gl_PointCoord * 2.0 - 1.0;
  float r = dot(c, c);
  float a = (exp(-r * 6.0) + 0.35 * exp(-r * 1.6)) * vColor.a;
  if (a < 0.004) discard;
  gl_FragColor = vec4(vColor.rgb * (1.0 + exp(-r * 14.0)), a);
}
`;
