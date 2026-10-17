// J.A.R.V.I.S. energy core – volumetric plasma nucleus, noise-distorted shell, tilted orbit rings.
// Driven by real state only: audio level (Jarvis' voice / Sir's mic), activity energy, turbulence (thinking),
// call pulse and tint (cyan normal, amber when an approval waits). Quality < 1 drops the rings + octaves.
#version 460 core
#include <flutter/runtime_effect.glsl>

precision mediump float;

uniform vec2 uSize;
uniform float uTime;
uniform float uLevel;
uniform float uEnergy;
uniform float uTurb;
uniform float uPulse;
uniform vec3 uTint;
uniform float uDim;
uniform float uQuality;

out vec4 fragColor;

float hash(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x),
             mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
}

float fbm(vec2 p) {
  float v = 0.0;
  float a = 0.5;
  for (int i = 0; i < 5; i++) {
    if (float(i) > 2.0 + uQuality * 2.0) break;
    v += a * noise(p);
    p = p * 2.03 + vec2(1.7, 9.2);
    a *= 0.5;
  }
  return v;
}

void main() {
  vec2 frag = FlutterFragCoord().xy;
  vec2 uv = (frag - 0.5 * uSize) / min(uSize.x, uSize.y);
  float r = length(uv);
  float a = atan(uv.y, uv.x);
  float t = uTime;
  float lvl = clamp(uLevel, 0.0, 1.0);

  float coreR = 0.115 + 0.045 * lvl + 0.008 * sin(t * 1.3);
  // periodic in the angle (cos/sin) – raw atan() would leave a seam at ±pi
  float spin = a + t * 0.15 * (1.0 + uTurb * 2.0);
  vec2 q = vec2(cos(spin), sin(spin)) * 2.2 + vec2(r * 6.0 - t * 0.4, r * 2.0);
  float n = fbm(q * 1.3 + fbm(q + t * 0.1) * uTurb * 2.0);
  float shellR = 0.215 + 0.035 * lvl + 0.03 * (n - 0.5) * (1.0 + uTurb);
  float shell = exp(-pow((r - shellR) * 30.0, 2.0)) * (0.5 + 0.65 * n);

  float plasma = fbm(uv * 9.0 + vec2(t * 0.3, -t * 0.25) + n * 2.0);
  float nucleus = smoothstep(coreR, 0.0, r);
  float inner = smoothstep(shellR, coreR * 0.5, r) * (0.3 + 0.7 * plasma);

  float glow = exp(-r * 7.5) * (0.55 + 0.8 * uEnergy + lvl);
  float halo = exp(-pow((r - shellR - 0.06) * 9.0, 2.0)) * 0.22 * (0.5 + uEnergy);

  float rings = 0.0;
  for (int i = 0; i < 3; i++) {
    float fi = float(i);
    float ang = t * (0.11 + 0.05 * fi) + fi * 2.1;
    mat2 rot = mat2(cos(ang), -sin(ang), sin(ang), cos(ang));
    vec2 p = rot * uv;
    p.y *= 2.6 + fi * 0.8;
    float rr = length(p);
    float target = 0.31 + 0.045 * fi;
    float pa = atan(p.y, p.x);
    float arc = smoothstep(0.15, 0.75, 0.5 + 0.5 * sin(pa * (1.0 + fi) + t * (0.6 + 0.3 * fi) + fi * 1.7));
    rings += exp(-pow((rr - target) * 150.0, 2.0)) * arc * (0.14 + 0.08 * sin(a * 3.0 + t + fi));
  }

  float pulse = 0.0;
  if (uPulse > 0.0) {
    float ph = fract(t * 0.7);
    pulse = exp(-pow((r - (0.25 + ph * 0.32)) * 30.0, 2.0)) * (1.0 - ph) * uPulse;
  }

  vec3 ice = vec3(0.81, 0.96, 1.0);
  vec3 deep = vec3(0.02, 0.2, 0.3);
  vec3 col = deep * glow;
  col += uTint * (shell * 1.2 + inner * 0.85 + halo + rings * uQuality + pulse);
  col += ice * (nucleus * 1.35 + pow(inner, 3.0) * 0.6);

  float grey = dot(col, vec3(0.33));
  col = mix(col, vec3(grey), uDim) * (1.0 - 0.6 * uDim);
  col = max(col + (hash(frag + fract(t) * 97.0) - 0.5) * 0.012, vec3(0.0));
  // premultiplied, alpha = brightness: the core adds light to whatever lies behind it (no visible square)
  float alpha = clamp(max(max(col.r, col.g), col.b), 0.0, 1.0);
  fragColor = vec4(min(col, vec3(alpha)), alpha);
}
