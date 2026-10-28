// Frame-driven motion helpers (no CSS transitions: they do not render in Remotion).
import { Easing, interpolate, spring } from "remotion";

export const EASE_OUT = Easing.bezier(0.16, 1, 0.3, 1); // expo-like settle: UI arriving
export const EASE_IN_OUT = Easing.bezier(0.65, 0, 0.35, 1); // camera moves
export const EASE_IN = Easing.bezier(0.7, 0, 0.84, 0); // leaving

/** 0→1 between `start` and `start + dur` frames. */
export function prog(frame: number, start: number, dur: number, easing = EASE_OUT): number {
  return interpolate(frame, [start, start + Math.max(1, dur)], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing,
  });
}

export function pop(frame: number, fps: number, delay = 0, damping = 14, mass = 0.6): number {
  return spring({ frame: frame - delay, fps, config: { damping, mass, stiffness: 180 } });
}

/** Typewriter: characters visible after `start` at `cps` characters per second. */
export function typed(text: string, frame: number, start: number, fps: number, cps = 22): string {
  const n = Math.floor(Math.max(0, frame - start) * (cps / fps));
  return text.slice(0, Math.min(text.length, n));
}

/** Typing speed (chars/s) so that `chars` typed from `start` are done by frame `doneBy` – never slower than `base`.
 * Keeps long texts in short scenes readable: the text has to stand still for a moment before the cut. */
export function pace(chars: number, start: number, doneBy: number, fps: number, base = 22): number {
  return Math.max(base, (chars * fps) / Math.max(6, doneBy - start));
}

/** Frames the typewriter needs for `text`. */
export const typeFrames = (text: string, fps: number, cps = 22): number => Math.ceil((text.length / cps) * fps);

export const mix = (a: number, b: number, t: number): number => a + (b - a) * t;

/** Deterministic pseudo-random in [0,1) – renders must be identical frame by frame. */
export function rnd(seed: number): number {
  const x = Math.sin(seed * 12.9898 + 78.233) * 43758.5453;
  return x - Math.floor(x);
}
