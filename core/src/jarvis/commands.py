"""Slash commands: the single list Desktop and Mobile show when Sir types ``/`` (served by ``GET /api/commands``).

The server only describes the commands; the apps execute them. ``action`` tells a client what to do:

* ``prompt``   – send ``prompt`` (``{args}`` replaced by the text after the command) as a normal chat message
* ``model``    – switch the Claude model for this conversation (``choices``; ``auto`` = automatic selection)
* ``new``      – start a new conversation
* ``stop``     – interrupt the running answer
* ``navigate`` – open a screen (``target``: agents | tasks | approvals | memory | settings)
* ``killswitch`` – engage the emergency stop (clients ask for confirmation first)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

COMMANDS_VERSION = 1


@dataclass(frozen=True, slots=True)
class Command:
    name: str  # without the leading "/"
    description: str
    action: str
    usage: str = ""
    category: str = "Allgemein"
    prompt: str | None = None
    target: str | None = None
    choices: tuple[str, ...] = field(default_factory=tuple)
    confirm: bool = False


COMMANDS: tuple[Command, ...] = (
    Command("neu", "Neues Gespräch beginnen", "new", category="Gespräch"),
    Command("stop", "Laufende Antwort abbrechen", "stop", category="Gespräch"),
    Command(
        "modell",
        "Claude-Modell für dieses Gespräch wählen",
        "model",
        usage="/modell auto|haiku|sonnet|opus",
        category="Gespräch",
        choices=("auto", "haiku", "sonnet", "opus"),
    ),
    Command("agenten", "Agent-Monitor: laufende Jobs und Aufgaben", "navigate", category="Ansicht", target="agents"),
    Command("aufgaben", "Mehrstufige Aufgaben anzeigen", "navigate", category="Ansicht", target="tasks"),
    Command("freigaben", "Offene Freigaben anzeigen", "navigate", category="Ansicht", target="approvals"),
    Command("gedaechtnis", "Gedächtnis durchsuchen", "navigate", category="Ansicht", target="memory"),
    Command("einstellungen", "Einstellungen öffnen", "navigate", category="Ansicht", target="settings"),
    Command(
        "status",
        "Systemstatus und laufende Arbeiten zusammenfassen",
        "prompt",
        category="Fragen",
        prompt="Gib mir einen kurzen Statusbericht: Systemzustand, laufende Aufgaben, Coding-Jobs, offene Freigaben.",
    ),
    Command(
        "heute",
        "Was steht heute an?",
        "prompt",
        category="Fragen",
        prompt="Was steht heute an? Termine, fällige Aufgaben und Wichtiges aus dem Gedächtnis.",
    ),
    Command(
        "merke",
        "Etwas im Gedächtnis ablegen",
        "prompt",
        usage="/merke <Text>",
        category="Fragen",
        prompt="Merke dir bitte: {args}",
    ),
    Command(
        "suche",
        "Im Gedächtnis suchen",
        "prompt",
        usage="/suche <Begriff>",
        category="Fragen",
        prompt="Suche im Gedächtnis nach: {args}",
    ),
    Command(
        "code",
        "Entwicklungsaufgabe als Coding-Job starten",
        "prompt",
        usage="/code <Auftrag>",
        category="Agenten",
        prompt="Starte dafür einen Coding-Job: {args}",
    ),
    Command(
        "notaus",
        "Not-Aus: stoppt alle Aktionen und Agenten",
        "killswitch",
        category="System",
        confirm=True,
    ),
)


def command_list() -> dict[str, Any]:
    cmds = []
    for c in COMMANDS:
        d = asdict(c)
        d["choices"] = list(c.choices)
        d["usage"] = c.usage or f"/{c.name}"
        cmds.append({k: v for k, v in d.items() if v not in (None, [], "") or k in ("usage", "confirm")})
    return {"version": COMMANDS_VERSION, "commands": cmds}
