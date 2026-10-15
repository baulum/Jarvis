import { Mic, MicOff, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Reactor, reactorMode } from "../components/Reactor";
import { Button, KeyValue, StatusText } from "../components/ui";
import { useQuery } from "../hooks/useQuery";
import { formatRelative } from "../lib/format";
import { api, useApp } from "../store/app";

const STATE_TEXT: Record<string, string> = {
  idle: "Bereit",
  listening: "Hört zu",
  recording: "Nimmt auf",
  thinking: "Denkt nach",
  speaking: "Spricht",
  muted: "Mikrofon stumm",
  offline: "Sprach-Daemon offline",
};

const HISTORY = 96;

export function VoiceView() {
  const voice = useApp((s) => s.voice);
  const status = useApp((s) => s.status);
  const pushToast = useApp((s) => s.pushToast);
  const settings = useQuery(() => api.settings(), "settings");
  const [ptt, setPtt] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [levels, setLevels] = useState<number[]>(() => Array(HISTORY).fill(0));
  const pttRef = useRef(false);

  const daemonOnline = status?.components.voice_daemon === "ok" || Boolean(voice.connected && status?.components.voice_daemon !== "offline");
  const muted = Boolean(voice.muted);
  const mode = reactorMode(voice.state, daemonOnline, muted);
  const label = STATE_TEXT[mode] ?? voice.state ?? "Unbekannt";
  const voiceCfg = (settings.data?.voice ?? {}) as { wake_word?: string; language?: string; tts?: { provider?: string }; stt?: { provider?: string } };

  // rolling level history (only real samples from voice.status events)
  useEffect(() => {
    if (voice.updated_at === undefined) return;
    setLevels((prev) => [...prev.slice(1), typeof voice.level === "number" ? voice.level : 0]);
  }, [voice.updated_at, voice.level]);

  async function command(action: "mute" | "unmute" | "ptt_start" | "ptt_stop" | "interrupt") {
    setBusy(action);
    try {
      await api.voiceCommand(action);
    } catch (e) {
      pushToast({ tone: "bad", title: "Sprachbefehl fehlgeschlagen", body: (e as Error).message });
    } finally {
      setBusy(null);
    }
  }

  const startPtt = () => {
    if (pttRef.current || !daemonOnline || muted) return;
    pttRef.current = true;
    setPtt(true);
    void command("ptt_start");
  };
  const stopPtt = () => {
    if (!pttRef.current) return;
    pttRef.current = false;
    setPtt(false);
    void command("ptt_stop");
  };

  return (
    <div className="flex h-full">
      <div className="flex min-w-0 flex-1 flex-col items-center justify-center gap-8 px-8 py-8">
        <div className="brackets relative flex aspect-square w-[min(26rem,52vh)] items-center justify-center rounded-2xl">
          <Reactor mode={mode} level={voice.level ?? 0} size={360} className="h-full w-full p-6" />
        </div>
        <div className="flex flex-col items-center gap-2">
          <p className={`type-display text-[1.75rem] ${mode === "offline" ? "text-ink-faint" : mode === "muted" ? "text-ember" : "text-ink"}`} aria-live="polite">
            {label}
          </p>
          {voice.detail && daemonOnline && <p className="max-w-[52ch] text-center text-xs text-ink-dim">{voice.detail}</p>}
        </div>
        <Waveform levels={levels} active={daemonOnline && !muted} />
        <div className="flex items-center gap-3">
          <Button
            variant={muted ? "danger" : "outline"}
            disabled={!daemonOnline}
            busy={busy === "mute" || busy === "unmute"}
            onClick={() => void command(muted ? "unmute" : "mute")}
            icon={muted ? <MicOff className="size-4" /> : <Mic className="size-4" />}
          >
            {muted ? "Stummschaltung aufheben" : "Mikrofon stumm"}
          </Button>
          <button
            type="button"
            disabled={!daemonOnline || muted}
            onPointerDown={(e) => {
              e.currentTarget.setPointerCapture(e.pointerId);
              startPtt();
            }}
            onPointerUp={stopPtt}
            onPointerCancel={stopPtt}
            onKeyDown={(e) => {
              if ((e.key === " " || e.key === "Enter") && !e.repeat) {
                e.preventDefault();
                startPtt();
              }
            }}
            onKeyUp={(e) => {
              if (e.key === " " || e.key === "Enter") stopPtt();
            }}
            aria-pressed={ptt}
            className={`h-11 rounded-full border px-6 text-sm font-medium transition-all disabled:opacity-40 ${
              ptt ? "scale-[0.98] border-arc bg-arc text-void shadow-[0_0_24px] shadow-arc/40" : "border-arc-dim/60 bg-arc-deep text-arc hover:border-arc"
            }`}
          >
            {ptt ? "Spricht … loslassen zum Senden" : "Push-to-Talk – gedrückt halten"}
          </button>
          <Button
            variant="ghost"
            disabled={!daemonOnline}
            busy={busy === "interrupt"}
            onClick={() => void command("interrupt")}
            icon={<Square className="size-3.5" />}
            title="Laufende Sprachausgabe stoppen"
          >
            Stopp
          </Button>
        </div>
      </div>
      <aside className="flex w-[300px] shrink-0 flex-col gap-6 overflow-y-auto border-l border-seam px-6 py-6">
        <div>
          <h2 className="type-title mb-3 text-sm">Sprach-Daemon</h2>
          {daemonOnline ? (
            <StatusText tone="ok">Verbunden</StatusText>
          ) : (
            <div className="flex flex-col gap-2">
              <StatusText tone="bad">Offline</StatusText>
              <p className="text-xs text-ink-dim">
                Der Daemon ist nicht mit dem Core verbunden. Starte ihn mit <code className="type-data text-ink">scripts/start.sh</code> oder{" "}
                <code className="type-data text-ink">jarvisctl voice</code>. Bis dahin funktionieren Stimme und Push-to-Talk nicht.
              </p>
            </div>
          )}
        </div>
        <KeyValue
          rows={[
            ["Wake-Word", voice.wake_word === false ? "aus" : voiceCfg.wake_word ? <span className="type-data">{voiceCfg.wake_word}</span> : "–"],
            ["Wake-Word aktiv", daemonOnline ? (voice.wake_word === false ? "Nein" : muted ? "Pausiert (stumm)" : "Ja") : "–"],
            ["Mikrofon", micLabel(voice.mic_permission)],
            ["Spracherkennung", <StatusText key="stt" tone={status?.components.stt === "ok" ? "ok" : "bad"}>{status?.components.stt === "ok" ? "Bereit" : "Nicht erreichbar"}</StatusText>],
            ["Sprachausgabe", status?.tts ? `${status.tts.active ?? "–"}${status.tts.fallback ? " (Ersatz aktiv)" : ""}` : "–"],
            ["Letztes Signal", voice.updated_at ? formatRelative(voice.updated_at) : "–"],
          ]}
        />
        <p className="text-2xs leading-relaxed text-ink-faint">
          Push-to-Talk funktioniert auch über das Menüleisten-Symbol. Freigaben werden per Stimme nie erteilt.
        </p>
      </aside>
    </div>
  );
}

function micLabel(p: string | null | undefined): string {
  if (p === "granted") return "Erlaubt";
  if (p === "denied") return "Verweigert";
  if (!p || p === "unknown") return "Unbekannt";
  return p;
}

function Waveform({ levels, active }: { levels: number[]; active: boolean }) {
  return (
    <div className="flex h-12 w-[min(34rem,90%)] items-center gap-[2px]" aria-hidden>
      {levels.map((l, i) => {
        const h = Math.max(2, Math.round(l * 46));
        return (
          <span
            key={i}
            className={`flex-1 rounded-full ${active ? "bg-arc" : "bg-ink-faint"}`}
            style={{ height: h, opacity: 0.2 + (i / levels.length) * 0.7 }}
          />
        );
      })}
    </div>
  );
}
