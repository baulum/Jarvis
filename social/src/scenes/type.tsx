// Typography-led scenes: kinetic type (five styles) and a POV title card.
import { useCurrentFrame, useVideoConfig } from "remotion";
import { SAFE_W, fit, useTheme } from "../brand";
import { EASE_IN_OUT, pop, prog, rnd, typed } from "../motion";
import { Dotted, Label, Stage } from "../ui";

interface Line {
  text: string;
  accent?: boolean;
}

export function KineticType({ lines, style = "stack", kicker }: { lines: Line[]; style?: string; kicker?: string }) {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const t = useTheme();
  const longest = lines.reduce((a, l) => (l.text.length > a.length ? l.text : a), "");
  const size = fit(longest, SAFE_W, style === "slam" ? 200 : 168, 64);
  const step = Math.max(4, Math.floor((durationInFrames * 0.45) / Math.max(1, lines.length)));

  return (
    <Stage style={{ gap: 6 }}>
      {kicker ? <Label style={{ marginBottom: 28, opacity: prog(frame, 0, 8) }}>{kicker}</Label> : null}
      {lines.map((l, i) => {
        const start = i * step;
        const color = l.accent ? t.accent : t.fg;
        const common = {
          fontSize: size,
          fontWeight: 700,
          letterSpacing: "-0.05em",
          lineHeight: 0.98,
          color,
          whiteSpace: "nowrap" as const,
        };
        if (style === "typewriter") {
          return (
            <div key={i} style={{ ...common, fontWeight: 600 }}>
              <Dotted text={typed(l.text, frame, start, fps, 26)} />
              {frame >= start && typed(l.text, frame, start, fps, 26).length < l.text.length ? (
                <span style={{ color: t.accent }}>▍</span>
              ) : null}
            </div>
          );
        }
        if (style === "slam") {
          const s = pop(frame, fps, start, 11, 0.5);
          return (
            <div key={i} style={{ ...common, scale: String(2.4 - 1.4 * s), opacity: Math.min(1, s * 1.6), transformOrigin: "left center" }}>
              <Dotted text={l.text} />
            </div>
          );
        }
        if (style === "split") {
          const p = prog(frame, start, 14);
          return (
            <div key={i} style={{ ...common, overflow: "hidden", paddingBottom: 8 }}>
              <div style={{ translate: `0 ${(1 - p) * 110}%` }}>
                <Dotted text={l.text} />
              </div>
            </div>
          );
        }
        if (style === "scale") {
          const p = prog(frame, start, 18, EASE_IN_OUT);
          return (
            <div key={i} style={{ ...common, opacity: p, letterSpacing: `${-0.05 + (1 - p) * 0.25}em`, filter: `blur(${(1 - p) * 14}px)` }}>
              <Dotted text={l.text} />
            </div>
          );
        }
        // stack: words rise one after another
        const words = l.text.split(" ");
        return (
          <div key={i} style={{ ...common, display: "flex", flexWrap: "wrap", columnGap: "0.24em" }}>
            {words.map((w, j) => {
              const p = prog(frame, start + j * 3, 12);
              return (
                <span key={j} style={{ display: "inline-block", opacity: p, translate: `0 ${(1 - p) * 60}px`, rotate: `${(1 - p) * (rnd(i * 9 + j) * 6 - 3)}deg` }}>
                  <Dotted text={w} />
                </span>
              );
            })}
          </div>
        );
      })}
    </Stage>
  );
}

export function PovTitle({ text, caption }: { text: string; caption?: string }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = useTheme();
  const size = fit(text, SAFE_W * 2.2, 96, 60);
  const p = pop(frame, fps, 0, 16);
  return (
    <Stage>
      <div
        style={{
          alignSelf: "flex-start",
          fontWeight: 800,
          fontSize: 120,
          letterSpacing: "-0.04em",
          color: t.surface === "accent" ? t.fg : t.accent,
          scale: String(0.6 + 0.4 * p),
          transformOrigin: "left bottom",
        }}
      >
        POV:
      </div>
      <div style={{ fontSize: size, fontWeight: 600, lineHeight: 1.08, letterSpacing: "-0.03em", textWrap: "balance" }}>
        <Dotted text={typed(text, frame, 6, fps, 30)} />
      </div>
      {caption ? <Label style={{ opacity: prog(frame, 22, 10) }}>{caption}</Label> : null}
    </Stage>
  );
}
