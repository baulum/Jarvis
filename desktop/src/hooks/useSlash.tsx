// Slash commands for a chat input: suggestion list while typing "/", keyboard handling and execution.
import { useCallback, useEffect, useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import { Button, Dialog } from "../components/ui";
import { SlashMenu } from "../components/SlashMenu";
import { parseSlash, promptFor, slashItems, type SlashCommand, type SlashItem } from "../lib/slash";
import type { ViewId } from "../store/app";
import { api, useApp } from "../store/app";

let cache: SlashCommand[] | null = null;

/** The command list of the core (fetched once per run, shared by every input). */
export function useCommands(): SlashCommand[] {
  const token = useApp((s) => s.token);
  const [list, setList] = useState<SlashCommand[]>(cache ?? []);
  useEffect(() => {
    if (cache || !token) return;
    let cancelled = false;
    api
      .commands()
      .then((r) => {
        cache = r.commands;
        if (!cancelled) setList(r.commands);
      })
      .catch(() => undefined); // no list -> "/" is just text
    return () => {
      cancelled = true;
    };
  }, [token]);
  return list;
}

const VIEWS: Record<string, ViewId> = {
  agents: "agents",
  tasks: "tasks",
  approvals: "approvals",
  memory: "memory",
  settings: "settings",
};

export interface SlashHandle {
  menu: ReactNode;
  dialog: ReactNode;
  /** Feed keydown events of the input; true = consumed. */
  onKeyDown: (e: KeyboardEvent<HTMLTextAreaElement>) => boolean;
  /** Call before sending a message; true = it was a command and has been handled. */
  tryRun: (text: string) => Promise<boolean>;
}

export function useSlash(opts: {
  draft: string;
  setDraft: (v: string) => void;
  sessionId: string | null;
  /** send plain chat text (used by prompt commands) */
  sendText: (text: string) => Promise<void> | void;
  onNew: () => void;
}): SlashHandle {
  const { draft, setDraft, sessionId, sendText, onNew } = opts;
  const commands = useCommands();
  const pushToast = useApp((s) => s.pushToast);
  const setView = useApp((s) => s.setView);
  // selection and "closed with Esc" belong to one draft text; a changed draft starts afresh
  const [nav, setNav] = useState({ draft: "", index: 0, dismissed: false });
  const fresh = nav.draft === draft;
  const index = fresh ? nav.index : 0;
  const dismissed = fresh && nav.dismissed;
  const setIndex = (i: number | ((n: number) => number)) =>
    setNav({ draft, index: typeof i === "function" ? i(index) : i, dismissed: false });
  const [confirm, setConfirm] = useState<SlashCommand | null>(null);
  const items = useMemo(() => (dismissed ? [] : slashItems(draft, commands)), [draft, commands, dismissed]);

  const execute = useCallback(
    async (command: SlashCommand, args: string) => {
      const fail = (title: string, body?: string) => pushToast({ tone: "warn", title, body });
      try {
        switch (command.action) {
          case "prompt": {
            const p = promptFor(command, args);
            if ("error" in p) return fail(p.error);
            await sendText(p.text);
            return;
          }
          case "model": {
            const choice = args.toLowerCase();
            if (!command.choices?.includes(choice)) return fail(`Modell wählen: ${command.usage}`);
            await api.setAgentModel({
              model: choice === "auto" ? null : choice,
              scope: sessionId ? "session" : "default",
              session_id: sessionId,
            });
            pushToast({ tone: "ok", title: choice === "auto" ? "Automatische Modellwahl" : `Modell: ${choice}` });
            return;
          }
          case "new":
            onNew();
            return;
          case "stop": {
            const r = await api.interruptChat();
            if (!r.interrupted) pushToast({ tone: "info", title: "Es läuft gerade keine Antwort." });
            return;
          }
          case "navigate": {
            const view = VIEWS[command.target ?? ""];
            if (view) setView(view);
            return;
          }
          case "killswitch":
            setConfirm(command);
            return;
        }
      } catch (e) {
        pushToast({ tone: "bad", title: `/${command.name} fehlgeschlagen`, body: (e as Error).message });
      }
    },
    [onNew, pushToast, sendText, sessionId, setView],
  );

  const pick = useCallback(
    (item: SlashItem) => {
      if (item.runs) {
        setDraft("");
        void execute(item.command, item.arg ?? "");
      } else {
        setDraft(item.insert);
      }
    },
    [execute, setDraft],
  );

  const tryRun = useCallback(
    async (text: string) => {
      const parsed = parseSlash(text, commands);
      if (!parsed) return false;
      setDraft("");
      await execute(parsed.command, parsed.args);
      return true;
    },
    [commands, execute, setDraft],
  );

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>): boolean => {
    if (items.length === 0 || e.nativeEvent.isComposing) return false;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setIndex((i) => (i + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length);
      return true;
    }
    if (e.key === "Tab") {
      e.preventDefault();
      setDraft(items[index]!.insert);
      return true;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      pick(items[index]!);
      return true;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      setNav({ draft, index: 0, dismissed: true });
      return true;
    }
    return false;
  };

  const menu = items.length > 0 ? <SlashMenu items={items} index={index} onHover={setIndex} onPick={pick} /> : null;

  const dialog = (
    <Dialog
      open={confirm !== null}
      onClose={() => setConfirm(null)}
      title="Not-Aus auslösen?"
      tone="danger"
      footer={
        <>
          <Button variant="ghost" onClick={() => setConfirm(null)} data-autofocus>Abbrechen</Button>
          <Button
            variant="danger"
            onClick={() => {
              setConfirm(null);
              void api
                .setKillSwitch(true, "Not-Aus über Slash-Befehl")
                .catch((e: Error) => pushToast({ tone: "bad", title: "Not-Aus fehlgeschlagen", body: e.message }));
            }}
          >
            Not-Aus auslösen
          </Button>
        </>
      }
    >
      Alle laufenden Aktionen und Agenten werden gestoppt, bis der Not-Aus wieder aufgehoben wird.
    </Dialog>
  );

  return { menu, dialog, onKeyDown, tryRun };
}
