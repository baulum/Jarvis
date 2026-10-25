// Reconnect delays: 1 s, 2 s, 4 s … capped at 60 s, each with ±20 % jitter so a restart of several clients
// does not hit WhatsApp in lockstep. Purely a resilience measure – it never retries a ban (403) or a logout.

export interface BackoffOptions {
  baseMs?: number;
  maxMs?: number;
  jitter?: number;
  random?: () => number;
}

export function backoffDelay(attempt: number, opts: BackoffOptions = {}): number {
  const base = opts.baseMs ?? 1000;
  const max = opts.maxMs ?? 60_000;
  const jitter = opts.jitter ?? 0.2;
  const random = opts.random ?? Math.random;
  const raw = Math.min(max, base * 2 ** Math.max(0, attempt));
  const spread = raw * jitter * (random() * 2 - 1);
  return Math.max(0, Math.round(Math.min(max, raw + spread)));
}
