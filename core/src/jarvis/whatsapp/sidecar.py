"""The Core's side of the WhatsApp sidecar: start, supervise and talk to ``whatsapp-sidecar`` (Node, Baileys).

* **Transport:** JSON lines over the child's stdin/stdout – no port, no socket. Only this process holds the pipes,
  which makes the channel authenticated by construction (``protocol.ts`` documents the frames).
* **Secrets:** the session key goes to the sidecar in the ``init`` request through the pipe – never on the command
  line or in the environment. ``JARVIS_SECRET_*`` variables are removed from the child's environment.
* **Supervision:** the sidecar is restarted with exponential backoff (1 s … 60 s) whenever it exits; after the
  restart it is initialised again and resumes the stored session by itself (no new QR scan). Pending requests of a
  crashed sidecar fail with ``sidecar_exited``. When the Core goes away, the sidecar notices the closed stdin and
  exits on its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_LINE_BYTES = 32 * 1024 * 1024
PROTOCOL_VERSION = 1
METHODS = (
    "init",
    "connect",
    "disconnect",
    "logout",
    "status",
    "send",
    "markRead",
    "download",
    "fetchHistory",
    "groupMetadata",
    "checkNumber",
    "shutdown",
)
_ENV_KEEP = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "XDG_RUNTIME_DIR",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "SSL_CERT_FILE",
    "NODE_EXTRA_CA_CERTS",
)

EventHandler = Callable[[str, dict[str, Any]], Awaitable[None]]


class SidecarError(Exception):
    """A request failed in (or on the way to) the sidecar; ``message`` is German and safe to show."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def find_node(binary: str = "node") -> str | None:
    found = shutil.which(binary)
    if found:
        return found
    candidates = ["/opt/homebrew/bin/node", "/usr/local/bin/node", "/usr/bin/node"]
    if sys.platform == "win32":
        candidates = [
            str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs" / "node.exe"),
            str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "nodejs" / "node.exe"),
        ]
    return next((c for c in candidates if c and Path(c).is_file()), None)


def child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in _ENV_KEEP}
    env["NODE_ENV"] = "production"
    env["NODE_NO_WARNINGS"] = "1"
    return env


class SidecarClient:
    def __init__(
        self,
        *,
        command: Callable[[], Sequence[str] | None],
        init_params: Callable[[], dict[str, Any]],
        on_event: EventHandler,
        log_path: Path,
        request_timeout_s: float = 60.0,
        ready_timeout_s: float = 20.0,
        cwd: Path | None = None,
    ) -> None:
        self._command = command
        self._init_params = init_params
        self._on_event = on_event
        self.log_path = log_path
        self.request_timeout_s = request_timeout_s
        self.ready_timeout_s = ready_timeout_s
        self.cwd = cwd
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._seq = 0
        self._supervisor: asyncio.Task[None] | None = None
        self._stopping = False
        self._ready = asyncio.Event()
        self._hello = asyncio.Event()
        self._write_lock = asyncio.Lock()
        self.restarts = 0
        self.last_error: str | None = None
        self.info: dict[str, Any] = {}
        self.started_at: float | None = None

    # ------------------------------------------------------------------ lifecycle
    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None and self._ready.is_set()

    async def start(self) -> None:
        if self._supervisor is not None and not self._supervisor.done():
            return
        self._stopping = False
        self._supervisor = asyncio.create_task(self._supervise(), name="whatsapp-sidecar")

    async def stop(self) -> None:
        self._stopping = True
        proc = self._proc
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.request("shutdown", {}, timeout=5), 6)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), 5)
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), 5)
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
        if self._supervisor is not None:
            self._supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._supervisor
        self._supervisor = None
        self._fail_pending("sidecar_exited", "Der WhatsApp-Dienst wurde beendet.")

    async def wait_ready(self, timeout: float) -> bool:  # noqa: ASYNC109 - bounded wait, not a cancel scope
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
            return True
        except TimeoutError:
            return False

    async def _supervise(self) -> None:
        backoff = 1.0
        while not self._stopping:
            argv = self._command()
            if not argv:
                self.last_error = self.last_error or "WhatsApp-Dienst kann nicht gestartet werden"
                await self._emit("sidecar", {"state": "unavailable", "error": self.last_error})
                await asyncio.sleep(30)
                continue
            began = time.monotonic()
            code = await self._run_once(list(argv))
            if self._stopping:
                return
            self.restarts += 1
            self.last_error = f"WhatsApp-Dienst beendet (Code {code})"
            await self._emit("sidecar", {"state": "exited", "code": code, "restart_in_s": backoff})
            logger.warning("WhatsApp sidecar exited with code %s – restart in %.0f s", code, backoff)
            if time.monotonic() - began > 120:
                backoff = 1.0
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _run_once(self, argv: list[str]) -> int | None:
        self._ready.clear()
        self._hello.clear()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("ab") as log:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=log,
                    env=child_env(),
                    cwd=str(self.cwd) if self.cwd else None,
                    limit=MAX_LINE_BYTES,
                )
            except OSError as e:
                self.last_error = f"WhatsApp-Dienst konnte nicht starten: {e}"
                logger.error("%s", self.last_error)
                return None
            self._proc = proc
            reader = asyncio.create_task(self._read(proc), name="whatsapp-sidecar-reader")
            try:
                await asyncio.wait_for(self._hello.wait(), self.ready_timeout_s)
                self.info = await self.request("init", self._init_params(), timeout=30, require_ready=False)
                self.started_at = time.time()
                self._ready.set()
                self.last_error = None
                await self._emit(
                    "sidecar", {"state": "ready", **{k: v for k, v in self.info.items() if k != "sessionKey"}}
                )
            except (TimeoutError, SidecarError) as e:
                self.last_error = f"WhatsApp-Dienst antwortet nicht ({type(e).__name__})"
                logger.error("WhatsApp sidecar did not start: %s", e)
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
            code = await proc.wait()
            self._ready.clear()
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader
            self._proc = None
            self._fail_pending("sidecar_exited", "Der WhatsApp-Dienst wurde unerwartet beendet.")
            return code

    # ------------------------------------------------------------------ messaging
    async def _read(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        while True:
            try:
                line = await proc.stdout.readline()
            except (asyncio.LimitOverrunError, ValueError):
                logger.error("WhatsApp sidecar sent an oversized line – dropped")
                continue
            if not line:
                return
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("WhatsApp sidecar: unreadable frame dropped")
                continue
            if not isinstance(frame, dict):
                continue
            kind = frame.get("type")
            if kind == "res":
                fut = self._pending.pop(str(frame.get("id")), None)
                if fut is None or fut.done():
                    continue
                if frame.get("ok"):
                    fut.set_result(frame.get("result"))
                else:
                    err = frame.get("error") or {}
                    fut.set_exception(
                        SidecarError(str(err.get("code") or "failed"), str(err.get("message") or "Fehler"))
                    )
            elif kind == "event":
                name = str(frame.get("event") or "")
                raw_data = frame.get("data")
                data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
                if name == "ready":
                    if data.get("protocol") != PROTOCOL_VERSION:
                        logger.error(
                            "WhatsApp sidecar speaks protocol %s, expected %s", data.get("protocol"), PROTOCOL_VERSION
                        )
                    self._hello.set()
                    continue
                await self._emit(name, data)

    async def _emit(self, name: str, data: dict[str, Any]) -> None:
        try:
            await self._on_event(name, data)
        except Exception:
            logger.exception("WhatsApp event handler failed for %s", name)

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,  # noqa: ASYNC109 - per-request deadline of the IPC call
        require_ready: bool = True,
    ) -> Any:
        if method not in METHODS:
            raise SidecarError("invalid", f"Unbekannte Methode {method}")
        if require_ready and not self._ready.is_set():
            if self._stopping or not await self.wait_ready(5):
                raise SidecarError("unavailable", self.last_error or "Der WhatsApp-Dienst läuft nicht.")
        proc = self._proc
        if proc is None or proc.stdin is None or proc.returncode is not None:
            raise SidecarError("unavailable", "Der WhatsApp-Dienst läuft nicht.")
        self._seq += 1
        rid = str(self._seq)
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        line = (
            json.dumps({"type": "req", "id": rid, "method": method, "params": params or {}}, ensure_ascii=False) + "\n"
        )
        try:
            async with self._write_lock:
                proc.stdin.write(line.encode("utf-8"))
                await proc.stdin.drain()
        except (ConnectionError, RuntimeError) as e:
            self._pending.pop(rid, None)
            raise SidecarError("unavailable", f"Der WhatsApp-Dienst ist nicht erreichbar ({type(e).__name__}).") from e
        try:
            return await asyncio.wait_for(fut, timeout or self.request_timeout_s)
        except TimeoutError:
            self._pending.pop(rid, None)
            raise SidecarError(
                "timeout", f"Der WhatsApp-Dienst hat nicht rechtzeitig geantwortet ({method})."
            ) from None

    def _fail_pending(self, code: str, message: str) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(SidecarError(code, message))
        self._pending.clear()
