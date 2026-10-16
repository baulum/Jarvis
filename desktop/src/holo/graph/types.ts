// Shapes of /api/graph (core/src/jarvis/graph.py) and the categories the interface distinguishes.

export interface GraphNodeDTO {
  id: string;
  kind: string;
  label: string;
  group: string;
  weight: number;
  meta: Record<string, unknown>;
}

export interface GraphEdgeDTO {
  source: string;
  target: string;
  relation: string;
  weight: number;
}

export interface GraphDTO {
  nodes: GraphNodeDTO[];
  edges: GraphEdgeDTO[];
  counts: Record<string, number>;
  truncated: Record<string, number>;
  version: number;
  build_ms?: number;
}

/** The 14 kinds of knowledge the brain shows with their own glyph (plus a neutral "entity"). */
export type Category =
  | "memory"
  | "person"
  | "customer"
  | "project"
  | "company"
  | "email"
  | "appointment"
  | "task"
  | "document"
  | "skill"
  | "tool"
  | "agent"
  | "conversation"
  | "decision"
  | "entity";

export const CATEGORY_OF: Record<string, Category> = {
  note: "memory",
  technical: "memory",
  preference: "memory",
  process: "memory",
  recurring: "memory",
  person: "person",
  customer: "customer",
  project: "project",
  company: "company",
  email: "email",
  appointment: "appointment",
  schedule: "appointment",
  task: "task",
  commitment: "task",
  document: "document",
  skill: "skill",
  tool: "tool",
  connector: "tool",
  agent: "agent",
  coding: "agent",
  conversation: "conversation",
  decision: "decision",
  entity: "entity",
  place: "entity",
  product: "entity",
};

export function categoryOf(kind: string): Category {
  return CATEGORY_OF[kind] ?? "memory";
}

export const CATEGORY_LABEL: Record<Category, string> = {
  memory: "Erinnerungen",
  person: "Personen",
  customer: "Kunden",
  project: "Projekte",
  company: "Unternehmen",
  email: "E-Mails",
  appointment: "Termine",
  task: "Aufgaben",
  document: "Dokumente",
  skill: "Skills",
  tool: "Werkzeuge",
  agent: "Agenten",
  conversation: "Gespräche",
  decision: "Entscheidungen",
  entity: "Bezüge",
};

export const KIND_LABEL: Record<string, string> = {
  note: "Notiz",
  technical: "Wissen",
  preference: "Präferenz",
  process: "Ablauf",
  recurring: "Wiederkehrend",
  person: "Person",
  customer: "Kunde",
  company: "Unternehmen",
  project: "Projekt",
  email: "E-Mail",
  appointment: "Termin",
  schedule: "Zeitplan",
  task: "Aufgabe",
  commitment: "Verpflichtung",
  document: "Dokument",
  skill: "Skill",
  tool: "Werkzeug",
  connector: "Connector",
  agent: "Agent",
  coding: "Coding-Auftrag",
  conversation: "Gespräch",
  decision: "Entscheidung",
  entity: "Bezug",
  place: "Ort",
  product: "Produkt",
};

/** Palette: ice blue for knowledge, white for the system itself, amber only for attention. */
export const CATEGORY_COLOR: Record<Category, [number, number, number]> = {
  memory: [0.55, 0.82, 0.9],
  person: [0.78, 0.9, 0.96],
  customer: [0.42, 0.83, 0.9],
  project: [0.6, 0.88, 0.82],
  company: [0.5, 0.74, 0.88],
  email: [0.66, 0.78, 0.86],
  appointment: [0.7, 0.84, 0.92],
  task: [0.72, 0.86, 0.8],
  document: [0.6, 0.72, 0.8],
  skill: [0.52, 0.64, 0.74],
  tool: [0.62, 0.7, 0.78],
  agent: [0.92, 0.97, 1.0],
  conversation: [0.56, 0.7, 0.8],
  decision: [0.8, 0.88, 0.98],
  entity: [0.38, 0.48, 0.56],
};
