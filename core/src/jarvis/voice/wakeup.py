"""Wake-up routine on the Mac: "Hey Jarvis, wake up" / "Hey Jarvis, wach auf" plays Sir's MP3 and opens his programs.

Settings (enabled, MP3 path, volume, programs) are chosen in the desktop app and stored in ``wakeup.json`` in the
data dir, next to the other runtime settings. Until that file exists, ``voice.wakeup`` in config/jarvis.yaml
provides the start values (default: on, no MP3, no programs).

The phrase itself is recognised by the voice daemon (``voice.commands.is_wake_up``); it plays the sound and asks the
core (``POST /api/voice/wakeup``) to open the programs. Nothing fails silently: a missing or invalid MP3 and every
program that cannot be found end up in the result (and in a ``voice.wakeup`` event for the desktop).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from jarvis.config import WakeupCfg

logger = logging.getLogger(__name__)

MAX_APPS = 30
AFPLAY = "/usr/bin/afplay"
OPEN = "/usr/bin/open"
_BUNDLE_ID = re.compile(r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+){2,}$")


def supported() -> bool:
    """The routine uses afplay and `open` – macOS only. Elsewhere it is hidden and does nothing."""
    return sys.platform == "darwin"


# ------------------------------------------------------------------ settings
@dataclass(slots=True)
class WakeupSettings:
    enabled: bool = True
    sound: str = ""  # absolute path to an MP3; "" = built-in power-up sound
    volume: float = 1.0  # 0.0 … 1.0
    apps: list[str] = field(default_factory=list)  # app names or .app paths, opened in this order

    @classmethod
    def from_cfg(cls, cfg: WakeupCfg) -> WakeupSettings:
        return cls(enabled=cfg.enabled, sound=cfg.sound, volume=cfg.volume, apps=list(cfg.apps))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _validated(base: WakeupSettings, patch: dict[str, Any], check_file: bool) -> WakeupSettings:
    """``base`` with the known keys of ``patch`` applied; raises ValueError with a readable German message."""
    out = WakeupSettings(**base.to_dict())
    if "enabled" in patch:
        if not isinstance(patch["enabled"], bool):
            raise ValueError("enabled muss true oder false sein")
        out.enabled = patch["enabled"]
    if "volume" in patch:
        vol = patch["volume"]
        if isinstance(vol, bool) or not isinstance(vol, int | float) or not 0.0 <= float(vol) <= 1.0:
            raise ValueError("Lautstärke muss zwischen 0 und 1 liegen")
        out.volume = float(vol)
    if "sound" in patch:
        sound = patch["sound"]
        if sound is None:
            sound = ""
        if not isinstance(sound, str):
            raise ValueError("sound muss ein Dateipfad sein")
        sound = sound.strip()
        if sound and check_file:
            error = check_sound(sound)
            if error:
                raise ValueError(error)
        out.sound = str(Path(sound).expanduser()) if sound else ""
    if "apps" in patch:
        apps = patch["apps"]
        if not isinstance(apps, list) or not all(isinstance(a, str) for a in apps):
            raise ValueError("apps muss eine Liste von Programmnamen oder .app-Pfaden sein")
        cleaned: list[str] = []
        for a in apps:
            a = a.strip()
            if a and a not in cleaned:
                cleaned.append(a)
        if len(cleaned) > MAX_APPS:
            raise ValueError(f"Höchstens {MAX_APPS} Programme")
        out.apps = cleaned
    return out


class WakeupStore:
    """``wakeup.json`` – loaded once, written on every change (like approval-mode.json, alert-policy.json)."""

    def __init__(self, path: Path, defaults: WakeupCfg | None = None) -> None:
        self.path = path
        self.defaults = WakeupSettings.from_cfg(defaults or WakeupCfg())
        self.settings = self._load()

    def _load(self) -> WakeupSettings:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return WakeupSettings(**self.defaults.to_dict())
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Ignoring unreadable %s: %s", self.path.name, e)
            return WakeupSettings(**self.defaults.to_dict())
        if not isinstance(data, dict):
            logger.warning("Ignoring invalid %s: not an object", self.path.name)
            return WakeupSettings(**self.defaults.to_dict())
        try:
            # a sound file that disappeared later is reported when the routine runs, not hidden at load time
            return _validated(self.defaults, data, check_file=False)
        except ValueError as e:
            logger.warning("Ignoring invalid %s: %s", self.path.name, e)
            return WakeupSettings(**self.defaults.to_dict())

    def update(self, patch: dict[str, Any]) -> WakeupSettings:
        """Validates (including the MP3 when a new one is set) and persists. Raises ValueError."""
        new_sound = patch.get("sound")
        check = isinstance(new_sound, str) and str(Path(new_sound.strip()).expanduser()) != self.settings.sound
        self.settings = _validated(self.settings, patch, check_file=check)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.settings.to_dict(), indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
        return self.settings

    def to_dict(self) -> dict[str, Any]:
        s = self.settings
        return {**s.to_dict(), "sound_error": check_sound(s.sound) if s.sound else None, "supported": supported()}


# ------------------------------------------------------------------ sound
def check_sound(path: str) -> str | None:
    """None if ``path`` is a readable MP3, otherwise a message for Sir (German, like the rest of the UI)."""
    if not path.strip():
        return "Keine MP3-Datei festgelegt."
    p = Path(path.strip()).expanduser()
    if not p.exists():
        return f"MP3-Datei nicht gefunden: {p}"
    if not p.is_file():
        return f"Keine Datei: {p}"
    if p.suffix.lower() != ".mp3":
        return f"Keine MP3-Datei (Endung {p.suffix or 'fehlt'}): {p.name}"
    try:
        with p.open("rb") as f:
            head = f.read(4)
    except OSError as e:
        return f"MP3-Datei nicht lesbar: {p.name} ({e.strerror or e})"
    if not head:
        return f"MP3-Datei ist leer: {p.name}"
    # an ID3v2 tag or an MPEG audio frame (11 sync bits) at the start
    if not (head.startswith(b"ID3") or (len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)):
        return f"Keine gültige MP3-Datei: {p.name}"
    return None


Spawn = Callable[..., Awaitable[Any]]


async def play_sound(
    path: str, volume: float, max_wait_s: float, spawn: Spawn = asyncio.create_subprocess_exec
) -> str | None:
    """Plays the MP3 with afplay. Waits until it has finished or ``max_wait_s`` passed (it keeps playing then).

    Returns None on success, otherwise the error message. afplay rejects broken files within milliseconds, so a
    short ``max_wait_s`` still reports them."""
    error = check_sound(path)
    if error:
        return error
    p = Path(path.strip()).expanduser()
    try:
        proc = await spawn(
            AFPLAY,
            "-v",
            f"{max(0.0, min(1.0, volume)):.2f}",
            str(p),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        return f"Wiedergabe nicht möglich: {e}"
    try:
        code = await asyncio.wait_for(proc.wait(), max_wait_s)
    except TimeoutError:
        _reap(proc)
        return None
    if code != 0:
        detail = ""
        if proc.stderr is not None:
            with contextlib.suppress(Exception):
                detail = (await proc.stderr.read()).decode(errors="replace").strip()
        return f"MP3-Datei konnte nicht abgespielt werden: {p.name}" + (f" ({detail[:160]})" if detail else "")
    return None


_bg: set[asyncio.Task[Any]] = set()


def _reap(proc: Any) -> None:
    """Keep waiting for a sound that is still playing so it does not become a zombie."""

    async def wait() -> None:
        with contextlib.suppress(Exception):
            await proc.wait()

    t = asyncio.get_running_loop().create_task(wait())
    _bg.add(t)
    t.add_done_callback(_bg.discard)


# ------------------------------------------------------------------ programs
def app_command(entry: str) -> tuple[list[str] | None, str | None]:
    """`open` command for one entry, or (None, reason) if it obviously cannot work.

    "Safari" → ``open -a Safari``; "/Applications/Safari.app" → ``open -a <path>`` (checked first);
    "com.apple.Safari" → ``open -b``; "https://…" → ``open <url>``."""
    e = entry.strip()
    if not e:
        return None, "leerer Eintrag"
    if e.startswith(("http://", "https://")):
        return [OPEN, e], None
    if "/" in e or e.startswith("~"):
        p = Path(e).expanduser()
        if not p.is_absolute():
            return None, "Pfad muss absolut sein"
        if not p.exists():
            return None, "Programm nicht gefunden"
        if p.suffix.lower() != ".app":
            return None, "kein Programm (.app)"
        return [OPEN, "-a", str(p)], None
    if _BUNDLE_ID.match(e) and not e.lower().endswith(".app"):
        return [OPEN, "-b", e], None
    return [OPEN, "-a", e.removesuffix(".app")], None


Runner = Callable[[list[str]], Awaitable[tuple[int, str]]]


async def run_command(cmd: list[str], timeout_s: float = 20.0) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        return 124, "Zeitüberschreitung"
    return proc.returncode or 0, err.decode(errors="replace").strip()


def _open_failure(code: int, stderr: str) -> str:
    low = stderr.lower()
    if "unable to find application" in low or "can't find app" in low or "does not exist" in low:
        return "Programm nicht gefunden"
    if code == 124:
        return "Zeitüberschreitung beim Starten"
    return f"Start fehlgeschlagen ({stderr[:160]})" if stderr else f"Start fehlgeschlagen (Code {code})"


async def open_apps(
    apps: list[str], runner: Runner = run_command, delay_s: float = 0.0
) -> tuple[list[str], list[dict[str, str]]]:
    """Opens every program in order. Unknown ones are skipped and reported; the rest still start."""
    opened: list[str] = []
    failed: list[dict[str, str]] = []
    for i, entry in enumerate(apps):
        cmd, reason = app_command(entry)
        if cmd is None:
            failed.append({"app": entry, "reason": reason or "ungültig"})
            continue
        code, err = await runner(cmd)
        if code != 0:
            failed.append({"app": entry, "reason": _open_failure(code, err)})
            continue
        opened.append(entry)
        if delay_s and i < len(apps) - 1:
            await asyncio.sleep(delay_s)
    return opened, failed


def summary(result: dict[str, Any]) -> str:
    """One spoken sentence about what went wrong ("" if nothing did)."""
    parts: list[str] = []
    if result.get("sound_error"):
        parts.append("Die Weckmusik konnte ich nicht abspielen")
    failed = [str(f.get("app", "")) for f in result.get("failed") or []]
    names = [Path(n).stem if "/" in n else n for n in failed]
    if len(names) == 1:
        parts.append(f"{names[0]} konnte ich nicht öffnen")
    elif names:
        parts.append(f"{len(names)} Programme konnte ich nicht öffnen: {', '.join(names[:4])}")
    if not parts:
        return ""
    return "Sir, " + "; ".join(parts) + ". Details stehen in der App."
