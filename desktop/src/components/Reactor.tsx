// The arc reactor: the one expressive instrument of the UI. It visualises the voice pipeline
// (idle | listening | recording | thinking | speaking) and the live input/output level.
import { useEffect, useId, useRef } from "react";
import { useReducedMotion } from "framer-motion";

export type ReactorMode = "idle" | "listening" | "recording" | "thinking" | "speaking" | "offline" | "muted";

const TICKS = 36;
const SEGMENTS = 12;

function polar(r: number, a: number): [number, number] {
  return [100 + r * Math.cos(a), 100 + r * Math.sin(a)];
}

function segmentPath(r1: number, r2: number, a0: number, a1: number): string {
  const [x0, y0] = polar(r2, a0);
  const [x1, y1] = polar(r2, a1);
  const [x2, y2] = polar(r1, a1);
  const [x3, y3] = polar(r1, a0);
  return `M${x0.toFixed(2)},${y0.toFixed(2)} A${r2},${r2} 0 0 1 ${x1.toFixed(2)},${y1.toFixed(2)} L${x2.toFixed(2)},${y2.toFixed(2)} A${r1},${r1} 0 0 0 ${x3.toFixed(2)},${y3.toFixed(2)} Z`;
}

const SEG_PATHS = Array.from({ length: SEGMENTS }, (_, i) => {
  const step = (Math.PI * 2) / SEGMENTS;
  const a0 = i * step - Math.PI / 2 + 0.06;
  return segmentPath(52, 66, a0, a0 + step - 0.12);
});

const TICK_LINES = Array.from({ length: TICKS }, (_, i) => {
  const a = (i / TICKS) * Math.PI * 2 - Math.PI / 2;
  const long = i % 3 === 0;
  const [x1, y1] = polar(long ? 82 : 85, a);
  const [x2, y2] = polar(90, a);
  return { x1, y1, x2, y2, long };
});

const SPIN: Record<ReactorMode, number> = {
  idle: 0.006,
  listening: 0.02,
  recording: 0.035,
  thinking: 0.11,
  speaking: 0.03,
  offline: 0,
  muted: 0,
};

export function Reactor({
  mode,
  level = 0,
  size = 280,
  className = "",
  label,
}: {
  mode: ReactorMode;
  level?: number;
  size?: number;
  className?: string;
  label?: string;
}) {
  const reduced = useReducedMotion();
  const gid = `rc-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const ringRef = useRef<SVGGElement>(null);
  const arcsRef = useRef<SVGGElement>(null);
  const coreRef = useRef<SVGCircleElement>(null);
  const haloRef = useRef<SVGCircleElement>(null);
  const segRefs = useRef<(SVGPathElement | null)[]>([]);
  const target = useRef(level);
  const modeRef = useRef(mode);
  target.current = Number.isFinite(level) ? Math.max(0, Math.min(1, level)) : 0;
  modeRef.current = mode;

  useEffect(() => {
    let raf = 0;
    let angle = 0;
    let arcAngle = 0;
    let smooth = 0;
    let t = 0;
    const frame = () => {
      const m = modeRef.current;
      t += 1;
      // ease the level so sparse status events still produce fluid motion
      const live = m === "recording" || m === "speaking" || m === "listening";
      const goal = live ? target.current : m === "thinking" ? 0.25 + 0.15 * Math.sin(t / 18) : 0;
      smooth += (goal - smooth) * 0.12;
      if (!reduced) {
        angle = (angle + SPIN[m]) % 360;
        arcAngle = (arcAngle - SPIN[m] * 9) % 360;
      }
      ringRef.current?.setAttribute("transform", `rotate(${angle.toFixed(3)} 100 100)`);
      arcsRef.current?.setAttribute("transform", `rotate(${arcAngle.toFixed(3)} 100 100)`);
      const coreR = 22 + smooth * 14;
      coreRef.current?.setAttribute("r", coreR.toFixed(2));
      haloRef.current?.setAttribute("r", (34 + smooth * 26).toFixed(2));
      haloRef.current?.setAttribute("opacity", (0.18 + smooth * 0.5).toFixed(3));
      const lit = Math.round(smooth * SEGMENTS * 1.4);
      segRefs.current.forEach((el, i) => {
        if (!el) return;
        const base = m === "offline" || m === "muted" ? 0.16 : 0.42;
        const on = i < lit || (m === "thinking" && (i + Math.floor(t / 6)) % SEGMENTS < 3);
        el.setAttribute("opacity", (on ? 0.95 : base).toFixed(2));
      });
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [reduced]);

  const dim = mode === "offline";
  const muted = mode === "muted";
  const stroke = dim ? "var(--color-ink-faint)" : muted ? "var(--color-ember)" : "var(--color-arc)";

  return (
    <svg
      viewBox="0 0 200 200"
      width={size}
      height={size}
      className={className}
      role="img"
      aria-label={label ?? `Sprachstatus: ${mode}`}
    >
      <defs>
        <radialGradient id={gid} cx="50%" cy="50%" r="50%">
          <stop offset="0" stopColor={dim ? "#9fb1ba" : "#f3fdff"} />
          <stop offset="0.45" stopColor={dim ? "#52666f" : muted ? "#e2a14c" : "#8fe4f1"} />
          <stop offset="1" stopColor={dim ? "#1b262d" : "#2a8394"} stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle ref={haloRef} cx="100" cy="100" r="40" fill={`url(#${gid})`} opacity={dim ? 0.08 : 0.2} />
      <g ref={ringRef} stroke={stroke} strokeLinecap="round">
        {TICK_LINES.map((tk, i) => (
          <line
            key={i}
            x1={tk.x1}
            y1={tk.y1}
            x2={tk.x2}
            y2={tk.y2}
            strokeWidth={tk.long ? 1.1 : 0.6}
            opacity={tk.long ? 0.55 : 0.25}
          />
        ))}
      </g>
      <circle cx="100" cy="100" r="74" fill="none" stroke={stroke} strokeWidth="0.5" opacity="0.35" />
      <g ref={arcsRef} fill="none" stroke={stroke} strokeWidth="1.2" strokeLinecap="round" opacity={dim ? 0.2 : 0.6}>
        <path d={describeArc(78, -80, -20)} />
        <path d={describeArc(78, 40, 100)} />
        <path d={describeArc(78, 160, 220)} />
      </g>
      <g fill={stroke}>
        {SEG_PATHS.map((d, i) => (
          <path
            key={i}
            d={d}
            ref={(el) => {
              segRefs.current[i] = el;
            }}
            opacity="0.42"
          />
        ))}
      </g>
      <circle cx="100" cy="100" r="44" fill="none" stroke={stroke} strokeWidth="1.5" opacity="0.5" />
      <circle ref={coreRef} cx="100" cy="100" r="22" fill={`url(#${gid})`} />
      <circle cx="100" cy="100" r="9" fill={dim ? "#6c7f88" : "#effcff"} opacity={dim ? 0.5 : 0.95} />
    </svg>
  );
}

function describeArc(r: number, startDeg: number, endDeg: number): string {
  const a0 = (startDeg * Math.PI) / 180;
  const a1 = (endDeg * Math.PI) / 180;
  const [x0, y0] = polar(r, a0);
  const [x1, y1] = polar(r, a1);
  const large = endDeg - startDeg > 180 ? 1 : 0;
  return `M${x0.toFixed(2)},${y0.toFixed(2)} A${r},${r} 0 ${large} 1 ${x1.toFixed(2)},${y1.toFixed(2)}`;
}

/** Maps the raw voice state into a reactor mode. */
export function reactorMode(state: string | undefined, connected: boolean, muted: boolean): ReactorMode {
  if (!connected) return "offline";
  if (muted) return "muted";
  switch (state) {
    case "listening":
    case "recording":
    case "thinking":
    case "speaking":
      return state;
    default:
      return "idle";
  }
}
