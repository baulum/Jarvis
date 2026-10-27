"""Linux backend: X11 via xdotool/wmctrl/xclip, Wayland as far as the compositor allows (wtype, wl-clipboard,
grim), freedesktop tools for the rest (xdg-open, notify-send, .desktop files, FileManager1 over D-Bus),
PulseAudio/PipeWire for volume.

Wayland deliberately forbids global input injection and window lists for normal programs; there the probe
reports mouse/window control as unavailable *with the reason* (X11 session or ydotool needed).

Controls (``ui.tree``/``ui.press``) go through AT-SPI, the accessibility bus – on X11 *and* Wayland. Its Python
binding (PyGObject) comes from the distribution, not from pip, so the agent runs a small helper with the system's
``python3`` (``apt install python3-gi gir1.2-atspi-2.0``). On Wayland the same helper also tells which program is in
the foreground. The search index is Tracker/LocalSearch (GNOME) when it is there.
"""

from __future__ import annotations

import asyncio
import configparser
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import psutil

from jarvis_device.backends.base import (
    BackendError,
    CapStatus,
    Capture,
    DesktopBackend,
    Runner,
    UiTarget,
    Unavailable,
    fuzzy_pick,
    run_process,
)
from jarvis_device.backends.keys import xdotool_combo

XDOTOOL_BUTTON = {"left": "1", "middle": "2", "right": "3"}

ATSPI_CHECK = "import gi; gi.require_version('Atspi', '2.0'); from gi.repository import Atspi"
ATSPI_MISSING = "AT-SPI fehlt (apt install python3-gi gir1.2-atspi-2.0)"

# Runs with the system python3 (PyGObject); request on stdin, one JSON object on stdout. Paths are child indices
# from the program's accessible root ("0", "0/1", "0/1/4") – the same ones ui.tree reports.
ATSPI_SCRIPT = r"""
import json, sys
try:
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
except Exception as e:
    print(json.dumps({"error": "AT-SPI nicht verfügbar: %s" % e})); sys.exit(0)

req = json.load(sys.stdin)
PRESS = ("click", "press", "activate", "toggle", "jump", "open", "select")

def apps():
    d = Atspi.get_desktop(0)
    for i in range(d.get_child_count()):
        a = d.get_child_at_index(i)
        if a is not None:
            yield a

def active_window(app):
    for i in range(app.get_child_count()):
        w = app.get_child_at_index(i)
        if w is not None and w.get_state_set().contains(Atspi.StateType.ACTIVE):
            return w
    return None

def find_app():
    pid, name = req.get("pid"), (req.get("app") or "").casefold()
    cands = list(apps())
    if pid:
        for a in cands:
            if a.get_process_id() == pid:
                return a
    if name:
        for exact in (True, False):
            for a in cands:
                n = (a.get_name() or "").casefold()
                if (n == name) if exact else (name in n or n in name and n):
                    return a
        return None
    for a in cands:
        if active_window(a) is not None:
            return a
    return None

count = 0
def node(acc, path, depth):
    global count
    count += 1
    n = {"id": path, "role": acc.get_role_name() or "", "title": acc.get_name() or ""}
    desc = acc.get_description()
    if desc:
        n["description"] = desc[:200]
    n["enabled"] = acc.get_state_set().contains(Atspi.StateType.ENABLED)
    try:
        comp = acc.get_component_iface()
        if comp is not None:
            e = comp.get_extents(Atspi.CoordType.SCREEN)
            if e.width > 0 and e.height > 0:
                n["bounds"] = {"x": e.x, "y": e.y, "width": e.width, "height": e.height}
    except Exception:
        pass
    if depth < req.get("max_depth", 5):
        kids = []
        for i in range(min(acc.get_child_count(), 200)):
            if count >= req.get("max_nodes", 250):
                break
            c = acc.get_child_at_index(i)
            if c is not None:
                kids.append(node(c, "%s/%d" % (path, i), depth + 1))
        if kids:
            n["children"] = kids
    return n

def resolve(root, path):
    acc = root
    for part in path.split("/")[1:]:
        acc = acc.get_child_at_index(int(part))
        if acc is None:
            raise LookupError("Bedienelement %s nicht gefunden (ui.tree neu lesen)" % path)
    return acc

def search(root, role, title):
    stack, seen = [root], 0
    while stack and seen < 5000:
        acc = stack.pop(0)
        seen += 1
        r = (acc.get_role_name() or "").replace(" ", "").casefold()
        t = (acc.get_name() or "").casefold()
        if (not role or r == role) and (not title or t == title):
            return acc
        for i in range(min(acc.get_child_count(), 200)):
            c = acc.get_child_at_index(i)
            if c is not None:
                stack.append(c)
    raise LookupError("Kein passendes Bedienelement gefunden")

def press(acc):
    act = acc.get_action_iface()
    n = act.get_n_actions() if act is not None else 0
    if n <= 0:
        raise ValueError("Element lässt sich nicht drücken (keine Aktion) – per Klick auf seine Position bedienen")
    names = [(act.get_action_name(i) or "").casefold() for i in range(n)]
    idx = next((i for i, nm in enumerate(names) if nm in PRESS), 0)
    act.do_action(idx)
    return names[idx]

try:
    op = req.get("op")
    if op == "frontmost":
        for a in apps():
            w = active_window(a)
            if w is not None:
                print(json.dumps({"app": a.get_name() or "", "title": w.get_name() or "", "pid": a.get_process_id()}))
                break
        else:
            print(json.dumps({"app": "", "title": ""}))
        sys.exit(0)
    app = find_app()
    if app is None:
        raise LookupError("Programm nicht gefunden oder ohne Barrierefreiheit (AT-SPI)")
    if op == "tree":
        out = node(app, "0", 0)
        out["pid"] = app.get_process_id()
        print(json.dumps(out))
    else:
        if req.get("path"):
            el = resolve(app, req["path"])
        else:
            role = (req.get("role") or "").replace(" ", "").casefold()
            if role.startswith("ax"):
                role = role[2:]
            el = search(app, role, (req.get("title") or "").casefold())
        how = press(el)
        print(json.dumps({"ok": True, "title": el.get_name() or "", "role": el.get_role_name() or "", "method": how}))
except Exception as e:
    print(json.dumps({"error": str(e) or type(e).__name__}))
"""


def parse_tracker(text: str) -> list[str]:
    """``tracker3 search`` / ``localsearch search`` print file:// URIs (with colours and snippets around them)."""
    out: list[str] = []
    for m in re.finditer(r"file://(/[^\s\x1b]+)", text):
        path = unquote(m.group(1))
        if path not in out:
            out.append(path)
    return out


def session_type(environ: Mapping[str, str]) -> str:
    if environ.get("WAYLAND_DISPLAY") or environ.get("XDG_SESSION_TYPE") == "wayland":
        return "wayland"
    if environ.get("DISPLAY"):
        return "x11"
    return "headless"


def parse_wmctrl(text: str) -> list[dict[str, Any]]:
    """``wmctrl -lpG``: id desktop pid x y w h host title…"""
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        parts = line.split(None, 8)
        if len(parts) < 8:
            continue
        try:
            wid = int(parts[0], 16)
            desktop, pid, x, y, w, h = (int(p) for p in parts[1:7])
        except ValueError:
            continue
        if desktop < 0:  # sticky panels / docks
            continue
        title = parts[8] if len(parts) > 8 else ""
        out.append({"window_id": wid, "pid": pid, "title": title, "bounds": {"x": x, "y": y, "width": w, "height": h}})
    return out


def desktop_dirs(environ: Mapping[str, str]) -> list[Path]:
    data_home = environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    data_dirs = (environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    dirs = [Path(data_home) / "applications"] + [Path(d) / "applications" for d in data_dirs if d]
    dirs += [
        Path("/var/lib/flatpak/exports/share/applications"),
        Path.home() / ".local/share/flatpak/exports/share/applications",
    ]
    return dirs


def read_desktop_entries(dirs: list[Path], lang: str = "de") -> list[dict[str, str]]:
    """Visible applications from .desktop files (Name, localized name, id, path)."""
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*.desktop")):
            did = str(f.relative_to(d)).replace("/", "-")
            if did in seen:
                continue
            seen.add(did)
            cp = configparser.RawConfigParser(strict=False, interpolation=None)
            try:
                cp.read(f, encoding="utf-8")
            except (configparser.Error, UnicodeDecodeError, OSError):
                continue
            if not cp.has_section("Desktop Entry"):
                continue
            e = cp["Desktop Entry"]
            if e.get("Type", "Application") != "Application" or e.get("NoDisplay", "false").lower() == "true":
                continue
            if e.get("Hidden", "false").lower() == "true":
                continue
            name = e.get(f"Name[{lang}]") or e.get("Name") or did.removesuffix(".desktop")
            out.append({"name": name, "generic": e.get("Name", name), "id": did, "path": str(f)})
    return out


class LinuxBackend(DesktopBackend):
    platform = "linux"

    def __init__(
        self,
        run: Runner = run_process,
        which: Callable[[str], str | None] = shutil.which,
        environ: Mapping[str, str] | None = None,
        atspi_probe: Callable[[], CapStatus] | None = None,
    ) -> None:
        super().__init__(run, which)
        self.environ = os.environ if environ is None else environ
        self.kind = session_type(self.environ)
        self._entries: list[dict[str, str]] | None = None
        self._atspi_probe = atspi_probe or self._probe_atspi
        self._atspi: CapStatus | None = None

    def session(self) -> str:
        return self.kind

    # ------------------------------------------------------------------ AT-SPI helper
    def system_python(self) -> str | None:
        """The distribution's python3 (it has PyGObject; the agent's own venv does not)."""
        return "/usr/bin/python3" if Path("/usr/bin/python3").exists() else self.have("python3")

    def _probe_atspi(self) -> CapStatus:
        py = self.system_python()
        if py is None:
            return ("unavailable", "python3 fehlt")
        try:
            res = subprocess.run([py, "-c", ATSPI_CHECK], capture_output=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return ("unavailable", ATSPI_MISSING)
        if res.returncode != 0:
            return ("unavailable", ATSPI_MISSING)
        return ("available", "")

    def atspi(self) -> CapStatus:
        if self._atspi is None:
            self._atspi = self._atspi_probe()
        return self._atspi

    async def _atspi_call(self, req: dict[str, Any]) -> dict[str, Any]:
        state, why = self.atspi()
        py = self.system_python()
        if state == "unavailable" or py is None:
            raise Unavailable(why or ATSPI_MISSING)
        res = await self.run([py, "-c", ATSPI_SCRIPT], input=json.dumps(req).encode(), timeout=40)
        try:
            data = json.loads(res.text.strip().splitlines()[-1])
        except (ValueError, IndexError) as e:
            raise BackendError("AT-SPI: " + res.stderr.decode("utf-8", errors="replace").strip()[:200]) from e
        if not isinstance(data, dict):
            raise BackendError("AT-SPI lieferte keine lesbare Antwort")
        if data.get("error"):
            raise BackendError(str(data["error"])[:300])
        return data

    # ------------------------------------------------------------------ capabilities
    def probe(self) -> dict[str, CapStatus]:
        if self.kind == "headless":
            reason = "Keine grafische Sitzung (DISPLAY/WAYLAND_DISPLAY fehlen)"
            return {cap: ("unavailable", reason) for cap in _DESKTOP_CAPS} | self._audio_caps()
        at_state, at_why = self.atspi()
        ui: CapStatus = (
            ("degraded", "AT-SPI: nur Programme mit Barrierefreiheit (GTK, Qt, Firefox; Chromium/Electron mit "
             "--force-renderer-accessibility)")
            if at_state != "unavailable"
            else ("unavailable", at_why)
        )  # fmt: skip
        x11 = self.kind == "x11"
        xdo, wm = self.have("xdotool"), self.have("wmctrl")
        wayland_input = self.have("wtype")
        try:
            import mss  # noqa: F401

            has_mss = True
        except ImportError:
            has_mss = False
        caps: dict[str, CapStatus] = {}
        if x11:
            caps["windows.list"] = (
                ("available", "") if wm or xdo else ("unavailable", "wmctrl fehlt (apt install wmctrl)")
            )
            caps["windows.focus"] = caps["windows.list"]
            caps["apps.list"] = caps["windows.list"]
            caps["apps.quit"] = ("available", "") if wm else ("unavailable", "wmctrl fehlt (apt install wmctrl)")
            caps["input.keyboard"] = (
                ("available", "") if xdo else ("unavailable", "xdotool fehlt (apt install xdotool)")
            )
            caps["input.mouse"] = caps["input.keyboard"]
            caps["screen.capture"] = (
                ("available", "")
                if has_mss or self.have("import", "scrot", "maim")
                else ("unavailable", "Python-Paket mss fehlt (pip install mss)")
            )
            clip = self.have("xclip", "xsel")
            caps["clipboard"] = ("available", "") if clip else ("unavailable", "xclip fehlt (apt install xclip)")
            caps["windows.frontmost"] = (
                ("available", "")
                if xdo
                else ("degraded", "Über AT-SPI (xdotool fehlt)")
                if at_state != "unavailable"
                else ("unavailable", "xdotool fehlt (apt install xdotool)")
            )
        else:
            no_wl = "Wayland erlaubt anderen Programmen keine Fensterliste – X11-Sitzung verwenden"
            caps["windows.list"] = ("unavailable", no_wl)
            caps["windows.focus"] = ("unavailable", no_wl)
            caps["apps.list"] = ("degraded", "Nur Prozesse (Wayland gibt keine Fensterliste heraus)")
            caps["apps.quit"] = ("degraded", "Nur über Prozessende (SIGTERM) – Wayland gibt keine Fenster heraus")
            caps["input.keyboard"] = (
                ("degraded", "wtype: tippt ins fokussierte Fenster")
                if wayland_input
                else ("unavailable", "Wayland erlaubt keine Tastatureingaben anderer Programme (wtype installieren)")
            )
            caps["input.mouse"] = ("unavailable", "Wayland erlaubt keine Maussteuerung durch andere Programme")
            caps["screen.capture"] = (
                ("available", "")
                if self.have("grim")
                else ("degraded", "Über gnome-screenshot")
                if self.have("gnome-screenshot")
                else ("unavailable", "grim oder gnome-screenshot fehlt")
            )
            caps["clipboard"] = (
                ("available", "")
                if self.have("wl-paste") and self.have("wl-copy")
                else ("unavailable", "wl-clipboard fehlt (apt install wl-clipboard)")
            )
            caps["windows.frontmost"] = (
                ("degraded", "Über AT-SPI: Programm und Fenstertitel, keine Fenster-ID")
                if at_state != "unavailable"
                else ("unavailable", "Wayland nennt das aktive Fenster nicht – AT-SPI installieren (" + at_why + ")")
            )
        caps["apps.launch"] = (
            ("available", "")
            if self.have("gtk-launch", "gio")
            else ("degraded", "Nur Programme im PATH (gtk-launch fehlt)")
        )
        caps["files.reveal"] = (
            ("available", "")
            if self.have("dbus-send")
            else ("degraded", "Öffnet nur den Ordner (dbus-send fehlt)")
            if self.have("xdg-open")
            else ("unavailable", "xdg-open fehlt (xdg-utils)")
        )
        caps["browser.open"] = (
            ("available", "") if self.have("xdg-open") else ("unavailable", "xdg-open fehlt (xdg-utils)")
        )
        caps["notifications"] = (
            ("available", "") if self.have("notify-send") else ("unavailable", "notify-send fehlt (libnotify-bin)")
        )
        caps["ui.elements"] = ui
        return caps | self._audio_caps()

    def _audio_caps(self) -> dict[str, CapStatus]:
        vol = self.have("pactl", "wpctl", "amixer")
        tts = self.have("espeak-ng", "espeak", "spd-say")
        player = self.have("ffplay", "mpg123", "mpv", "paplay", "aplay")
        return {
            "audio.volume": ("available", "") if vol else ("unavailable", "pactl/wpctl/amixer fehlt"),
            "audio.speak": ("available", "") if tts else ("unavailable", "espeak-ng fehlt (apt install espeak-ng)"),
            "audio.play": ("available", "") if player else ("unavailable", "Kein Audioplayer (ffplay, mpg123, paplay)"),
        }

    async def _ok(self, argv: list[str], *, input: bytes | None = None, timeout: float = 15) -> str:
        res = await self.run(argv, input=input, timeout=timeout)
        if res.returncode != 0:
            raise BackendError(f"{Path(argv[0]).name}: " + res.stderr.decode("utf-8", errors="replace").strip()[:200])
        return res.text

    # ------------------------------------------------------------------ windows and programs
    async def list_windows(self) -> list[dict[str, Any]]:
        if self.kind != "x11":
            raise Unavailable("Wayland erlaubt anderen Programmen keine Fensterliste – X11-Sitzung verwenden")
        wm = self.have("wmctrl")
        if wm is None:
            raise Unavailable("wmctrl fehlt (apt install wmctrl)")
        wins = parse_wmctrl(await self._ok([wm, "-lpG"]))
        active = await self._active_id()
        for w in wins:
            try:
                proc = psutil.Process(w["pid"]).name() if w["pid"] > 0 else ""
            except psutil.Error:
                proc = ""
            w["process"], w["app"], w["active"] = proc, proc, w["window_id"] == active
        return wins

    async def _active_id(self) -> int | None:
        xdo = self.have("xdotool")
        if xdo is None:
            return None
        res = await self.run([xdo, "getactivewindow"], timeout=5)
        try:
            return int(res.text.strip())
        except ValueError:
            return None

    async def list_apps(self) -> list[dict[str, Any]]:
        if self.kind == "x11":
            apps: dict[int, dict[str, Any]] = {}
            for w in await self.list_windows():
                a = apps.setdefault(w["pid"], {"name": w["app"], "pid": w["pid"], "active": False, "windows": []})
                a["windows"].append(w["title"])
                a["active"] = a["active"] or w["active"]
            return sorted(apps.values(), key=lambda a: str(a["name"]).lower())
        # Wayland: no windows – the user's GUI-ish processes
        uid = os.getuid() if hasattr(os, "getuid") else None
        out = []
        for p in psutil.process_iter(["pid", "name", "uids", "terminal"]):
            info = p.info
            if uid is not None and info.get("uids") and info["uids"].real != uid:
                continue
            if info.get("terminal"):
                continue
            out.append({"name": info.get("name") or "", "pid": info["pid"], "active": False, "windows": []})
        return out[:200]

    def entries(self) -> list[dict[str, str]]:
        if self._entries is None:
            lang = (self.environ.get("LANG") or "de")[:2]
            self._entries = read_desktop_entries(desktop_dirs(self.environ), lang)
        return self._entries

    async def open_app(self, name: str) -> dict[str, Any]:
        if self.kind == "x11" and (self.have("wmctrl")):
            wins = await self.list_windows()
            running = fuzzy_pick(name, [(w["app"], w) for w in wins] + [(w["title"], w) for w in wins])
            if running is not None:
                await self.focus_window(running["window_id"], None, None)
                return {"activated": running["app"], "title": running["title"]}
        entry = fuzzy_pick(name, [(e["name"], e) for e in self.entries()] + [(e["generic"], e) for e in self.entries()])
        if entry is not None:
            if gtk := self.have("gtk-launch"):
                await self._ok([gtk, entry["id"].removesuffix(".desktop")], timeout=20)
                return {"launched": entry["name"], "desktop_id": entry["id"]}
            if gio := self.have("gio"):
                await self._ok([gio, "launch", entry["path"]], timeout=20)
                return {"launched": entry["name"], "desktop_id": entry["id"]}
        exe = self.have(name)
        if exe:
            subprocess.Popen(
                [exe], start_new_session=True, close_fds=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return {"launched": exe}
        raise BackendError(f"Programm „{name}“ nicht gefunden (.desktop-Dateien und PATH durchsucht)")

    async def quit_app(self, name: str | None, pid: int | None) -> dict[str, Any]:
        if self.kind == "x11" and (wm := self.have("wmctrl")):
            wins = await self.list_windows()
            if pid:
                targets = [w for w in wins if w["pid"] == pid]
            elif name:
                first = fuzzy_pick(name, [(w["app"], w) for w in wins] + [(w["title"], w) for w in wins])
                targets = [w for w in wins if first is not None and w["pid"] == first["pid"]]
            else:
                raise BackendError("name oder pid angeben")
            if not targets:
                raise BackendError("Kein passendes Programm mit Fenster gefunden")
            for w in targets:
                await self._ok([wm, "-ic", hex(w["window_id"])])
            return {"closed_windows": len(targets), "app": targets[0]["app"], "pid": targets[0]["pid"]}
        procs = [
            p
            for p in psutil.process_iter(["pid", "name"])
            if (pid and p.pid == pid) or (name and str(p.info.get("name") or "").casefold() == name.casefold())
        ]
        if not procs:
            raise BackendError("Kein passender Prozess gefunden")
        for p in procs:
            p.terminate()
        return {"terminated": [p.pid for p in procs]}

    async def focus_window(self, window_id: int | None, title: str | None, app: str | None) -> dict[str, Any]:
        wins = await self.list_windows()
        target = next((w for w in wins if w["window_id"] == window_id), None) if window_id else None
        if target is None and (app or title):
            q = app or title or ""
            target = fuzzy_pick(q, [(w["app"], w) for w in wins] + [(w["title"], w) for w in wins])
        if target is None:
            raise BackendError("Kein passendes Fenster gefunden")
        if wm := self.have("wmctrl"):
            await self._ok([wm, "-ia", hex(target["window_id"])])
        elif xdo := self.have("xdotool"):
            await self._ok([xdo, "windowactivate", "--sync", str(target["window_id"])])
        return {"window_id": target["window_id"], "title": target["title"], "app": target["app"]}

    async def frontmost(self) -> dict[str, Any]:
        xdo = self.have("xdotool")
        if self.kind != "x11" or xdo is None:
            if self.kind != "headless" and self.atspi()[0] != "unavailable":
                return await self._atspi_call({"op": "frontmost"})
            raise Unavailable("Vordergrundfenster nur unter X11 mit xdotool oder über AT-SPI bekannt")
        wid = await self._active_id()
        if wid is None:
            return {"app": "", "title": ""}
        title = (await self.run([xdo, "getwindowname", str(wid)], timeout=5)).text.strip()
        pid_txt = (await self.run([xdo, "getwindowpid", str(wid)], timeout=5)).text.strip()
        try:
            proc = psutil.Process(int(pid_txt)).name()
        except (ValueError, psutil.Error):
            proc = ""
        return {"app": proc, "process": proc, "title": title, "window_id": wid}

    # ------------------------------------------------------------------ input
    async def type_text(self, text: str, should_stop: Callable[[], bool]) -> None:
        chunks = [text[i : i + 200] for i in range(0, len(text), 200)]
        for chunk in chunks:
            if should_stop():
                raise BackendError("Not-Aus – Tippen abgebrochen")
            if self.kind == "x11" and (xdo := self.have("xdotool")):
                await self._ok([xdo, "type", "--clearmodifiers", "--delay", "8", "--", chunk], timeout=60)
            elif self.kind == "wayland" and (wt := self.have("wtype")):
                await self._ok([wt, "--", chunk], timeout=60)
            else:
                raise Unavailable("Tippen braucht xdotool (X11) oder wtype (Wayland)")

    async def key(self, key: str, modifiers: list[str]) -> None:
        if self.kind == "x11" and (xdo := self.have("xdotool")):
            await self._ok([xdo, "key", "--clearmodifiers", xdotool_combo(key, modifiers)])
            return
        if self.kind == "wayland" and (wt := self.have("wtype")):
            from jarvis_device.backends.keys import KEYSYM, KEYSYM_MODIFIERS, canonical_key, canonical_modifiers

            mods = [KEYSYM_MODIFIERS[m] for m in canonical_modifiers(modifiers)]
            k = canonical_key(key)
            argv = [wt]
            for m in mods:
                argv += ["-M", m]
            argv += ["-k", KEYSYM.get(k, k)] if len(k) > 1 else [k]
            for m in reversed(mods):
                argv += ["-m", m]
            await self._ok(argv)
            return
        raise Unavailable("Tastenkombinationen brauchen xdotool (X11) oder wtype (Wayland)")

    def _xdo(self) -> str:
        xdo = self.have("xdotool")
        if self.kind != "x11" or xdo is None:
            raise Unavailable("Maussteuerung braucht eine X11-Sitzung mit xdotool")
        return xdo

    async def move(self, x: int, y: int) -> None:
        await self._ok([self._xdo(), "mousemove", "--sync", str(x), str(y)])

    async def click(self, x: int, y: int, button: str, count: int) -> None:
        xdo = self._xdo()
        await self._ok(
            [
                xdo,
                "mousemove",
                "--sync",
                str(x),
                str(y),
                "click",
                "--repeat",
                str(max(1, count)),
                "--delay",
                "80",
                XDOTOOL_BUTTON.get(button, "1"),
            ]
        )

    async def scroll(self, dx: int, dy: int) -> None:
        xdo = self._xdo()
        if dy:
            await self._ok([xdo, "click", "--repeat", str(abs(dy)), "5" if dy > 0 else "4"])
        if dx:
            await self._ok([xdo, "click", "--repeat", str(abs(dx)), "7" if dx > 0 else "6"])

    # ------------------------------------------------------------------ screen
    async def screenshot(self, window_id: int | None, display: int | None) -> Capture:
        if self.kind == "x11":
            try:
                import mss
                import mss.tools
            except ImportError as e:
                raise Unavailable("Python-Paket mss fehlt (pip install mss)") from e
            region: dict[str, int] | None = None
            if window_id:
                w = next((w for w in await self.list_windows() if w["window_id"] == window_id), None)
                if w is None:
                    raise BackendError("Fenster nicht gefunden")
                b = w["bounds"]
                region = {"left": b["x"], "top": b["y"], "width": b["width"], "height": b["height"]}

            def grab() -> Capture:
                with mss.mss() as sct:
                    mon = region or dict(sct.monitors[display if display and display < len(sct.monitors) else 0])
                    img = sct.grab(mon)
                    png = mss.tools.to_png(img.rgb, img.size) or b""
                    return Capture(png, img.size[0], img.size[1], int(mon["left"]), int(mon["top"]))

            return await asyncio.to_thread(grab)
        if self.kind == "wayland":
            if grim := self.have("grim"):
                res = await self.run([grim, "-t", "png", "-"], timeout=20)
                if res.returncode != 0:
                    raise BackendError("grim: " + res.stderr.decode(errors="replace")[:200])
                return _capture_from_png(res.stdout)
            if gs := self.have("gnome-screenshot"):
                import tempfile

                with tempfile.TemporaryDirectory() as td:
                    out = Path(td) / "shot.png"
                    await self._ok([gs, "-f", str(out)], timeout=20)
                    return _capture_from_png(out.read_bytes())
        raise Unavailable("Keine grafische Sitzung für Screenshots")

    # ------------------------------------------------------------------ clipboard
    async def clipboard_get(self) -> str:
        if self.kind == "wayland" and (wp := self.have("wl-paste")):
            res = await self.run([wp, "--no-newline"], timeout=5)
            return res.text if res.returncode == 0 else ""
        if xc := self.have("xclip"):
            res = await self.run([xc, "-selection", "clipboard", "-o"], timeout=5)
            return res.text if res.returncode == 0 else ""
        if xs := self.have("xsel"):
            res = await self.run([xs, "--clipboard", "--output"], timeout=5)
            return res.text if res.returncode == 0 else ""
        raise Unavailable("xclip/xsel oder wl-clipboard fehlt")

    async def clipboard_set(self, text: str) -> None:
        data = text.encode("utf-8")
        if self.kind == "wayland" and (wc := self.have("wl-copy")):
            await self._ok([wc], input=data)
            return
        if xc := self.have("xclip"):
            await self._ok([xc, "-selection", "clipboard", "-i"], input=data)
            return
        if xs := self.have("xsel"):
            await self._ok([xs, "--clipboard", "--input"], input=data)
            return
        raise Unavailable("xclip/xsel oder wl-clipboard fehlt")

    # ------------------------------------------------------------------ shell integration
    async def reveal(self, path: str) -> None:
        if dbus := self.have("dbus-send"):
            res = await self.run(
                [
                    dbus,
                    "--session",
                    "--print-reply",
                    "--dest=org.freedesktop.FileManager1",
                    "--type=method_call",
                    "/org/freedesktop/FileManager1",
                    "org.freedesktop.FileManager1.ShowItems",
                    f"array:string:file://{quote(path)}",
                    "string:",
                ],
                timeout=10,
            )
            if res.returncode == 0:
                return
        if xdg := self.have("xdg-open"):
            await self._ok([xdg, str(Path(path).parent)])
            return
        raise Unavailable("Weder FileManager1 (D-Bus) noch xdg-open verfügbar")

    async def open_url(self, url: str) -> None:
        xdg = self.have("xdg-open")
        if xdg is None:
            raise Unavailable("xdg-open fehlt (xdg-utils)")
        subprocess.Popen([xdg, url], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    async def notify(self, title: str, message: str) -> None:
        ns = self.have("notify-send")
        if ns is None:
            raise Unavailable("notify-send fehlt (libnotify-bin)")
        await self._ok([ns, "--app-name=J.A.R.V.I.S.", "--", title, message])

    # ------------------------------------------------------------------ controls (AT-SPI)
    async def _ui_req(self, op: str, target: UiTarget) -> dict[str, Any]:
        if self.kind == "headless":
            raise Unavailable("Keine grafische Sitzung")
        req: dict[str, Any] = {"op": op, "app": target.app, "pid": target.pid}
        if target.window_id and not (target.app or target.pid):
            win = next((w for w in await self.list_windows() if w["window_id"] == target.window_id), None)
            if win is None:
                raise BackendError("Fenster nicht gefunden")
            req["pid"] = win["pid"]
        return req

    async def ui_tree(self, target: UiTarget, max_depth: int, max_nodes: int) -> dict[str, Any]:
        req = await self._ui_req("tree", target) | {"max_depth": max_depth, "max_nodes": max_nodes}
        return await self._atspi_call(req)

    async def ui_press(
        self, target: UiTarget, path: str | None, role: str | None, title: str | None
    ) -> dict[str, Any]:
        req = await self._ui_req("press", target) | {"path": path, "role": role, "title": title}
        return await self._atspi_call(req)

    # ------------------------------------------------------------------ search index
    async def index_search(
        self, query: str, roots: Sequence[str], limit: int, names_only: bool
    ) -> tuple[str, list[str]] | None:
        if names_only:
            if loc := self.have("plocate", "locate"):
                res = await self.run([loc, "-i", "-l", str(limit * 5), "--", query], timeout=20)
                if res.returncode in (0, 1):  # 1 = nothing found
                    paths = [p for p in res.text.splitlines() if query.casefold() in Path(p).name.casefold()]
                    return Path(loc).name, paths
            return None
        for exe in ("localsearch", "tracker3"):
            if tool := self.have(exe):
                res = await self.run(
                    [tool, "search", "--disable-color", "--limit", str(limit), "--", query], timeout=30
                )
                if res.returncode == 0:
                    return "tracker", parse_tracker(res.text)
        return None  # no index (or Tracker off): the agent scans the allowed folders itself

    # ------------------------------------------------------------------ audio
    async def volume(self, op: str, level: int | None) -> dict[str, Any]:
        if pactl := self.have("pactl"):
            sink = "@DEFAULT_SINK@"
            if op == "set":
                await self._ok([pactl, "set-sink-volume", sink, f"{int(level or 0)}%"])
            elif op in ("mute", "unmute"):
                await self._ok([pactl, "set-sink-mute", sink, "1" if op == "mute" else "0"])
            vol = await self._ok([pactl, "get-sink-volume", sink])
            mute = await self._ok([pactl, "get-sink-mute", sink])
            m = re.search(r"(\d+)%", vol)
            return {"level": int(m.group(1)) if m else 0, "muted": "yes" in mute.lower() or "ja" in mute.lower()}
        if wp := self.have("wpctl"):
            sink = "@DEFAULT_AUDIO_SINK@"
            if op == "set":
                await self._ok([wp, "set-volume", sink, f"{int(level or 0)}%"])
            elif op in ("mute", "unmute"):
                await self._ok([wp, "set-mute", sink, "1" if op == "mute" else "0"])
            out = await self._ok([wp, "get-volume", sink])
            m = re.search(r"Volume:\s*([\d.]+)", out)
            return {"level": round(float(m.group(1)) * 100) if m else 0, "muted": "MUTED" in out}
        if amixer := self.have("amixer"):
            if op == "set":
                await self._ok([amixer, "-q", "sset", "Master", f"{int(level or 0)}%"])
            elif op in ("mute", "unmute"):
                await self._ok([amixer, "-q", "sset", "Master", "mute" if op == "mute" else "unmute"])
            out = await self._ok([amixer, "sget", "Master"])
            m = re.search(r"\[(\d+)%\]", out)
            return {"level": int(m.group(1)) if m else 0, "muted": "[off]" in out}
        raise Unavailable("pactl/wpctl/amixer fehlt")

    async def speak(self, text: str) -> None:
        if es := self.have("espeak-ng", "espeak"):
            await self._ok([es, "-v", "de", "--stdin"], input=text.encode("utf-8"), timeout=120)
            return
        if sd := self.have("spd-say"):
            await self._ok([sd, "-l", "de", "-w", "--", text], timeout=120)
            return
        raise Unavailable("espeak-ng fehlt (apt install espeak-ng)")

    async def play(self, path: str) -> None:
        for argv in (
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", path],
            ["mpg123", "-q", path],
            ["mpv", "--no-video", "--really-quiet", path],
            ["paplay", path],
            ["aplay", "-q", path],
        ):
            if exe := self.have(argv[0]):
                await self._ok([exe, *argv[1:]], timeout=600)
                return
        raise Unavailable("Kein Audioplayer installiert (ffplay, mpg123, mpv, paplay)")


def _capture_from_png(png: bytes) -> Capture:
    if png[:8] != b"\x89PNG\r\n\x1a\n" or len(png) < 24:
        raise BackendError("Ungültiges PNG vom Screenshot-Werkzeug")
    width = int.from_bytes(png[16:20], "big")
    height = int.from_bytes(png[20:24], "big")
    return Capture(png, width, height)


_DESKTOP_CAPS = (
    "apps.list",
    "apps.launch",
    "apps.quit",
    "windows.list",
    "windows.focus",
    "input.keyboard",
    "input.mouse",
    "screen.capture",
    "clipboard",
    "files.reveal",
    "browser.open",
    "notifications",
)
