"""Automations (script folder, Apple Shortcuts), controls (ui.tree/ui.press), frontmost and content search – the
dispatcher with a fake backend, the backends with recorded command lines (no OS involved)."""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from agent_fakes import FakeBackend, FakeRun

from jarvis_device.actions import automations
from jarvis_device.backends.base import BackendError, CapStatus, ProcResult, UiTarget, Unavailable
from jarvis_device.backends.linux import LinuxBackend, parse_tracker
from jarvis_device.backends.macos import MacBackend
from jarvis_device.backends.windows import WindowsBackend, search_literal, uia_role
from jarvis_device.dispatcher import CommandError
from jarvis_device.protocol import READ_ONLY_ACTIONS
from jarvis_device.security import PathPolicy, PolicyError

posix_only = pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX-Skript")


def _script(folder: Path, name: str, body: str, executable: bool = True) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_text(body, encoding="utf-8")
    if executable:
        p.chmod(0o755)
    return p


class UiBackend(FakeBackend):
    """Fake backend that also reports controls and a search index."""

    def __init__(self, index: tuple[str, list[str]] | None = None) -> None:
        super().__init__()
        self.index = index

    def probe(self) -> dict[str, CapStatus]:
        return super().probe() | {"ui.elements": ("available", ""), "windows.frontmost": ("available", "")}

    async def ui_tree(self, target: UiTarget, max_depth: int, max_nodes: int) -> dict[str, Any]:
        self.calls.append(("ui_tree", target, max_depth, max_nodes))
        return {"id": "0", "role": "window", "children": [{"id": "0/0", "role": "Button", "title": "OK"}]}

    async def ui_press(self, target: UiTarget, path: str | None, role: str | None, title: str | None) -> dict[str, Any]:
        self.calls.append(("ui_press", target, path, role, title))
        return {"ok": True}

    async def index_search(self, query, roots, limit, names_only):
        self.calls.append(("index_search", query, tuple(roots), limit, names_only))
        return self.index


# --------------------------------------------------------------------------- automations folder
@posix_only
def test_posix_scripts_are_listed_by_kind(tmp_path):
    folder = tmp_path / "automations"
    _script(folder, "backup.sh", "echo x")
    _script(folder, "aufräumen", "#!/bin/sh\necho y")
    _script(folder, "notiz.txt", "kein Skript", executable=False)
    _script(folder, "nicht-ausführbar", "x", executable=False)
    _script(folder, ".versteckt.sh", "echo")
    assert [s["file"] for s in automations.list_scripts(folder, "linux")] == ["aufräumen", "backup.sh"]
    assert automations.list_scripts(tmp_path / "fehlt", "linux") == []


def test_windows_kinds_and_launchers(tmp_path):
    folder = tmp_path / "automations"
    for name in ("Licht an.ps1", "build.cmd", "alt.bat", "tool.py", "app.exe", "skript.sh"):
        _script(folder, name, "x", executable=False)
    names = [s["file"] for s in automations.list_scripts(folder, "win32")]
    assert names == ["alt.bat", "app.exe", "build.cmd", "Licht an.ps1", "tool.py"]

    def which(n: str) -> str | None:
        return {"powershell.exe": "C:/ps.exe", "cmd.exe": "C:/cmd.exe", "py": "C:/py.exe"}.get(n)

    ps = automations.argv_for(folder / "Licht an.ps1", "win32", which)
    assert ps[0] == "C:/ps.exe" and ps[-2:] == ["-File", str(folder / "Licht an.ps1")]
    assert "Bypass" in ps
    assert automations.argv_for(folder / "build.cmd", "win32", which)[:3] == ["C:/cmd.exe", "/d", "/c"]
    assert automations.argv_for(folder / "tool.py", "win32", which)[0] == "C:/py.exe"
    with pytest.raises(RuntimeError):
        automations.argv_for(folder / "tool.py", "win32", lambda n: None)


def test_script_lookup_rejects_paths_and_ambiguous_names(tmp_path):
    folder = tmp_path / "automations"
    _script(folder, "licht.ps1", "x", executable=False)
    _script(folder, "licht.cmd", "x", executable=False)
    _script(folder, "musik.py", "x", executable=False)
    assert automations.find_script(folder, "MUSIK", "win32") == folder / "musik.py"
    assert automations.find_script(folder, "licht.cmd", "win32") == folder / "licht.cmd"
    with pytest.raises(ValueError, match="Mehrere Automationen"):
        automations.find_script(folder, "licht", "win32")
    for bad in ("../musik.py", "..", "a/b", "c:\\x", ""):
        assert automations.find_script(folder, bad, "win32") is None


def test_automations_folder_is_protected_from_the_core(tmp_path):
    home = tmp_path / "agent-home"
    policy = PathPolicy([tmp_path], protected=[home])
    with pytest.raises(PolicyError):
        policy.check(str(automations.folder(home) / "neu.sh"))


# --------------------------------------------------------------------------- dispatcher: automations
async def test_automation_capability_follows_config_and_folder(make_dispatcher, tmp_path):
    folder = tmp_path / "agent-home" / "automations"
    d = make_dispatcher(automations_dir=folder)
    st = d.capabilities()["automation"]
    assert st["status"] == "degraded" and "Noch keine Automationen" in st["reason"]
    _script(folder, "hallo.py", "print('hi')")
    assert d.capabilities()["automation"]["status"] == "available"
    off = make_dispatcher(automations_dir=folder, automations=False)
    assert off.capabilities()["automation"]["status"] == "unavailable"
    with pytest.raises(CommandError) as e:
        await off.run("automation.run", {"name": "hallo"})
    assert e.value.code == "unavailable" and "abgeschaltet" in e.value.message


@posix_only
async def test_automation_runs_a_script_with_input(make_dispatcher, tmp_path):
    folder = tmp_path / "agent-home" / "automations"
    _script(folder, "echo.sh", 'read line; echo "stdin=$line env=$JARVIS_INPUT name=$JARVIS_AUTOMATION"; exit 3\n')
    d = make_dispatcher(automations_dir=folder)
    listing = await d.run("automation.list", {})
    assert listing["automations"] == [{"name": "echo", "file": "echo.sh", "kind": "script", "type": "sh"}]
    assert listing["folder"] == str(folder)
    res = await d.run("automation.run", {"name": "echo", "input": "Licht"})
    assert res["exit_code"] == 3 and res["ok"] is False
    assert res["output"].strip() == "stdin=Licht env=Licht name=echo"


async def test_unknown_automation_names_what_exists(make_dispatcher, tmp_path):
    folder = tmp_path / "agent-home" / "automations"
    _script(folder, "backup.py", "print(1)")
    d = make_dispatcher(automations_dir=folder)
    with pytest.raises(CommandError) as e:
        await d.run("automation.run", {"name": "gibtsnicht"})
    assert e.value.code == "invalid" and "backup" in e.value.message


async def test_automation_and_applescript_are_blocked_by_the_kill_switch(make_dispatcher, tmp_path):
    folder = tmp_path / "agent-home" / "automations"
    _script(folder, "backup.py", "print(1)")
    d = make_dispatcher(automations_dir=folder)
    d.kill.remote = True
    assert "automation.list" in READ_ONLY_ACTIONS
    assert "automation.run" not in READ_ONLY_ACTIONS and "automation.applescript" not in READ_ONLY_ACTIONS
    with pytest.raises(CommandError) as e:
        await d.run("automation.run", {"name": "backup"})
    assert e.value.code == "kill_switch"
    assert (await d.run("automation.list", {}))["automations"][0]["name"] == "backup"


async def test_applescript_outside_macos_says_why(make_dispatcher):
    backend = FakeBackend(caps={"automation.applescript": ("available", "")})
    d = make_dispatcher(backend)
    with pytest.raises(CommandError) as e:
        await d.run("automation.applescript", {"source": "beep"})
    assert e.value.code == "unavailable" and "nur auf macOS" in e.value.message


# --------------------------------------------------------------------------- dispatcher: controls, frontmost
async def test_ui_tree_and_press_pass_the_target_and_clamp_limits(make_dispatcher):
    backend = UiBackend()
    d = make_dispatcher(backend)
    tree = await d.run("ui.tree", {"app": "Rechner", "max_depth": 50, "max_nodes": 5})
    assert tree["children"][0]["title"] == "OK"
    assert backend.calls[-1] == ("ui_tree", UiTarget(app="Rechner"), 8, 10)
    await d.run("ui.press", {"window_id": 42, "role": "Button", "title": "OK"})
    assert backend.calls[-1] == ("ui_press", UiTarget(window_id=42), None, "Button", "OK")
    with pytest.raises(CommandError) as e:
        await d.run("ui.press", {"app": "Rechner"})
    assert e.value.code == "invalid"
    backend.front = {"app": "Editor", "title": "Unbenannt"}
    assert await d.run("windows.frontmost", {}) == {"app": "Editor", "title": "Unbenannt"}


async def test_ui_press_is_not_read_only(make_dispatcher):
    d = make_dispatcher(UiBackend())
    d.kill.remote = True
    assert (await d.run("ui.tree", {}))["id"] == "0"
    with pytest.raises(CommandError) as e:
        await d.run("ui.press", {"path": "0/0"})
    assert e.value.code == "kill_switch"


# --------------------------------------------------------------------------- dispatcher: content search
async def test_content_search_uses_the_index_but_only_inside_the_roots(make_dispatcher, root, tmp_path):
    inside = root / "Rechnung.txt"
    inside.write_text("Betrag 42")
    outside = tmp_path / "geheim.txt"
    outside.write_text("Betrag")
    backend = UiBackend(index=("spotlight", [str(outside), str(inside), str(root / "weg.txt"), str(inside)]))
    d = make_dispatcher(backend)
    res = await d.run("files.search", {"query": "Betrag", "mode": "content"})
    assert res["source"] == "spotlight"
    assert [r["path"] for r in res["results"]] == [str(inside.resolve())]
    with pytest.raises(CommandError):
        await d.run("files.search", {"query": "x", "mode": "regex"})


async def test_content_search_without_index_scans_text_files(make_dispatcher, root):
    (root / "a.md").write_text("Treffen mit Herrn Stark am Montag")
    (root / "stark-notizen.txt").write_text("nichts")
    (root / "bild.bin").write_bytes(b"\x00\x01Stark")
    (root / "b.txt").write_text("anderes")
    d = make_dispatcher(UiBackend(index=None))
    res = await d.run("files.search", {"query": "stark", "mode": "content"})
    assert res["source"] == "scan" and res["complete"] is True
    got = {Path(r["path"]).name: r["match"] for r in res["results"]}
    assert got == {"a.md": "content", "stark-notizen.txt": "name"}


# --------------------------------------------------------------------------- Windows
def test_windows_ui_roles_and_search_literals():
    assert uia_role("AXButton") == "Button"
    assert uia_role("menuitem") == "MenuItem"
    assert uia_role("link") == "Hyperlink"
    assert uia_role("checkBox") == "CheckBox"
    assert search_literal("50%_off' OR 1=1 [x]\n") == "50off' OR 1=1 x"


def _windows(answer: bytes = b"", code: int = 0) -> tuple[WindowsBackend, FakeRun]:
    run = FakeRun({"powershell.exe": ProcResult(code, answer, b"Fehler")})
    return WindowsBackend(run=run, which=lambda n: "powershell.exe" if n.startswith("powershell") else None), run


async def test_windows_ui_automation_request_travels_as_json_in_the_environment():
    b, run = _windows(b'{"id":"0","role":"Window","title":"Rechner"}\r\n')
    tree = await b.ui_tree(UiTarget(window_id=0x1234), 4, 100)
    assert tree["title"] == "Rechner"
    script = base64.b64decode(run.calls[-1][0][4]).decode("utf-16-le")
    assert "UIAutomationClient" in script and "$env:JARVIS_UIA" in script
    assert json.loads(run.envs[-1]["JARVIS_UIA"]) == {"op": "tree", "hwnd": 0x1234, "max_depth": 4, "max_nodes": 100}
    await b.ui_press(UiTarget(window_id=7), None, "AXButton", "Gleich'; Remove-Item")
    req = json.loads(run.envs[-1]["JARVIS_UIA"])
    assert req == {"op": "press", "hwnd": 7, "path": None, "role": "Button", "title": "Gleich'; Remove-Item"}
    assert "Remove-Item" not in base64.b64decode(run.calls[-1][0][4]).decode("utf-16-le")


async def test_windows_search_falls_back_when_the_index_fails():
    b, run = _windows(b"C:\\Users\\tony\\a.txt\r\nC:\\Users\\tony\\b.txt\r\n")
    assert await b.index_search("Betrag", ["C:\\Users\\tony"], 20, False) == (
        "windows_search",
        ["C:\\Users\\tony\\a.txt", "C:\\Users\\tony\\b.txt"],
    )
    env = run.envs[-1]
    assert env["JARVIS_ROOTS"] == "C:/Users/tony" and env["JARVIS_NAMES"] == "0" and env["JARVIS_LIMIT"] == "20"
    broken, _ = _windows(code=1)
    assert await broken.index_search("Betrag", ["C:\\"], 20, False) is None
    assert await b.index_search("%%", ["C:\\"], 20, False) is None


def test_windows_reports_ui_elements_and_frontmost():
    b, _ = _windows()
    if sys.platform != "win32":
        assert b.probe()["ui.elements"][0] == "unavailable"


# --------------------------------------------------------------------------- Linux
def _linux(environ: dict[str, str], tools: set[str], answers=None, atspi=("available", "")):
    run = FakeRun(answers)
    which = lambda n: f"/usr/bin/{n}" if n in tools else None  # noqa: E731
    return LinuxBackend(run=run, which=which, environ=environ, atspi_probe=lambda: atspi), run


def test_tracker_output_is_parsed():
    out = "\x1b[1mfile:///home/tony/Rechnung%20M%C3%A4rz.pdf\x1b[0m\n  …Betrag…\nfile:///home/tony/a.txt\n"
    assert parse_tracker(out + "file:///home/tony/a.txt\n") == ["/home/tony/Rechnung März.pdf", "/home/tony/a.txt"]


def test_linux_ui_and_frontmost_capabilities():
    wl, _ = _linux({"WAYLAND_DISPLAY": "w"}, set())
    caps = wl.probe()
    assert caps["ui.elements"][0] == "degraded" and "AT-SPI" in caps["ui.elements"][1]
    assert caps["windows.frontmost"][0] == "degraded"
    missing, _ = _linux({"WAYLAND_DISPLAY": "w"}, set(), atspi=("unavailable", "AT-SPI fehlt (apt …)"))
    caps = missing.probe()
    assert caps["ui.elements"] == ("unavailable", "AT-SPI fehlt (apt …)")
    assert caps["windows.frontmost"][0] == "unavailable"
    x11, _ = _linux({"DISPLAY": ":0"}, {"xdotool"})
    assert x11.probe()["windows.frontmost"] == ("available", "")
    headless, _ = _linux({}, set())
    assert headless.probe()["ui.elements"][0] == "unavailable"


async def test_linux_ui_tree_runs_the_atspi_helper_with_the_request_on_stdin():
    tree = {"id": "0", "role": "application", "title": "gedit", "pid": 99}
    b, run = _linux(
        {"WAYLAND_DISPLAY": "w"}, {"python3"}, {"python3 -c": ProcResult(0, json.dumps(tree).encode(), b"")}
    )
    assert await b.ui_tree(UiTarget(app="gedit"), 3, 50) == tree
    argv, stdin = run.calls[-1]
    assert Path(argv[0]).name == "python3" and argv[1] == "-c" and "Atspi" in argv[2]
    assert json.loads(stdin or b"") == {"op": "tree", "app": "gedit", "pid": None, "max_depth": 3, "max_nodes": 50}
    front = await b.frontmost()  # Wayland: the helper answers instead of xdotool
    assert front == tree and json.loads(run.calls[-1][1] or b"") == {"op": "frontmost"}


async def test_linux_ui_errors_and_missing_atspi():
    err = ProcResult(0, b'{"error": "Kein passendes Bedienelement gefunden"}', b"")
    b, _ = _linux({"DISPLAY": ":0"}, {"python3"}, {"python3 -c": err})
    with pytest.raises(BackendError, match="Kein passendes"):
        await b.ui_press(UiTarget(), None, "push button", "OK")
    missing, _ = _linux({"DISPLAY": ":0"}, {"python3"}, atspi=("unavailable", "AT-SPI fehlt"))
    with pytest.raises(Unavailable, match="AT-SPI fehlt"):
        await missing.ui_tree(UiTarget(), 3, 50)
    headless, _ = _linux({}, {"python3"})
    with pytest.raises(Unavailable):
        await headless.ui_tree(UiTarget(), 3, 50)


async def test_linux_index_search_tracker_and_locate():
    answers = {
        "localsearch search": ProcResult(0, b"file:///home/tony/a.txt\n", b""),
        "plocate -i": ProcResult(0, b"/home/tony/Stark.txt\n/home/tony/stark/x.txt\n", b""),
    }
    b, run = _linux({"DISPLAY": ":0"}, {"localsearch", "plocate"}, answers)
    assert await b.index_search("Betrag", ["/home/tony"], 10, False) == ("tracker", ["/home/tony/a.txt"])
    assert run.calls[-1][0][-2:] == ["--", "Betrag"]
    assert await b.index_search("stark", ["/home/tony"], 10, True) == ("plocate", ["/home/tony/Stark.txt"])
    none, _ = _linux({"DISPLAY": ":0"}, set())
    assert await none.index_search("x", ["/"], 10, False) is None


# --------------------------------------------------------------------------- macOS
async def test_mac_controls_shortcuts_and_spotlight(monkeypatch):
    run = FakeRun({"mdfind": ProcResult(0, b"/Users/tony/a.txt\n/Users/tony/b.txt\n", b"")})
    b = MacBackend(run=run, which=lambda n: f"/usr/bin/{n}")
    calls: list[tuple[str, str, Any]] = []

    def fake_request(method, path, body=None):
        calls.append((method, path, body))
        if path == "/frontmost":
            return {"name": "Safari", "bundleId": "com.apple.Safari", "focusedWindowTitle": "Start"}
        if path == "/shortcuts":
            return ["Licht an", "Fokus"]
        return {"ok": True}

    monkeypatch.setattr(b, "_request", fake_request)
    assert await b.frontmost() == {"app": "Safari", "bundle_id": "com.apple.Safari", "title": "Start"}
    await b.ui_tree(UiTarget(), 4, 100)
    assert calls[-1] == ("POST", "/ui/tree", {"name": "Safari", "maxDepth": 4, "maxNodes": 100})
    await b.ui_press(UiTarget(pid=7), "0/1", None, None)
    assert calls[-1] == ("POST", "/ui/press", {"pid": 7, "path": "0/1"})
    assert await b.shortcuts_list() == ["Licht an", "Fokus"]
    assert (await b.shortcut_run("Licht an", "50"))["kind"] == "shortcut"
    assert calls[-1] == ("POST", "/shortcuts/run", {"name": "Licht an", "input": "50"})
    assert await b.index_search("Betrag", ["/Users/tony"], 10, False) == (
        "spotlight",
        ["/Users/tony/a.txt", "/Users/tony/b.txt"],
    )
    assert run.calls[-1][0] == ["/usr/bin/mdfind", "-onlyin", "/Users/tony", "Betrag"]


async def test_mac_dispatcher_lists_shortcuts_and_scripts(make_dispatcher, tmp_path, monkeypatch):
    b = MacBackend(which=lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(b, "_request", lambda m, p, body=None: ["Licht an"] if p == "/shortcuts" else {})
    folder = tmp_path / "agent-home" / "automations"
    _script(folder, "backup.sh", "echo")
    d = make_dispatcher(b, automations_dir=folder)
    items = (await d.handlers["automation.list"]({}))["automations"]
    assert [(i["name"], i["kind"]) for i in items] == [("Licht an", "shortcut"), ("backup", "script")]
    assert os.access(folder / "backup.sh", os.X_OK)
