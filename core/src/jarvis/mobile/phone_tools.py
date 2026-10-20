"""``phone_*`` agent tools: Jarvis operates Sir's Android phone (counterpart of the ``mac_*`` tools).

Everything goes through ``jarvis.mobile.phone_control.PhoneControl`` and the J.A.R.V.I.S. app's
AccessibilityService. Risk levels mirror the Mac tools: reading/opening is low, tap/type/key are ``high`` and
therefore go through the approval system. Password fields are never read or filled (enforced on the phone).
"""

from __future__ import annotations

import base64
import time
from typing import TYPE_CHECKING, Any

from jarvis.mobile.phone_control import PhoneError
from jarvis.tools.registry import ToolContext, ToolSpec

if TYPE_CHECKING:
    from jarvis.app_state import AppState

HINT = "Nur für Sirs HANDY (Android) – für den Mac die mac_*-Werkzeuge. "
MAX_NODES = 400


def _obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


def phone_tools(state: AppState) -> list[ToolSpec]:
    from jarvis.tools.executor import ToolUserError

    async def run(action: str, args: dict[str, Any] | None = None, timeout_s: float = 25.0) -> dict[str, Any]:
        try:
            return await state.phone.run(action, args, timeout_s=timeout_s)
        except PhoneError as e:
            raise ToolUserError(str(e)) from e

    async def open_app(ctx: ToolContext, a: dict[str, Any]) -> Any:
        if not (a.get("name") or a.get("package")):
            raise ToolUserError("name oder package angeben, z. B. name='WhatsApp'.")
        return await run("open_app", {k: a[k] for k in ("name", "package") if a.get(k)}, 30)

    async def list_apps(ctx: ToolContext, a: dict[str, Any]) -> Any:
        return await run("list_apps")

    async def ui_tree(ctx: ToolContext, a: dict[str, Any]) -> Any:
        data = await run("ui_tree")
        nodes = data.get("nodes")
        if isinstance(nodes, list) and len(nodes) > MAX_NODES:
            data["nodes"] = nodes[:MAX_NODES]
            data["truncated"] = True
        return data

    async def tap(ctx: ToolContext, a: dict[str, Any]) -> Any:
        has_xy = a.get("x") is not None and a.get("y") is not None
        if bool(a.get("element_id")) == has_xy:
            raise ToolUserError("Entweder element_id oder x und y angeben (nicht beides, nicht keins).")
        args = {"element_id": a["element_id"]} if a.get("element_id") else {"x": a["x"], "y": a["y"]}
        return await run("tap", args)

    async def type_text(ctx: ToolContext, a: dict[str, Any]) -> Any:
        args: dict[str, Any] = {"text": a["text"], "append": bool(a.get("append", False))}
        if a.get("element_id"):
            args["element_id"] = a["element_id"]
        return await run("type_text", args)

    async def scroll(ctx: ToolContext, a: dict[str, Any]) -> Any:
        args: dict[str, Any] = {"direction": a["direction"]}
        if a.get("element_id"):
            args["element_id"] = a["element_id"]
        return await run("scroll", args)

    async def key(ctx: ToolContext, a: dict[str, Any]) -> Any:
        return await run("key", {"key": a["key"]})

    async def screenshot(ctx: ToolContext, a: dict[str, Any]) -> Any:
        data = await run("screenshot", None, 35)
        try:
            raw = base64.b64decode(data["image_base64"], validate=True)
        except (KeyError, ValueError, TypeError) as e:
            raise ToolUserError("Das Handy hat keinen brauchbaren Screenshot geliefert.") from e
        shots_dir = state.settings.paths.data_dir / "screenshots"
        shots_dir.mkdir(parents=True, exist_ok=True)
        path = shots_dir / f"phone-{int(time.time())}.jpg"
        path.write_bytes(raw)
        question = a.get("question") or "Beschreibe knapp, was auf dem Handy-Bildschirm zu sehen ist."
        if state.agent is not None:  # Claude (subscription) reads the image with its Read tool
            description = await state.oneshot.run(
                f"Lies die Bilddatei {path} und beantworte auf Deutsch knapp: {question}", read_dir=shots_dir
            )
        else:
            assert state.ollama is not None
            description = await state.ollama.chat(
                state.settings.llm.agent_model,
                [{"role": "user", "content": question, "images": [data["image_base64"]]}],
                purpose="vision",
                num_predict=400,
            )
        return {"path": str(path), "width": data.get("width"), "height": data.get("height"), "description": description}

    S = "system"  # like the other mobile_* tools (no connector of its own that could be switched off)
    return [
        ToolSpec(
            "phone_list_apps",
            HINT + "Listet die auf dem Handy installierten, startbaren Apps (Name + Package).",
            _obj({}),
            list_apps,
            "read",
            S,
            timeout_s=40,
            summarize=lambda a: "Apps auf dem Handy auflisten",
        ),
        ToolSpec(
            "phone_open_app",
            HINT + "Startet eine App auf dem Handy („öffne WhatsApp auf meinem Handy“). name = Anzeigename "
            "(unscharfe Suche, z. B. 'WhatsApp'), alternativ package (z. B. 'com.whatsapp'). Bei mehreren "
            "Treffern kommt eine Liste zurück – dann mit package erneut aufrufen.",
            _obj({"name": {"type": "string", "maxLength": 80}, "package": {"type": "string", "maxLength": 200}}),
            open_app,
            "low",
            S,
            timeout_s=50,
            summarize=lambda a: f"App auf dem Handy öffnen: {a.get('name') or a.get('package')}",
        ),
        ToolSpec(
            "phone_ui_tree",
            HINT + "Liest den aktuellen Handy-Bildschirm (Bedienungshilfe): Text, Beschreibung, Klickbarkeit, "
            "Bounds und eine element_id je Element, die phone_tap/phone_type_text/phone_scroll verwenden. "
            "Passwortfelder werden nie ausgelesen. Nach jeder Aktion neu lesen, die IDs gelten nur für diesen Stand.",
            _obj({}),
            ui_tree,
            "read",
            S,
            untrusted_output=True,
            timeout_s=40,
            summarize=lambda a: "Handy-Bildschirm auslesen",
        ),
        ToolSpec(
            "phone_tap",
            HINT + "Tippt auf dem Handy ein Element (element_id aus phone_ui_tree) oder eine Bildschirmposition "
            "(x, y in Pixeln) an.",
            _obj({"element_id": {"type": "string", "maxLength": 80}, "x": {"type": "number"}, "y": {"type": "number"}}),
            tap,
            "high",
            S,
            timeout_s=40,
            summarize=lambda a: "Auf dem Handy antippen: " + (a.get("element_id") or f"({a.get('x')}, {a.get('y')})"),
        ),
        ToolSpec(
            "phone_type_text",
            HINT + "Schreibt Text in ein Eingabefeld auf dem Handy: das per element_id gewählte oder das gerade "
            "fokussierte. Ersetzt den Inhalt, mit append=true wird angehängt. Passwortfelder werden abgelehnt.",
            _obj(
                {
                    "text": {"type": "string", "minLength": 1, "maxLength": 600},
                    "element_id": {"type": "string", "maxLength": 80},
                    "append": {"type": "boolean"},
                },
                ["text"],
            ),
            type_text,
            "high",
            S,
            timeout_s=40,
            summarize=lambda a: f"Auf dem Handy tippen ({len(a['text'])} Zeichen): {a['text'][:60]!r}",
        ),
        ToolSpec(
            "phone_scroll",
            HINT + "Scrollt auf dem Handy. direction='down' zeigt Inhalt weiter unten (wie ein Wisch nach oben), "
            "'up' Inhalt weiter oben, 'left'/'right' seitlich. Optional element_id des Bereichs.",
            _obj(
                {
                    "direction": {"enum": ["up", "down", "left", "right"]},
                    "element_id": {"type": "string", "maxLength": 80},
                },
                ["direction"],
            ),
            scroll,
            "low",
            S,
            timeout_s=40,
            summarize=lambda a: f"Auf dem Handy scrollen: {a['direction']}",
        ),
        ToolSpec(
            "phone_key",
            HINT + "Systemtaste auf dem Handy: back (zurück), home, recents (App-Übersicht), notifications "
            "(Benachrichtigungen herunterziehen).",
            _obj({"key": {"enum": ["back", "home", "recents", "notifications"]}}, ["key"]),
            key,
            "high",
            S,
            timeout_s=40,
            summarize=lambda a: f"Handy-Taste: {a['key']}",
        ),
        ToolSpec(
            "phone_screenshot",
            HINT + "Macht einen Screenshot vom Handy-Bildschirm (Android 11+, über die Bedienungshilfe) und "
            "analysiert ihn. Für Inhalte, die phone_ui_tree nicht liefert (Bilder, Spiele). Ohne question wird der "
            "Bildschirm knapp beschrieben.",
            _obj({"question": {"type": "string", "maxLength": 400}}),
            screenshot,
            "low",
            S,
            untrusted_output=True,
            timeout_s=150,
            summarize=lambda a: "Screenshot vom Handy",
        ),
    ]
