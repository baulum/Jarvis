// Phone scenes: messenger chat and lock-screen notifications. Generic UI, no app logos.
import { useCurrentFrame, useVideoConfig } from "remotion";
import { C, FONT, MONO } from "../brand";
import { pop, prog } from "../motion";
import { Phone, Stage } from "../ui";

interface Msg {
  from: "me" | "them";
  text: string;
}

const CHAT_STYLES = {
  whatsapp: { bg: "#efeae2", head: "#f6f5f3", me: "#d9fdd3", them: "#ffffff", meText: C.ink },
  imessage: { bg: "#ffffff", head: "#f7f7f8", me: "#2f7cf6", them: "#e9e9eb", meText: C.white },
} as const;

export function Chat({ app = "whatsapp", contact, messages }: { app?: "whatsapp" | "imessage"; contact: string; messages: Msg[] }) {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const st = CHAT_STYLES[app] ?? CHAT_STYLES.whatsapp;
  const per = Math.max(10, Math.floor((durationInFrames - 10) / Math.max(1, messages.length)));
  const shown = messages.map((m, i) => ({ m, start: 4 + i * per + (m.from === "them" ? Math.floor(per * 0.4) : 0) }));
  const typing = shown.find((s, i) => s.m.from === "them" && frame >= 4 + i * per && frame < s.start);
  return (
    <Stage>
      <Phone screen={st.bg} style={{ height: 1180 }}>
        <div style={{ background: st.head, padding: "96px 40px 26px", display: "flex", alignItems: "center", gap: 24, borderBottom: "1px solid rgba(0,0,0,0.06)" }}>
          <div style={{ width: 76, height: 76, borderRadius: 38, background: C.line, display: "grid", placeItems: "center", fontSize: 34, fontWeight: 600, color: C.slate, fontFamily: FONT }}>
            {contact.slice(0, 1).toUpperCase()}
          </div>
          <div style={{ fontFamily: FONT }}>
            <div style={{ fontSize: 38, fontWeight: 600, color: C.ink }}>{contact}</div>
            <div style={{ fontSize: 26, color: C.slate }}>{typing ? "schreibt …" : "online"}</div>
          </div>
        </div>
        <div style={{ padding: "30px 30px", display: "flex", flexDirection: "column", gap: 18, fontFamily: FONT }}>
          {shown.map(({ m, start }, i) => {
            if (frame < start) return null;
            const p = pop(frame, fps, start, 13);
            const me = m.from === "me";
            return (
              <div
                key={i}
                style={{
                  alignSelf: me ? "flex-end" : "flex-start",
                  maxWidth: "82%",
                  background: me ? st.me : st.them,
                  color: me ? st.meText : C.ink,
                  borderRadius: 30,
                  padding: "20px 28px",
                  fontSize: 42,
                  lineHeight: 1.22,
                  boxShadow: "0 2px 0 rgba(0,0,0,0.06)",
                  scale: String(0.7 + 0.3 * p),
                  opacity: Math.min(1, p * 1.5),
                  transformOrigin: me ? "right bottom" : "left bottom",
                }}
              >
                {m.text}
              </div>
            );
          })}
          {typing ? (
            <div style={{ alignSelf: "flex-start", background: st.them, borderRadius: 30, padding: "24px 30px", display: "flex", gap: 10 }}>
              {[0, 1, 2].map((d) => (
                <div key={d} style={{ width: 16, height: 16, borderRadius: 8, background: C.quiet, opacity: 0.35 + 0.65 * Math.abs(Math.sin((frame + d * 5) / 5)) }} />
              ))}
            </div>
          ) : null}
        </div>
      </Phone>
    </Stage>
  );
}

interface Note {
  app: string;
  title: string;
  body?: string;
}

export function Notification({ app, title, body, time = "jetzt", stack = [] }: Note & { time?: string; stack?: Note[] }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const all: (Note & { main?: boolean })[] = [{ app, title, body, main: true }, ...stack];
  return (
    <Stage>
      <Phone screen="linear-gradient(170deg,#1b2a6b 0%,#0b0c0e 70%)" style={{ height: 1180 }}>
        <div style={{ position: "absolute", top: 150, width: "100%", textAlign: "center", color: C.white, fontFamily: FONT }}>
          <div style={{ fontSize: 30, opacity: 0.8 }}>Dienstag, 9:41</div>
          <div style={{ fontSize: 190, fontWeight: 600, letterSpacing: "-0.04em", lineHeight: 1 }}>9:41</div>
        </div>
        <div style={{ position: "absolute", top: 520, left: 26, right: 26, display: "flex", flexDirection: "column", gap: 16 }}>
          {all.map((n, i) => {
            const start = 6 + i * 12;
            const p = pop(frame, fps, start, 15);
            if (frame < start) return null;
            return (
              <div
                key={i}
                style={{
                  background: "rgba(245,245,247,0.82)",
                  backdropFilter: "blur(20px)",
                  borderRadius: 40,
                  padding: "26px 30px",
                  fontFamily: FONT,
                  color: C.ink,
                  translate: `0 ${(1 - p) * -140}px`,
                  opacity: Math.min(1, p * 1.4),
                  scale: n.main ? "1" : "0.96",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 8 }}>
                  <div style={{ width: 44, height: 44, borderRadius: 11, background: n.main ? C.accent : C.graphite, display: "grid", placeItems: "center", color: C.white, fontSize: 22, fontWeight: 700 }}>
                    {n.app.slice(0, 1).toUpperCase()}
                  </div>
                  <div style={{ flex: 1, fontSize: 28, fontFamily: MONO, letterSpacing: "0.04em", textTransform: "uppercase", color: C.slate }}>{n.app}</div>
                  <div style={{ fontSize: 26, color: C.slate }}>{n.main ? time : "vor 2 Min."}</div>
                </div>
                <div style={{ fontSize: 44, fontWeight: 650, lineHeight: 1.12 }}>{n.title}</div>
                {n.body ? <div style={{ fontSize: 40, lineHeight: 1.2, marginTop: 6, color: "#2b2d33" }}>{n.body}</div> : null}
              </div>
            );
          })}
        </div>
        <div style={{ position: "absolute", bottom: 26, left: "50%", marginLeft: -110, width: 220, height: 10, borderRadius: 5, background: "rgba(255,255,255,0.7)", opacity: prog(frame, 0, 8) }} />
      </Phone>
    </Stage>
  );
}
