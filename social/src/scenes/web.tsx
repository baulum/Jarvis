// Web scenes: browser (old/new/loading/404), tab chaos, 3D browser, search results, split screen, cursor story,
// glass stack. The "new site" is a neutral example layout – never a real customer site.
import type { CSSProperties } from "react";
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { C, FONT, MONO, SAFE, SAFE_W, fit, useTheme } from "../brand";
import { EASE_IN_OUT, EASE_OUT, mix, pace, pop, prog, rnd, typed } from "../motion";
import { Button, Cursor, Dotted, Headline, Label, Stage, Window } from "../ui";

export function OldSite({ scale = 1 }: { scale?: number }) {
  const frame = useCurrentFrame();
  return (
    <div style={{ position: "absolute", inset: 0, background: "#c9c9c9", fontFamily: "'Times New Roman', Times, serif", color: "#000", overflow: "hidden", fontSize: 30 * scale }}>
      <div style={{ background: "linear-gradient(#5a7fd6,#22408f)", color: "#ff0", padding: 18 * scale, fontSize: 46 * scale, fontWeight: 700, textAlign: "center", textShadow: "2px 2px #000" }}>
        Willkommen auf unserer Homepage!!!
      </div>
      <div style={{ background: "#000", color: "#0f0", fontFamily: "'Courier New', monospace", padding: 6 * scale, whiteSpace: "nowrap", translate: `${600 - ((frame * 6) % 1600)}px 0` }}>
        +++ NEU: Jetzt auch mit Kontaktformular +++ Letzte Aktualisierung: 2011 +++
      </div>
      <div style={{ display: "flex", gap: 14 * scale, padding: 14 * scale }}>
        <div style={{ width: "30%", background: "#e0e0e0", border: "2px outset #fff", padding: 10 * scale, lineHeight: 1.6 }}>
          <u style={{ color: "#00e" }}>Startseite</u>
          <br />
          <u style={{ color: "#00e" }}>Über uns</u>
          <br />
          <u style={{ color: "#551a8b" }}>Gästebuch</u>
          <br />
          <u style={{ color: "#00e" }}>Links</u>
        </div>
        <div style={{ flex: 1, background: "#fff", border: "2px inset #999", padding: 14 * scale, lineHeight: 1.35 }}>
          <b>Herzlich Willkommen!</b> Diese Seite befindet sich im Aufbau.
          <div style={{ margin: `${14 * scale}px 0`, height: 90 * scale, background: "repeating-linear-gradient(45deg,#ffd400 0 18px,#000 18px 36px)" }} />
          <span style={{ fontSize: 22 * scale }}>Optimiert für 1024×768 und Internet Explorer 6.</span>
        </div>
      </div>
      <div style={{ textAlign: "center", fontFamily: "'Courier New', monospace", fontSize: 26 * scale }}>
        Besucher: <span style={{ background: "#000", color: "#f00", padding: "0 8px" }}>000{(4211 + frame).toString().slice(-4)}</span>
      </div>
    </div>
  );
}

export function NewSite({ headline = "Ihr Betrieb. Online.", scale = 1, reveal = 1 }: { headline?: string; scale?: number; reveal?: number }) {
  const s = scale;
  return (
    <div style={{ position: "absolute", inset: 0, background: C.canvas, fontFamily: FONT, color: C.ink, overflow: "hidden" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: `${26 * s}px ${34 * s}px` }}>
        <div style={{ fontWeight: 800, fontSize: 30 * s, letterSpacing: "-0.03em" }}>
          Muster<span style={{ color: C.accent }}>.</span>
        </div>
        <div style={{ display: "flex", gap: 20 * s, fontSize: 22 * s, color: C.slate }}>
          <span>Leistungen</span>
          <span>Kontakt</span>
        </div>
      </div>
      <div style={{ padding: `${40 * s}px ${34 * s}px`, opacity: reveal, translate: `0 ${(1 - reveal) * 40}px` }}>
        <div style={{ fontFamily: MONO, fontSize: 18 * s, letterSpacing: "0.14em", color: C.slate, textTransform: "uppercase" }}>Beispielseite</div>
        <div style={{ fontSize: 74 * s, fontWeight: 700, letterSpacing: "-0.045em", lineHeight: 1, marginTop: 16 * s }}>
          <Dotted text={headline} color={C.accent} />
        </div>
        <div style={{ marginTop: 26 * s, display: "inline-block", background: C.accent, color: C.white, borderRadius: 999, padding: `${14 * s}px ${26 * s}px`, fontSize: 24 * s, fontWeight: 600 }}>
          Termin anfragen →
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 * s, marginTop: 36 * s }}>
          {[C.graphite, C.accentSoft, C.white, C.line].map((c, i) => (
            <div key={i} style={{ height: 120 * s, borderRadius: 18 * s, background: c }} />
          ))}
        </div>
      </div>
    </div>
  );
}

function NotFound() {
  return (
    <div style={{ position: "absolute", inset: 0, background: C.white, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", fontFamily: FONT, color: C.ink, gap: 12 }}>
      <div style={{ fontSize: 200, fontWeight: 800, letterSpacing: "-0.06em" }}>404</div>
      <div style={{ fontSize: 44, color: C.slate }}>Seite nicht gefunden</div>
    </div>
  );
}

export function Browser({ url, state = "old", headline }: { url: string; state?: "old" | "new" | "loading" | "not_found"; headline?: string }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = pop(frame, fps, 0, 16);
  const shownUrl = typed(url, frame, 2, fps, 34);
  return (
    <Stage>
      {headline ? <Headline text={headline} size={fit(headline, SAFE_W * 2, 80, 52)} style={{ opacity: prog(frame, 4, 10) }} /> : null}
      <div style={{ scale: String(0.9 + 0.1 * p), opacity: Math.min(1, p * 1.5) }}>
        <Window url={shownUrl} height={880}>
          {state === "old" ? <OldSite /> : null}
          {state === "new" ? <NewSite reveal={prog(frame, 6, 16)} /> : null}
          {state === "not_found" ? <NotFound /> : null}
          {state === "loading" ? (
            <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", background: C.white }}>
              <div style={{ width: 120, height: 120, borderRadius: 60, border: `12px solid ${C.line}`, borderTopColor: C.accent, rotate: `${frame * 10}deg` }} />
            </div>
          ) : null}
        </Window>
      </div>
    </Stage>
  );
}

export function TabChaos({ tabs, punchline }: { tabs: string[]; punchline: string }) {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const t = useTheme();
  const per = Math.max(3, Math.floor((durationInFrames * 0.55) / tabs.length));
  return (
    <Stage>
      <div style={{ position: "relative", height: 900 }}>
        {tabs.map((tab, i) => {
          const start = i * per;
          if (frame < start) return null;
          const p = pop(frame, fps, start, 12);
          const x = rnd(i + 1) * 220 - 40;
          const y = i * 92;
          return (
            <div
              key={i}
              style={{
                position: "absolute",
                left: x,
                top: y,
                width: SAFE_W - 180,
                padding: "22px 30px",
                borderRadius: "22px 22px 0 0",
                background: i % 2 ? C.white : "#f2f3f5",
                color: C.ink,
                fontSize: 40,
                fontFamily: FONT,
                boxShadow: "0 18px 40px -18px rgba(11,12,14,0.45)",
                display: "flex",
                justifyContent: "space-between",
                rotate: `${(rnd(i * 3) - 0.5) * 6}deg`,
                scale: String(0.6 + 0.4 * p),
                opacity: Math.min(1, p * 1.5),
              }}
            >
              <span style={{ overflow: "hidden", whiteSpace: "nowrap", textOverflow: "ellipsis" }}>{tab}</span>
              <span style={{ color: C.quiet, marginLeft: 20 }}>×</span>
            </div>
          );
        })}
      </div>
      <Headline text={punchline} size={fit(punchline, SAFE_W * 2, 92, 54)} style={{ color: t.fg, opacity: prog(frame, durationInFrames * 0.6, 10) }} />
    </Stage>
  );
}

export function Browser3D({ headline, url }: { headline: string; url: string }) {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const rot = interpolate(frame, [0, durationInFrames], [-28, 8], { easing: EASE_IN_OUT });
  const tilt = interpolate(frame, [0, durationInFrames], [18, 4], { easing: EASE_IN_OUT });
  const p = prog(frame, 0, 18);
  return (
    <Stage>
      <Headline text={headline} size={fit(headline, SAFE_W * 2, 96, 56)} style={{ opacity: prog(frame, 6, 12), translate: `0 ${(1 - prog(frame, 6, 12)) * 40}px` }} />
      <div style={{ perspective: 2000, height: 980, position: "relative" }}>
        {[2, 1, 0].map((layer) => (
          <div
            key={layer}
            style={{
              position: "absolute",
              inset: 0,
              transform: `rotateY(${rot}deg) rotateX(${tilt}deg) translateZ(${-layer * 160 * p}px) translateX(${layer * 50 * p}px)`,
              transformStyle: "preserve-3d",
              opacity: layer === 0 ? 1 : 0.35 / layer,
            }}
          >
            <Window url={layer === 0 ? url : ""} height={940}>
              {layer === 0 ? <NewSite reveal={prog(frame, 10, 18)} scale={1.05} /> : <div style={{ position: "absolute", inset: 0, background: C.accentSoft }} />}
            </Window>
          </div>
        ))}
      </div>
    </Stage>
  );
}

interface Result {
  title: string;
  url: string;
  note?: string;
  highlight?: boolean;
}

export function Search({ query, results }: { query: string; results: Result[] }) {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const cps = pace(query.length, 2, durationInFrames * 0.35, fps, 26);
  const q = typed(query, frame, 2, fps, cps);
  const typedDone = 2 + Math.ceil((query.length / cps) * fps);
  return (
    <Stage>
      <div style={{ background: C.white, borderRadius: 999, padding: "30px 42px", display: "flex", alignItems: "center", gap: 26, boxShadow: "0 20px 50px -24px rgba(11,12,14,0.4)" }}>
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none">
          <circle cx="11" cy="11" r="7" stroke={C.slate} strokeWidth="2.4" />
          <path d="M20 20l-4-4" stroke={C.slate} strokeWidth="2.4" strokeLinecap="round" />
        </svg>
        <div style={{ fontSize: 46, color: C.ink, fontFamily: FONT, whiteSpace: "nowrap", overflow: "hidden" }}>
          {q}
          {q.length < query.length || frame % 16 < 8 ? <span style={{ color: C.accent }}>|</span> : null}
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
        {results.map((r, i) => {
          const p = prog(frame, typedDone + 4 + i * 6, 12);
          return (
            <div
              key={i}
              style={{
                background: C.white,
                borderRadius: 26,
                padding: "28px 34px",
                fontFamily: FONT,
                opacity: p,
                translate: `0 ${(1 - p) * 40}px`,
                outline: r.highlight ? `6px solid ${C.accent}` : "none",
                scale: r.highlight ? String(mix(1, 1.03, prog(frame, typedDone + 20, 10))) : "1",
              }}
            >
              <div style={{ fontFamily: MONO, fontSize: 28, color: C.quiet }}>{r.url}</div>
              <div style={{ fontSize: 44, fontWeight: 600, color: r.highlight ? C.accentDeep : "#1a0dab", marginTop: 6, lineHeight: 1.12 }}>{r.title}</div>
              {r.note ? <div style={{ fontSize: 40, color: C.slate, marginTop: 8, lineHeight: 1.2 }}>{r.note}</div> : null}
            </div>
          );
        })}
      </div>
    </Stage>
  );
}

export function SplitScreen({ left_label, right_label, left_caption, right_caption }: { left_label: string; right_label: string; left_caption?: string; right_caption?: string }) {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  // the divider sweeps from the right towards the middle: "old" fills the screen first, "new" takes over
  const split = interpolate(frame, [0, durationInFrames * 0.5], [100, 50], { extrapolateRight: "clamp", easing: EASE_OUT });
  const label = (text: string, caption: string | undefined, side: "l" | "r"): CSSProperties & { text: string; caption?: string } => ({ text, caption, ...(side === "l" ? { left: SAFE.left } : { right: SAFE.right }) });
  const tags = [label(left_label, left_caption, "l"), label(right_label, right_caption, "r")];
  return (
    <AbsoluteFill style={{ background: C.ink }}>
      <AbsoluteFill style={{ clipPath: `inset(0 ${100 - split}% 0 0)` }}>
        <div style={{ position: "absolute", top: 380, bottom: 520, left: 40, width: 1000 }}>
          <OldSite scale={1.15} />
        </div>
      </AbsoluteFill>
      <AbsoluteFill style={{ clipPath: `inset(0 0 0 ${split}%)` }}>
        <div style={{ position: "absolute", top: 380, bottom: 520, left: 40, width: 1000, borderRadius: 30, overflow: "hidden" }}>
          <NewSite scale={1.25} reveal={prog(frame, durationInFrames * 0.3, 14)} />
        </div>
      </AbsoluteFill>
      <div style={{ position: "absolute", top: 330, bottom: 470, left: `${split}%`, width: 8, marginLeft: -4, background: C.white, boxShadow: `0 0 0 6px ${C.accent}` }} />
      {tags.map(({ text, caption, ...pos }, i) => (
        <div key={i} style={{ position: "absolute", top: SAFE.top - 10, ...pos, textAlign: i ? "right" : "left", color: C.white, fontFamily: FONT, opacity: i ? prog(frame, durationInFrames * 0.35, 10) : 1 }}>
          <div style={{ fontSize: 56, fontWeight: 700, letterSpacing: "-0.03em" }}>
            <Dotted text={text} color={C.accentOnDark} />
          </div>
          {caption ? <div style={{ fontSize: 40, color: "rgba(255,255,255,0.72)" }}>{caption}</div> : null}
        </div>
      ))}
    </AbsoluteFill>
  );
}

export function CursorClick({ headline, button, steps = [] }: { headline?: string; button: string; steps?: string[] }) {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const t = useTheme();
  const travelEnd = Math.floor(durationInFrames * 0.55);
  const x = interpolate(frame, [0, travelEnd], [760, 300], { extrapolateRight: "clamp", easing: EASE_IN_OUT });
  const y = interpolate(frame, [0, travelEnd], [1180, 120 + (headline ? 220 : 0) + steps.length * 96 + 40], { extrapolateRight: "clamp", easing: EASE_IN_OUT });
  const press = frame >= travelEnd && frame < travelEnd + 6 ? 1 : 0;
  const clicked = frame >= travelEnd + 3;
  return (
    <Stage center={false} style={{ paddingTop: 120 }}>
      {headline ? <Headline text={headline} size={fit(headline, SAFE_W * 2, 88, 54)} /> : null}
      {steps.map((s, i) => {
        const p = prog(frame, i * 6, 10);
        return (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 22, fontSize: 46, opacity: p, translate: `${(1 - p) * -30}px 0` }}>
            <div style={{ width: 52, height: 52, borderRadius: 26, background: t.fill, color: t.onFill, display: "grid", placeItems: "center", fontSize: 30, fontWeight: 700 }}>✓</div>
            {s}
          </div>
        );
      })}
      <div style={{ position: "relative" }}>
        <Button text={button} style={{ scale: String(press ? 0.94 : clicked ? 1.04 : 1), background: clicked ? t.fill : t.panel, color: clicked ? t.onFill : t.fg, border: clicked ? "none" : `3px solid ${t.line}` }} />
        {clicked ? (
          <div style={{ position: "absolute", left: 210, top: 60, width: 40, height: 40, marginLeft: -20, marginTop: -20, borderRadius: 999, border: `6px solid ${t.accent}`, scale: String(1 + (frame - travelEnd) * 0.35), opacity: Math.max(0, 1 - (frame - travelEnd) / 14) }} />
        ) : null}
      </div>
      <div style={{ position: "absolute", left: 0, top: 0 }}>
        <Cursor x={x} y={y} pressed={press} />
      </div>
    </Stage>
  );
}

export function GlassStack({ headline, items }: { headline: string; items: string[] }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = useTheme();
  return (
    <AbsoluteFill style={{ background: t.surface === "light" ? C.canvas : C.ink, overflow: "hidden" }}>
      {/* two soft light sources in the brand blue give the glass something to refract */}
      <div style={{ position: "absolute", width: 900, height: 900, borderRadius: 450, background: C.accent, filter: "blur(160px)", opacity: 0.55, left: -260 + Math.sin(frame / 30) * 40, top: 360 }} />
      <div style={{ position: "absolute", width: 700, height: 700, borderRadius: 350, background: C.accentOnDark, filter: "blur(170px)", opacity: 0.35, right: -260, top: 1000 + Math.cos(frame / 34) * 40 }} />
      <Stage style={{ gap: 26 }}>
        <Headline text={headline} size={fit(headline, SAFE_W * 2, 92, 56)} style={{ color: t.surface === "light" ? C.ink : C.white, marginBottom: 20 }} />
        {items.map((it, i) => {
          const p = pop(frame, fps, 8 + i * 7, 15);
          return (
            <div
              key={i}
              style={{
                padding: "34px 38px",
                borderRadius: 34,
                background: t.surface === "light" ? "rgba(255,255,255,0.55)" : "rgba(255,255,255,0.08)",
                border: `1.5px solid ${t.surface === "light" ? "rgba(255,255,255,0.9)" : "rgba(255,255,255,0.18)"}`,
                backdropFilter: "blur(28px)",
                color: t.surface === "light" ? C.ink : C.white,
                fontSize: 48,
                fontWeight: 560,
                letterSpacing: "-0.015em",
                display: "flex",
                alignItems: "center",
                gap: 24,
                translate: `${(1 - p) * 120}px 0`,
                opacity: Math.min(1, p * 1.4),
              }}
            >
              <span style={{ fontFamily: MONO, fontSize: 32, color: t.surface === "light" ? C.accent : C.accentOnDark }}>0{i + 1}</span>
              {it}
            </div>
          );
        })}
      </Stage>
    </AbsoluteFill>
  );
}

export { Label };
