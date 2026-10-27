"""``devices`` connector: the agent's tools for computers that run the Jarvis Device Agent.

The tools exist only while at least one device agent is paired – on a Mac without one, the agent's tool list is
exactly what it was before. Every tool takes an optional ``device`` (name, host name or id); with one device online
it may be omitted. Risk levels follow the mac_* tools: typing, clicking and shell commands are ``high``.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Awaitable, Callable
from typing import Any

from jarvis.connectors.base import Connector, Health, Manifest
from jarvis.devices.hub import DeviceError
from jarvis.security.guards import wrap_untrusted
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext, ToolSpec

S = "devices"
DEVICE = {
    "device": {"type": "string", "description": "Gerätename, Hostname oder ID (leer = einziges verbundenes Gerät)"}
}
MODIFIERS = {
    "type": "array",
    "items": {"type": "string", "enum": ["ctrl", "shift", "alt", "win", "cmd", "super", "fn"]},
}


def _obj(props: dict[str, Any], req: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": {**DEVICE, **props}, "required": req or [], "additionalProperties": False}


def _on(a: dict[str, Any]) -> str:
    return f" auf {a['device']}" if a.get("device") else ""


class DevicesConnector(Connector):
    manifest = Manifest(
        id="devices",
        name="Geräte (Jarvis Device Agent)",
        description="Steuert gekoppelte Rechner (Windows, Linux, Mac) über den Jarvis Device Agent: Programme, "
        "Fenster, Maus, Tastatur, Screenshots, Dateien, Terminal, Claude Code, Browser, Benachrichtigungen, Audio.",
        category="system",
        auth="local",
        default_enabled=True,
        setup_instructions="Auf dem Rechner den Jarvis Device Agent installieren (scripts/windows/install.ps1 bzw. "
        "scripts/linux/install.sh) und mit `jarvis-device pair` koppeln; den Kopplungscode erzeugt "
        "`jarvisctl devices pair` oder die Desktop-App unter Geräte. Verbindung über Tailscale empfohlen.",
    )

    def missing_settings(self) -> list[str]:
        return [] if self.state.device_hub.has_paired else ["gekoppelter Jarvis Device Agent"]

    async def health(self) -> Health:
        online = self.state.device_hub.online()
        paired = len(self.state.device_hub.paired)
        if not online:
            return Health("degraded", f"{paired} gekoppelt, keins verbunden")
        return Health(
            "ok",
            f"{len(online)} von {paired} verbunden: " + ", ".join(c.name for c in online),
            details={"devices": [c.name for c in online]},
        )

    def tools(self) -> list[ToolSpec]:
        def action(
            name: str,
            build: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
            timeout: Callable[[dict[str, Any]], float] | None = None,
            wrap: Callable[[Any], Any] | None = None,
        ) -> Callable[[ToolContext, dict[str, Any]], Awaitable[Any]]:
            async def handler(ctx: ToolContext, a: dict[str, Any]) -> Any:
                args = build(a) if build else {k: v for k, v in a.items() if k != "device"}
                try:
                    data = await self.state.device_hub.call(
                        a.get("device"), name, args, timeout(a) if timeout else None
                    )
                except DeviceError as e:
                    raise ToolUserError(e.message) from e
                return wrap(data) if wrap else data

            return handler

        async def device_list(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return [
                {
                    "name": d["name"],
                    "online": d["online"],
                    "platform": d.get("platform"),
                    "hostname": d.get("hostname"),
                    "unavailable": sorted(
                        c for c, st in d.get("capabilities", {}).items() if st["status"] == "unavailable"
                    ),
                }
                for d in self.state.device_hub.describe()
            ]

        async def screenshot(ctx: ToolContext, a: dict[str, Any]) -> Any:
            args = {k: v for k, v in a.items() if k in ("window_id", "display")} | {"max_width": 1600}
            try:
                shot = await self.state.device_hub.call(a.get("device"), "screen.capture", args, 60)
            except DeviceError as e:
                raise ToolUserError(e.message) from e
            return await self._analyse_screenshot(shot, a.get("question"))

        def untrusted(label: str, limit: int) -> Callable[[Any], Any]:
            def wrap(data: Any) -> Any:
                if isinstance(data, dict) and "text" in data:
                    return {**data, "text": wrap_untrusted(str(data["text"]), label, limit)}
                return wrap_untrusted(str(data), label, limit)

            return wrap

        def shell_args(a: dict[str, Any]) -> dict[str, Any]:
            return {k: a[k] for k in ("argv", "command", "cwd", "timeout_s") if k in a}

        def shell_out(data: Any) -> Any:
            if isinstance(data, dict):
                for k, n in (("stdout", 8000), ("stderr", 4000)):
                    if isinstance(data.get(k), str):
                        data[k] = data[k][-n:]
            return data

        def automation_out(data: Any) -> Any:
            if isinstance(data, dict):
                for k, n in (("output", 8000), ("stderr", 4000)):
                    if isinstance(data.get(k), str):
                        data[k] = wrap_untrusted(data[k][-n:], "automation", n)
            return data

        def ui_target(a: dict[str, Any]) -> dict[str, Any]:
            return {k: a[k] for k in ("app", "pid", "window_id") if k in a}

        def ui_tree_args(a: dict[str, Any]) -> dict[str, Any]:
            return ui_target(a) | {"max_depth": min(int(a.get("max_depth", 5)), 8), "max_nodes": 250}

        def ui_press_args(a: dict[str, Any]) -> dict[str, Any]:
            return ui_target(a) | {k: a[k] for k in ("path", "role", "title") if a.get(k)}

        return [
            ToolSpec(
                "device_list",
                "Listet gekoppelte Geräte (Jarvis Device Agent) mit Status und fehlenden Fähigkeiten.",
                _obj({}),
                device_list,
                "read",
                S,
            ),
            ToolSpec(
                "device_system_info",
                "Systeminformationen eines Geräts: OS, CPU, RAM, Datenträger, Akku, Laufzeit.",
                _obj({}),
                action("system.info"),
                "read",
                S,
            ),
            ToolSpec(
                "device_apps_list", "Laufende Programme auf einem Gerät.", _obj({}), action("apps.list"), "read", S
            ),
            ToolSpec(
                "device_app_open",
                "Startet ein Programm auf einem Gerät oder holt es nach vorn (Name wie im Startmenü, z. B. 'Rechner').",
                _obj({"name": {"type": "string", "minLength": 1}}, ["name"]),
                action("apps.open"),
                "low",
                S,
                summarize=lambda a: f"Programm öffnen{_on(a)}: {a['name']}",
            ),
            ToolSpec(
                "device_app_quit",
                "Beendet ein Programm auf einem Gerät regulär (Fenster schließen).",
                _obj({"name": {"type": "string"}, "pid": {"type": "integer"}}),
                action("apps.quit"),
                "medium",
                S,
                summarize=lambda a: f"Programm beenden{_on(a)}: {a.get('name') or a.get('pid')}",
            ),
            ToolSpec(
                "device_windows_list",
                "Sichtbare Fenster eines Geräts (ID, Titel, Programm, Position).",
                _obj({}),
                action("windows.list"),
                "read",
                S,
            ),
            ToolSpec(
                "device_window_focus",
                "Holt ein Fenster (window_id oder Titel/Programm) in den Vordergrund.",
                _obj({"window_id": {"type": "integer"}, "title": {"type": "string"}, "app": {"type": "string"}}),
                action("windows.focus"),
                "low",
                S,
            ),
            ToolSpec(
                "device_frontmost",
                "Aktives Programm und Fenstertitel eines Geräts.",
                _obj({}),
                action("windows.frontmost"),
                "read",
                S,
            ),
            ToolSpec(
                "device_ui_tree",
                "Liest die Bedienelemente eines Programms auf einem Gerät (macOS Accessibility, Windows UI Automation, "
                "Linux AT-SPI); ohne app/pid/window_id das Programm im Vordergrund. Elemente haben eine id (Pfad) für "
                "device_ui_press und meist bounds (Bildschirmkoordinaten).",
                _obj(
                    {
                        "app": {"type": "string"},
                        "pid": {"type": "integer"},
                        "window_id": {"type": "integer"},
                        "max_depth": {"type": "integer", "minimum": 1, "maximum": 8},
                    }
                ),
                action("ui.tree", build=ui_tree_args, timeout=lambda a: 60, wrap=_compact_tree),
                "read",
                S,
                untrusted_output=True,
            ),
            ToolSpec(
                "device_ui_press",
                "Drückt ein Bedienelement (Button, Menüpunkt, Kontrollkästchen) auf einem Gerät – per path aus "
                "device_ui_tree oder per role/title (z. B. role='Button', title='OK').",
                _obj(
                    {
                        "app": {"type": "string"},
                        "pid": {"type": "integer"},
                        "window_id": {"type": "integer"},
                        "path": {"type": "string"},
                        "role": {"type": "string"},
                        "title": {"type": "string"},
                    }
                ),
                action("ui.press", build=ui_press_args, timeout=lambda a: 60),
                "high",
                S,
                summarize=lambda a: (
                    f"Bedienelement drücken{_on(a)}: {a.get('title') or a.get('path') or a.get('role')}"
                    + (f" in {a['app']}" if a.get("app") else "")
                ),
            ),
            ToolSpec(
                "device_screenshot",
                "Screenshot eines Geräts aufnehmen und analysieren (optional ein Fenster oder Bildschirm).",
                _obj(
                    {"question": {"type": "string"}, "window_id": {"type": "integer"}, "display": {"type": "integer"}}
                ),
                screenshot,
                "low",
                S,
                untrusted_output=True,
                timeout_s=120,
            ),
            ToolSpec(
                "device_click",
                "Klickt an Bildschirmkoordinaten (Pixel des Screenshots). Mit app wird das Programm vorher aktiviert "
                "und geprüft.",
                _obj(
                    {
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "button": {"type": "string", "enum": ["left", "right", "middle"]},
                        "count": {"type": "integer", "minimum": 1, "maximum": 3},
                        "app": {"type": "string"},
                    },
                    ["x", "y"],
                ),
                action("input.click"),
                "high",
                S,
                summarize=lambda a: (
                    f"Klick{_on(a)} bei ({a['x']}, {a['y']})" + (f" in {a['app']}" if a.get("app") else "")
                ),
            ),
            ToolSpec(
                "device_mouse_move",
                "Bewegt den Mauszeiger an Bildschirmkoordinaten.",
                _obj({"x": {"type": "number"}, "y": {"type": "number"}}, ["x", "y"]),
                action("input.move"),
                "medium",
                S,
                summarize=lambda a: f"Maus bewegen{_on(a)} nach ({a['x']}, {a['y']})",
            ),
            ToolSpec(
                "device_scroll",
                "Scrollt (dy > 0 nach unten, dx > 0 nach rechts), optional an einer Position.",
                _obj(
                    {
                        "dx": {"type": "integer"},
                        "dy": {"type": "integer"},
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                    }
                ),
                action("input.scroll"),
                "medium",
                S,
                summarize=lambda a: f"Scrollen{_on(a)} ({a.get('dx', 0)}, {a.get('dy', 0)})",
            ),
            ToolSpec(
                "device_type_text",
                "Tippt Text auf einem Gerät. Mit app wird das Programm vorher aktiviert und geprüft.",
                _obj({"text": {"type": "string", "maxLength": 5000}, "app": {"type": "string"}}, ["text"]),
                action("input.type"),
                "high",
                S,
                summarize=lambda a: f"Tippen{_on(a)} ({len(a['text'])} Zeichen): {a['text'][:60]!r}",
            ),
            ToolSpec(
                "device_key",
                "Drückt eine Taste/Kombination (z. B. key='s', modifiers=['ctrl']; key='enter', 'tab', 'f5').",
                _obj({"key": {"type": "string"}, "modifiers": MODIFIERS, "app": {"type": "string"}}, ["key"]),
                action("input.key"),
                "high",
                S,
                summarize=lambda a: f"Taste{_on(a)} {'+'.join([*a.get('modifiers', []), a['key']])}",
            ),
            ToolSpec(
                "device_clipboard_get",
                "Liest Text aus der Zwischenablage eines Geräts.",
                _obj({}),
                action("clipboard.get", wrap=untrusted("zwischenablage", 6000)),
                "low",
                S,
                untrusted_output=True,
            ),
            ToolSpec(
                "device_clipboard_set",
                "Schreibt Text in die Zwischenablage eines Geräts.",
                _obj({"text": {"type": "string"}}, ["text"]),
                action("clipboard.set"),
                "low",
                S,
            ),
            ToolSpec(
                "device_files_list",
                "Listet einen Ordner auf einem Gerät (nur freigegebene Ordner des Agents).",
                _obj({"path": {"type": "string"}}, ["path"]),
                action("files.list"),
                "read",
                S,
            ),
            ToolSpec(
                "device_file_read",
                "Liest eine Datei auf einem Gerät (Text, max. 1 MB).",
                _obj(
                    {"path": {"type": "string"}, "max_bytes": {"type": "integer", "minimum": 1, "maximum": 1048576}},
                    ["path"],
                ),
                action("files.read", wrap=untrusted("datei", 20000)),
                "low",
                S,
                untrusted_output=True,
            ),
            ToolSpec(
                "device_file_write",
                "Schreibt eine Textdatei auf einem Gerät (nur freigegebene Ordner).",
                _obj(
                    {
                        "path": {"type": "string"},
                        "content": {"type": "string", "maxLength": 1000000},
                        "overwrite": {"type": "boolean"},
                    },
                    ["path", "content"],
                ),
                action("files.write"),
                "medium",
                S,
                writes=True,
                summarize=lambda a: f"Datei schreiben{_on(a)}: {a['path']} ({len(a['content'])} Zeichen)",
            ),
            ToolSpec(
                "device_files_search",
                "Sucht Dateien auf einem Gerät (unterhalb der freigegebenen Ordner): mode 'name' (Standard) nach "
                "Dateinamen, mode 'content' auch nach Inhalt über den Suchindex des Systems (Spotlight, Windows-Suche, "
                "Tracker), ohne Index per Durchsuchen.",
                _obj(
                    {
                        "query": {"type": "string", "minLength": 1},
                        "root": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                        "mode": {"type": "string", "enum": ["name", "content"]},
                    },
                    ["query"],
                ),
                action("files.search", timeout=lambda a: 90),
                "read",
                S,
            ),
            ToolSpec(
                "device_files_move",
                "Verschiebt/benennt eine Datei auf einem Gerät um (nur freigegebene Ordner).",
                _obj({"from_path": {"type": "string"}, "to_path": {"type": "string"}}, ["from_path", "to_path"]),
                action("files.move"),
                "medium",
                S,
                writes=True,
                summarize=lambda a: f"Datei verschieben{_on(a)}: {a['from_path']} → {a['to_path']}",
            ),
            ToolSpec(
                "device_files_mkdir",
                "Legt einen Ordner auf einem Gerät an (nur freigegebene Ordner).",
                _obj({"path": {"type": "string"}}, ["path"]),
                action("files.mkdir"),
                "low",
                S,
            ),
            ToolSpec(
                "device_files_reveal",
                "Zeigt eine Datei im Dateimanager des Geräts (Explorer/Finder).",
                _obj({"path": {"type": "string"}}, ["path"]),
                action("files.reveal"),
                "low",
                S,
            ),
            ToolSpec(
                "device_shell_run",
                "Führt einen Terminal-Befehl auf einem Gerät aus (argv-Liste bevorzugt; command wird in der Shell "
                "des Geräts ausgeführt: PowerShell auf Windows, sh auf Linux/Mac).",
                _obj(
                    {
                        "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                        "command": {"type": "string", "maxLength": 4000},
                        "cwd": {"type": "string"},
                        "timeout_s": {"type": "integer", "minimum": 1, "maximum": 600},
                    }
                ),
                action("shell.run", build=shell_args, timeout=lambda a: float(a.get("timeout_s", 60)), wrap=shell_out),
                "high",
                S,
                timeout_s=620,
                summarize=lambda a: f"Terminal{_on(a)}: " + (" ".join(a.get("argv") or []) or a.get("command", "")),
            ),
            ToolSpec(
                "device_claude_code",
                "Startet Claude Code (claude -p) auf einem Gerät in einem Projektordner und liefert das Ergebnis.",
                _obj(
                    {
                        "prompt": {"type": "string", "minLength": 3, "maxLength": 20000},
                        "cwd": {"type": "string"},
                        "model": {"type": "string", "enum": ["haiku", "sonnet", "opus"]},
                        "timeout_s": {"type": "integer", "minimum": 30, "maximum": 840},
                    },
                    ["prompt", "cwd"],
                ),
                action("claude.run", timeout=lambda a: float(a.get("timeout_s", 600))),
                "high",
                S,
                untrusted_output=True,
                timeout_s=860,
                summarize=lambda a: f"Claude Code{_on(a)} in {a['cwd']}: {a['prompt'][:100]}",
            ),
            ToolSpec(
                "device_open_url",
                "Öffnet eine URL im Standardbrowser eines Geräts.",
                _obj({"url": {"type": "string", "pattern": "^(https?|mailto):"}}, ["url"]),
                action("browser.open"),
                "low",
                S,
                summarize=lambda a: f"URL öffnen{_on(a)}: {a['url']}",
            ),
            ToolSpec(
                "device_browser_automate",
                "Automatisiert einen Browser auf einem Gerät (Playwright): Schritte goto/click/fill/press/wait/text/"
                "screenshot, z. B. [{'goto': 'https://…'}, {'fill': '#q', 'value': 'Jarvis'}, {'press': 'Enter'}, "
                "{'text': 'body'}].",
                _obj(
                    {
                        "steps": {"type": "array", "items": {"type": "object"}, "minItems": 1, "maxItems": 40},
                        "headless": {"type": "boolean"},
                    },
                    ["steps"],
                ),
                action("browser.automate", timeout=lambda a: 180, wrap=untrusted("webseite", 12000)),
                "high",
                S,
                untrusted_output=True,
                timeout_s=200,
                summarize=lambda a: f"Browser-Automatisierung{_on(a)} ({len(a['steps'])} Schritte)",
            ),
            ToolSpec(
                "device_notify",
                "Zeigt eine Benachrichtigung auf einem Gerät.",
                _obj(
                    {"title": {"type": "string", "maxLength": 120}, "message": {"type": "string", "maxLength": 500}},
                    ["title", "message"],
                ),
                action("notify"),
                "low",
                S,
                summarize=lambda a: f"Benachrichtigung{_on(a)}: {a['title']}",
            ),
            ToolSpec(
                "device_automations_list",
                "Automationen eines Geräts: Skripte aus dem Automationen-Ordner des Agents (Windows/Linux/Mac) und "
                "Apple-Kurzbefehle (Mac).",
                _obj({}),
                action("automation.list", timeout=lambda a: 30),
                "read",
                S,
            ),
            ToolSpec(
                "device_automation_run",
                "Führt eine Automation auf einem Gerät aus (Skriptname oder Apple-Kurzbefehl); input geht an "
                "stdin bzw. den Kurzbefehl.",
                _obj(
                    {
                        "name": {"type": "string", "minLength": 1, "maxLength": 256},
                        "input": {"type": "string", "maxLength": 100000},
                        "timeout_s": {"type": "integer", "minimum": 5, "maximum": 600},
                    },
                    ["name"],
                ),
                action("automation.run", timeout=lambda a: float(a.get("timeout_s", 300)) + 10, wrap=automation_out),
                "medium",
                S,
                timeout_s=640,
                summarize=lambda a: f"Automation ausführen{_on(a)}: {a['name']}",
            ),
            ToolSpec(
                "device_applescript",
                "Führt AppleScript auf einem gekoppelten Mac aus (nur wenn dessen JarvisBridge es erlaubt).",
                _obj({"source": {"type": "string", "minLength": 1, "maxLength": 20000}}, ["source"]),
                action("automation.applescript", timeout=lambda a: 90),
                "high",
                S,
                summarize=lambda a: f"AppleScript{_on(a)}: {a['source'][:120]}",
            ),
            ToolSpec(
                "device_audio",
                "Audio auf einem Gerät: Lautstärke lesen/setzen (level 0–100), stumm/laut, Text vorlesen, "
                "Datei abspielen.",
                _obj(
                    {
                        "action": {
                            "type": "string",
                            "enum": ["volume_get", "volume_set", "mute", "unmute", "speak", "play"],
                        },
                        "level": {"type": "integer", "minimum": 0, "maximum": 100},
                        "text": {"type": "string", "maxLength": 2000},
                        "path": {"type": "string"},
                    },
                    ["action"],
                ),
                self._audio_handler(),
                "low",
                S,
                summarize=lambda a: f"Audio{_on(a)}: {a['action']}",
            ),
        ]

    def _audio_handler(self) -> Callable[[ToolContext, dict[str, Any]], Awaitable[Any]]:
        async def handler(ctx: ToolContext, a: dict[str, Any]) -> Any:
            kind = a["action"]
            if kind in ("volume_get", "volume_set", "mute", "unmute"):
                name, args = "audio.volume", {"op": kind.removeprefix("volume_"), "level": a.get("level")}
            elif kind == "speak":
                if not a.get("text"):
                    raise ToolUserError("text angeben")
                name, args = "audio.speak", {"text": a["text"]}
            else:
                if not a.get("path"):
                    raise ToolUserError("path angeben")
                name, args = "audio.play", {"path": a["path"]}
            try:
                return await self.state.device_hub.call(a.get("device"), name, args, 120)
            except DeviceError as e:
                raise ToolUserError(e.message) from e

        return handler

    async def _analyse_screenshot(self, shot: Any, question: str | None) -> dict[str, Any]:
        if not isinstance(shot, dict) or not shot.get("png_base64"):
            raise ToolUserError("Das Gerät hat kein Bild geliefert")
        shots_dir = self.state.settings.paths.data_dir / "screenshots"
        shots_dir.mkdir(parents=True, exist_ok=True)
        png = base64.b64decode(shot["png_base64"])
        path = shots_dir / f"device-{int(time.time() * 1000)}.png"
        path.write_bytes(png)
        q = question or "Beschreibe knapp, was auf dem Bildschirm zu sehen ist."
        if self.state.agent is not None:  # Claude (subscription) reads the PNG with its Read tool
            description = await self.state.oneshot.run(
                f"Lies die Bilddatei {path} und beantworte auf Deutsch knapp: {q}", read_dir=shots_dir
            )
        else:
            assert self.state.ollama is not None
            description = await self.state.ollama.chat(
                self.state.settings.llm.agent_model,
                [{"role": "user", "content": q, "images": [shot["png_base64"]]}],
                purpose="vision",
                num_predict=400,
            )
        return {
            "path": str(path),
            "width": shot.get("width"),
            "height": shot.get("height"),
            "scale": shot.get("scale"),
            "analysis": wrap_untrusted(description, "bildschirminhalt", 4000),
        }


TREE_KEYS = ("id", "role", "title", "value", "description", "enabled", "automation_id", "bounds")


def _compact_tree(node: Any) -> Any:
    """The controls tree without empty fields; the root keeps app/pid/truncation hints."""
    if not isinstance(node, dict):
        return node
    out = {k: node[k] for k in TREE_KEYS if node.get(k) not in (None, "", [])}
    for k in ("app", "pid", "nodeCount", "truncated"):
        if node.get(k) not in (None, ""):
            out[k] = node[k]
    kids = node.get("children") or []
    if kids:
        out["children"] = [_compact_tree(c) for c in kids if isinstance(c, dict)]
    return out
