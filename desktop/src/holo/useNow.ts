import { useEffect, useState } from "react";

/** performance.now(), refreshed every ``intervalMs`` – for running timers in the interface. */
export function useNow(intervalMs = 500): number {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    const id = setInterval(() => setNow(performance.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
