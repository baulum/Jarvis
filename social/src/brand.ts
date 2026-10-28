// PJE Systems brand, taken from pje-systems.de (CSS tokens, icon.svg, logo) on 2026-10-28 – nothing invented:
// ink #0b0c0e, graphite #1c1e22, canvas #e3e5e8, line #d3d6dc, slate #50545b, quiet #595e66,
// accent #2651f0, accent-deep #1a3fd0, accent-on-dark #8fa6ff, accent-soft #eaefff; Geist / Geist Mono.
// The site ends its headlines with a full stop in the accent colour ("Was wir bauen.") – that dot is our motif.
import { loadFont } from "@remotion/fonts";
import { createContext, useContext } from "react";
import { staticFile } from "remotion";
import type { Palette, Surface } from "./types";

export const C = {
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
  logo: string;
}

const SURFACES: Record<Surface, Omit<Theme, "bg"> & { bg: string }> = {
  dark: {
    surface: "dark",
    bg: C.ink,
    fg: C.white,
    muted: "rgba(255,255,255,0.64)",
    accent: C.accentOnDark,
    fill: C.accent,
    onFill: C.white,
    panel: C.graphite,
    line: "rgba(255,255,255,0.12)",
    logo: "brand/pje-logo-white.png",
  },
  light: {
    surface: "light",
    bg: C.canvas,
    fg: C.ink,
    muted: C.slate,
    accent: C.accent,
    fill: C.accent,
    onFill: C.white,
    panel: C.white,
    line: C.line,
    logo: "brand/pje-logo-ink.png",
  },
  accent: {
    surface: "accent",
    bg: C.accent,
    fg: C.white,
    muted: "rgba(255,255,255,0.78)",
    accent: C.white,
    fill: C.ink,
    onFill: C.white,
    panel: C.accentDeep,
    line: "rgba(255,255,255,0.22)",
    logo: "brand/pje-logo-white.png",
  },
};

/** Default surface and background tint of each palette variant (all built from the tokens above). */
const PALETTES: Record<Palette, { surface: Surface; bg?: string }> = {
  ink: { surface: "dark" },
  graphite: { surface: "dark", bg: C.graphite },
  canvas: { surface: "light" },
  paper: { surface: "light", bg: C.white },
  electric: { surface: "accent" },
};

export function themeFor(palette: Palette, surface?: Surface): Theme {
  const p = PALETTES[palette] ?? PALETTES.ink;
  const s = surface ?? p.surface;
  const base = SURFACES[s];
  return { ...base, bg: s === p.surface && p.bg ? p.bg : base.bg };
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
