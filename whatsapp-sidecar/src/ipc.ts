// JSON lines over a pair of streams (stdin/stdout of the sidecar). See protocol.ts for the frames.
import type { Readable, Writable } from "node:stream";
import { MAX_LINE_BYTES, SidecarError } from "./protocol.js";

export type RequestHandler = (method: string, params: Record<string, unknown>) => Promise<unknown>;

export class LineIpc {
  private buf: Buffer = Buffer.alloc(0);
  private queue: Promise<void> = Promise.resolve();
  private closed = false;
  private inFlight = new Set<Promise<unknown>>();

  constructor(
    private readonly input: Readable,
    private readonly output: Writable,
    private readonly handler: RequestHandler,
    private readonly onClose: () => void = () => undefined,
  ) {}

  start(): void {
    this.input.on("data", (chunk: Buffer | string) => this.onData(typeof chunk === "string" ? Buffer.from(chunk) : chunk));
    this.input.on("end", () => this.close());
    this.input.on("error", () => this.close());
  }

  private close(): void {
    if (this.closed) return;
    this.closed = true;
    this.onClose();
  }

  private onData(chunk: Buffer): void {
    this.buf = this.buf.length ? Buffer.concat([this.buf, chunk]) : chunk;
    for (;;) {
      const nl = this.buf.indexOf(0x0a);
      if (nl < 0) break;
      const line = this.buf.subarray(0, nl).toString("utf8");
      this.buf = this.buf.subarray(nl + 1);
      this.onLine(line);
    }
    if (this.buf.length > MAX_LINE_BYTES) {
      this.buf = Buffer.alloc(0); // a runaway line is dropped, the pipe stays usable
      this.event("protocol_error", { message: "line too long" });
    }
  }

  private onLine(line: string): void {
    if (!line.trim()) return;
    let frame: unknown;
    try {
      frame = JSON.parse(line);
    } catch {
      this.event("protocol_error", { message: "invalid JSON" });
      return;
    }
    if (!frame || typeof frame !== "object") return;
    const { type, id, method, params } = frame as { type?: unknown; id?: unknown; method?: unknown; params?: unknown };
    if (type !== "req" || typeof id !== "string" || typeof method !== "string") {
      this.event("protocol_error", { message: "unexpected frame" });
      return;
    }
    const p = params && typeof params === "object" && !Array.isArray(params) ? (params as Record<string, unknown>) : {};
    const done = this.handler(method, p).then(
      (result) => this.write({ type: "res", id, ok: true, result: result ?? null }),
      (e: unknown) => {
        const code = e instanceof SidecarError ? e.code : "failed";
        const message = e instanceof Error ? e.message : String(e);
        return this.write({ type: "res", id, ok: false, error: { code, message: message.slice(0, 1000) } });
      },
    );
    this.inFlight.add(done);
    void done.finally(() => this.inFlight.delete(done));
  }

  /** Resolves once every request received so far has been answered and written. */
  async settle(): Promise<void> {
    while (this.inFlight.size) await Promise.allSettled([...this.inFlight]);
    await this.queue;
  }

  event(event: string, data: unknown): void {
    this.write({ type: "event", event, data });
  }

  /** Writes are queued and wait for 'drain', so a burst (history sync) never overruns the pipe. */
  write(frame: unknown): Promise<void> {
    const line = JSON.stringify(frame) + "\n";
    this.queue = this.queue.then(
      () =>
        new Promise<void>((resolve) => {
          if (this.output.destroyed || this.output.writableEnded) return resolve();
          if (this.output.write(line)) resolve();
          else this.output.once("drain", () => resolve());
        }),
    );
    return this.queue;
  }

  flush(): Promise<void> {
    return this.queue;
  }
}
