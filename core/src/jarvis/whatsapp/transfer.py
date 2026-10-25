"""Files between the Jarvis server and a computer running the Jarvis Device Agent – over its existing,
authenticated WebSocket (TLS gateway, certificate pinned, usually over Tailscale). No file server, no new port.

* **pull** (device → server), e.g. "schick Jonathan die PDF von meinem Mac-Desktop" while the server runs on
  Windows: ``files.stat`` (size + SHA-256 on the device) → ``files.read_chunk`` in pieces of 4 MiB → the assembled
  file must have the announced hash, otherwise it is discarded.
* **push** (server → device), e.g. "speichere das WhatsApp-Bild auf meinem Mac-Desktop": ``files.write_chunk``
  appends the pieces to a hidden part file next to the target; the device checks the SHA-256 before it renames the
  file to a free name. The device's own folder policy (allowed folders, key folders blocked, symlinks) applies.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from jarvis.whatsapp.files import FileRejected, create_exclusive, safe_file_name

CHUNK_BYTES = 4 * 1024 * 1024  # base64 in a JSON frame stays far below the 24 MiB frame limit
Progress = Callable[[int, int], Awaitable[None]]


class DeviceCaller(Protocol):
    async def call(
        self, device: str | None, action: str, args: dict[str, Any] | None = None, timeout_s: float | None = None
    ) -> Any: ...


class TransferError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Pulled:
    path: Path
    name: str
    size: int
    sha256: str
    device: str
    remote_path: str


async def _noop(done: int, total: int) -> None:
    return None


async def pull_file(
    hub: DeviceCaller,
    device: str | None,
    remote_path: str,
    dest_dir: Path,
    *,
    max_bytes: int,
    progress: Progress = _noop,
) -> Pulled:
    st = await hub.call(device, "files.stat", {"path": remote_path, "hash": True}, 180)
    if not isinstance(st, dict) or not st.get("is_file"):
        raise TransferError("Auf dem Gerät ist das keine Datei.")
    size = int(st.get("size") or 0)
    expected = str(st.get("sha256") or "")
    if size > max_bytes:
        raise FileRejected(f"Die Datei ist zu groß für den Versand ({size} Bytes, erlaubt {max_bytes}).")
    if len(expected) != 64:
        raise TransferError("Das Gerät hat keine Prüfsumme geliefert (Device Agent zu alt?).")
    name = safe_file_name(str(st.get("name") or Path(str(st.get("path") or remote_path)).name))
    dest_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path, fd = create_exclusive(dest_dir, f".pull-{secrets.token_hex(6)}-{name}")
    h = hashlib.sha256()
    done = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while done < size:
                part = await hub.call(
                    device, "files.read_chunk", {"path": remote_path, "offset": done, "length": CHUNK_BYTES}, 120
                )
                data = base64.b64decode(str((part or {}).get("data") or ""), validate=True)
                if not data:
                    raise TransferError("Die Übertragung brach ab (leerer Block).")
                out.write(data)
                h.update(data)
                done += len(data)
                if done > size:
                    raise TransferError("Die Datei hat sich während der Übertragung verändert.")
                await progress(done, size)
        if h.hexdigest() != expected:
            raise TransferError("Prüfsumme stimmt nicht – die Datei wurde verworfen.")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return Pulled(path, name, size, expected, str(st.get("device") or device or ""), str(st.get("path") or remote_path))


async def push_file(
    hub: DeviceCaller, device: str | None, local: Path, remote_path: str, *, progress: Progress = _noop
) -> dict[str, Any]:
    size = local.stat().st_size
    h = hashlib.sha256()
    with local.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    digest = h.hexdigest()
    upload = secrets.token_hex(8)
    done = 0
    result: Any = None
    with local.open("rb") as f:
        while True:
            data = f.read(CHUNK_BYTES)
            final = done + len(data) >= size
            args: dict[str, Any] = {
                "upload_id": upload,
                "path": remote_path,
                "offset": done,
                "data": base64.b64encode(data).decode(),
                "final": final,
            }
            if final:
                args["sha256"] = digest
                args["size"] = size
            result = await hub.call(device, "files.write_chunk", args, 120)
            done += len(data)
            await progress(done, size)
            if final:
                break
    if not isinstance(result, dict) or result.get("sha256") != digest:
        raise TransferError("Das Gerät hat die Prüfsumme nicht bestätigt.")
    return {"path": result.get("path"), "size": size, "sha256": digest}
