// One icon per activity phase (event stream, process lanes).
import { Activity, BookmarkPlus, Bot, Cpu, DatabaseZap, MessageSquareText, Send, ShieldAlert, Wrench, type LucideIcon } from "lucide-react";
import type { Phase } from "./graph/live";

export const PHASE_ICON: Record<Phase, LucideIcon> = {
  request: MessageSquareText,
  llm: Cpu,
  response: Send,
  tool: Wrench,
  "memory.retrieve": DatabaseZap,
  "memory.store": BookmarkPlus,
  agent: Bot,
  approval: ShieldAlert,
  system: Activity,
};
