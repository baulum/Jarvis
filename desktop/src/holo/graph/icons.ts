// Glyph atlas: one monochrome line icon per category, rasterised once into a texture for the node shader.
// White strokes on transparent; the shader tints them with the node colour.
import {
  Building2,
  CalendarClock,
  CircleDashed,
  Contact,
  FileText,
  FolderKanban,
  GraduationCap,
  ListTodo,
  Mail,
  MessagesSquare,
  Orbit,
  Signpost,
  Sparkle,
  UserRound,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { Category } from "./types";

export const ICON_OF: Record<Category, LucideIcon> = {
  memory: Sparkle,
  person: UserRound,
  customer: Contact,
  project: FolderKanban,
  company: Building2,
  email: Mail,
  appointment: CalendarClock,
  task: ListTodo,
  document: FileText,
  skill: GraduationCap,
  tool: Wrench,
  agent: Orbit,
  conversation: MessagesSquare,
  decision: Signpost,
  entity: CircleDashed,
};

export const ATLAS_CATEGORIES = Object.keys(ICON_OF) as Category[];
export const ATLAS_COLS = 4;
export const ATLAS_ROWS = 4;
const CELL = 128;

export function iconIndex(c: Category): number {
  return Math.max(0, ATLAS_CATEGORIES.indexOf(c));
}

/** Draws the atlas into a canvas; resolves once every SVG has been rasterised. */
export async function buildIconAtlas(): Promise<HTMLCanvasElement> {
  const canvas = document.createElement("canvas");
  canvas.width = ATLAS_COLS * CELL;
  canvas.height = ATLAS_ROWS * CELL;
  const ctx = canvas.getContext("2d");
  if (!ctx) return canvas;
  await Promise.all(
    ATLAS_CATEGORIES.map(
      (cat, i) =>
        new Promise<void>((resolve) => {
          const svg = renderToStaticMarkup(
            createElement(ICON_OF[cat], { size: 96, color: "#ffffff", strokeWidth: 1.35, absoluteStrokeWidth: false }),
          );
          const img = new Image();
          img.onload = () => {
            const x = (i % ATLAS_COLS) * CELL + 16;
            const y = Math.floor(i / ATLAS_COLS) * CELL + 16;
            ctx.drawImage(img, x, y, 96, 96);
            resolve();
          };
          img.onerror = () => resolve();
          img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
        }),
    ),
  );
  return canvas;
}
