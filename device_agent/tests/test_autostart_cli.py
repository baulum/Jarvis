"""Autostart entries per OS and the CLI commands that only touch the config."""

from __future__ import annotations

import json
import plistlib

from jarvis_device import autostart
from jarvis_device.cli import main
from jarvis_device.config import AgentConfig


def test_windows_task_runs_pythonw_at_logon(tmp_path):
    py = tmp_path / "python.exe"
    py.write_text("")
    (tmp_path / "pythonw.exe").write_text("")
    argv = autostart.windows_task_create(str(py))
    assert argv[:3] == ["schtasks", "/Create", "/TN"] and "ONLOGON" in argv and "LIMITED" in argv
    tr = argv[argv.index("/TR") + 1]
    assert "pythonw.exe" in tr and tr.endswith("-m jarvis_device run")
    assert autostart.windows_task_delete()[-1] == "/F"


def test_linux_autostart_entry_quotes_paths_with_spaces():
    entry = autostart.linux_desktop_entry("/home/sir/My Tools/bin/python")
    assert 'Exec="/home/sir/My Tools/bin/python" -m jarvis_device run' in entry
    assert "Terminal=false" in entry
    assert autostart.linux_autostart_path({"XDG_CONFIG_HOME": "/x"}).as_posix() == "/x/autostart/jarvis-device.desktop"


def test_mac_launch_agent_plist():
    data = plistlib.loads(autostart.mac_plist("/usr/bin/python3"))
    assert data["Label"] == autostart.MAC_LABEL
    assert data["ProgramArguments"] == ["/usr/bin/python3", "-m", "jarvis_device", "run"]
    assert data["LimitLoadToSessionType"] == "Aqua" and data["RunAtLoad"] is True


def test_cli_roots_config_killswitch_and_unpair(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_DEVICE_HOME", str(tmp_path / "home"))
    projects = tmp_path / "Projekte"
    projects.mkdir()
    assert main(["roots", "add", str(projects)]) == 0
    assert main(["config", "terminal", "off"]) == 0
    assert main(["config", "browser", "off"]) == 0
    assert main(["killswitch", "on"]) == 0
    assert (tmp_path / "home" / "KILL").exists()
    assert main(["killswitch", "off"]) == 0
    cfg = AgentConfig.load(tmp_path / "home" / "config.json")
    assert str(projects.resolve()) in cfg.allowed_roots and cfg.terminal == "off" and not cfg.browser_automation
    cfg.token = "jdv_x"
    cfg.save(tmp_path / "home" / "config.json")
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "jdv_x" not in out and '"token": "gesetzt"' in out
    assert main(["unpair"]) == 0
    assert AgentConfig.load(tmp_path / "home" / "config.json").token == ""
    assert main(["run"]) == 2  # not paired


def test_cli_capabilities_lists_every_capability(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_DEVICE_HOME", str(tmp_path / "home"))
    assert main(["capabilities"]) == 0
    out = capsys.readouterr().out
    for cap in ("screen.capture", "input.mouse", "terminal", "claude_code", "files.read"):
        assert cap in out
    json.dumps(out)
