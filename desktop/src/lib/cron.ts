// Human-readable German descriptions for the 5-field cron expressions accepted by the core (croniter).

const WEEKDAYS = ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"];

const FIELD_RANGES: [number, number][] = [
  [0, 59], // minute
  [0, 23], // hour
  [1, 31], // day of month
  [1, 12], // month
  [0, 7], // day of week (0 and 7 = Sunday)
];

const PART_RE = /^(\*|\d+(-\d+)?)(\/\d+)?$/;

/** Light syntactic validation; the core performs the authoritative check. */
export function validateCron(expr: string): string | null {
  const fields = expr.trim().split(/\s+/);
  if (fields.length !== 5) return "Ein Cron-Ausdruck hat genau fünf Felder: Minute Stunde Tag Monat Wochentag.";
  for (let i = 0; i < 5; i += 1) {
    const field = fields[i] ?? "";
    const [lo, hi] = FIELD_RANGES[i] ?? [0, 0];
    for (const part of field.split(",")) {
      if (!PART_RE.test(part)) return `Feld ${i + 1} („${field}“) ist ungültig.`;
      const nums = part.replace(/\/\d+$/, "").split("-").filter((p) => p !== "*").map(Number);
      if (nums.some((n) => n < lo || n > hi)) return `Feld ${i + 1} erlaubt nur Werte von ${lo} bis ${hi}.`;
    }
  }
  return null;
}

const pad = (n: string) => n.padStart(2, "0");

function isNum(s: string | undefined): s is string {
  return s !== undefined && /^\d+$/.test(s);
}

/** Describes common patterns; falls back to a generic sentence for anything exotic. */
export function describeCron(expr: string): string {
  if (validateCron(expr)) return "Ungültiger Ausdruck";
  const [min, hour, dom, mon, dow] = expr.trim().split(/\s+/) as [string, string, string, string, string];
  const at = isNum(min) && isNum(hour) ? `um ${pad(hour)}:${pad(min)} Uhr` : null;

  if (min.startsWith("*/") && hour === "*" && dom === "*" && mon === "*" && dow === "*") {
    return `alle ${min.slice(2)} Minuten`;
  }
  if (min === "*" && hour === "*" && dom === "*" && mon === "*" && dow === "*") return "jede Minute";
  if (isNum(min) && hour === "*" && dom === "*" && mon === "*" && dow === "*") {
    return `stündlich zur Minute ${min}`;
  }
  if (isNum(min) && hour.startsWith("*/") && dom === "*" && mon === "*" && dow === "*") {
    return `alle ${hour.slice(2)} Stunden zur Minute ${min}`;
  }
  if (at && dom === "*" && mon === "*") {
    if (dow === "*") return `täglich ${at}`;
    if (dow === "1-5") return `werktags (Mo–Fr) ${at}`;
    if (dow === "0,6" || dow === "6,0") return `am Wochenende ${at}`;
    if (isNum(dow)) return `jeden ${WEEKDAYS[Number(dow)]} ${at}`;
    const days = dow.split(",");
    if (days.every(isNum)) return `${days.map((d) => WEEKDAYS[Number(d)]).join(", ")} ${at}`;
  }
  if (at && isNum(dom) && mon === "*" && dow === "*") return `am ${dom}. jedes Monats ${at}`;
  if (at && isNum(dom) && isNum(mon) && dow === "*") return `jährlich am ${dom}.${mon}. ${at}`;
  return `nach Muster „${expr.trim()}“`;
}

export const CRON_EXAMPLES: { expr: string; label: string }[] = [
  { expr: "0 8 * * 1-5", label: "werktags 08:00" },
  { expr: "30 7 * * *", label: "täglich 07:30" },
  { expr: "*/15 * * * *", label: "alle 15 Minuten" },
  { expr: "0 18 * * 5", label: "freitags 18:00" },
  { expr: "0 9 1 * *", label: "monatlich am 1." },
];
