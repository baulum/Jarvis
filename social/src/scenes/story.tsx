// Satire formats (always marked "Satire · fiktiv"), brand facts and the end card.
import { AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { C, FONT, MONO, SAFE, SAFE_W, fit, useTheme } from "../brand";
import { EASE_IN_OUT, EASE_OUT, pop, prog, rnd } from "../motion";
import type { Fact } from "../types";
import { Button, Dotted, Headline, Label, SatireTag, Stage } from "../ui";

export function Review({ stars = 1, author, text }: { stars?: number; author: string; text: string }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = pop(frame, fps, 0, 14);
  return (
    <Stage>
      <SatireTag />
      <div style={{ background: C.white, color: C.ink, borderRadius: 40, padding: "52px 50px", fontFamily: FONT, scale: String(0.9 + 0.1 * p), opacity: Math.min(1, p * 1.6), boxShadow: "0 50px 100px -50px rgba(0,0,0,0.5)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 24 }}>
          <div style={{ width: 96, height: 96, borderRadius: 48, background: C.line, display: "grid", placeItems: "center", fontSize: 44, fontWeight: 700, color: C.slate }}>{author.slice(0, 1)}</div>
          <div>
            <div style={{ fontSize: 46, fontWeight: 650 }}>{author}</div>
            <div style={{ fontSize: 32, color: C.quiet }}>Bewertung · fiktiv</div>
          </div>
        </div>
        <div style={{ display: "flex", gap: 10, margin: "30px 0 22px" }}>
          {[0, 1, 2, 3, 4].map((i) => {
            const on = i < stars;
            const sp = pop(frame, fps, 6 + i * 3, 10);
            return (
              <svg key={i} width="78" height="78" viewBox="0 0 24 24" style={{ scale: String(0.4 + 0.6 * sp) }}>
                <path d="M12 2.5l2.9 6 6.6.9-4.8 4.6 1.2 6.5L12 17.4l-5.9 3.1 1.2-6.5L2.5 9.4l6.6-.9z" fill={on ? C.warn : "#e4e6ea"} />
              </svg>
            );
          })}
        </div>
        <div style={{ fontSize: 50, lineHeight: 1.22, letterSpacing: "-0.01em" }}>„{text}“</div>
      </div>
    </Stage>
  );
}

export function Weather({ location, forecast, temp, outlook = [] }: { location: string; forecast: string; temp: string; outlook?: string[] }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = pop(frame, fps, 0, 18);
  return (
    <AbsoluteFill style={{ background: "linear-gradient(180deg,#2a2f3a 0%,#0b0c0e 100%)", color: C.white, fontFamily: FONT }}>
      {/* rain: deterministic streaks */}
      {Array.from({ length: 70 }, (_, i) => {
        const x = rnd(i) * 1080;
        const speed = 28 + rnd(i + 99) * 18;
        const y = ((frame * speed + rnd(i + 7) * 2000) % 2100) - 100;
        return <div key={i} style={{ position: "absolute", left: x, top: y, width: 3, height: 70, background: "rgba(143,166,255,0.35)", rotate: "12deg" }} />;
      })}
      <SatireTag />
      <div style={{ position: "absolute", top: SAFE.top + 60, left: SAFE.left, right: SAFE.right, display: "flex", flexDirection: "column", gap: 20 }}>
        <Label style={{ color: "rgba(255,255,255,0.7)" }}>Wetterbericht</Label>
        <div style={{ fontSize: fit(location, SAFE_W, 84, 52), fontWeight: 650, letterSpacing: "-0.03em", lineHeight: 1.05 }}>{location}</div>
        <div style={{ display: "flex", alignItems: "center", gap: 30, marginTop: 30, scale: String(0.8 + 0.2 * p), transformOrigin: "left center" }}>
          <svg width="220" height="160" viewBox="0 0 64 44">
            <path d="M18 40h30a12 12 0 0 0 0-24 16 16 0 0 0-30-2A13 13 0 0 0 18 40z" fill="#9aa0aa" />
            <path d="M30 30l-5 10h6l-4 8" stroke={C.warn} strokeWidth="3" fill="none" strokeLinejoin="round" opacity={frame % 24 < 3 ? 1 : 0.15} />
          </svg>
          <div style={{ fontSize: 200, fontWeight: 300, letterSpacing: "-0.06em" }}>{temp}</div>
        </div>
        <div style={{ fontSize: fit(forecast, SAFE_W, 110, 60), fontWeight: 700, letterSpacing: "-0.04em" }}>
          <Dotted text={forecast} color={C.accentOnDark} />
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 14, marginTop: 20 }}>
          {outlook.map((o, i) => (
            <div key={i} style={{ fontSize: 44, color: "rgba(255,255,255,0.82)", opacity: prog(frame, 14 + i * 7, 10), borderTop: "1px solid rgba(255,255,255,0.16)", paddingTop: 14 }}>
              {o}
            </div>
          ))}
        </div>
      </div>
    </AbsoluteFill>
  );
}

export function Dating({ name, bio, verdict = "left" }: { name: string; bio: string; verdict?: "left" | "right" }) {
  const frame = useCurrentFrame();
  const { durationInFrames, fps } = useVideoConfig();
  const swipeStart = Math.floor(durationInFrames * 0.62);
  const sw = prog(frame, swipeStart, 12, EASE_IN_OUT);
  const dir = verdict === "left" ? -1 : 1;
  const p = pop(frame, fps, 0, 15);
  return (
    <Stage>
      <SatireTag />
      <div style={{ position: "relative", height: 1080, scale: String(0.92 + 0.08 * p) }}>
        <div
          style={{
            position: "absolute",
            inset: 0,
            borderRadius: 50,
            overflow: "hidden",
            background: `linear-gradient(160deg, ${C.graphite}, ${C.ink})`,
            translate: `${sw * dir * 900}px ${sw * 80}px`,
            rotate: `${sw * dir * 18}deg`,
            boxShadow: "0 50px 100px -40px rgba(0,0,0,0.6)",
          }}
        >
          <div style={{ position: "absolute", top: 60, left: 60, right: 60, height: 540, borderRadius: 30, overflow: "hidden" }}>
            <div style={{ position: "absolute", inset: 0, background: "#c9c9c9" }} />
            <div style={{ position: "absolute", inset: 0, scale: "0.62", transformOrigin: "top left", width: "161%", height: "161%" }}>
              <OldSiteThumb />
            </div>
          </div>
          <div style={{ position: "absolute", left: 60, right: 60, top: 640, color: C.white, fontFamily: FONT }}>
            <div style={{ fontSize: 66, fontWeight: 700, letterSpacing: "-0.03em" }}>{name}</div>
            <div style={{ fontSize: 44, lineHeight: 1.25, color: "rgba(255,255,255,0.8)", marginTop: 12 }}>{bio}</div>
          </div>
        </div>
        <div style={{ position: "absolute", bottom: -40, left: 0, right: 0, display: "flex", justifyContent: "center", gap: 80 }}>
          {(["left", "right"] as const).map((v) => {
            const active = v === verdict && frame >= swipeStart - 6;
            return (
              <div
                key={v}
                style={{
                  width: 150,
                  height: 150,
                  borderRadius: 75,
                  background: active ? (v === "left" ? C.danger : C.ok) : C.white,
                  color: active ? C.white : v === "left" ? C.danger : C.ok,
                  display: "grid",
                  placeItems: "center",
                  fontSize: 76,
                  fontWeight: 700,
                  boxShadow: "0 20px 40px -20px rgba(0,0,0,0.5)",
                  scale: active ? String(1 + 0.15 * Math.sin(prog(frame, swipeStart - 6, 8) * Math.PI)) : "1",
                }}
              >
                {v === "left" ? "×" : "♥"}
              </div>
            );
          })}
        </div>
      </div>
    </Stage>
  );
}

function OldSiteThumb() {
  return (
    <div style={{ position: "absolute", inset: 0, fontFamily: "'Times New Roman', serif", background: "#c9c9c9" }}>
      <div style={{ background: "linear-gradient(#5a7fd6,#22408f)", color: "#ff0", padding: 20, fontSize: 52, fontWeight: 700, textAlign: "center", textShadow: "2px 2px #000" }}>Willkommen!!!</div>
      <div style={{ margin: 20, height: 120, background: "repeating-linear-gradient(45deg,#ffd400 0 22px,#000 22px 44px)" }} />
      <div style={{ margin: 20, fontSize: 38 }}>Diese Seite befindet sich im Aufbau.</div>
    </div>
  );
}

export function FactScene({ fact_id, kicker, facts = [] }: { fact_id: string; kicker?: string; facts?: Fact[] }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = useTheme();
  const fact = facts.find((f) => f.id === fact_id);
  if (!fact) return <Stage />; // the Core rejects unknown fact ids before rendering
  const p = pop(frame, fps, 2, 13, 0.55);
  return (
    <Stage>
      {kicker ? <Label>{kicker}</Label> : null}
      <div style={{ fontSize: fit(fact.value, SAFE_W, 300, 90, 0.58), fontWeight: 750, letterSpacing: "-0.06em", lineHeight: 0.95, scale: String(0.7 + 0.3 * p), transformOrigin: "left bottom", color: t.fg }}>
        <Dotted text={fact.value} />
      </div>
      <div style={{ fontSize: fit(fact.label, SAFE_W * 2, 64, 46), fontWeight: 500, lineHeight: 1.15, color: t.muted, opacity: prog(frame, 10, 12), textWrap: "balance" }}>{fact.label}</div>
    </Stage>
  );
}

export function EndCard({ headline, button, url, subline }: { headline: string; button: string; url: string; subline?: string }) {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const t = useTheme();
  const logoP = prog(frame, 0, 14, EASE_OUT);
  const btnP = pop(frame, fps, 10, 14);
  // the brand dot travels in and lands as the full stop of the headline
  const dotX = interpolate(frame, [0, 16], [700, 0], { extrapolateRight: "clamp", easing: EASE_OUT });
  return (
    <Stage style={{ gap: 44 }}>
      <Img src={staticFile(t.logo)} style={{ width: 420, alignSelf: "flex-start", opacity: logoP, translate: `0 ${(1 - logoP) * 30}px` }} />
      <div style={{ fontSize: fit(headline, SAFE_W * 2, 104, 62), fontWeight: 700, letterSpacing: "-0.045em", lineHeight: 1.0, textWrap: "balance" }}>
        {headline.replace(/\.$/, "")}
        <span style={{ display: "inline-block", color: t.surface === "accent" ? C.ink : C.accent, translate: `${dotX}px 0` }}>.</span>
      </div>
      {subline ? <div style={{ fontSize: 46, color: t.muted, lineHeight: 1.2 }}>{subline}</div> : null}
      <Button text={button} style={{ scale: String(0.85 + 0.15 * btnP), opacity: Math.min(1, btnP * 1.5), boxShadow: frame > durationInFrames - 30 ? `0 0 0 ${((frame % 30) / 30) * 26}px rgba(38,81,240,${0.35 * (1 - (frame % 30) / 30)})` : "none" }} />
      <div style={{ fontFamily: MONO, fontSize: 46, color: t.fg, letterSpacing: "0.02em", opacity: prog(frame, 16, 10) }}>{url}</div>
    </Stage>
  );
}

export { Headline };
