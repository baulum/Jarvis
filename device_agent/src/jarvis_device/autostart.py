"""Start the agent at log-on – inside the user's desktop session (UI automation, clipboard and audio exist only
there; a system service would see none of it).

Windows: Task Scheduler task "at log on" running ``pythonw -m jarvis_device run`` (no console window).
Linux:   XDG autostart entry ``~/.config/autostart/jarvis-device.desktop`` (gets DISPLAY/WAYLAND_DISPLAY).
macOS:   LaunchAgent ``~/Library/LaunchAgents/com.jarvis.device-agent.plist`` (Aqua session, KeepAlive).
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

TASK_NAME = "Jarvis Device Agent"
MAC_LABEL = "com.jarvis.device-agent"


def python_gui(executable: str | None = None) -> str:
    """``pythonw.exe`` next to ``python.exe`` on Windows (no console window), otherwise the interpreter itself."""
    exe = Path(executable or sys.executable)
    if exe.name.lower() == "python.exe":
        windowed = exe.with_name("pythonw.exe")
        if windowed.exists():
            return str(windowed)
    return str(exe)


def agent_command(executable: str | None = None) -> list[str]:
    return [python_gui(executable), "-m", "jarvis_device", "run"]


# --------------------------------------------------------------------------- Windows
def windows_task_create(executable: str | None = None) -> list[str]:
    cmd = agent_command(executable)
    tr = subprocess.list2cmdline(cmd)
    return ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", tr, "/SC", "ONLOGON", "/RL", "LIMITED", "/F"]


def windows_task_delete() -> list[str]:
    return ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]


# --------------------------------------------------------------------------- Linux
def linux_autostart_path(environ: dict[str, str] | None = None) -> Path:
    env = dict(os.environ) if environ is None else environ
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / "jarvis-device.desktop"


def linux_desktop_entry(executable: str | None = None) -> str:
    cmd = agent_command(executable)
    exec_line = " ".join(_desktop_quote(a) for a in cmd)
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Jarvis Device Agent\n"
        "Comment=Lässt J.A.R.V.I.S. diesen Rechner steuern\n"
        f"Exec={exec_line}\n"
        "Terminal=false\n"
        "NoDisplay=true\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def _desktop_quote(arg: str) -> str:
    """Quoting rules of the Desktop Entry spec for the Exec key."""
    if not any(c in arg for c in " \t\n\"'\\><~|&;$*?#()`"):
        return arg
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return f'"{escaped}"'


# --------------------------------------------------------------------------- macOS
def mac_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"


def mac_plist(executable: str | None = None, log_dir: Path | None = None) -> bytes:
    logs = log_dir or Path.home() / "Library" / "Logs" / "JarvisDevice"
    return plistlib.dumps(
        {
            "Label": MAC_LABEL,
            "ProgramArguments": agent_command(executable),
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "LimitLoadToSessionType": "Aqua",
            "StandardOutPath": str(logs / "agent.out.log"),
            "StandardErrorPath": str(logs / "agent.err.log"),
        }
    )


# --------------------------------------------------------------------------- dispatch
def enable(platform: str | None = None) -> str:
    plat = sys.platform if platform is None else platform
    if plat.startswith("win"):
        res = subprocess.run(windows_task_create(), capture_output=True, text=True, check=False)
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip() or res.stdout.strip())
        return f"Aufgabenplanung: „{TASK_NAME}“ startet den Agent bei der Anmeldung"
    if plat == "darwin":
        path = mac_plist_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        (Path.home() / "Library" / "Logs" / "JarvisDevice").mkdir(parents=True, exist_ok=True)
        path.write_bytes(mac_plist())
        uid = os.getuid()
        subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(path)], capture_output=True, check=False)
        res = subprocess.run(
            ["launchctl", "bootstrap", f"gui/{uid}", str(path)], capture_output=True, text=True, check=False
        )
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip())
        return f"LaunchAgent {path}"
    path = linux_autostart_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(linux_desktop_entry(), encoding="utf-8")
    return f"Autostart-Eintrag {path} (wirkt ab der nächsten Anmeldung)"


def disable(platform: str | None = None) -> str:
    plat = sys.platform if platform is None else platform
    if plat.startswith("win"):
        subprocess.run(windows_task_delete(), capture_output=True, check=False)
        return "Aufgabe entfernt"
    if plat == "darwin":
        path = mac_plist_path()
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], capture_output=True, check=False)
        path.unlink(missing_ok=True)
        return "LaunchAgent entfernt"
    linux_autostart_path().unlink(missing_ok=True)
    return "Autostart-Eintrag entfernt"
