// Linking WhatsApp: QR code (scanned in WhatsApp › Verknüpfte Geräte) or an 8-character pairing code.
// The QR image is rendered on the Core (SVG) and only sent to owner devices; it refreshes every ~20 s.
import { KeyRound, QrCode, RefreshCw } from "lucide-react";
import { useState } from "react";
import { useAction } from "../../hooks/useQuery";
import { stateLabel, type WaStatus } from "../../lib/whatsapp";
import { api } from "../../store/app";
import { Button, Field, StatusText, Tabs } from "../ui";

export function LoginPanel({ status, onChanged }: { status: WaStatus; onChanged: () => void }) {
  const [mode, setMode] = useState<"qr" | "pairing">(status.state === "pairing" ? "pairing" : "qr");
  const [phone, setPhone] = useState("");
  const act = useAction();
  const waiting = status.state === "qr" || status.state === "pairing" || status.state === "connecting";

  const start = () =>
    void act.run(mode, () => api.whatsapp.connect(mode, mode === "pairing" ? phone : undefined)).then(onChanged);

  return (
    <div className="grid h-full grid-cols-1 gap-8 overflow-y-auto px-8 py-8 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
      <div className="flex flex-col gap-5">
        <div>
          <h3 className="type-display text-[1.35rem] text-ink">WhatsApp verknüpfen</h3>
          <p className="mt-2 max-w-[46ch] text-sm text-ink-dim">
            Jarvis wird wie WhatsApp Web als verknüpftes Gerät angemeldet – ohne Browser, direkt auf dem Jarvis-Server.
            Die Sitzung bleibt verschlüsselt gespeichert, ein Neustart braucht keinen neuen Scan.
          </p>
        </div>
        <Tabs
          value={mode}
          onChange={setMode}
          items={[
            { id: "qr", label: "QR-Code" },
            { id: "pairing", label: "Kopplungscode" },
          ]}
        />
        {mode === "pairing" && (
          <Field label="Eigene Telefonnummer mit Ländervorwahl" hint="Nur Ziffern, z. B. 4915112345678 – sie wird nicht gespeichert.">
            {(id) => <input id={id} className="field type-data" inputMode="tel" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="49…" />}
          </Field>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="primary"
            icon={mode === "qr" ? <QrCode className="size-4" /> : <KeyRound className="size-4" />}
            busy={act.busy === mode}
            disabled={mode === "pairing" && phone.replace(/\D/g, "").length < 7}
            onClick={start}
          >
            {waiting && status.state === mode ? "Neuen Code anfordern" : mode === "qr" ? "Mit QR-Code verbinden" : "Kopplungscode anfordern"}
          </Button>
          {status.state && <StatusText tone={waiting ? "warn" : "idle"} pulse={waiting}>{stateLabel(status.state)}</StatusText>}
        </div>
        {status.error && <p className="text-xs text-ember">{status.error}</p>}
        {status.sidecar && !status.sidecar.running && (
          <p className="rounded-md border border-alarm/30 bg-alarm-deep/40 px-3 py-2 text-xs text-ink">{status.sidecar.error ?? "Der WhatsApp-Dienst läuft nicht."}</p>
        )}
        <ol className="flex flex-col gap-1.5 text-xs text-ink-dim">
          <li>1. WhatsApp auf dem Handy öffnen</li>
          <li>2. Einstellungen › Verknüpfte Geräte › Gerät hinzufügen</li>
          <li>3. {mode === "qr" ? "Den Code rechts scannen" : "„Stattdessen mit Telefonnummer verknüpfen“ wählen und den Code eingeben"}</li>
        </ol>
      </div>

      <div className="flex items-start justify-center lg:justify-start">
        <div className="brackets flex min-h-[19rem] w-[19rem] flex-col items-center justify-center gap-3 rounded-lg border border-seam bg-void p-5">
          {status.state === "qr" && status.qr_svg ? (
            // the SVG comes from our own Core (segno), not from WhatsApp content
            <div className="w-full rounded-md bg-white p-2" aria-label="QR-Code für WhatsApp" role="img" dangerouslySetInnerHTML={{ __html: status.qr_svg }} />
          ) : status.state === "pairing" && status.pairing_code ? (
            <>
              <p className="text-xs text-ink-dim">Kopplungscode</p>
              <p className="type-data-lg selectable text-[2.4rem] tracking-[0.12em] text-arc">{status.pairing_code}</p>
              <p className="text-2xs text-ink-faint">Gilt nur wenige Minuten</p>
            </>
          ) : (
            <p className="flex items-center gap-2 text-xs text-ink-faint">
              {waiting ? <RefreshCw className="size-3.5 spin-slow" aria-hidden /> : null}
              {waiting ? "Code wird erzeugt …" : "Noch kein Code angefordert"}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
