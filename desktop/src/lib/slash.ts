// Slash commands: the list comes from the core (GET /api/commands, shared with the phone app); this file only
// does matching and the pure parts of execution.

export type SlashAction = "prompt" | "model" | "new" | "stop" | "navigate" | "killswitch";

export interface SlashCommand {
  name: string;
  description: string;
  usage: string;
  action: SlashAction;
  category?: string;
  prompt?: string;
  target?: string;
  choices?: string[];
  confirm?: boolean;
}

export interface SlashItem {
  /** text put into the input when the item is picked */
  insert: string;
  label: string;
  description: string;
  command: SlashCommand;
  /** a choice of the command's argument list (second step) */
  arg?: string;
  /** picking runs the command right away (no arguments needed) */
  runs: boolean;
}

export const takesArgs = (c: SlashCommand): boolean => Boolean(c.choices?.length) || c.usage.includes("<") || Boolean(c.prompt?.includes("{args}"));

/** Suggestions for the current input; empty when the input is not a command being typed. */
export function slashItems(draft: string, commands: SlashCommand[]): SlashItem[] {
  if (!draft.startsWith("/") || draft.includes("\n")) return [];
  const second = /^\/(\S+)\s+(\S*)$/.exec(draft);
  if (second) {
    const cmd = commands.find((c) => c.name === second[1]!.toLowerCase());
    const prefix = second[2]!.toLowerCase();
    if (!cmd?.choices?.length) return [];
    return cmd.choices
      .filter((ch) => ch.startsWith(prefix) && ch !== prefix)
      .map((ch) => ({ insert: `/${cmd.name} ${ch}`, label: `/${cmd.name} ${ch}`, description: cmd.description, command: cmd, arg: ch, runs: true }));
  }
  const first = /^\/(\S*)$/.exec(draft);
  if (!first) return [];
  const q = first[1]!.toLowerCase();
  const score = (c: SlashCommand) => (c.name.startsWith(q) ? 0 : c.name.includes(q) ? 1 : q.length >= 3 && c.description.toLowerCase().includes(q) ? 2 : 9);
  return commands
    .filter((c) => score(c) < 9)
    .sort((a, b) => score(a) - score(b))
    .map((c) => ({ insert: takesArgs(c) ? `/${c.name} ` : `/${c.name}`, label: c.usage, description: c.description, command: c, runs: !takesArgs(c) }));
}

/** `/name args` -> command + args, or null if the text is not a known command. */
export function parseSlash(text: string, commands: SlashCommand[]): { command: SlashCommand; args: string } | null {
  const m = /^\/(\S+)(?:\s+([\s\S]*))?$/.exec(text.trim());
  if (!m) return null;
  const command = commands.find((c) => c.name === m[1]!.toLowerCase());
  return command ? { command, args: (m[2] ?? "").trim() } : null;
}

/** Chat text for a `prompt` command, or an error message when the required argument is missing. */
export function promptFor(command: SlashCommand, args: string): { text: string } | { error: string } {
  const tpl = command.prompt ?? "";
  if (tpl.includes("{args}") && !args) return { error: `Bitte ergänzen: ${command.usage}` };
  return { text: tpl.replace("{args}", args) };
}
