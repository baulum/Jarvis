// Sign-in of the browser client and the connection form of the desktop app (Core on another computer).
import { useState } from "react";
import { saveRemoteCore, saveWebToken, validateCoreUrl, validateToken } from "../lib/connection";
import { Button, Field } from "./ui";

/** Browser client (served by the Core at /app): sign in once with a device token. */
export function SignIn({ onDone }: { onDone: () => void }) {
  const [value, setValue] = useState("");
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = () => {
    const problem = validateToken(value);
    if (problem) {
      setError(problem);
      return;
    }
    saveWebToken(value, remember);
    onDone();
  };
  return (
    <div className="flex h-full items-center justify-center p-10">
      <form className="flex w-full max-w-md flex-col gap-4" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <h1 className="text-lg text-ink">J.A.R.V.I.S. – Anmeldung</h1>
        <p className="text-sm text-ink-dim">Geräte-Token dieses Browsers. Auf dem Core-Rechner erzeugen: <span className="type-data">jarvisctl pair "Browser" --role owner</span></p>
        <Field label="Geräte-Token">{(id) => (
          <input id={id} className="field" type="password" autoComplete="off" value={value} onChange={(e) => setValue(e.target.value)} placeholder="jdv_…" />
        )}</Field>
        <label className="flex items-center gap-2 text-sm text-ink-dim">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> Auf diesem Gerät merken
        </label>
        {error && <p className="text-sm text-alarm">{error}</p>}
        <Button variant="primary" type="submit">Anmelden</Button>
      </form>
    </div>
  );
}

/** Desktop app on a computer without its own Core: connect to the Core on the Mac (over Tailscale). */
export function RemoteCoreForm({ onSaved, initialUrl = "" }: { onSaved: () => void; initialUrl?: string }) {
  const [url, setUrl] = useState(initialUrl);
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    const problem = validateCoreUrl(url) ?? validateToken(value);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    try {
      await saveRemoteCore(url, value);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="flex flex-col gap-4" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <p className="text-sm text-ink-dim">
        Mit dem Jarvis Core auf einem anderen Rechner verbinden. Auf dem Mac: <span className="type-data">tailscale serve --bg --https=443 https+insecure://localhost:8767</span>,
        in config/jarvis.yaml <span className="type-data">clients.remote_api: true</span> und ein Token mit <span className="type-data">jarvisctl pair "Windows-PC" --role owner</span>.
      </p>
      <Field label="Core-Adresse">{(id) => (
        <input id={id} className="field" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://mac.tail1234.ts.net" />
      )}</Field>
      <Field label="Geräte-Token">{(id) => (
        <input id={id} className="field" type="password" autoComplete="off" value={value} onChange={(e) => setValue(e.target.value)} placeholder="jdv_…" />
      )}</Field>
      {error && <p className="text-sm text-alarm">{error}</p>}
      <Button variant="primary" type="submit" busy={busy}>Verbinden</Button>
    </form>
  );
}
