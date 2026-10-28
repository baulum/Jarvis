// The composition: scenes from the storyboard, one after another, each with its entry transition.
import type { ComponentType, ReactNode } from "react";
import { useEffect, useState } from "react";
import { AbsoluteFill, Sequence, continueRender, delayRender, useCurrentFrame } from "remotion";
import { C, ThemeContext, loadBrandFonts, themeFor } from "./brand";
import { EASE_OUT, prog, rnd } from "./motion";
import { Chat, Notification } from "./scenes/phone";
import { Dating, EndCard, FactScene, Review, Weather } from "./scenes/story";
import { Countdown, ErrorDialog, Loading, SystemCrash, Terminal } from "./scenes/system";
import { KineticType, PovTitle } from "./scenes/type";
import { Browser, Browser3D, CursorClick, GlassStack, Search, SplitScreen, TabChaos } from "./scenes/web";
import type { ComponentName, Scene, Storyboard, TransitionKind } from "./types";
import { sceneFrames } from "./types";
import { SatireTag } from "./ui";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const SCENES: Record<ComponentName, ComponentType<any>> = {
  kinetic_type: KineticType,
  pov_title: PovTitle,
  chat: Chat,
  error_dialog: ErrorDialog,
  system_crash: SystemCrash,
  browser: Browser,
  tab_chaos: TabChaos,
  notification: Notification,
  terminal: Terminal,
  search: Search,
  split_screen: SplitScreen,
  glass_stack: GlassStack,
  browser_3d: Browser3D,
  cursor_click: CursorClick,
  countdown: Countdown,
  loading: Loading,
  fact: FactScene,
  weather: Weather,
  review: Review,
  dating: Dating,
  end_card: EndCard,
};

/** Components that are satire by nature and draw their own "Satire · fiktiv" tag. */
const SELF_TAGGED = new Set<ComponentName>(["review", "weather", "dating"]);

const ENTER_FRAMES = 9;

function Enter({ kind = "cut", children }: { kind?: TransitionKind; children: ReactNode }) {
  const frame = useCurrentFrame();
  const p = prog(frame, 0, ENTER_FRAMES, EASE_OUT);
  if (kind === "cut" || p >= 1) return <AbsoluteFill>{children}</AbsoluteFill>;
  const style: Record<string, string | number> = {};
  let overlay: ReactNode = null;
  switch (kind) {
    case "fade":
      style.opacity = p;
      break;
    case "slide_up":
      style.translate = `0 ${(1 - p) * 100}%`;
      break;
    case "slide_left":
      style.translate = `${(1 - p) * 100}% 0`;
      break;
    case "push":
      style.translate = `${(1 - p) * 60}% 0`;
      style.scale = String(0.92 + 0.08 * p);
      break;
    case "wipe":
      style.clipPath = `inset(0 ${(1 - p) * 100}% 0 0)`;
      break;
    case "zoom":
      style.scale = String(1.35 - 0.35 * p);
      style.opacity = Math.min(1, p * 1.6);
      break;
    case "blur":
      style.filter = `blur(${(1 - p) * 40}px)`;
      style.opacity = Math.min(1, p * 1.4);
      break;
    case "whip":
      style.translate = `${(1 - p) * -45}% 0`;
      style.filter = `blur(${(1 - p) * 60}px)`;
      break;
    case "iris":
      style.clipPath = `circle(${p * 120}% at 50% 50%)`;
      break;
    case "glitch": {
      const k = 1 - p;
      style.translate = `${(rnd(frame * 7) - 0.5) * 120 * k}px ${(rnd(frame * 3) - 0.5) * 40 * k}px`;
      style.filter = `drop-shadow(${18 * k}px 0 0 ${C.accent}) drop-shadow(${-18 * k}px 0 0 ${C.danger})`;
      overlay = (
        <AbsoluteFill style={{ backgroundImage: `repeating-linear-gradient(0deg, rgba(255,255,255,${0.12 * k}) 0 3px, transparent 3px ${8 + Math.floor(rnd(frame) * 20)}px)` }} />
      );
      break;
    }
    case "flash":
      overlay = <AbsoluteFill style={{ background: C.white, opacity: 1 - p }} />;
      break;
  }
  return (
    <AbsoluteFill>
      <AbsoluteFill style={style}>{children}</AbsoluteFill>
      {overlay}
    </AbsoluteFill>
  );
}

function SceneView({ scene, board }: { scene: Scene; board: Storyboard }) {
  const Comp = SCENES[scene.component];
  const theme = themeFor(board.palette, scene.surface);
  const props = scene.component === "fact" ? { ...scene.props, facts: board.facts ?? [] } : scene.props;
  const tagged = (board.satire || scene.component === "chat") && !SELF_TAGGED.has(scene.component) && scene.component !== "end_card";
  return (
    <ThemeContext.Provider value={theme}>
      <AbsoluteFill style={{ background: theme.bg }}>
        <Enter kind={scene.transition}>{Comp ? <Comp {...props} /> : null}</Enter>
        {tagged ? <SatireTag /> : null}
      </AbsoluteFill>
    </ThemeContext.Provider>
  );
}

export function SocialVideo(board: Storyboard) {
  const [handle] = useState(() => delayRender("Loading brand fonts"));
  useEffect(() => {
    loadBrandFonts().then(
      () => continueRender(handle),
      () => continueRender(handle),
    );
  }, [handle]);
  let from = 0;
  return (
    <AbsoluteFill style={{ background: C.ink }}>
      {board.scenes.map((s) => {
        const frames = sceneFrames(s);
        const el = (
          <Sequence key={s.id} from={from} durationInFrames={frames} name={`${s.id} · ${s.component}`}>
            <SceneView scene={s} board={board} />
          </Sequence>
        );
        from += frames;
        return el;
      })}
    </AbsoluteFill>
  );
}
