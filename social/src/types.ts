// Storyboard contract between the Jarvis Core and these compositions. The Core validates every storyboard against
// the JSON schema in core/src/jarvis/social/storyboard.py before it renders; keep both in sync.

export type Palette = "ink" | "canvas" | "electric" | "graphite" | "paper";
export type Surface = "dark" | "light" | "accent";
export type Tone = "humorvoll" | "provokant" | "minimalistisch" | "cineastisch" | "angebotsorientiert";
export type TransitionKind =
  | "cut"
  | "fade"
  | "slide_up"
  | "slide_left"
  | "push"
  | "wipe"
  | "zoom"
  | "glitch"
  | "flash"
  | "blur"
  | "whip"
  | "iris";

export type ComponentName =
  | "kinetic_type"
  | "pov_title"
  | "chat"
  | "error_dialog"
  | "system_crash"
  | "browser"
  | "tab_chaos"
  | "notification"
  | "terminal"
  | "search"
  | "split_screen"
  | "glass_stack"
  | "browser_3d"
  | "cursor_click"
  | "countdown"
  | "loading"
  | "fact"
  | "weather"
  | "review"
  | "dating"
  | "end_card";

export interface Sfx {
  sound: string;
  at: number;
  gain?: number;
}

export interface Scene {
  id: string;
  component: ComponentName;
  duration: number; // seconds
  transition?: TransitionKind;
  surface?: Surface;
  props: Record<string, unknown>;
  sfx?: Sfx[];
}

export interface Fact {
  id: string;
  value: string;
  label: string;
}

export interface Storyboard {
  version: 1;
  title: string;
  category: string;
  tone: Tone;
  palette: Palette;
  satire?: boolean;
  scenes: Scene[];
  /** Brand facts the `fact` component may show (filled in by the Core from config/social/brand.yaml). */
  facts?: Fact[];
}

export const FPS = 30;
export const WIDTH = 1080;
export const HEIGHT = 1920;

export const sceneFrames = (s: Scene): number => Math.max(12, Math.round(s.duration * FPS));
export const totalFrames = (b: Storyboard): number => b.scenes.reduce((n, s) => n + sceneFrames(s), 0);
