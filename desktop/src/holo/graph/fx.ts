// Imperative effect queue between the live reducer and the 3D layer. Effects are short-lived render state
// (impulses in flight, waves, region glow levels); keeping them out of React/zustand means no re-render per
// impulse. Every entry here was pushed by applyStep for a real activity step.
import { GROUP_COLOR, type RegionGroup } from "./anatomy";
import type { Effect, LiveState, Tone } from "./live";

export interface Impulse {
  path: string[];
  tone: Tone;
  /** seconds (performance.now()/1000) */
  born: number;
  /** seconds per path segment */
  segment: number;
}

export interface Wave {
  at: string;
  tone: Tone;
  strength: number;
  born: number;
}

export const TONE_COLOR: Record<Tone, [number, number, number]> = {
  signal: [0.72, 0.95, 1.0],
  data: [0.5, 0.98, 0.86],
  memory: [0.78, 0.72, 1.0],
  ok: [0.62, 1.0, 0.8],
  error: [1.0, 0.42, 0.36],
  agent: [0.52, 1.0, 0.76],
  warn: [1.0, 0.74, 0.36],
};

export const GROUPS: RegionGroup[] = ["input", "core", "memory", "tools", "agents", "output"];

const MAX_IMPULSES = 160;
const MAX_WAVES = 6;

export const fx = {
  impulses: [] as Impulse[],
  waves: [] as Wave[],
  /** bumped glow level per region group (decays in the render loop) */
  glow: Object.fromEntries(GROUPS.map((g) => [g, 0])) as Record<RegionGroup, number>,
  glowTone: Object.fromEntries(GROUPS.map((g) => [g, "signal"])) as Record<RegionGroup, Tone>,
  /** node ids that should play their growth animation once they appear in the graph */
  grow: new Map<string, number>(),
};

/** Queues the effects of one step. Impulses with several pulses are staggered so they read as a stream. */
export function pushEffects(effects: Effect[], now = performance.now() / 1000): void {
  for (const e of effects) {
    if (e.kind === "impulse") {
      const path = e.path.filter(Boolean);
      if (path.length < 2) continue;
      for (let i = 0; i < e.count; i += 1) fx.impulses.push({ path, tone: e.tone, born: now + i * 0.14, segment: 0.62 });
    } else if (e.kind === "wave") {
      fx.waves.push({ at: e.at, tone: e.tone, strength: e.strength, born: now });
    } else if (e.kind === "glow") {
      fx.glow[e.group] = Math.min(1.4, Math.max(fx.glow[e.group], 0) + e.strength);
      fx.glowTone[e.group] = e.tone;
    } else if (e.kind === "grow") {
      fx.grow.set(e.node, now);
    }
  }
  if (fx.impulses.length > MAX_IMPULSES) fx.impulses.splice(0, fx.impulses.length - MAX_IMPULSES);
  if (fx.waves.length > MAX_WAVES) fx.waves.splice(0, fx.waves.length - MAX_WAVES);
}

/** Drops finished impulses/waves and lets glow decay. Called once per frame. */
export function stepFx(now: number, dt: number): void {
  fx.impulses = fx.impulses.filter((i) => now - i.born < i.segment * (i.path.length - 1) + 0.4);
  fx.waves = fx.waves.filter((w) => now - w.born < 1.8);
  const k = Math.exp(-dt / 0.9);
  for (const g of GROUPS) fx.glow[g] *= k;
  for (const [id, t] of fx.grow) if (now - t > 30) fx.grow.delete(id);
}

/**
 * Glow a region keeps while real work runs in it (a tool call holds the motor cortex, a turn the core …) –
 * the "pulsing activity areas". Nothing glows without a running process.
 */
export function heldGlow(live: LiveState): Record<RegionGroup, number> {
  const out = Object.fromEntries(GROUPS.map((g) => [g, 0])) as Record<RegionGroup, number>;
  for (const p of Object.values(live.processes)) {
    if (p.until !== undefined) continue;
    if (p.phase === "tool") out.tools = Math.max(out.tools, 0.5);
    else if (p.phase === "approval") out.tools = Math.max(out.tools, 0.3);
    else if (p.key.startsWith("turn:")) {
      out.core = Math.max(out.core, 0.38);
      if (p.label === "Generiere Antwort") out.output = Math.max(out.output, 0.3);
    } else if (p.phase === "agent" && p.status === "running") out.agents = Math.max(out.agents, 0.4);
    else if (p.key === "voice") {
      if (p.label === "Spricht") out.output = Math.max(out.output, 0.35);
      else out.input = Math.max(out.input, 0.3);
    }
  }
  return out;
}

export function groupColor(g: RegionGroup, tone: Tone): [number, number, number] {
  return tone === "error" || tone === "warn" ? TONE_COLOR[tone] : GROUP_COLOR[g];
}

/** For tests / view resets. */
export function resetFx(): void {
  fx.impulses = [];
  fx.waves = [];
  for (const g of GROUPS) fx.glow[g] = 0;
  fx.grow.clear();
}
