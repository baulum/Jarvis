// PJE Systems brand, taken from pje-systems.de (CSS tokens, icon.svg, logo) on 2026-10-28 – nothing invented:
// ink #0b0c0e, graphite #1c1e22, canvas #e3e5e8, line #d3d6dc, slate #50545b, quiet #595e66,
// accent #2651f0, accent-deep #1a3fd0, accent-on-dark #8fa6ff, accent-soft #eaefff; Geist / Geist Mono.
// The site ends its headlines with a full stop in the accent colour ("Was wir bauen.") – that dot is our motif.
// Other companies (Jarvis company profiles) bring their own colours and name in `Storyboard.brand`; `applyBrand`
// swaps them in before a frame renders, missing shades are derived (same formulas as core/src/jarvis/social/qa.py).
import { loadFont } from "@remotion/fonts";
import { createContext, useContext } from "react";
import { staticFile } from "remotion";
import type { BrandInfo, Palette, Surface } from "./types";

const PJE = {
  ink: "#0b0c0e",
  inkSoft: "#16171a",
  graphite: "#1c1e22",
  canvas: "#e3e5e8",
  line: "#d3d6dc",
  slate: "#50545b",
  quiet: "#595e66",
  white: "#ffffff",
  accent: "#2651f0",
  accentDeep: "#1a3fd0",
  accentOnDark: "#8fa6ff",
  accentSoft: "#eaefff",
  // functional colours for the parodied system UI (warnings/errors), muted so they never fight the brand blue
  danger: "#e5484d",
  warn: "#f5a524",
  ok: "#30a46c",
} as const;
const PJE_LOGO = { on_dark: "brand/pje-logo-white.png", on_light: "brand/pje-logo-ink.png" };

/** The colours of the brand being rendered (PJE Systems unless the storyboard brings another company's). */
export const C: { -readonly [K in keyof typeof PJE]: string } = { ...PJE };
let current: { name: string; logo: { on_dark?: string; on_light?: string } } = { name: "PJE Systems", logo: PJE_LOGO };

const HEX = /^#[0-9a-fA-F]{6}$/;
const rgb = (hex: string): number[] => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));

/** `a` moved towards `b` by `t` (0…1) – same formula as `mix` in qa.py. */
export function mix(a: string, b: string, t: number): string {
  const [x, y] = [rgb(a), rgb(b)];
  return "#" + x.map((v, i) => Math.round(v + (y[i] - v) * t).toString(16).padStart(2, "0")).join("");
}

function lum(hex: string): number {
  const lin = rgb(hex).map((c) => c / 255).map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [lum(a), lum(b)].sort((m, n) => n - m);
  return (hi + 0.05) / (lo + 0.05);
}

/** Text colour on an accent fill: white or ink, whichever reads better. */
export const onAccent = (): string => (contrast(C.white, C.accent) >= contrast(C.ink, C.accent) ? C.white : C.ink);

/** Accent as rgba (glows, pulses). */
export function accentRgba(alpha: number): string {
  const [r, g, b] = rgb(C.accent);
  return `rgba(${r},${g},${b},${alpha})`;
}

/** Called with the storyboard before the frame renders; without a brand (Studio sample) it is PJE Systems. */
export function applyBrand(brand?: BrandInfo): void {
  Object.assign(C, PJE);
  current = { name: "PJE Systems", logo: PJE_LOGO };
  if (!brand) return;
  const col = Object.fromEntries(Object.entries(brand.colors ?? {}).filter(([, v]) => typeof v === "string" && HEX.test(v))) as NonNullable<BrandInfo["colors"]>;
  // shades are only derived for colours that differ from PJE's – PJE Systems keeps its exact tokens
  const ink = col.ink && col.ink.toLowerCase() !== PJE.ink ? col.ink : null;
  const accent = col.accent && col.accent.toLowerCase() !== PJE.accent ? col.accent : null;
  if (ink) {
    C.ink = ink;
    C.inkSoft = mix(ink, PJE.white, 0.04);
  }
  C.graphite = col.graphite ?? (ink ? mix(ink, PJE.white, 0.08) : PJE.graphite);
  if (col.canvas) C.canvas = col.canvas;
  if (accent) {
    C.accent = accent;
    C.accentSoft = mix(accent, PJE.white, 0.9);
  }
  C.accentDeep = col.accent_deep ?? (accent ? mix(accent, "#000000", 0.18) : PJE.accentDeep);
  C.accentOnDark = col.accent_on_dark ?? (accent ? mix(accent, PJE.white, 0.45) : PJE.accentOnDark);
  current = { name: brand.name ?? "", logo: brand.logo ?? {} };
}

export const brandName = (): string => current.name;

export const FONT = "Geist";
export const MONO = "Geist Mono";

let fontsLoading: Promise<unknown> | null = null;
export function loadBrandFonts(): Promise<unknown> {
  fontsLoading ??= Promise.all([
    loadFont({ family: FONT, url: staticFile("fonts/Geist-Variable.woff2"), weight: "100 900" }),
    loadFont({ family: MONO, url: staticFile("fonts/GeistMono-Variable.woff2"), weight: "100 900" }),
  ]);
  return fontsLoading;
}

export interface Theme {
  surface: Surface;
  bg: string;
  fg: string;
  muted: string;
  /** accent for text/strokes on this surface (contrast-checked) */
  accent: string;
  /** accent for fills (buttons, dots) */
  fill: string;
  onFill: string;
  panel: string;
  line: string;
  /** logo file in public/ for this surface; null = the company name is drawn as a wordmark */
  logo: string | null;
}

const MIN_CONTRAST = 4.5;

/** The surfaces from the current brand colours; accent text falls back to the foreground if it would not read. */
function surfaces(): Record<Surface, Theme> {
  const on = onAccent();
  const light = on !== C.white;
  return {
    dark: {
      surface: "dark",
      bg: C.ink,
      fg: C.white,
      muted: "rgba(255,255,255,0.64)",
      accent: contrast(C.accentOnDark, C.ink) >= MIN_CONTRAST ? C.accentOnDark : C.white,
      fill: C.accent,
      onFill: on,
      panel: C.graphite,
      line: "rgba(255,255,255,0.12)",
      logo: current.logo.on_dark ?? null,
    },
    light: {
      surface: "light",
      bg: C.canvas,
      fg: C.ink,
      muted: C.slate,
      accent: contrast(C.accent, C.canvas) >= MIN_CONTRAST ? C.accent : C.ink,
      fill: C.accent,
      onFill: on,
      panel: C.white,
      line: C.line,
      logo: current.logo.on_light ?? null,
    },
    accent: {
      surface: "accent",
      bg: C.accent,
      fg: on,
      muted: light ? "rgba(11,12,14,0.72)" : "rgba(255,255,255,0.78)",
      accent: on,
      fill: C.ink,
      onFill: C.white,
      panel: C.accentDeep,
      line: light ? "rgba(11,12,14,0.18)" : "rgba(255,255,255,0.22)",
      logo: (light ? current.logo.on_light : current.logo.on_dark) ?? null,
    },
  };
}

/** Default surface and background tint of each palette variant (all built from the tokens above). */
const PALETTES: Record<Palette, { surface: Surface; bg?: "graphite" | "white" }> = {
  ink: { surface: "dark" },
  graphite: { surface: "dark", bg: "graphite" },
  canvas: { surface: "light" },
  paper: { surface: "light", bg: "white" },
  electric: { surface: "accent" },
};

export function themeFor(palette: Palette, surface?: Surface): Theme {
  const p = PALETTES[palette] ?? PALETTES.ink;
  const s = surface ?? p.surface;
  const base = surfaces()[s];
  return { ...base, bg: s === p.surface && p.bg ? C[p.bg] : base.bg };
}

export const ThemeContext = createContext<Theme>(themeFor("ink"));
export const useTheme = (): Theme => useContext(ThemeContext);

// Safe area for 9:16 feeds (Reels, TikTok, Shorts, Facebook): top bar, caption/buttons at the bottom, action rail
// on the right. Text never goes outside; backgrounds may.
export const SAFE = { top: 250, bottom: 430, left: 80, right: 170 } as const;
export const SAFE_W = 1080 - SAFE.left - SAFE.right;
export const MIN_TEXT = 44; // px at 1080 wide: readable on a phone held at arm's length

/** Font size that fits `text` into `width` on one line (Geist ≈ 0.55em per char), never below MIN_TEXT. */
export function fit(text: string, width: number, max: number, min = MIN_TEXT, perChar = 0.56): number {
  const longest = Math.max(1, ...text.split("\n").map((l) => l.length));
  return Math.round(Math.max(min, Math.min(max, width / (longest * perChar))));
}
