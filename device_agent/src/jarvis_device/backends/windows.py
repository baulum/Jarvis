"""Windows 10/11 backend: Win32 (ctypes) for windows, mouse, keyboard and clipboard; PowerShell for Start-menu
apps, toasts, volume (Core Audio) and speech (SAPI); mss for screenshots.

The agent runs in the user's interactive session (Task Scheduler "at log on"), never as a service – UI
automation, the clipboard and audio only exist there. Windows of elevated programs (UAC) ignore input from a
non-elevated process (UIPI); the probe says so instead of pretending otherwise.

All structures use fixed-width integer types, so their layout can be checked on any 64-bit host (tests).
"""

from __future__ import annotations

import asyncio
import base64
import ctypes
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

import psutil

from jarvis_device.backends.base import (
    BackendError,
    CapStatus,
    Capture,
    DesktopBackend,
    Runner,
    Unavailable,
    fuzzy_pick,
    run_process,
)
from jarvis_device.backends.keys import VK, VK_EXTENDED, VK_MODIFIERS, canonical_key, canonical_modifiers

# --------------------------------------------------------------------------- Win32 types (fixed width)
DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
LONG = ctypes.c_int32
ULONG_PTR = ctypes.c_size_t

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000
WHEEL_DELTA = 120
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
GW_OWNER = 4
SW_RESTORE = 9
WM_CLOSE = 0x0010
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
DWMWA_CLOAKED = 14
VK_MENU = 0x12
VK_RETURN = 0x0D
VK_TAB = 0x09

BUTTONS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", LONG),
        ("dy", LONG),
        ("mouseData", DWORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", WORD),
        ("wScan", WORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", DWORD), ("u", _INPUTUNION)]


class RECT(ctypes.Structure):
    _fields_ = [("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG)]


def key_input(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return inp


def mouse_input(dx: int = 0, dy: int = 0, data: int = 0, flags: int = 0) -> INPUT:
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = MOUSEINPUT(dx=dx, dy=dy, mouseData=data & 0xFFFFFFFF, dwFlags=flags, time=0, dwExtraInfo=0)
    return inp


def vk_inputs(vk: int, up: bool) -> INPUT:
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if vk in VK_EXTENDED else 0)
    return key_input(vk=vk, flags=flags)


def text_inputs(text: str) -> list[INPUT]:
    """Unicode key events for ``text`` (each UTF-16 code unit down/up; newline = Enter, tab = Tab)."""
    out: list[INPUT] = []
    for ch in text.replace("\r\n", "\n"):
        if ch == "\r":
            continue
        if ch in "\n\t":
            vk = VK_RETURN if ch == "\n" else VK_TAB
            out += [key_input(vk=vk), key_input(vk=vk, flags=KEYEVENTF_KEYUP)]
            continue
        data = ch.encode("utf-16-le")
        for i in range(0, len(data), 2):
            unit = int.from_bytes(data[i : i + 2], "little")
            out += [
                key_input(scan=unit, flags=KEYEVENTF_UNICODE),
                key_input(scan=unit, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
            ]
    return out


def normalize(x: float, y: float, virtual: tuple[int, int, int, int]) -> tuple[int, int]:
    """Screen pixel → 0…65535 over the whole virtual desktop (all monitors), as SendInput expects."""
    vx, vy, vw, vh = virtual
    nx = round((x - vx) * 65535 / max(1, vw - 1))
    ny = round((y - vy) * 65535 / max(1, vh - 1))
    return max(0, min(65535, nx)), max(0, min(65535, ny))


def encoded(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode()


PS_UTF8 = (
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    "[Console]::InputEncoding = [System.Text.Encoding]::UTF8; "
)

START_APPS_SCRIPT = PS_UTF8 + "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"

TOAST_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$t = [Security.SecurityElement]::Escape($env:JARVIS_TITLE)
$m = [Security.SecurityElement]::Escape($env:JARVIS_MESSAGE)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$body = "<binding template='ToastGeneric'><text>$t</text><text>$m</text></binding>"
$xml.LoadXml("<toast><visual>$body</visual></toast>")
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show($toast)
"""

VOLUME_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume {
  int f(); int g(); int h(); int i();
  int SetMasterVolumeLevelScalar(float fLevel, System.Guid pguidEventContext);
  int j();
  int GetMasterVolumeLevelScalar(out float pfLevel);
  int k(); int l(); int m(); int n();
  int SetMute([MarshalAs(UnmanagedType.Bool)] bool bMute, System.Guid pguidEventContext);
  int GetMute(out bool pbMute);
}
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {
  int Activate(ref System.Guid id, int clsCtx, int activationParams, out IAudioEndpointVolume aev);
}
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {
  int f();
  int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice endpoint);
}
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumeratorComObject { }
public class JarvisAudio {
  static IAudioEndpointVolume Vol() {
    var enumerator = new MMDeviceEnumeratorComObject() as IMMDeviceEnumerator;
    IMMDevice dev = null;
    Marshal.ThrowExceptionForHR(enumerator.GetDefaultAudioEndpoint(0, 1, out dev));
    IAudioEndpointVolume epv = null;
    var epvid = typeof(IAudioEndpointVolume).GUID;
    Marshal.ThrowExceptionForHR(dev.Activate(ref epvid, 23, 0, out epv));
    return epv;
  }
  public static float Volume {
    get { float v = -1; Marshal.ThrowExceptionForHR(Vol().GetMasterVolumeLevelScalar(out v)); return v; }
    set { Marshal.ThrowExceptionForHR(Vol().SetMasterVolumeLevelScalar(value, System.Guid.Empty)); }
  }
  public static bool Mute {
    get { bool mute; Marshal.ThrowExceptionForHR(Vol().GetMute(out mute)); return mute; }
    set { Marshal.ThrowExceptionForHR(Vol().SetMute(value, System.Guid.Empty)); }
  }
}
'@
switch ($env:JARVIS_OP) {
  'set'    { [JarvisAudio]::Volume = [single]([int]$env:JARVIS_LEVEL / 100.0) }
  'mute'   { [JarvisAudio]::Mute = $true }
  'unmute' { [JarvisAudio]::Mute = $false }
}
@{ level = [int][math]::Round([JarvisAudio]::Volume * 100); muted = [JarvisAudio]::Mute } | ConvertTo-Json -Compress
"""

SPEAK_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$de = $s.GetInstalledVoices() |
  Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.TwoLetterISOLanguageName -eq 'de' } |
  Select-Object -First 1
if ($de) { $s.SelectVoice($de.VoiceInfo.Name) }
$s.Speak([Console]::In.ReadToEnd())
$s.Dispose()
"""

PLAY_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationCore
$p = New-Object System.Windows.Media.MediaPlayer
$p.Open([Uri]::new($env:JARVIS_AUDIO))
$n = 0
while (-not $p.NaturalDuration.HasTimeSpan -and $n -lt 100) { Start-Sleep -Milliseconds 50; $n++ }
$p.Play()
if ($p.NaturalDuration.HasTimeSpan) {
  Start-Sleep -Milliseconds ([int]$p.NaturalDuration.TimeSpan.TotalMilliseconds + 150)
}
else { Start-Sleep -Seconds 3 }
$p.Close()
"""


def _load(name: str) -> Any:
    if sys.platform != "win32":
        return None
    return ctypes.WinDLL(name, use_last_error=True)  # type: ignore[attr-defined]


class WindowsBackend(DesktopBackend):
    platform = "windows"

    def __init__(self, run: Runner = run_process, which: Callable[[str], str | None] | None = None) -> None:
        import shutil

        super().__init__(run, which or shutil.which)
        self.user32 = _load("user32")
        self.kernel32 = _load("kernel32")
        self.dwmapi = _load("dwmapi")
        self._start_apps: tuple[float, list[dict[str, str]]] = (0.0, [])
        if self.user32 is not None:
            self._prototypes()
            self._dpi_aware()

    # ------------------------------------------------------------------ setup
    def _prototypes(self) -> None:
        u, k = self.user32, self.kernel32
        vp = ctypes.c_void_p
        u.GetForegroundWindow.restype = vp
        u.GetWindowTextLengthW.argtypes = [vp]
        u.GetWindowTextW.argtypes = [vp, ctypes.c_wchar_p, ctypes.c_int]
        u.IsWindowVisible.argtypes = [vp]
        u.IsIconic.argtypes = [vp]
        u.GetWindowThreadProcessId.argtypes = [vp, ctypes.POINTER(DWORD)]
        u.GetWindowThreadProcessId.restype = DWORD
        u.GetWindowRect.argtypes = [vp, ctypes.POINTER(RECT)]
        u.SetForegroundWindow.argtypes = [vp]
        u.BringWindowToTop.argtypes = [vp]
        u.ShowWindow.argtypes = [vp, ctypes.c_int]
        u.PostMessageW.argtypes = [vp, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        u.GetWindow.argtypes = [vp, ctypes.c_uint]
        u.GetWindow.restype = vp
        u.GetWindowLongPtrW.argtypes = [vp, ctypes.c_int]
        u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
        u.SendInput.restype = ctypes.c_uint
        u.VkKeyScanW.argtypes = [ctypes.c_wchar]
        u.VkKeyScanW.restype = ctypes.c_short
        u.OpenClipboard.argtypes = [vp]
        u.GetClipboardData.argtypes = [ctypes.c_uint]
        u.GetClipboardData.restype = vp
        u.SetClipboardData.argtypes = [ctypes.c_uint, vp]
        u.SetClipboardData.restype = vp
        u.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]
        k.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        k.GlobalAlloc.restype = vp
        k.GlobalLock.argtypes = [vp]
        k.GlobalLock.restype = vp
        k.GlobalUnlock.argtypes = [vp]
        k.GlobalFree.argtypes = [vp]
        k.GlobalFree.restype = vp
        if self.dwmapi is not None:
            self.dwmapi.DwmGetWindowAttribute.argtypes = [vp, DWORD, vp, DWORD]

    def _dpi_aware(self) -> None:
        """Physical pixels everywhere (screenshots, window rects, SendInput) – otherwise scaled monitors drift."""
        try:
            self.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
            return
        except (AttributeError, OSError):
            pass
        try:
            ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            self.user32.SetProcessDPIAware()

    def _need(self) -> None:
        if self.user32 is None:
            raise Unavailable("Nur unter Windows verfügbar")

    def powershell(self) -> str | None:
        return self.have("powershell.exe", "powershell")  # Windows PowerShell 5.1: WinRT toasts need it

    def session(self) -> str:
        return os.environ.get("SESSIONNAME", "console").lower()

    def probe(self) -> dict[str, CapStatus]:
        if self.user32 is None:
            return {cap: ("unavailable", "Nur unter Windows verfügbar") for cap in _DESKTOP_CAPS}
        uipi = "Fenster von Programmen mit Administratorrechten nehmen keine Eingaben an (UAC/UIPI)"
        ps = self.powershell()
        no_ps = ("unavailable", "Windows PowerShell (powershell.exe) nicht gefunden")
        try:
            import mss  # noqa: F401

            shot: CapStatus = ("available", "")
        except ImportError:
            shot = ("unavailable", "Python-Paket mss fehlt (pip install mss)")
        return {
            "apps.list": ("available", ""),
            "apps.launch": ("available", "") if ps else ("degraded", "Nur laufende Programme und Programme im PATH"),
            "apps.quit": ("available", ""),
            "windows.list": ("available", ""),
            "windows.focus": ("available", ""),
            "input.keyboard": ("degraded", uipi),
            "input.mouse": ("degraded", uipi),
            "screen.capture": shot,
            "clipboard": ("available", ""),
            "files.reveal": ("available", "")
            if self.have("explorer.exe", "explorer")
            else ("unavailable", "Explorer fehlt"),
            "browser.open": ("available", ""),
            "notifications": ("available", "") if ps else no_ps,
            "audio.volume": ("available", "") if ps else no_ps,
            "audio.speak": ("available", "") if ps else no_ps,
            "audio.play": ("available", "") if ps else no_ps,
        }

    # ------------------------------------------------------------------ windows (sync helpers run in threads)
    def _title(self, hwnd: int) -> str:
        n = self.user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        self.user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def _pid(self, hwnd: int) -> int:
        pid = DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def _cloaked(self, hwnd: int) -> bool:
        if self.dwmapi is None:
            return False
        val = DWORD()
        res = self.dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val))
        return res == 0 and val.value != 0

    def _rect(self, hwnd: int) -> RECT:
        r = RECT()
        self.user32.GetWindowRect(hwnd, ctypes.byref(r))
        return r

    def _windows(self) -> list[dict[str, Any]]:
        self._need()
        out: list[dict[str, Any]] = []
        names: dict[int, str] = {}
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t)  # type: ignore[attr-defined]
        fg = self.user32.GetForegroundWindow()

        def cb(hwnd: int, _: int) -> int:
            if not hwnd or not self.user32.IsWindowVisible(hwnd) or self.user32.GetWindow(hwnd, GW_OWNER):
                return 1
            if self.user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW or self._cloaked(hwnd):
                return 1
            title = self._title(hwnd)
            if not title:
                return 1
            pid = self._pid(hwnd)
            if pid not in names:
                try:
                    names[pid] = psutil.Process(pid).name()
                except psutil.Error:
                    names[pid] = ""
            r = self._rect(hwnd)
            out.append(
                {
                    "window_id": int(hwnd),
                    "title": title,
                    "pid": pid,
                    "process": names[pid],
                    "app": names[pid].rsplit(".", 1)[0],
                    "bounds": {"x": r.left, "y": r.top, "width": r.right - r.left, "height": r.bottom - r.top},
                    "minimized": bool(self.user32.IsIconic(hwnd)),
                    "active": int(hwnd) == (fg or 0),
                }
            )
            return 1

        self.user32.EnumWindows(WNDENUMPROC(cb), 0)
        return out

    async def list_windows(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._windows)

    async def list_apps(self) -> list[dict[str, Any]]:
        wins = await self.list_windows()
        apps: dict[int, dict[str, Any]] = {}
        for w in wins:
            a = apps.setdefault(
                w["pid"], {"name": w["app"], "process": w["process"], "pid": w["pid"], "active": False, "windows": []}
            )
            a["windows"].append(w["title"])
            a["active"] = a["active"] or w["active"]
        return sorted(apps.values(), key=lambda a: str(a["name"]).lower())

    def _activate(self, hwnd: int) -> bool:
        if self.user32.IsIconic(hwnd):
            self.user32.ShowWindow(hwnd, SW_RESTORE)
        # Windows only lets the process that received the last input change the foreground window; a synthetic
        # Alt press makes this process that one.
        self._send([vk_inputs(VK_MENU, False), vk_inputs(VK_MENU, True)])
        ok = bool(self.user32.SetForegroundWindow(hwnd))
        self.user32.BringWindowToTop(hwnd)
        return ok

    def _match_windows(self, wins: list[dict[str, Any]], title: str | None, app: str | None) -> dict[str, Any] | None:
        if app:
            cands = [(w["app"], w) for w in wins] + [(w["title"], w) for w in wins]
            return fuzzy_pick(app, cands)
        if title:
            return fuzzy_pick(title, [(w["title"], w) for w in wins])
        return None

    async def focus_window(self, window_id: int | None, title: str | None, app: str | None) -> dict[str, Any]:
        wins = await self.list_windows()
        target = next((w for w in wins if w["window_id"] == window_id), None) if window_id else None
        target = target or self._match_windows(wins, title, app)
        if target is None:
            raise BackendError("Kein passendes Fenster gefunden")
        await asyncio.to_thread(self._activate, target["window_id"])
        return {"window_id": target["window_id"], "title": target["title"], "app": target["app"]}

    async def frontmost(self) -> dict[str, Any]:
        self._need()
        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            return {"app": "", "title": ""}
        pid = self._pid(hwnd)
        try:
            proc = psutil.Process(pid).name()
        except psutil.Error:
            proc = ""
        return {
            "app": proc.rsplit(".", 1)[0],
            "process": proc,
            "title": self._title(hwnd),
            "pid": pid,
            "window_id": int(hwnd),
        }

    # ------------------------------------------------------------------ programs
    async def start_apps(self) -> list[dict[str, str]]:
        at, cached = self._start_apps
        if cached and time.monotonic() - at < 300:
            return cached
        ps = self.powershell()
        if ps is None:
            return []
        res = await self.run(
            [ps, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded(START_APPS_SCRIPT)], timeout=30
        )
        if res.returncode != 0:
            return []
        try:
            data = json.loads(res.text or "[]")
        except ValueError:
            return []
        apps = [data] if isinstance(data, dict) else list(data)
        clean = [{"name": str(a.get("Name", "")), "app_id": str(a.get("AppID", ""))} for a in apps if a.get("AppID")]
        self._start_apps = (time.monotonic(), clean)
        return clean

    async def open_app(self, name: str) -> dict[str, Any]:
        wins = await self.list_windows()
        running = self._match_windows(wins, None, name)
        if running is not None:
            await asyncio.to_thread(self._activate, running["window_id"])
            return {"activated": running["app"], "title": running["title"]}
        start = await self.start_apps()
        hit = fuzzy_pick(name, [(a["name"], a) for a in start])
        explorer = self.have("explorer.exe", "explorer")
        if hit is not None and explorer:
            subprocess.Popen([explorer, f"shell:AppsFolder\\{hit['app_id']}"], close_fds=True)
            return {"launched": hit["name"], "app_id": hit["app_id"]}
        exe = self.have(name, name + ".exe")
        if exe:
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            subprocess.Popen([exe], close_fds=True, creationflags=flags)
            return {"launched": exe}
        raise BackendError(f"Programm „{name}“ nicht gefunden (weder im Startmenü noch im PATH)")

    async def quit_app(self, name: str | None, pid: int | None) -> dict[str, Any]:
        wins = await self.list_windows()
        if pid:
            targets = [w for w in wins if w["pid"] == pid]
        elif name:
            first = self._match_windows(wins, None, name)
            targets = [w for w in wins if first is not None and w["pid"] == first["pid"]]
        else:
            raise BackendError("name oder pid angeben")
        if not targets:
            raise BackendError("Kein passendes Programm mit Fenster gefunden")
        for w in targets:
            self.user32.PostMessageW(w["window_id"], WM_CLOSE, 0, 0)
        return {"closed_windows": len(targets), "app": targets[0]["app"], "pid": targets[0]["pid"]}

    # ------------------------------------------------------------------ input
    def _send(self, inputs: list[INPUT]) -> None:
        if not inputs:
            return
        arr = (INPUT * len(inputs))(*inputs)
        sent = self.user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
        if sent != len(inputs):
            raise BackendError(
                "Eingabe wurde blockiert (Zielfenster mit Administratorrechten oder gesperrter Bildschirm)"
            )

    async def type_text(self, text: str, should_stop: Callable[[], bool]) -> None:
        self._need()
        events = text_inputs(text)
        for i in range(0, len(events), 2):
            if should_stop():
                raise BackendError("Not-Aus – Tippen abgebrochen")
            await asyncio.to_thread(self._send, events[i : i + 2])
            await asyncio.sleep(0.004)

    def _vk_for(self, key: str) -> tuple[int, list[int]]:
        k = canonical_key(key)
        if k in VK:
            return VK[k], []
        if len(k) == 1:
            scan = int(self.user32.VkKeyScanW(k))
            if scan == -1:
                raise BackendError(f"Taste „{key}“ gibt es im aktuellen Tastaturlayout nicht")
            vk, state = scan & 0xFF, (scan >> 8) & 0xFF
            implied = [
                m
                for bit, m in ((1, VK_MODIFIERS["shift"]), (2, VK_MODIFIERS["ctrl"]), (4, VK_MODIFIERS["alt"]))
                if state & bit
            ]
            return vk, implied
        raise BackendError(f"Unbekannte Taste „{key}“")

    async def key(self, key: str, modifiers: list[str]) -> None:
        self._need()
        vk, implied = self._vk_for(key)
        mods = [VK_MODIFIERS[m] for m in canonical_modifiers(modifiers)]
        mods += [m for m in implied if m not in mods]
        seq = [vk_inputs(m, False) for m in mods] + [vk_inputs(vk, False), vk_inputs(vk, True)]
        seq += [vk_inputs(m, True) for m in reversed(mods)]
        await asyncio.to_thread(self._send, seq)

    def _virtual(self) -> tuple[int, int, int, int]:
        m = self.user32.GetSystemMetrics
        return m(SM_XVIRTUALSCREEN), m(SM_YVIRTUALSCREEN), m(SM_CXVIRTUALSCREEN), m(SM_CYVIRTUALSCREEN)

    def _move_input(self, x: int, y: int) -> INPUT:
        nx, ny = normalize(x, y, self._virtual())
        return mouse_input(nx, ny, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)

    async def move(self, x: int, y: int) -> None:
        self._need()
        await asyncio.to_thread(self._send, [self._move_input(x, y)])

    async def click(self, x: int, y: int, button: str, count: int) -> None:
        self._need()
        down, up = BUTTONS.get(button, BUTTONS["left"])
        await asyncio.to_thread(self._send, [self._move_input(x, y)])
        for _ in range(max(1, count)):
            await asyncio.to_thread(self._send, [mouse_input(flags=down), mouse_input(flags=up)])
            await asyncio.sleep(0.05)

    async def scroll(self, dx: int, dy: int) -> None:
        self._need()
        events = []
        if dy:
            events.append(mouse_input(data=-dy * WHEEL_DELTA, flags=MOUSEEVENTF_WHEEL))
        if dx:
            events.append(mouse_input(data=dx * WHEEL_DELTA, flags=MOUSEEVENTF_HWHEEL))
        await asyncio.to_thread(self._send, events)

    # ------------------------------------------------------------------ screen
    async def screenshot(self, window_id: int | None, display: int | None) -> Capture:
        self._need()
        try:
            import mss
            import mss.tools
        except ImportError as e:
            raise Unavailable("Python-Paket mss fehlt (pip install mss)") from e

        def grab() -> Capture:
            with mss.mss() as sct:
                if window_id:
                    r = self._rect(window_id)
                    region = {"left": r.left, "top": r.top, "width": r.right - r.left, "height": r.bottom - r.top}
                else:
                    idx = display if display is not None and 0 <= display < len(sct.monitors) else 0
                    region = dict(sct.monitors[idx])
                if region["width"] <= 0 or region["height"] <= 0:
                    raise BackendError("Fenster ist minimiert oder unsichtbar")
                img = sct.grab(region)
                png = mss.tools.to_png(img.rgb, img.size)
                return Capture(png or b"", img.size[0], img.size[1], int(region["left"]), int(region["top"]))

        return await asyncio.to_thread(grab)

    # ------------------------------------------------------------------ clipboard
    def _open_clipboard(self) -> None:
        for _ in range(10):  # another program may hold it for a moment
            if self.user32.OpenClipboard(None):
                return
            time.sleep(0.05)
        raise BackendError("Zwischenablage ist belegt")

    def _clip_get(self) -> str:
        self._need()
        self._open_clipboard()
        try:
            if not self.user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return ""
            handle = self.user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            ptr = self.kernel32.GlobalLock(handle)
            try:
                return ctypes.wstring_at(ptr) if ptr else ""
            finally:
                self.kernel32.GlobalUnlock(handle)
        finally:
            self.user32.CloseClipboard()

    def _clip_set(self, text: str) -> None:
        self._need()
        data = (text + "\0").encode("utf-16-le")
        handle = self.kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            raise BackendError("Speicher für die Zwischenablage nicht verfügbar")
        ptr = self.kernel32.GlobalLock(handle)
        ctypes.memmove(ptr, data, len(data))
        self.kernel32.GlobalUnlock(handle)
        self._open_clipboard()
        try:
            self.user32.EmptyClipboard()
            if not self.user32.SetClipboardData(CF_UNICODETEXT, handle):
                self.kernel32.GlobalFree(handle)
                raise BackendError("Zwischenablage konnte nicht gesetzt werden")
        finally:
            self.user32.CloseClipboard()

    async def clipboard_get(self) -> str:
        return await asyncio.to_thread(self._clip_get)

    async def clipboard_set(self, text: str) -> None:
        await asyncio.to_thread(self._clip_set, text)

    # ------------------------------------------------------------------ shell integration
    async def reveal(self, path: str) -> None:
        explorer = self.have("explorer.exe", "explorer")
        if explorer is None:
            raise Unavailable("Explorer nicht gefunden")
        subprocess.Popen([explorer, f"/select,{path}"], close_fds=True)

    async def open_url(self, url: str) -> None:
        await asyncio.to_thread(os.startfile, url)  # type: ignore[attr-defined]

    async def _ps(self, script: str, env: dict[str, str], *, input: bytes | None = None, timeout: float = 30) -> str:
        ps = self.powershell()
        if ps is None:
            raise Unavailable("Windows PowerShell (powershell.exe) nicht gefunden")
        res = await self.run(
            [ps, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded(PS_UTF8 + script)],
            input=input,
            timeout=timeout,
            env={**os.environ, **env},
        )
        if res.returncode != 0:
            raise BackendError("PowerShell: " + res.stderr.decode("utf-8", errors="replace").strip()[:200])
        return res.text.strip()

    async def notify(self, title: str, message: str) -> None:
        await self._ps(TOAST_SCRIPT, {"JARVIS_TITLE": title, "JARVIS_MESSAGE": message})

    async def volume(self, op: str, level: int | None) -> dict[str, Any]:
        out = await self._ps(VOLUME_SCRIPT, {"JARVIS_OP": op, "JARVIS_LEVEL": str(level or 0)})
        try:
            data = json.loads(out.splitlines()[-1])
        except (ValueError, IndexError) as e:
            raise BackendError("Lautstärke nicht lesbar") from e
        return {"level": int(data.get("level", 0)), "muted": bool(data.get("muted"))}

    async def speak(self, text: str) -> None:
        await self._ps(SPEAK_SCRIPT, {}, input=text.encode("utf-8"), timeout=120)

    async def play(self, path: str) -> None:
        await self._ps(PLAY_SCRIPT, {"JARVIS_AUDIO": path}, timeout=600)


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
    "audio.volume",
    "audio.speak",
    "audio.play",
)
