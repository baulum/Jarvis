"""Process-tree control that works on POSIX and Windows.

On macOS/Linux this is exactly ``os.killpg`` (the children were started with ``start_new_session=True``, so the
pid is also the process-group id). Windows has no process groups in that sense: the tree below the pid is
terminated through psutil. Errors keep the POSIX contract – ``ProcessLookupError`` when the process is gone.
"""

from __future__ import annotations

import os
import signal

import psutil


def terminate_group(pid: int, sig: int = signal.SIGTERM) -> None:
    """Send ``sig`` to the process group ``pid`` (POSIX) or terminate/kill the process tree (Windows)."""
    killpg = getattr(os, "killpg", None)
    if killpg is not None:
        killpg(pid, sig)
        return
    _terminate_tree(pid, force=sig != signal.SIGTERM)


def _terminate_tree(pid: int, force: bool) -> None:
    try:
        root = psutil.Process(pid)
        procs = [*root.children(recursive=True), root]
    except psutil.NoSuchProcess as e:
        raise ProcessLookupError(pid) from e
    except psutil.AccessDenied as e:
        raise PermissionError(pid) from e
    for p in procs:
        try:
            p.kill() if force else p.terminate()
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as e:
            raise PermissionError(p.pid) from e
