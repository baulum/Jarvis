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

/** The 16 kinds of knowledge the brain shows with their own glyph (plus a neutral "entity"). */
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
  | "connector"
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
  connector: "connector",
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
  connector: "Anbindungen",
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

/** One hue per kind of knowledge so the brain can be read at a glance; white is J.A.R.V.I.S. itself. */
export const CATEGORY_COLOR: Record<Category, [number, number, number]> = {
  memory: [0.57, 0.77, 0.87],
  person: [0.91, 0.65, 0.78],
  customer: [0.96, 0.68, 0.28],
  project: [0.33, 0.83, 0.54],
  company: [0.64, 0.57, 0.87],
  email: [0.35, 0.80, 0.89],
  appointment: [0.94, 0.89, 0.30],
  task: [0.56, 0.84, 0.36],
  document: [0.55, 0.67, 0.85],
  skill: [0.77, 0.50, 0.90],
  tool: [0.98, 0.45, 0.22],
  connector: [0.12, 0.88, 0.69],
  agent: [1.00, 1.00, 1.00],
  conversation: [0.84, 0.52, 0.79],
  decision: [0.98, 0.84, 0.41],
  entity: [0.43, 0.50, 0.57],
};
