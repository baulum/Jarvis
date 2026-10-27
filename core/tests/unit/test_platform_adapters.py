"""PlatformAdapter contract: detection, capability reports with reasons, OS commands – checked on any host."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from jarvis import config as config_mod
from jarvis.platform import (
    LinuxAdapter,
    MacAdapter,
    WindowsAdapter,
    create_adapter,
    detect_headless,
    detect_platform_id,
)
from jarvis.platform.base import ps_quote
from jarvis.platform.capabilities import CAPABILITIES, CapabilityReport, available, unavailable


def which_from(*names: str):
    return lambda n: (f"/usr/bin/{n}" if not n.endswith(".exe") else f"C:\\Windows\\{n}") if n in names else None


ALL_TOOLS = which_from(
    "open", "afplay", "say", "ffmpeg", "sips", "launchctl", "explorer", "explorer.exe", "powershell",
    "powershell.exe", "schtasks", "schtasks.exe", "xdg-open", "dbus-send", "espeak-ng", "systemctl", "ffplay",
)  # fmt: skip


WIN_TOOLS = which_from("explorer.exe", "powershell.exe", "schtasks.exe")


def test_detection_follows_sys_platform_and_can_be_forced() -> None:
    assert detect_platform_id({}, "darwin") == "mac"
    assert detect_platform_id({}, "win32") == "windows"
    assert detect_platform_id({}, "linux") == "linux"
    assert detect_platform_id({}, "freebsd14") == "linux"
    assert detect_platform_id({"JARVIS_PLATFORM": "Windows"}, "darwin") == "windows"
    with pytest.raises(ValueError):
        detect_platform_id({"JARVIS_PLATFORM": "amiga"}, "linux")


def test_headless_detection() -> None:
    assert detect_headless("linux", {}) is True
    assert detect_headless("linux", {"DISPLAY": ":0"}) is False
    assert detect_headless("linux", {"WAYLAND_DISPLAY": "wayland-0"}) is False
    assert detect_headless("linux", {"DISPLAY": ":0", "JARVIS_HEADLESS": "1"}) is True
    assert detect_headless("mac", {}) is False and detect_headless("windows", {}) is False
    assert isinstance(create_adapter({"JARVIS_PLATFORM": "linux"}, which=ALL_TOOLS), LinuxAdapter)


def test_every_adapter_reports_every_capability_with_a_valid_state() -> None:
    for cls in (MacAdapter, WindowsAdapter, LinuxAdapter):
        report = cls(which=ALL_TOOLS, environ={"DISPLAY": ":0"}, importable=lambda m: True).capabilities()
        assert set(report) == set(CAPABILITIES), cls.__name__
        for cap, st in report.items():
            assert st.status in ("available", "degraded", "unavailable"), (cls.__name__, cap)
            if st.status == "unavailable":
                assert st.reason, f"{cls.__name__}.{cap}: unavailable without a reason"


def test_mac_adapter_describes_the_existing_mac_paths(tmp_path: Path) -> None:
    mac = MacAdapter(which=ALL_TOOLS, importable=lambda m: True, data_dir=tmp_path)
    caps = mac.capabilities()
    for cap in ("host.macos_bridge", "desktop.reveal", "desktop.open", "voice.wakeup_routine", "voice.tts.system",
                "process.groups", "coding.runner", "shell.run"):  # fmt: skip
        assert caps.state(cap).status == "available", cap
    # paths in the target system's form: the result is the same whichever OS runs the tests
    assert mac.reveal_command(PurePosixPath("/x/a b.pdf")) == ["/usr/bin/open", "-R", "/x/a b.pdf"]
    assert mac.open_command("https://example.com") == ["/usr/bin/open", "https://example.com"]
    assert mac.play_command(PurePosixPath("/s.mp3"), 0.5) == ["/usr/bin/afplay", "-v", "0.50", "/s.mp3"]
    assert mac.config_overlay(tmp_path) is None  # the Mac never loads an overlay
    assert mac.file_manager == "Finder"


def test_windows_adapter_reports_missing_mac_features_with_reasons() -> None:
    win = WindowsAdapter(which=WIN_TOOLS, importable=lambda m: m != "sounddevice")
    caps = win.capabilities()
    assert caps.state("host.macos_bridge").status == "unavailable"
    assert "Device Agent" in caps.reason("host.macos_bridge")
    assert caps.state("coding.runner").status == "unavailable" and "SIGSTOP" in caps.reason("coding.runner")
    assert caps.state("voice.wakeup_routine").status == "unavailable"
    assert caps.state("voice.capture").status == "unavailable" and "sounddevice" in caps.reason("voice.capture")
    assert caps.state("voice.tts.system").status == "available"  # SAPI over PowerShell
    assert caps.state("process.groups").status == "degraded"
    reveal = win.reveal_command(PureWindowsPath("C:/Users/Sir/a.pdf"))
    assert reveal == ["C:\\Windows\\explorer.exe", "/select,C:\\Users\\Sir\\a.pdf"]
    assert win.file_manager == "Explorer"


def test_windows_media_playback_quotes_the_path_safely() -> None:
    win = WindowsAdapter(which=WIN_TOOLS)
    argv = win.play_command(Path("C:/Musik/It's ‘odd’.mp3"), 0.8)
    assert argv is not None and argv[:4] == ["C:\\Windows\\powershell.exe", "-NoProfile", "-NonInteractive", "-Command"]
    assert "[Uri]::new('C:/Musik/It''s ‘‘odd’’.mp3')" in argv[4].replace("\\", "/")
    assert "$p.Volume = 0.80" in argv[4]
    assert ps_quote("a'b") == "'a''b'"
    assert ps_quote("‚x‛") == "'‚‚x‛‛'"


def test_windows_headless_has_no_explorer() -> None:
    win = WindowsAdapter(which=WIN_TOOLS, headless=True)
    assert win.reveal_command(Path("C:/a")) is None
    assert win.capabilities().state("desktop.reveal").status == "unavailable"


def test_linux_desktop_and_headless() -> None:
    desk = LinuxAdapter(which=ALL_TOOLS, environ={"DISPLAY": ":0"}, importable=lambda m: True)
    argv = desk.reveal_command(PurePosixPath("/home/sir/Datei mit Leerzeichen.pdf"))
    assert argv is not None and argv[0] == "/usr/bin/dbus-send"
    assert "array:string:file:///home/sir/Datei%20mit%20Leerzeichen.pdf" in argv
    assert desk.play_command(PurePosixPath("/a.mp3"), 0.4) == [
        "/usr/bin/ffplay", "-nodisp", "-autoexit", "-loglevel", "error", "-volume", "40", "/a.mp3",
    ]  # fmt: skip
    assert desk.session_type() == "x11"
    no_dbus = LinuxAdapter(which=which_from("xdg-open"), environ={"DISPLAY": ":0"})
    assert no_dbus.reveal_command(PurePosixPath("/home/sir/a.pdf")) == ["/usr/bin/xdg-open", "/home/sir"]
    assert no_dbus.capabilities().state("desktop.reveal").status == "degraded"
    headless = LinuxAdapter(which=ALL_TOOLS, environ={}, headless=True, importable=lambda m: False)
    caps = headless.capabilities()
    assert headless.reveal_command(Path("/a")) is None and headless.open_command("https://x") is None
    assert caps.state("desktop.open").status == "unavailable" and "headless" in caps.reason("desktop.open")
    assert caps.state("voice.capture").status == "unavailable" and "PortAudio" in caps.reason("voice.capture")
    assert caps.state("devices.remote").status == "available"  # the Core still serves device agents


def test_capability_report_fails_closed_and_explains() -> None:
    report = CapabilityReport({"a": available(), "b": unavailable("weil")})
    assert report.usable("a") and not report.usable("b") and not report.usable("unbekannt")
    assert report.missing(("a", "b", "c")) == ["b", "c"]
    assert report.explain(("a", "b")) == "b: weil"
    assert report.explain(("a",)) == ""
    assert report.to_dict()["b"] == {"status": "unavailable", "reason": "weil", "label": "b"}


def test_config_overlay_is_loaded_only_off_the_mac(monkeypatch: pytest.MonkeyPatch) -> None:
    mac = config_mod.load_settings({"JARVIS_PLATFORM": "mac"})
    assert config_mod.platform_overlay({"JARVIS_PLATFORM": "mac"}) is None
    assert mac.paths.data_dir.as_posix().endswith("Library/Application Support/Jarvis")
    assert mac.voice.native_audio is True and mac.clients.remote_api is False
    win = config_mod.load_settings({"JARVIS_PLATFORM": "windows"})
    assert win.paths.data_dir.parts[-3:] == ("AppData", "Roaming", "Jarvis")
    assert win.voice.native_audio is False and win.browser.engine == "edge"
    assert win.voice.tts.rate == mac.voice.tts.rate  # untouched keys come from jarvis.yaml
    linux = config_mod.load_settings({"JARVIS_PLATFORM": "linux"})
    assert linux.paths.data_dir.parts[-3:] == (".local", "share", "jarvis") and linux.browser.engine == "chromium"
    # environment overrides still win over the overlay
    env = config_mod.load_settings({"JARVIS_PLATFORM": "windows", "JARVIS__PATHS__DATA_DIR": "/srv/jarvis"})
    assert env.paths.data_dir == Path("/srv/jarvis")


def test_deep_merge_keeps_sibling_keys() -> None:
    base = {"voice": {"tts": {"provider": "local", "rate": 185}, "language": "de"}}
    top = {"voice": {"tts": {"provider": "system"}}}
    assert config_mod._deep_merge(base, top) == {
        "voice": {"tts": {"provider": "system", "rate": 185}, "language": "de"}
    }
