// Suggestion list above a chat input while a slash command is being typed.
import { useEffect, useRef } from "react";
import type { SlashItem } from "../lib/slash";

export function SlashMenu({
  items,
  index,
  onHover,
  onPick,
}: {
  items: SlashItem[];
  index: number;
  onHover: (i: number) => void;
  onPick: (item: SlashItem) => void;
}) {
  const active = useRef<HTMLLIElement>(null);
  useEffect(() => active.current?.scrollIntoView({ block: "nearest" }), [index]);
  return (
    <div className="absolute inset-x-0 bottom-full z-20 mb-2 overflow-hidden rounded-lg border border-seam-hi bg-hull shadow-xl shadow-black/50">
      <ul role="listbox" aria-label="Befehle" className="max-h-64 overflow-y-auto py-1">
        {items.map((it, i) => (
          <li
            key={it.insert}
            ref={i === index ? active : undefined}
            role="option"
            aria-selected={i === index}
            onMouseEnter={() => onHover(i)}
            onMouseDown={(e) => {
              e.preventDefault(); // keep the input focused
              onPick(it);
            }}
            className={`flex cursor-default items-baseline gap-3 px-3 py-1.5 ${i === index ? "bg-plate-hi" : ""}`}
          >
            <span className="type-data shrink-0 text-arc">{it.label}</span>
            <span className="min-w-0 flex-1 truncate text-xs text-ink-dim">{it.description}</span>
            {it.command.category && !it.arg && <span className="shrink-0 text-2xs text-ink-faint">{it.command.category}</span>}
          </li>
        ))}
      </ul>
      <p className="border-t border-seam px-3 py-1 text-2xs text-ink-faint">↑↓ wählen · Tab einfügen · Enter ausführen · Esc schließen</p>
    </div>
  );
}
