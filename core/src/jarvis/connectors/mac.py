"""macOS desktop control via the native JarvisBridge (Swift, 127.0.0.1:8766).

The bridge enforces its own token, kill switch, policy file and rate limits (defence in
depth); this connector adds Jarvis' risk levels/approvals and audit logging on top.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

import httpx

from jarvis.connectors.base import Connector, Health, Manifest
from jarvis.security.guards import wrap_untrusted
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext, ToolSpec

BRIDGE_URL = "http://127.0.0.1:8766"


def _obj(props: dict[str, Any], req: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": req or [], "additionalProperties": False}


SELECTOR = {"name": {"type": "string"}, "bundleId": {"type": "string"}, "pid": {"type": "integer"}}


class BridgeClient:
    def __init__(self, data_dir: Path) -> None:
        self.token_file = data_dir / "bridge.token"
        self._http = httpx.AsyncClient(base_url=BRIDGE_URL, timeout=httpx.Timeout(30, connect=2))

    def _headers(self) -> dict[str, str]:
        try:
            token = self.token_file.read_text().strip()
        except FileNotFoundError as e:
            raise ToolUserError("JarvisBridge ist nicht installiert (Token-Datei fehlt).") from e
        return {"Authorization": f"Bearer {token}"}

    async def request(self, method: str, path: str, json: dict[str, Any] | None = None) -> Any:
        try:
            r = await self._http.request(method, path, json=json, headers=self._headers())
        except httpx.ConnectError as e:
            raise ToolUserError(
                "JarvisBridge läuft nicht. Starte sie mit scripts/start.sh oder öffne JarvisBridge.app."
            ) from e
        if r.status_code >= 400:
            try:
                err = r.json()
            except ValueError:
                err = {"error": r.text[:200]}
            code = err.get("error", "error")
            if code == "permission_denied":
                raise ToolUserError(
                    f"macOS-Berechtigung fehlt: {err.get('permission')}. Bitte in "
                    "Systemeinstellungen › Datenschutz & Sicherheit für JarvisBridge erlauben."
                )
            if code == "kill_switch_engaged":
                raise ToolUserError("Not-Aus der Bridge ist aktiv.")
            raise ToolUserError(f"Bridge-Fehler {r.status_code}: {code} {err.get('message', '')}".strip())
        return r.json()

    async def health(self) -> dict[str, Any]:
        r = await self._http.get("/health", timeout=3)
        r.raise_for_status()
        return dict(r.json())

    async def set_killswitch(self, engaged: bool) -> None:
        try:
            await self.request("POST", "/killswitch", {"engaged": engaged})
        except ToolUserError:
            pass  # bridge also watches the shared KILL file

    async def close(self) -> None:
        await self._http.aclose()


class MacConnector(Connector):
    manifest = Manifest(
        id="mac",
        name="macOS Steuerung",
        description="Apps starten/wechseln, Fenster und Bedienelemente erkennen, tippen, klicken, Screenshots "
        "analysieren, Zwischenablage, Dateien, Kurzbefehle – über die native JarvisBridge.",
        category="system",
        auth="local",
        default_enabled=True,
        setup_instructions="bridge/scripts/build-app.sh ausführen, JarvisBridge.app starten (oder "
        "install-launchagent.sh) und in Systemeinstellungen › Datenschutz & Sicherheit "
        "'Bedienungshilfen' und 'Bildschirmaufnahme' für JarvisBridge erlauben.",
    )

    @property
    def bridge(self) -> BridgeClient:
        return self.state.bridge

    async def health(self) -> Health:
        try:
            h = await self.bridge.health()
        except Exception as e:
            return Health("error", f"Bridge nicht erreichbar ({type(e).__name__})")
        perms = h.get("permissions", {})
        missing = [k for k, v in perms.items() if not v]
        if h.get("killSwitch"):
            return Health("degraded", "Not-Aus aktiv", details=h)
        if missing:
            return Health("degraded", "Berechtigungen fehlen: " + ", ".join(missing), details=h)
        return Health("ok", "Bridge verbunden", details=h)

    def tools(self) -> list[ToolSpec]:
        b = lambda: self.bridge  # noqa: E731 - resolved lazily (bridge created at startup)

        async def list_apps(ctx: ToolContext, a: dict[str, Any]) -> Any:
            apps = await b().request("GET", "/apps")
            return [{"name": x["name"], "bundleId": x.get("bundleId"), "active": x.get("active")} for x in apps]

        async def open_app(ctx: ToolContext, a: dict[str, Any]) -> Any:
            body = {k: v for k, v in a.items() if k in ("name", "bundleId")}
            if not body:
                raise ToolUserError("name oder bundleId angeben")
            apps = await b().request("GET", "/apps")
            want = (a.get("bundleId") or a.get("name") or "").lower()
            running = next(
                (x for x in apps if want in (str(x.get("name", "")).lower(), str(x.get("bundleId", "")).lower())), None
            )
            if running:
                return await b().request("POST", "/apps/activate", {"pid": running["pid"]})
            return await b().request("POST", "/apps/launch", body)

        async def quit_app(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await b().request("POST", "/apps/quit", a)

        async def windows(ctx: ToolContext, a: dict[str, Any]) -> Any:
            ws = await b().request("GET", "/windows")
            return [{k: w.get(k) for k in ("windowId", "ownerName", "title", "bounds")} for w in ws][:40]

        async def frontmost(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await b().request("GET", "/frontmost")

        async def ui_tree(ctx: ToolContext, a: dict[str, Any]) -> Any:
            body = {**a, "maxDepth": min(int(a.get("maxDepth", 5)), 8), "maxNodes": 250}
            tree = await b().request("POST", "/ui/tree", body)
            return _compact_tree(tree)

        async def ui_press(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await b().request("POST", "/ui/press", a)

        async def type_text(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await b().request("POST", "/input/type", {"text": a["text"]})

        async def key(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await b().request("POST", "/input/key", a)

        async def click(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await b().request("POST", "/input/click", a)

        async def screenshot(ctx: ToolContext, a: dict[str, Any]) -> Any:
            shot = await b().request(
                "POST",
                "/screenshot",
                {k: v for k, v in a.items() if k in ("windowId", "displayId")} | {"maxWidth": 1280},
            )
            shots_dir = self.state.settings.paths.data_dir / "screenshots"
            shots_dir.mkdir(parents=True, exist_ok=True)
            png = base64.b64decode(shot["png_base64"])
            path = shots_dir / f"screen-{int(time.time())}.png"
            path.write_bytes(png)
            question = a.get("question") or "Beschreibe knapp, was auf dem Bildschirm zu sehen ist."
            description = await self.state.ollama.chat(
                self.state.settings.llm.agent_model,
                [{"role": "user", "content": question, "images": [shot["png_base64"]]}],
                purpose="vision",
                num_predict=400,
            )
            return {
                "path": str(path),
                "width": shot.get("width"),
                "height": shot.get("height"),
                "analysis": wrap_untrusted(description, "bildschirminhalt", 4000),
            }

        async def clipboard_get(ctx: ToolContext, a: dict[str, Any]) -> Any:
            clip = await b().request("GET", "/clipboard")
            return wrap_untrusted(str(clip.get("text") or ""), "zwischenablage", 6000)

        async def clipboard_set(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await b().request("POST", "/clipboard", {"text": a["text"]})

        async def files_search(ctx: ToolContext, a: dict[str, Any]) -> Any:
            body = {"query": a["query"], "limit": min(int(a.get("limit", 20)), 50)}
            if a.get("only_in"):
                body["onlyIn"] = a["only_in"]
            if a.get("names_only"):
                body["match"] = "name"
            return await b().request("POST", "/files/search", body)

        async def files_move(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await b().request("POST", "/files/move", {"from": a["from_path"], "to": a["to_path"]})

        async def files_mkdir(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await b().request("POST", "/files/mkdir", {"path": a["path"]})

        async def files_reveal(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await b().request("POST", "/files/reveal", {"path": a["path"]})

        async def open_url(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await b().request("POST", "/open-url", {"url": a["url"]})

        async def shortcuts(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await b().request("GET", "/shortcuts")

        async def shortcut_run(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await b().request("POST", "/shortcuts/run", a)

        async def applescript(ctx: ToolContext, a: dict[str, Any]) -> Any:
            return await b().request("POST", "/applescript", {"source": a["source"]})

        S = "mac"
        return [
            ToolSpec("mac_list_apps", "Listet laufende Programme.", _obj({}), list_apps, "read", S),
            ToolSpec(
                "mac_open_app",
                "Startet ein Programm oder holt es in den Vordergrund (z. B. name='Safari').",
                _obj({"name": {"type": "string"}, "bundleId": {"type": "string"}}),
                open_app,
                "low",
                S,
                summarize=lambda a: f"App öffnen: {a.get('name') or a.get('bundleId')}",
            ),
            ToolSpec(
                "mac_quit_app",
                "Beendet ein Programm regulär.",
                _obj(SELECTOR),
                quit_app,
                "medium",
                S,
                summarize=lambda a: f"App beenden: {a.get('name') or a.get('bundleId') or a.get('pid')}",
            ),
            ToolSpec("mac_list_windows", "Listet sichtbare Fenster.", _obj({}), windows, "read", S),
            ToolSpec(
                "mac_frontmost", "Aktive App, Fenster und fokussiertes Bedienelement.", _obj({}), frontmost, "read", S
            ),
            ToolSpec(
                "mac_ui_tree",
                "Liest die Bedienelemente einer App (Accessibility). Elemente haben eine "
                "id (Pfad), die mac_ui_press verwendet.",
                _obj({**SELECTOR, "maxDepth": {"type": "integer", "minimum": 1, "maximum": 8}}),
                ui_tree,
                "read",
                S,
                untrusted_output=True,
            ),
            ToolSpec(
                "mac_ui_press",
                "Drückt ein Bedienelement (Button, Menüpunkt) per Pfad oder Rolle/Titel.",
                _obj({**SELECTOR, "path": {"type": "string"}, "role": {"type": "string"}, "title": {"type": "string"}}),
                ui_press,
                "high",
                S,
                summarize=lambda a: (
                    f"Bedienelement drücken: {a.get('title') or a.get('path')} "
                    f"in {a.get('name') or a.get('bundleId') or a.get('pid')}"
                ),
            ),
            ToolSpec(
                "mac_type_text",
                "Tippt Text in das aktuell fokussierte Feld.",
                _obj({"text": {"type": "string", "maxLength": 5000}}, ["text"]),
                type_text,
                "high",
                S,
                summarize=lambda a: f"Text tippen ({len(a['text'])} Zeichen): {a['text'][:60]!r}",
            ),
            ToolSpec(
                "mac_key",
                "Drückt eine Taste/Tastenkombination (z. B. key='s', modifiers=['cmd']).",
                _obj(
                    {
                        "key": {"type": "string"},
                        "modifiers": {
                            "type": "array",
                            "items": {"type": "string", "enum": ["cmd", "shift", "alt", "ctrl", "fn"]},
                        },
                    },
                    ["key"],
                ),
                key,
                "high",
                S,
                summarize=lambda a: f"Taste {'+'.join([*a.get('modifiers', []), a['key']])}",
            ),
            ToolSpec(
                "mac_click",
                "Klickt an Bildschirmkoordinaten.",
                _obj(
                    {
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "button": {"type": "string", "enum": ["left", "right"]},
                        "count": {"type": "integer", "minimum": 1, "maximum": 2},
                    },
                    ["x", "y"],
                ),
                click,
                "high",
                S,
                summarize=lambda a: f"Klick bei ({a['x']}, {a['y']})",
            ),
            ToolSpec(
                "mac_screenshot",
                "Erstellt einen Screenshot und analysiert ihn mit dem lokalen Bildmodell.",
                _obj({"question": {"type": "string"}, "windowId": {"type": "integer"}}),
                screenshot,
                "low",
                S,
                untrusted_output=True,
                timeout_s=120,
            ),
            ToolSpec(
                "mac_clipboard_get",
                "Liest Text aus der Zwischenablage.",
                _obj({}),
                clipboard_get,
                "low",
                S,
                untrusted_output=True,
            ),
            ToolSpec(
                "mac_clipboard_set",
                "Schreibt Text in die Zwischenablage.",
                _obj({"text": {"type": "string"}}, ["text"]),
                clipboard_set,
                "low",
                S,
            ),
            ToolSpec(
                "mac_files_search",
                "Sucht Dateien per Spotlight.",
                _obj(
                    {
                        "query": {"type": "string"},
                        "only_in": {"type": "string"},
                        "names_only": {"type": "boolean"},
                        "limit": {"type": "integer"},
                    },
                    ["query"],
                ),
                files_search,
                "read",
                S,
            ),
            ToolSpec(
                "mac_files_move",
                "Verschiebt/organisiert eine Datei (nur in erlaubten Ordnern).",
                _obj({"from_path": {"type": "string"}, "to_path": {"type": "string"}}, ["from_path", "to_path"]),
                files_move,
                "medium",
                S,
                writes=True,
                summarize=lambda a: f"Datei verschieben: {a['from_path']} → {a['to_path']}",
            ),
            ToolSpec(
                "mac_files_mkdir",
                "Legt einen Ordner an (nur in erlaubten Ordnern).",
                _obj({"path": {"type": "string"}}, ["path"]),
                files_mkdir,
                "low",
                S,
            ),
            ToolSpec(
                "mac_files_reveal",
                "Zeigt eine Datei im Finder.",
                _obj({"path": {"type": "string"}}, ["path"]),
                files_reveal,
                "low",
                S,
            ),
            ToolSpec(
                "mac_open_url",
                "Öffnet eine URL im Standardbrowser.",
                _obj({"url": {"type": "string", "pattern": "^(https?|mailto):"}}, ["url"]),
                open_url,
                "low",
                S,
            ),
            ToolSpec("mac_shortcuts_list", "Listet Apple-Kurzbefehle.", _obj({}), shortcuts, "read", S),
            ToolSpec(
                "mac_shortcut_run",
                "Führt einen Apple-Kurzbefehl aus.",
                _obj({"name": {"type": "string"}, "input": {"type": "string"}}, ["name"]),
                shortcut_run,
                "medium",
                S,
                summarize=lambda a: f"Kurzbefehl ausführen: {a['name']}",
            ),
            ToolSpec(
                "mac_applescript",
                "Führt AppleScript aus (standardmäßig deaktiviert).",
                _obj({"source": {"type": "string"}}, ["source"]),
                applescript,
                "high",
                S,
                summarize=lambda a: f"AppleScript ausführen: {a['source'][:120]}",
            ),
        ]


def _compact_tree(node: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    out = {
        k: node[k]
        for k in ("id", "role", "title", "value", "description", "enabled")
        if node.get(k) not in (None, "", [])
    }
    kids = node.get("children") or []
    if kids:
        out["children"] = [_compact_tree(c, depth + 1) for c in kids]
    return out
