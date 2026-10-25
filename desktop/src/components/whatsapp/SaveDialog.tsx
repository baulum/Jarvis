// "Speichern unter": a received file to a folder on the Jarvis server or on a paired computer (Device Agent).
// Folders outside the allowed list need Sir's explicit approval – the Core asks, the dialog says so up front.
import { HardDrive, Laptop } from "lucide-react";
import { useState } from "react";
import { useAction, useQuery } from "../../hooks/useQuery";
import type { WaFile } from "../../lib/whatsapp";
import { api, useApp } from "../../store/app";
import { Button, Dialog, Field, Toggle } from "../ui";

export function SaveDialog({ file, onClose }: { file: WaFile; onClose: () => void }) {
  const loc = useQuery(() => api.whatsapp.locations(), "wa-locations");
  const act = useAction();
  const pushToast = useApp((s) => s.pushToast);
  const [target, setTarget] = useState<string>("server");
  const [destination, setDestination] = useState("~/Desktop/");
  const [outside, setOutside] = useState(false);
  const devices = loc.data?.devices.filter((d) => d.online) ?? [];

  const save = async () => {
    const res = await act.run("save", () =>
      api.whatsapp.saveFile(file.file_id, {
        destination: destination.trim(),
        ...(target !== "server" ? { device: target } : {}),
        ...(outside ? { allow_outside: true } : {}),
      }),
    );
    if (!res) return;
    if (res.status === "approval_required") {
      pushToast({ tone: "warn", title: "Freigabe nötig", body: res.summary ?? "Speichern außerhalb der freigegebenen Ordner", action: { label: "Ansehen", view: "approvals" } });
    } else {
      const data = (res.data ?? {}) as { path?: string; location?: string };
      pushToast({ tone: "ok", title: "Gespeichert", body: `${data.location ?? ""}: ${data.path ?? file.name}` });
    }
    onClose();
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={`„${file.name}“ speichern`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Abbrechen</Button>
          <Button variant="primary" busy={act.busy === "save"} disabled={!destination.trim()} onClick={() => void save()}>Speichern</Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div role="radiogroup" aria-label="Ziel" className="flex flex-wrap gap-2">
          <TargetButton active={target === "server"} onClick={() => setTarget("server")} icon={<HardDrive className="size-4" />} label="Jarvis-Server" />
          {devices.map((d) => (
            <TargetButton key={d.name} active={target === d.name} onClick={() => setTarget(d.name)} icon={<Laptop className="size-4" />} label={d.name} />
          ))}
        </div>
        <Field label="Ordner oder Dateipfad" hint={target === "server" ? "Relativ = ab dem Benutzerordner des Servers, z. B. Documents/Projekt X/" : `Pfad auf ${target}; der Device Agent erlaubt nur seine freigegebenen Ordner.`}>
          {(id) => <input id={id} className="field" value={destination} onChange={(e) => setDestination(e.target.value)} />}
        </Field>
        {target === "server" && loc.data && (
          <details className="text-xs">
            <summary className="cursor-default text-ink-dim hover:text-ink">Freigegebene Ordner</summary>
            <ul className="type-data mt-2 flex flex-col gap-0.5 text-ink-faint">
              {loc.data.allowed.map((a) => (
                <li key={a.path}>
                  <button type="button" className="hover:text-arc" onClick={() => setDestination(`${a.path}/`)}>{a.path}</button>
                </li>
              ))}
            </ul>
          </details>
        )}
        {target === "server" && (
          <label className="flex items-center gap-3 text-xs text-ink-dim">
            <Toggle checked={outside} onChange={setOutside} label="Außerhalb der freigegebenen Ordner speichern" />
            Außerhalb der freigegebenen Ordner (wird zur Freigabe vorgelegt)
          </label>
        )}
      </div>
    </Dialog>
  );
}

function TargetButton({ active, onClick, icon, label }: { active: boolean; onClick: () => void; icon: React.ReactNode; label: string }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      onClick={onClick}
      className={`inline-flex h-8 items-center gap-2 rounded-md border px-3 text-xs ${active ? "border-arc-dim bg-arc-deep text-arc" : "border-seam-hi text-ink-dim hover:text-ink"}`}
    >
      {icon}
      {label}
    </button>
  );
}
