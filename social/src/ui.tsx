// Shared building parts of the scenes: stage with safe area, headline with the brand dot, device frames, cursor.
import type { CSSProperties, ReactNode } from "react";
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { C, FONT, MONO, SAFE, useTheme } from "./brand";
import { EASE_IN_OUT } from "./motion";

/** Atmosphere behind every scene: a slow brand-blue light drifting across the frame plus fine grain, so no frame
 * is a flat fill. Subtle on purpose – it must never compete with the text. */
function Ambient() {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const t = useTheme();
  const k = interpolate(frame, [0, durationInFrames], [0, 1], { easing: EASE_IN_OUT });
  const glow = t.surface === "dark" ? "rgba(38,81,240,0.30)" : t.surface === "accent" ? "rgba(255,255,255,0.16)" : "rgba(38,81,240,0.10)";
  const x = 20 + 60 * k;
  const y = 18 + 14 * Math.sin(k * Math.PI);
  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      <AbsoluteFill style={{ background: `radial-gradient(70% 45% at ${x}% ${y}%, ${glow}, transparent 70%)` }} />
      <AbsoluteFill
        style={{
          opacity: t.surface === "light" ? 0.05 : 0.09,
          backgroundImage:
            "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='220' height='220'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>\")",
          backgroundPosition: `${(frame * 37) % 220}px ${(frame * 53) % 220}px`,
        }}
      />
    </AbsoluteFill>
  );
}

/** Slow camera push over the whole scene (≈2.5 %): keeps held frames alive between the actual animations. */
function Push({ children }: { children: ReactNode }) {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const s = interpolate(frame, [0, durationInFrames], [1, 1.025], { easing: EASE_IN_OUT });
  return <AbsoluteFill style={{ scale: String(s) }}>{children}</AbsoluteFill>;
}

export function Stage({ children, style, center = true }: { children?: ReactNode; style?: CSSProperties; center?: boolean }) {
  const t = useTheme();
  return (
    <AbsoluteFill style={{ background: t.bg, color: t.fg, fontFamily: FONT }}>
      <Ambient />
      <Push>
        <div
          style={{
            position: "absolute",
            top: SAFE.top,
            bottom: SAFE.bottom,
            left: SAFE.left,
            right: SAFE.right,
            display: "flex",
            flexDirection: "column",
            justifyContent: center ? "center" : "flex-start",
            gap: 36,
            ...style,
          }}
        >
          {children}
        </div>
      </Push>
    </AbsoluteFill>
  );
}

/** Text with the accent full stop of pje-systems.de: a trailing "." is drawn in the accent colour. */
export function Dotted({ text, color }: { text: string; color?: string }) {
  const t = useTheme();
  if (!text.endsWith(".") || text.endsWith("..")) return <>{text}</>;
  return (
    <>
      {text.slice(0, -1)}
      <span style={{ color: color ?? t.accent }}>.</span>
    </>
  );
}

export function Headline({
  text,
  size,
  weight = 640,
  style,
}: {
  text: string;
  size: number;
  weight?: number;
  style?: CSSProperties;
}) {
  return (
    <div
      style={{
        fontSize: size,
        fontWeight: weight,
        letterSpacing: size > 90 ? "-0.045em" : "-0.03em",
        lineHeight: 1.02,
        textWrap: "balance",
        ...style,
      }}
    >
      <Dotted text={text} />
    </div>
  );
}

export function Label({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  const t = useTheme();
  return (
    <div
      style={{
        fontFamily: MONO,
        fontSize: 30,
        letterSpacing: "0.14em",
        textTransform: "uppercase",
        color: t.muted,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

export function Button({ text, style }: { text: string; style?: CSSProperties }) {
  const t = useTheme();
  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 22,
        alignSelf: "flex-start",
        padding: "30px 46px",
        borderRadius: 999,
        background: t.fill,
        color: t.onFill,
        fontSize: 48,
        fontWeight: 600,
        letterSpacing: "-0.01em",
        ...style,
      }}
    >
      {text}
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none">
        <path d="M5 12h14M13 6l6 6-6 6" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>
  );
}

/** A browser/app window. Neutral grey controls – a generic window, no OS trademarks. */
export function Window({
  children,
  url,
  title,
  width = "100%",
  height,
  dark,
  style,
}: {
  children: ReactNode;
  url?: string;
  title?: string;
  width?: number | string;
  height?: number | string;
  dark?: boolean;
  style?: CSSProperties;
}) {
  const chrome = dark ? "#202227" : "#f4f5f7";
  const fg = dark ? "rgba(255,255,255,0.8)" : C.slate;
  return (
    <div
      style={{
        width,
        height,
        borderRadius: 28,
        overflow: "hidden",
        background: dark ? C.ink : C.white,
        boxShadow: "0 40px 90px -40px rgba(11,12,14,0.55), 0 0 0 1px rgba(11,12,14,0.08)",
        display: "flex",
        flexDirection: "column",
        ...style,
      }}
    >
      <div style={{ background: chrome, padding: "22px 26px", display: "flex", alignItems: "center", gap: 22 }}>
        <div style={{ display: "flex", gap: 12 }}>
          {[0, 1, 2].map((i) => (
            <div key={i} style={{ width: 20, height: 20, borderRadius: 10, background: dark ? "#3a3d44" : "#cfd2d8" }} />
          ))}
        </div>
        {url !== undefined ? (
          <div
            style={{
              flex: 1,
              background: dark ? "#2b2e35" : C.white,
              borderRadius: 14,
              padding: "12px 22px",
              fontFamily: MONO,
              fontSize: 30,
              color: fg,
              whiteSpace: "nowrap",
              overflow: "hidden",
            }}
          >
            {url}
          </div>
        ) : (
          <div style={{ flex: 1, fontFamily: MONO, fontSize: 28, color: fg, textAlign: "center", marginRight: 84 }}>{title}</div>
        )}
      </div>
      <div style={{ flex: 1, position: "relative" }}>{children}</div>
    </div>
  );
}

/** A phone outline (generic), content inside. */
export function Phone({ children, style, screen = C.ink }: { children: ReactNode; style?: CSSProperties; screen?: string }) {
  return (
    <div
      style={{
        width: 720,
        height: 1240,
        alignSelf: "center",
        borderRadius: 96,
        padding: 18,
        background: "#111216",
        boxShadow: "0 60px 120px -50px rgba(0,0,0,0.6), inset 0 0 0 3px #2a2c31",
        ...style,
      }}
    >
      <div style={{ position: "relative", width: "100%", height: "100%", borderRadius: 80, overflow: "hidden", background: screen }}>
        <div
          style={{ position: "absolute", top: 22, left: "50%", marginLeft: -90, width: 180, height: 50, borderRadius: 25, background: "#000", zIndex: 5 }}
        />
        {children}
      </div>
    </div>
  );
}

export function Cursor({ x, y, pressed = 0 }: { x: number; y: number; pressed?: number }) {
  return (
    <svg
      width="64"
      height="64"
      viewBox="0 0 24 24"
      style={{ position: "absolute", left: x, top: y, scale: String(1 - pressed * 0.15), zIndex: 20, filter: "drop-shadow(0 6px 10px rgba(0,0,0,0.35))" }}
    >
      <path d="M4 2l15 9.5-6.5 1.4 3.8 7.4-2.6 1.3-3.8-7.4L5 18.5z" fill={C.ink} stroke={C.white} strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  );
}

/** "Satire · fiktiv" – every parody is marked as such, inside the safe area. */
export function SatireTag() {
  return (
    <div
      style={{
        position: "absolute",
        top: SAFE.top - 20,
        left: SAFE.left,
        fontFamily: MONO,
        fontSize: 26,
        letterSpacing: "0.12em",
        textTransform: "uppercase",
        padding: "10px 18px",
        borderRadius: 999,
        background: "rgba(11,12,14,0.72)",
        color: "rgba(255,255,255,0.9)",
        zIndex: 50,
      }}
    >
      Satire · fiktiv
    </div>
  );
}
