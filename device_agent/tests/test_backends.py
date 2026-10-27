"""OS backends tested without the OS: struct layouts, key tables, command lines and output parsing."""

from __future__ import annotations

import base64
import ctypes
import json
import sys

import pytest
from agent_fakes import FakeRun

from jarvis_device.backends import keys
from jarvis_device.backends.base import ProcResult, Unavailable, fuzzy_pick
from jarvis_device.backends.linux import LinuxBackend, parse_wmctrl, read_desktop_entries, session_type
from jarvis_device.backends.macos import MacBackend
from jarvis_device.backends.windows import (
    INPUT,
    KEYEVENTF_KEYUP,
    KEYEVENTF_UNICODE,
    WindowsBackend,
    encoded,
    normalize,
    text_inputs,
)


# --------------------------------------------------------------------------- keys
def test_key_vocabulary_is_shared_by_all_platforms():
    assert keys.canonical_key("Return") == "enter"
    assert keys.canonical_key("ESC") == "esc"
    assert keys.canonical_key("A") == "A"
    assert keys.canonical_modifiers(["cmd", "Strg", "shift", "ctrl"]) == ["win", "ctrl", "shift"]
    with pytest.raises(keys.KeyError_):
        keys.canonical_key("hyperdrive")
    with pytest.raises(keys.KeyError_):
        keys.canonical_modifiers(["hyper"])
    assert keys.xdotool_combo("s", ["ctrl"]) == "ctrl+s"
    assert keys.xdotool_combo("enter", ["win", "shift"]) == "super+shift+Return"
    assert keys.xdotool_combo("+", ["ctrl"]) == "ctrl+plus"
    # on the Mac "win" is the Command key and "delete" the forward delete key
    assert keys.bridge_key("delete", ["win"]) == {"key": "forwarddelete", "modifiers": ["cmd"]}
    for name in ("enter", "tab", "f12", "pagedown", "volumeup"):
        assert name in keys.VK and name in keys.KEYSYM


# --------------------------------------------------------------------------- Windows
@pytest.mark.skipif(ctypes.sizeof(ctypes.c_void_p) != 8, reason="layout checked for 64-bit Windows")
def test_sendinput_structure_matches_64bit_windows():
    assert ctypes.sizeof(INPUT) == 40


def test_unicode_typing_events():
    events = text_inputs("aé\n😀")
    # a, é: one UTF-16 unit each; newline: Enter; emoji: surrogate pair → 2 units; every unit down + up
    assert len(events) == 2 + 2 + 2 + 4
    assert events[0].ki.wScan == ord("a") and events[0].ki.dwFlags == KEYEVENTF_UNICODE
    assert events[1].ki.dwFlags == KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
    assert events[4].ki.wVk == 0x0D
    assert [events[6].ki.wScan, events[8].ki.wScan] == [0xD83D, 0xDE00]


def test_absolute_mouse_coordinates_cover_the_virtual_desktop():
    virtual = (-1920, 0, 3840, 1080)  # second monitor left of the primary one
    assert normalize(-1920, 0, virtual) == (0, 0)
    assert normalize(1919, 1079, virtual) == (65535, 65535)
    assert normalize(0, 540, virtual)[0] == round(1920 * 65535 / 3839)
    assert normalize(99999, -5, virtual) == (65535, 0)


def test_windows_backend_off_windows_reports_why():
    b = WindowsBackend(which=lambda n: None)
    if sys.platform == "win32":
        pytest.skip("runs the real backend on Windows")
    caps = b.probe()
    assert caps["input.mouse"] == ("unavailable", "Nur unter Windows verfügbar")
    assert b._vk_for("enter") == (0x0D, [])


async def test_windows_toast_and_volume_pass_values_as_environment_not_code():
    run = FakeRun({"powershell.exe": ProcResult(0, b'{"level":40,"muted":false}\r\n', b"")})
    b = WindowsBackend(run=run, which=lambda n: "powershell.exe" if n.startswith("powershell") else None)
    title = "Hallo'; Remove-Item C:\\ -Recurse; '"
    await b.notify(title, "Build <fertig> & grün")
    argv, _ = run.calls[-1]
    assert argv[:4] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand"]
    script = base64.b64decode(argv[4]).decode("utf-16-le")
    assert "ToastNotificationManager" in script and "SecurityElement]::Escape($env:JARVIS_TITLE)" in script
    assert "Remove-Item" not in script  # the title never becomes part of the script
    assert await b.volume("set", 40) == {"level": 40, "muted": False}
    assert "JarvisAudio" in base64.b64decode(run.calls[-1][0][4]).decode("utf-16-le")
    assert encoded("x") == base64.b64encode("x".encode("utf-16-le")).decode()


async def test_windows_start_menu_apps_are_parsed():
    apps = json.dumps(
        [
            {"Name": "Rechner", "AppID": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"},
            {"Name": "Editor", "AppID": "{1AC14E77}\\notepad.exe"},
            {"Name": "ohne"},
        ]
    ).encode()
    run = FakeRun({"powershell.exe": ProcResult(0, apps, b"")})
    b = WindowsBackend(run=run, which=lambda n: "powershell.exe" if n.startswith("powershell") else None)
    got = await b.start_apps()
    assert [a["name"] for a in got] == ["Rechner", "Editor"]
    assert fuzzy_pick("rech", [(a["name"], a) for a in got])["app_id"].startswith("Microsoft.WindowsCalculator")


# --------------------------------------------------------------------------- Linux
WMCTRL = """0x03a00007  0 4242   10   20   800  600  host Unbenannt – Mousepad
0x01e00003 -1 1111    0    0   1920 30   host xfce4-panel
0x04400001  1 5151   50   60   1024 768  host Jarvis – Mozilla Firefox
"""


def test_wmctrl_output_is_parsed_and_panels_skipped():
    wins = parse_wmctrl(WMCTRL)
    assert [w["window_id"] for w in wins] == [0x03A00007, 0x04400001]
    assert wins[0]["title"] == "Unbenannt – Mousepad"
    assert wins[1]["bounds"] == {"x": 50, "y": 60, "width": 1024, "height": 768}


def test_desktop_entries_localized_and_hidden_ones_skipped(tmp_path):
    apps = tmp_path / "applications"
    apps.mkdir()
    (apps / "org.gnome.Calculator.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Calculator\nName[de]=Taschenrechner\nExec=gnome-calculator\n"
    )
    (apps / "hidden.desktop").write_text("[Desktop Entry]\nType=Application\nName=Hidden\nNoDisplay=true\n")
    (apps / "link.desktop").write_text("[Desktop Entry]\nType=Link\nName=Link\n")
    (apps / "broken.desktop").write_text("kein ini")
    entries = read_desktop_entries([apps], "de")
    assert entries == [
        {
            "name": "Taschenrechner",
            "generic": "Calculator",
            "id": "org.gnome.Calculator.desktop",
            "path": str(apps / "org.gnome.Calculator.desktop"),
        }
    ]


def test_session_detection():
    assert session_type({"DISPLAY": ":0"}) == "x11"
    assert session_type({"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}) == "wayland"
    assert session_type({}) == "headless"


def _linux(environ, tools, answers=None, atspi=("unavailable", "AT-SPI fehlt")):
    run = FakeRun(answers)
    which = lambda n: f"/usr/bin/{n}" if n in tools else None  # noqa: E731
    return LinuxBackend(run=run, which=which, environ=environ, atspi_probe=lambda: atspi), run


def test_linux_capabilities_explain_what_is_missing():
    headless, _ = _linux({}, set())
    caps = headless.probe()
    assert caps["screen.capture"][0] == "unavailable" and "DISPLAY" in caps["screen.capture"][1]
    wayland, _ = _linux({"WAYLAND_DISPLAY": "w"}, {"wtype", "grim", "wl-copy", "wl-paste", "xdg-open"})
    caps = wayland.probe()
    assert caps["input.mouse"][0] == "unavailable" and "Wayland" in caps["input.mouse"][1]
    assert caps["input.keyboard"][0] == "degraded"
    assert caps["screen.capture"][0] == "available"
    x11, _ = _linux({"DISPLAY": ":0"}, {"xdotool", "wmctrl", "xclip", "xdg-open", "dbus-send", "notify-send", "pactl"})
    caps = x11.probe()
    assert all(
        caps[c][0] == "available"
        for c in (
            "input.mouse",
            "input.keyboard",
            "windows.list",
            "clipboard",
            "files.reveal",
            "notifications",
            "audio.volume",
        )
    )
    bare, _ = _linux({"DISPLAY": ":0"}, set())
    assert bare.probe()["input.keyboard"] == ("unavailable", "xdotool fehlt (apt install xdotool)")


async def test_linux_x11_input_command_lines():
    b, run = _linux({"DISPLAY": ":0"}, {"xdotool", "wmctrl"})
    await b.click(10, 20, "right", 2)
    assert run.calls[-1][0] == [
        "/usr/bin/xdotool",
        "mousemove",
        "--sync",
        "10",
        "20",
        "click",
        "--repeat",
        "2",
        "--delay",
        "80",
        "3",
    ]
    await b.key("s", ["ctrl"])
    assert run.calls[-1][0] == ["/usr/bin/xdotool", "key", "--clearmodifiers", "ctrl+s"]
    await b.type_text("-- hallo", lambda: False)
    assert run.calls[-1][0][-2:] == ["--", "-- hallo"]  # text never parsed as an option
    await b.scroll(0, -3)
    assert run.calls[-1][0] == ["/usr/bin/xdotool", "click", "--repeat", "3", "4"]


async def test_linux_wayland_refuses_mouse_with_reason():
    b, _ = _linux({"WAYLAND_DISPLAY": "w"}, {"wtype"})
    with pytest.raises(Unavailable, match="X11"):
        await b.click(1, 1, "left", 1)
    await b.key("enter", ["ctrl"])


async def test_linux_volume_with_pactl():
    answers = {
        "pactl get-sink-volume": ProcResult(0, b"Volume: front-left: 26214 /  40% / -23.88 dB", b""),
        "pactl get-sink-mute": ProcResult(0, b"Mute: no", b""),
    }
    b, run = _linux({"DISPLAY": ":0"}, {"pactl"}, answers)
    assert await b.volume("set", 40) == {"level": 40, "muted": False}
    assert run.calls[0][0] == ["/usr/bin/pactl", "set-sink-volume", "@DEFAULT_SINK@", "40%"]


async def test_linux_clipboard_text_goes_through_stdin():
    b, run = _linux({"DISPLAY": ":0"}, {"xclip"})
    await b.clipboard_set("geheim; rm -rf /")
    argv, data = run.calls[-1]
    assert argv == ["/usr/bin/xclip", "-selection", "clipboard", "-i"] and data == b"geheim; rm -rf /"


# --------------------------------------------------------------------------- macOS
async def test_mac_backend_uses_the_bridge_routes(monkeypatch):
    b = MacBackend(which=lambda n: f"/usr/bin/{n}")
    calls = []

    def fake_request(method, path, body=None):
        calls.append((method, path, body))
        if path == "/apps":
            return [{"name": "Safari", "pid": 7, "bundleId": "com.apple.Safari"}]
        return {}

    monkeypatch.setattr(b, "_request", fake_request)
    await b.open_app("safari")
    await b.open_app("Notizen")
    await b.key("delete", ["win"])
    await b.click(5, 6, "left", 3)
    assert calls == [
        ("GET", "/apps", None),
        ("POST", "/apps/activate", {"pid": 7}),
        ("GET", "/apps", None),
        ("POST", "/apps/launch", {"name": "Notizen"}),
        ("POST", "/input/key", {"key": "forwarddelete", "modifiers": ["cmd"]}),
        ("POST", "/input/click", {"x": 5, "y": 6, "button": "left", "count": 2}),
    ]
    with pytest.raises(Unavailable):
        await b.move(1, 2)


def test_mac_backend_without_bridge_reports_why(monkeypatch):
    b = MacBackend(which=lambda n: f"/usr/bin/{n}", bridge_url="http://127.0.0.1:9")
    caps = b.probe()
    assert caps["screen.capture"][0] == "unavailable" and "JarvisBridge" in caps["screen.capture"][1]
    assert caps["notifications"] == ("available", "")


async def test_mac_notification_text_is_passed_as_argv():
    run = FakeRun()
    b = MacBackend(run=run, which=lambda n: f"/usr/bin/{n}")
    await b.notify('Titel" & do shell script "rm', "Text")
    argv, _ = run.calls[-1]
    assert argv[0] == "/usr/bin/osascript" and argv[3:] == ['Titel" & do shell script "rm', "Text"]
    assert "rm" not in argv[2]
