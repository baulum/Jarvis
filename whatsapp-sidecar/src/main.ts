// Entry point. Started by the Jarvis Core (core/src/jarvis/whatsapp/sidecar.py) as a child process:
// requests arrive on stdin, responses and events leave on stdout, redacted logs go to stderr. When the Core goes
// away (stdin closes) the sidecar disconnects and exits – it never keeps running as an orphan.
import { LineIpc } from "./ipc.js";
import { createHandler, BAILEYS_VERSION } from "./handlers.js";
import { PROTOCOL_VERSION } from "./protocol.js";
import { redactLine } from "./redact.js";

function logError(what: string, e: unknown): void {
  process.stderr.write(redactLine(JSON.stringify({ level: "error", msg: what, err: e instanceof Error ? e.message : String(e) })) + "\n");
}

let ipc: LineIpc;
let exiting = false;

async function exit(code = 0): Promise<void> {
  if (exiting) return;
  exiting = true;
  try {
    await Promise.race([ipc.settle(), new Promise((r) => setTimeout(r, 5000))]);
  } finally {
    process.exit(code);
  }
}

const { handler, connection } = createHandler({
  emit: (event, data) => ipc.event(event, data),
  exit: () => void exit(0),
});

ipc = new LineIpc(process.stdin, process.stdout, handler, () => {
  // the Core is gone: answer what is still running, then disconnect and leave
  void Promise.race([ipc.settle(), new Promise((r) => setTimeout(r, 5000))])
    .then(() => connection()?.disconnect())
    .catch(() => undefined)
    .finally(() => void exit(0));
});

process.on("unhandledRejection", (e) => logError("unhandled rejection", e));
process.on("uncaughtException", (e) => {
  logError("uncaught exception", e);
  void exit(1); // the Core restarts the sidecar with backoff
});
for (const sig of ["SIGTERM", "SIGINT"] as const) {
  process.on(sig, () => {
    const conn = connection();
    void (conn ? conn.disconnect() : Promise.resolve()).catch(() => undefined).finally(() => void exit(0));
  });
}

ipc.start();
ipc.event("ready", { protocol: PROTOCOL_VERSION, baileys: BAILEYS_VERSION, node: process.version, pid: process.pid });
