"""Jarvis startet sich selbst neu, nachdem ein Coding-Job Jarvis' eigenen Code ausgeliefert hat.

Ablauf (Beschreibung für Sir: README, Abschnitt „Coding-Jobs an Jarvis selbst“):

1. ``plan``: Nach der Auslieferung prüft der Runner, ob der Job das Repository betrifft, aus dem dieser Core läuft
   (``REPO_ROOT``), ob er Dateien geändert hat, die Core oder Desktop-App laden (``RESTART_PATHS``), und ob das
   lokale Projekt auf den gemergten Stand gebracht wurde (sonst lüde ein Neustart den alten Code). Nicht committete
   Änderungen an anderen Dateien im lokalen Projekt hindern das nicht (``CodingRunner._ship_local_sync``).
2. Erst nach ``_finish`` (Job-Ergebnis geschrieben, Meldung verschickt) wartet der Runner, bis kein Coding-Job mehr
   läuft oder wartet, und startet dann diesen Helfer als eigenständigen Prozess (``launch``, eigene Session: er
   überlebt das Ende des Cores).
3. Der Helfer (``python -m jarvis.coding.self_restart``, ``Restarter.restart``): Läuft die Desktop-App, wird sie
   zuerst per SIGTERM beendet (kein AppleScript: kein Automation-Dialog, und ihr reguläres Beenden würde über
   „Dienste beim Beenden stoppen“ alle Dienste stoppen). Dann wird der Core neu gestartet (launchd ``kickstart -k`` bzw.
   Stop + ``scripts/start.sh --no-voice --no-app``), per ``/api/health`` geprüft und die Desktop-App wieder geöffnet –
   nur, wenn sie vorher lief. Das Ergebnis steht in der Statusdatei (``STATUS_FILE`` im Datenordner).
4. Der neue Core trägt das Ergebnis beim Start in die auslösenden Jobs ein (``result.ship.self_restart``, Fehler
   zusätzlich unter ``result.warnings``). Die Auslieferung selbst wird dabei nie verändert.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import signal
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis.config import REPO_ROOT

logger = logging.getLogger(__name__)

# Changes below these paths reach the running Jarvis only through a restart: core code (including the MCP tools), its
# dependencies and migrations, the configuration (MCP servers, permissions …) and the desktop app. Docs, tests, the
# mobile app, the device agent, the bridge or the installer do not need one.
RESTART_PATHS = (
    "core/src/",
    "core/pyproject.toml",
    "core/uv.lock",
    "core/migrations/",
    "core/alembic.ini",
    "config/",
    "desktop/",
)
STATUS_FILE = "self-restart.json"
LOG_FILE = "self-restart.log"
LAUNCHD_LABEL = "com.jarvis.core"
DESKTOP_EXE = "/Contents/MacOS/jarvis-desktop"
CORE_HEALTH_TIMEOUT_S = 120.0
# The helper waits this long before acting: the job's "fertig" message is still being delivered by the core.
START_DELAY_S = 5.0


class RestartError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------- decision (runs inside the core)
def is_jarvis_repo(repo: Path, root: Path | None = None) -> bool:
    """The job's project is the repository this core runs from – Jarvis itself."""
    try:
        return repo.resolve() == (root or REPO_ROOT).resolve()
    except OSError:
        return False


def needs_restart(files: Sequence[str] | None) -> bool:
    """Do the changed files reach the running Jarvis only through a restart? Unknown (None) counts as yes."""
    if files is None:
        return True
    return any(f.startswith(RESTART_PATHS) for f in files)


def plan(ship: dict[str, Any], files: Sequence[str] | None, *, enabled: bool = True) -> bool:
    """After the delivery of a job on Jarvis itself: is a restart due? Records the decision as
    ``ship["self_restart"]`` (only when something was merged) and returns True when one is due."""
    if not ship.get("merged"):
        return False
    if not enabled:
        reason = "coding.self_restart ist ausgeschaltet"
    elif not needs_restart(files):
        reason = "nur Dateien geändert, die Core und Desktop-App nicht laden (z. B. Doku, Tests)"
    elif (ship.get("local_sync") or {}).get("state") != "synced":
        reason = "das lokale Projekt wurde nicht aktualisiert, ein Neustart lüde den alten Stand"
    else:
        ship["self_restart"] = {"state": "pending", "message": "Neustart geplant, sobald der Job abgeschlossen ist."}
        ship["message"] += " Jarvis wird nach Abschluss des Jobs neu gestartet (Core und, falls offen, Desktop-App)."
        return True
    ship["self_restart"] = {"state": "skipped", "message": f"Kein Neustart: {reason}."}
    ship["message"] += f" Kein Neustart von Jarvis: {reason}."
    return False


def read_status(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_status(path: Path, data: dict[str, Any]) -> None:
    """Atomic: the core and the helper read the file while the other one writes it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def launch(status: Path, *, log: Path, core_pid: int, health_url: str, root: Path, run_dir: Path) -> int:
    """Starts the helper as an independent process: its own session, so it survives the end of this core and is
    not hit by signals to the core's process group (``stop.sh``, launchd). Returns its pid."""
    log.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        sys.executable,
        "-m",
        "jarvis.coding.self_restart",
        "--status", str(status),
        "--core-pid", str(core_pid),
        "--health-url", health_url,
        "--root", str(root),
        "--run-dir", str(run_dir),
    ]  # fmt: skip
    with log.open("a", encoding="utf-8") as out:
        proc = subprocess.Popen(
            argv,
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    return proc.pid


def summary(outcome: dict[str, Any]) -> str:
    """One line for Sir: what happened to the core and the desktop app."""
    core, desktop = outcome.get("core") or {}, outcome.get("desktop") or {}
    if core.get("state") == "ok":
        parts = [f"Core neu gestartet und antwortet wieder ({core.get('method')})"]
    else:
        parts = [f"Core-Neustart fehlgeschlagen: {core.get('error') or 'unbekannter Fehler'}"]
    state = desktop.get("state")
    if state == "not_running":
        parts.append("Desktop-App lief nicht, nicht angefasst")
    elif state == "restarted":
        parts.append(f"Desktop-App beendet ({desktop.get('quit')}) und wieder geöffnet")
    else:
        parts.append(f"Desktop-App: {desktop.get('error') or 'Neustart fehlgeschlagen'}")
    return "; ".join(parts) + "."


# ---------------------------------------------------------------- helper (runs outside the core)
Run = Callable[[list[str], float], tuple[int | None, str]]


def _run(argv: list[str], timeout: float) -> tuple[int | None, str]:
    """(exit code, output tail); exit code None = timeout."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as e:
        return 127, str(e)
    except subprocess.TimeoutExpired:
        return None, f"Timeout nach {timeout:g} s"
    return proc.returncode, (proc.stdout + proc.stderr)[-600:]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _err(e: BaseException) -> str:
    return (str(e) or type(e).__name__)[:300]


class Restarter:
    """Restarts the core and – only if it is running – the desktop app, in this order: quit the app, restart the
    core, health check, reopen the app. Every step reports into the returned outcome; nothing raises."""

    def __init__(
        self,
        *,
        root: Path,
        core_pid: int,
        health_url: str,
        run_dir: Path,
        platform: str = sys.platform,
        run: Run = _run,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.root = root
        self.core_pid = core_pid
        self.health_url = health_url
        self.run_dir = run_dir
        self.platform = platform
        self.run = run
        self.sleep = sleep
        self.clock = clock

    def restart(self) -> dict[str, Any]:
        out: dict[str, Any] = {"core": {"state": "pending"}, "desktop": {"state": "not_running"}}
        try:
            running = self.desktop_pids()
        except Exception as e:
            running = {}
            out["desktop"] = {"state": "failed", "error": f"Prüfen, ob die Desktop-App läuft, misslang: {_err(e)}"}
        bundle = next(iter(running.values()), None)
        quit_done = False
        if running:
            try:
                out["desktop"] = {"state": "quit", "bundle": bundle, "quit": self.quit_desktop(list(running))}
                quit_done = True
            except Exception as e:
                out["desktop"] = {"state": "failed", "bundle": bundle, "error": f"Beenden fehlgeschlagen: {_err(e)}"}
        try:
            out["core"] = {"state": "restarted", "method": self.restart_core()}
            out["core"]["health"] = self.wait_healthy()
            out["core"]["state"] = "ok"
        except Exception as e:
            out["core"] = {**out["core"], "state": "failed", "error": _err(e)}
        if quit_done and bundle:  # whatever happened to the core: an app Jarvis quit is always opened again
            try:
                self.open_desktop(bundle)
                out["desktop"]["state"] = "restarted"
            except Exception as e:
                out["desktop"].update(state="failed", error=f"Wieder öffnen fehlgeschlagen: {_err(e)}")
        ok = out["core"]["state"] == "ok" and out["desktop"]["state"] in ("not_running", "restarted")
        out["state"] = "done" if ok else "failed"
        out["message"] = summary(out)
        return out

    # ------------------------------------------------------------ desktop app
    def desktop_pids(self) -> dict[int, str]:
        """Running desktop app processes: pid → app bundle path. Only macOS (other systems: never touched)."""
        if self.platform != "darwin":
            return {}
        code, text = self.run(["ps", "-axo", "pid=,command="], 10)
        if code != 0:
            raise RestartError(f"ps fehlgeschlagen (Exit {code}): {text.strip()}")
        found: dict[int, str] = {}
        for line in text.splitlines():
            pid, _, command = line.strip().partition(" ")
            bundle = command.strip().split(DESKTOP_EXE, 1)[0]
            # the app binary itself, not a command that only names it (``grep …/jarvis-desktop``)
            if DESKTOP_EXE in command and pid.isdigit() and bundle.startswith("/") and bundle.endswith(".app"):
                found[int(pid)] = bundle
        return found

    def quit_desktop(self, pids: list[int]) -> str:
        """SIGTERM, SIGKILL only if the app is still there afterwards. No AppleScript ``quit``: an Apple Event from
        this helper may raise a macOS Automation prompt and block, and the app's regular quit would also run
        ``stop.sh`` (setting „Dienste beim Beenden stoppen“). SIGTERM ends the app without that – the services keep
        running, the core is restarted next and the reopened app finds everything running."""
        for sig in (signal.SIGTERM, signal.SIGKILL):
            for pid in pids:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, sig)
            if self._wait_gone(pids, 10):
                return f"per {sig.name}"
        raise RestartError("die Desktop-App lässt sich nicht beenden und läuft weiter")

    def open_desktop(self, bundle: str) -> None:
        code, text = self.run(["open", bundle], 30)
        if code != 0:
            raise RestartError(f"open {bundle} fehlgeschlagen (Exit {code}): {text.strip()}")
        deadline = self.clock() + 30
        while self.clock() < deadline:
            if self.desktop_pids():
                return
            self.sleep(1)
        raise RestartError("die Desktop-App ist nach dem Öffnen nicht gestartet")

    # ------------------------------------------------------------ core
    def restart_core(self) -> str:
        """Restarts the core the way it is run: launchd job (``scripts/launchd/install.sh``) or pid file of the start
        script (``scripts/start.sh``, also used by the desktop app). Returns how."""
        if self.platform == "darwin":
            target = f"gui/{os.getuid()}/{LAUNCHD_LABEL}"
            code, _ = self.run(["launchctl", "print", target], 10)
            if code == 0:
                code, text = self.run(["launchctl", "kickstart", "-k", target], 60)
                if code != 0:
                    raise RestartError(f"launchctl kickstart fehlgeschlagen (Exit {code}): {text.strip()}")
                self._wait_gone([self.core_pid], 30)
                return f"launchd {LAUNCHD_LABEL}"
            script = self.root / "scripts" / "start.sh"
        elif self.platform.startswith("linux"):
            script = self.root / "scripts" / "linux" / "start.sh"
        else:
            raise RestartError(f"automatischer Neustart auf {self.platform} nicht unterstützt")
        if not script.is_file():
            raise RestartError(f"Startskript {script} fehlt")
        self.stop_core()
        code, text = self.run(["/bin/bash", str(script), "--no-voice", "--no-app"], 180)
        if code != 0:
            raise RestartError(f"{script.name} fehlgeschlagen (Exit {code}): {text.strip()[-300:]}")
        return f"{script.relative_to(self.root)} --no-voice --no-app"

    def stop_core(self) -> None:
        """Ends the old core (its python process and the ``uv run`` wrapper of the pid file). Only processes whose
        command line is the core are signalled – a stale pid file never hits a foreign process."""
        pidfile = self.run_dir / "core.pid"
        pids = {self.core_pid}
        with contextlib.suppress(OSError, ValueError):
            pids.add(int(pidfile.read_text().strip()))
        cores = [pid for pid in pids if alive(pid) and self._is_core(pid)]
        for pid in cores:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)
        if not self._wait_gone(cores, 30):
            for pid in cores:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
            self._wait_gone(cores, 5)
        with contextlib.suppress(OSError):
            pidfile.unlink()  # the start script only starts a core when no live pid is recorded

    def _is_core(self, pid: int) -> bool:
        code, text = self.run(["ps", "-o", "command=", "-p", str(pid)], 10)
        return code == 0 and "jarvisctl" in text and "serve" in text

    def wait_healthy(self, timeout: float = CORE_HEALTH_TIMEOUT_S) -> dict[str, Any]:
        """Polls ``/api/health`` until the new core answers; returns its answer."""
        deadline = self.clock() + timeout
        last = ""
        while self.clock() < deadline:
            try:
                with urllib.request.urlopen(self.health_url, timeout=5) as resp:  # noqa: S310 - fixed loopback URL
                    body = json.loads(resp.read() or b"{}")
                    return {"status": body.get("status"), "version": body.get("version")}
            except Exception as e:
                last = _err(e)
            self.sleep(2)
        raise RestartError(f"Core antwortet nicht innerhalb von {timeout:g} s ({self.health_url}: {last})")

    def _wait_gone(self, pids: Sequence[int], timeout: float) -> bool:
        deadline = self.clock() + timeout
        while any(alive(p) for p in pids):
            if self.clock() >= deadline:
                return False
            self.sleep(0.5)
        return True


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Jarvis nach einer Auslieferung an sich selbst neu starten")
    parser.add_argument("--status", required=True, type=Path)
    parser.add_argument("--core-pid", required=True, type=int)
    parser.add_argument("--health-url", required=True)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--delay", type=float, default=START_DELAY_S)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s self-restart %(message)s")
    status = read_status(args.status) or {}
    status.update(phase="running", helper_pid=os.getpid(), started_at=now())
    write_status(args.status, status)
    logger.info("Neustart für Job(s) %s", ", ".join(status.get("jobs") or []) or "?")
    time.sleep(max(0.0, args.delay))
    outcome = Restarter(
        root=args.root, core_pid=args.core_pid, health_url=args.health_url, run_dir=args.run_dir
    ).restart()
    logger.info("%s", outcome["message"])
    status.update(phase="finished", finished_at=now(), outcome=outcome)
    write_status(args.status, status)
    return 0 if outcome["state"] == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
