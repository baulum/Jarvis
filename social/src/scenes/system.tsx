// System-UI parodies: error dialog, crash screen, terminal, loading screen, countdown.
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { C, FONT, MONO, SAFE, SAFE_W, fit, useTheme } from "../brand";
import { EASE_IN_OUT, pace, pop, prog, rnd, typed } from "../motion";
import { Dotted, Headline, Label, Stage, Window } from "../ui";

const SEVERITY = {
  error: { color: C.danger, glyph: "!" },
  warning: { color: C.warn, glyph: "!" },
  critical: { color: C.danger, glyph: "×" },
} as const;

export function ErrorDialog({
  severity = "error",
  title,
  message,
  code,
  buttons = ["OK"],
}: {
  severity?: keyof typeof SEVERITY;
  title: string;
  message: string;
  code?: string;
  buttons?: string[];
}) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const sev = SEVERITY[severity] ?? SEVERITY.error;
  const p = pop(frame, fps, 0, 10, 0.5);
  const shake = frame < 14 ? Math.sin(frame * 2.3) * (14 - frame) * 1.4 : 0;
  return (
    <Stage>
      <div style={{ scale: String(0.82 + 0.18 * p), opacity: Math.min(1, p * 2), translate: `${shake}px 0` }}>
        <Window title="Systemmeldung" style={{ borderRadius: 34 }}>
          <div style={{ padding: "54px 50px 46px", display: "flex", flexDirection: "column", gap: 26, color: C.ink, fontFamily: FONT }}>
            <div style={{ display: "flex", alignItems: "center", gap: 28 }}>
              <div style={{ width: 108, height: 108, borderRadius: 54, background: sev.color, display: "grid", placeItems: "center", color: C.white, fontSize: 72, fontWeight: 800, flexShrink: 0 }}>
                {sev.glyph}
              </div>
              <div style={{ fontSize: fit(title, SAFE_W - 240, 64, 46, 0.6), fontWeight: 700, letterSpacing: "-0.02em", lineHeight: 1.08 }}>{title}</div>
            </div>
            <div style={{ fontSize: 44, lineHeight: 1.25, color: "#2b2d33" }}>{message}</div>
            {code ? <div style={{ fontFamily: MONO, fontSize: 32, color: C.quiet }}>Code: {code}</div> : null}
            <div style={{ display: "flex", gap: 20, justifyContent: "flex-end", marginTop: 8 }}>
              {buttons.map((b, i) => (
                <div
                  key={i}
                  style={{
                    padding: "20px 38px",
                    borderRadius: 18,
                    fontSize: 40,
                    fontWeight: 600,
                    background: i === buttons.length - 1 ? C.accent : "#eceef1",
                    color: i === buttons.length - 1 ? C.white : C.ink,
                  }}
                >
                  {b}
                </div>
              ))}
            </div>
          </div>
        </Window>
      </div>
    </Stage>
  );
}

export function SystemCrash({ headline, lines = [], percent }: { headline: string; lines?: string[]; percent?: number }) {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const t = useTheme();
  // glitch bursts: hard at the impact, then short irregular bursts – between them the text stands still to be read
  const burst = frame < 7 || (frame % 13 < 2 && rnd(Math.floor(frame / 13)) > 0.35);
  const amp = frame < 7 ? 1 - frame / 9 : burst ? 0.55 : 0;
  const shake = amp ? { x: (rnd(frame * 5) - 0.5) * 70 * amp, y: (rnd(frame * 11) - 0.5) * 40 * amp } : { x: 0, y: 0 };
  // CRT power-off in the last frames: the picture collapses to a line, then a dot
  const off = prog(frame, durationInFrames - 7, 6, EASE_IN_OUT);
  const pct = percent === undefined ? undefined : Math.round(interpolate(frame, [6, durationInFrames - 8], [0, percent], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }));
  const split = amp * 16;

  const body = (
    <div style={{ position: "absolute", top: SAFE.top, left: SAFE.left, right: SAFE.right, bottom: SAFE.bottom, display: "flex", flexDirection: "column", justifyContent: "center", gap: 34 }}>
      <div style={{ fontSize: 230, fontWeight: 300, lineHeight: 0.9, letterSpacing: "-0.04em", textShadow: split ? `${-split}px 0 ${C.accent}, ${split}px 0 ${C.danger}` : "none" }}>:(</div>
      <div style={{ fontFamily: FONT, fontSize: fit(headline, SAFE_W * 1.8, 96, 56), fontWeight: 700, lineHeight: 1.04, letterSpacing: "-0.03em", textWrap: "balance", textShadow: split ? `${-split * 0.6}px 0 ${C.accent}, ${split * 0.6}px 0 ${C.danger}` : "none" }}>
        <Dotted text={headline} color={C.accentOnDark} />
      </div>
      {lines.map((l, i) => (
        <div key={i} style={{ fontSize: 40, lineHeight: 1.3, color: "rgba(255,255,255,0.78)", opacity: prog(frame, 8 + i * 6, 6) }}>
          {l}
        </div>
      ))}
      {pct !== undefined ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div style={{ fontSize: 44, color: C.accentOnDark }}>{pct} % abgeschlossen</div>
          <div style={{ height: 10, background: "rgba(255,255,255,0.12)", borderRadius: 5, overflow: "hidden" }}>
            <div style={{ width: `${pct}%`, height: "100%", background: C.accentOnDark }} />
          </div>
        </div>
      ) : null}
    </div>
  );
  // during a burst the picture tears into horizontal bands that slip sideways
  const bands = burst ? 6 : 1;
  return (
    <AbsoluteFill style={{ background: "#000", fontFamily: MONO, color: C.white }}>
      <AbsoluteFill style={{ background: t.surface === "light" ? C.ink : t.bg, scale: `${1 + off * 0.02} ${1 - off * 0.995}`, filter: `brightness(${1 + off * 2.5})`, opacity: 1 - prog(frame, durationInFrames - 2, 2) }}>
        <AbsoluteFill style={{ translate: `${shake.x}px ${shake.y}px` }}>
          {Array.from({ length: bands }, (_, i) => {
            const top = (i / bands) * 100;
            const dx = bands > 1 ? (rnd(frame * 17 + i * 31) - 0.5) * 140 * amp : 0;
            return (
              <AbsoluteFill key={i} style={{ clipPath: bands > 1 ? `inset(${top}% 0 ${100 - top - 100 / bands}% 0)` : undefined, translate: `${dx}px 0` }}>
                {body}
              </AbsoluteFill>
            );
          })}
        </AbsoluteFill>
        {/* compression blocks */}
        {burst
          ? Array.from({ length: 5 }, (_, i) => (
              <div key={i} style={{ position: "absolute", left: `${rnd(frame * 3 + i) * 85}%`, top: `${rnd(frame * 7 + i * 13) * 90}%`, width: 60 + rnd(frame + i * 5) * 260, height: 14 + rnd(frame * 2 + i) * 50, background: [C.accent, C.danger, C.white, C.accentOnDark, C.graphite][i], opacity: 0.55 * amp + 0.25 }} />
            ))
          : null}
        {/* scanlines + rolling bar */}
        <AbsoluteFill style={{ backgroundImage: "repeating-linear-gradient(0deg, rgba(255,255,255,0.05) 0 2px, transparent 2px 6px)" }} />
        <div style={{ position: "absolute", left: 0, right: 0, height: 220, top: `${((frame * 2.4) % 120) - 10}%`, background: "linear-gradient(180deg, transparent, rgba(255,255,255,0.05), transparent)" }} />
        <AbsoluteFill style={{ background: "radial-gradient(120% 80% at 50% 50%, transparent 55%, rgba(0,0,0,0.55))" }} />
        {frame < 3 ? <AbsoluteFill style={{ background: C.white, opacity: 1 - frame / 3 }} /> : null}
      </AbsoluteFill>
    </AbsoluteFill>
  );
}

interface TermLine {
  kind: "cmd" | "out" | "ok" | "err";
  text: string;
}

export function Terminal({ lines, title = "terminal" }: { lines: TermLine[]; title?: string }) {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  // all lines are on screen by ~65 % of the scene: commands type faster when the scene is short
  const cmdChars = lines.reduce((n, l) => n + (l.kind === "cmd" ? l.text.length : 0), 0);
  const gap = Math.min(7, Math.max(3, Math.floor((durationInFrames * 0.25) / Math.max(1, lines.length))));
  const cps = pace(cmdChars, 4, durationInFrames * 0.65 - gap * lines.length, fps, 24);
  let at = 4;
  const timed = lines.map((l) => {
    const start = at;
    at += l.kind === "cmd" ? Math.ceil((l.text.length / cps) * fps) + gap : gap;
    return { l, start };
  });
  const color = { cmd: C.white, out: "rgba(255,255,255,0.68)", ok: "#6ee7a8", err: "#ff8a8d" } as const;
  return (
    <Stage>
      <Window title={title} dark style={{ minHeight: 860 }}>
        <div style={{ padding: "40px 40px", fontFamily: MONO, fontSize: 40, lineHeight: 1.45, display: "flex", flexDirection: "column", gap: 4 }}>
          {timed.map(({ l, start }, i) => {
            if (frame < start) return null;
            const text = l.kind === "cmd" ? typed(l.text, frame, start, fps, cps) : l.text;
            const done = text.length === l.text.length;
            return (
              <div key={i} style={{ color: color[l.kind], whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                {l.kind === "cmd" ? <span style={{ color: C.accentOnDark }}>$ </span> : l.kind === "ok" ? "✓ " : l.kind === "err" ? "✗ " : ""}
                {text}
                {l.kind === "cmd" && !done ? <span style={{ background: C.white, color: C.ink }}> </span> : null}
              </div>
            );
          })}
          {frame % 20 < 10 && timed.every(({ l, start }) => frame > start + (l.text.length / cps) * fps) ? (
            <div style={{ color: C.accentOnDark }}>$ <span style={{ background: C.white }}> </span></div>
          ) : null}
        </div>
      </Window>
    </Stage>
  );
}

export function Loading({ label, stall_at = 99, caption }: { label: string; stall_at?: number; caption?: string }) {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const t = useTheme();
  // fast at first, then stuck at `stall_at` – the classic feeling of a slow page
  const pct = Math.min(stall_at, Math.round(interpolate(frame, [0, durationInFrames * 0.45], [0, stall_at], { extrapolateRight: "clamp", easing: EASE_IN_OUT })));
  const spin = frame * 9;
  return (
    <Stage>
      <div style={{ width: 150, height: 150, borderRadius: 75, border: `14px solid ${t.line}`, borderTopColor: t.fill, rotate: `${spin}deg`, alignSelf: "flex-start" }} />
      <Headline text={label} size={fit(label, SAFE_W * 1.9, 96, 56)} />
      <div style={{ height: 22, borderRadius: 11, background: t.line, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: t.fill }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <Label>{caption ?? "Bitte warten"}</Label>
        <div style={{ fontFamily: MONO, fontSize: 64, fontWeight: 500, color: t.fg }}>{pct} %</div>
      </div>
    </Stage>
  );
}

export function Countdown({ from = 3, label, end_text }: { from?: number; label: string; end_text: string }) {
  const frame = useCurrentFrame();
  const { durationInFrames, fps } = useVideoConfig();
  const t = useTheme();
  const tick = Math.max(6, Math.floor((durationInFrames - 14) / (from + 1)));
  const idx = Math.floor(frame / tick);
  const n = from - idx;
  const local = frame - idx * tick;
  const p = pop(local, fps, 0, 12, 0.5);
  const showEnd = idx >= from;
  return (
    <Stage>
      <Label>{label}</Label>
      <div style={{ position: "relative", height: 640, display: "flex", alignItems: "center" }}>
        <svg width="640" height="640" style={{ position: "absolute", left: 0 }}>
          <circle cx="320" cy="320" r="296" stroke={t.line} strokeWidth="18" fill="none" />
          <circle cx="320" cy="320" r="296" stroke={t.fill} strokeWidth="18" fill="none" strokeDasharray={2 * Math.PI * 296} strokeDashoffset={2 * Math.PI * 296 * (local / tick)} transform="rotate(-90 320 320)" />
        </svg>
        <div style={{ width: 640, textAlign: "center", fontSize: showEnd ? fit(end_text, 560, 150, 64) : 420, fontWeight: 700, letterSpacing: "-0.06em", scale: String(1.4 - 0.4 * p), opacity: Math.min(1, p * 2), color: showEnd ? t.accent : t.fg }}>
          {showEnd ? end_text : n}
        </div>
      </div>
    </Stage>
  );
}
