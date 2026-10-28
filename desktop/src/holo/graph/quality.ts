// Adaptive quality: three tiers, picked from the device and adjusted by the measured frame rate.
// Low = phones/tablets and weak GPUs: fewer neurons, a coarser cortex mesh, no bloom, pixel ratio 1.

export type Tier = 0 | 1 | 2;

export interface Quality {
  tier: Tier;
  neurons: number;
  /** cells across the longest side of the brain for the cortex mesh */
  shellRes: number;
  synapseLinks: number;
  bloom: boolean;
  dprMax: number;
  dust: number;
}

export const QUALITY: Record<Tier, Quality> = {
  0: { tier: 0, neurons: 900, shellRes: 40, synapseLinks: 1, bloom: false, dprMax: 1, dust: 250 },
  1: { tier: 1, neurons: 1900, shellRes: 52, synapseLinks: 2, bloom: false, dprMax: 1.25, dust: 650 },
  2: { tier: 2, neurons: 3200, shellRes: 64, synapseLinks: 2, bloom: true, dprMax: 1.5, dust: 1100 },
};

export interface DeviceInfo {
  coarsePointer: boolean;
  width: number;
  cores: number;
  memoryGb?: number;
}

export function detectTier(d: DeviceInfo): Tier {
  if (d.coarsePointer || d.width < 760) return 0;
  if (d.cores <= 4 || (d.memoryGb !== undefined && d.memoryGb <= 4)) return 1;
  return 2;
}

export function deviceInfo(): DeviceInfo {
  const nav = navigator as Navigator & { deviceMemory?: number };
  return {
    coarsePointer: typeof matchMedia === "function" && matchMedia("(pointer: coarse)").matches,
    width: window.innerWidth,
    cores: nav.hardwareConcurrency || 4,
    memoryGb: nav.deviceMemory,
  };
}

export function stepTier(t: Tier, delta: -1 | 1, max: Tier): Tier {
  return Math.max(0, Math.min(max, t + delta)) as Tier;
}
