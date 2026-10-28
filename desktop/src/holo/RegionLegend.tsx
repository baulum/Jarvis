// Brain view, bottom left: what the regions stand for. A region is marked active only while real work runs in
// it; hovering an entry shows where it lies in the brain.
import { useMemo } from "react";
import { GROUP_COLOR, GROUP_HINT, GROUP_LABEL } from "./graph/anatomy";
import { GROUPS, heldGlow } from "./graph/fx";
import { useBrain } from "./store";

export function RegionLegend() {
  const live = useBrain((s) => s.live);
  const setHover = useBrain((s) => s.setRegionHover);
  const held = useMemo(() => heldGlow(live), [live]);
  return (
    <ul className="pointer-events-auto flex flex-col gap-0.5" aria-label="Regionen des Gehirns">
      {GROUPS.map((g) => {
        const [r, gr, b] = GROUP_COLOR[g];
        const active = held[g] > 0;
        return (
          <li key={g}>
            <button
              type="button"
              title={GROUP_HINT[g]}
              onMouseEnter={() => setHover(g)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(g)}
              onBlur={() => setHover(null)}
              className="flex items-center gap-2 rounded px-1.5 py-0.5 text-2xs text-ink-faint hover:text-ink-dim"
            >
              <span
                className={`size-1.5 rounded-full ${active ? "live-dot" : ""}`}
                style={{ background: `rgb(${Math.round(r * 255)} ${Math.round(gr * 255)} ${Math.round(b * 255)})`, opacity: active ? 1 : 0.55 }}
                aria-hidden
              />
              <span className={active ? "text-ink-dim" : undefined}>{GROUP_LABEL[g]}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
