"""File actions below the allowed roots (``PathPolicy``): list, read, write, search by name, move, mkdir."""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import hashlib
import os
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis_device.security import PathPolicy

MAX_LIST = 500
MAX_READ = 1024 * 1024
DEFAULT_READ = 200_000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".cache", "AppData", "Library"}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


def list_dir(policy: PathPolicy, raw: str) -> dict[str, Any]:
    path = policy.check(raw, must_exist=True)
    if not path.is_dir():
        raise NotADirectoryError(str(path))
    entries: list[dict[str, Any]] = []
    truncated = False
    for i, entry in enumerate(sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))):
        if i >= MAX_LIST:
            truncated = True
            break
        try:
            st = entry.stat()
        except OSError:
            continue
        entries.append(
            {
                "name": entry.name,
                "type": "dir" if entry.is_dir() else "file",
                "size": st.st_size if entry.is_file() else None,
                "modified": _iso(st.st_mtime),
            }
        )
    return {"path": str(path), "entries": entries, "truncated": truncated}


def read_file(policy: PathPolicy, raw: str, max_bytes: int | None = None) -> dict[str, Any]:
    path = policy.check(raw, must_exist=True)
    if not path.is_file():
        raise IsADirectoryError(str(path))
    limit = max(1, min(MAX_READ, int(max_bytes or DEFAULT_READ)))
    size = path.stat().st_size
    with path.open("rb") as f:
        data = f.read(limit)
    if b"\x00" in data[:8192]:
        return {"path": str(path), "size": size, "binary": True, "text": ""}
    return {
        "path": str(path),
        "size": size,
        "binary": False,
        "truncated": size > limit,
        "text": data.decode("utf-8", errors="replace"),
    }


def write_file(policy: PathPolicy, raw: str, content: str, overwrite: bool = False) -> dict[str, Any]:
    path = policy.check(raw)
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} existiert bereits (overwrite=true zum Ersetzen)")
    if path.exists() and path.is_dir():
        raise IsADirectoryError(str(path))
    policy.check(str(path.parent))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.jarvis-tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
    return {"path": str(path), "bytes": len(content.encode("utf-8"))}


def move(policy: PathPolicy, src_raw: str, dst_raw: str) -> dict[str, Any]:
    src = policy.check(src_raw, must_exist=True)
    dst = policy.check(dst_raw)
    if dst.exists() and dst.is_dir():
        dst = dst / src.name
        policy.check(str(dst))
    if dst.exists():
        raise FileExistsError(f"{dst} existiert bereits")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return {"from": str(src), "to": str(dst)}


def mkdir(policy: PathPolicy, raw: str) -> dict[str, Any]:
    path = policy.check(raw)
    path.mkdir(parents=True, exist_ok=True)
    return {"path": str(path)}


def search(policy: PathPolicy, query: str, root: str | None, limit: int = 50, budget_s: float = 20.0) -> dict[str, Any]:
    """Name search (substring, or a glob with * ?) below ``root`` (default: every allowed root)."""
    roots = [policy.check(root, must_exist=True)] if root else list(policy.roots)
    q = query.strip()
    pattern = q if any(ch in q for ch in "*?[") else None
    needle = q.casefold()
    hits: list[dict[str, Any]] = []
    deadline = time.monotonic() + budget_s
    complete = True
    for base in roots:
        for dirpath, dirnames, filenames in os.walk(base):
            if time.monotonic() > deadline:
                complete = False
                break
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for name in [*dirnames, *filenames]:
                ok = fnmatch.fnmatch(name.casefold(), pattern.casefold()) if pattern else needle in name.casefold()
                if not ok:
                    continue
                full = Path(dirpath) / name
                try:
                    policy.check(str(full))
                except PermissionError:
                    continue
                hits.append({"path": str(full), "type": "dir" if name in dirnames else "file"})
                if len(hits) >= limit:
                    return {"results": hits, "complete": False}
        if not complete:
            break
    return {"results": hits, "complete": complete}


CONTENT_MAX_FILE = 2 * 1024 * 1024


def content_search(
    policy: PathPolicy, query: str, root: str | None, limit: int = 50, budget_s: float = 25.0
) -> dict[str, Any]:
    """Name *or* content match without a search index (text files up to 2 MB): the fallback when the system has
    no index (no Windows Search, no Tracker) – slower, bounded by ``budget_s``."""
    roots = [policy.check(root, must_exist=True)] if root else list(policy.roots)
    needle = query.strip().casefold()
    if not needle:
        raise ValueError("query fehlt")
    raw = needle.encode("utf-8")
    hits: list[dict[str, Any]] = []
    deadline = time.monotonic() + budget_s
    for base in roots:
        for dirpath, dirnames, filenames in os.walk(base):
            if time.monotonic() > deadline:
                return {"results": hits, "complete": False, "source": "scan"}
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for name in filenames:
                full = Path(dirpath) / name
                match = "name" if needle in name.casefold() else ""
                if not match:
                    try:
                        if full.stat().st_size > CONTENT_MAX_FILE:
                            continue
                        data = full.read_bytes()
                    except OSError:
                        continue
                    if b"\x00" in data[:8192]:
                        continue  # binary
                    if raw not in data.lower() and needle not in data.decode("utf-8", errors="ignore").casefold():
                        continue
                    match = "content"
                try:
                    policy.check(str(full))
                except PermissionError:
                    continue
                hits.append({"path": str(full), "type": "file", "match": match})
                if len(hits) >= limit:
                    return {"results": hits, "complete": False, "source": "scan"}
    return {"results": hits, "complete": True, "source": "scan"}


def from_index(policy: PathPolicy, paths: list[str], roots: list[Path], limit: int) -> list[dict[str, Any]]:
    """Index hits (Spotlight, Windows Search, Tracker, locate) reduced to what the path policy allows."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in paths:
        try:
            p = policy.check(raw)
        except (PermissionError, OSError, ValueError):
            continue
        if not any(p == r or p.is_relative_to(r) for r in roots) or str(p) in seen or not p.exists():
            continue
        seen.add(str(p))
        out.append({"path": str(p), "type": "dir" if p.is_dir() else "file"})
        if len(out) >= limit:
            break
    return out


async def run_sync(fn: Any, *args: Any) -> Any:
    return await asyncio.to_thread(fn, *args)


# ---------------------------------------------------------------------------------------- binary transfer
# Used by the Core to move files between the server and this computer (e.g. a WhatsApp attachment to the
# desktop, a PDF from here to WhatsApp). Pieces travel base64-encoded in normal command frames; the SHA-256 of
# the whole file is checked on both ends.
CHUNK_MAX = 4 * 1024 * 1024
TRANSFER_MAX = 2 * 1024 * 1024 * 1024
_UPLOAD_ID = re.compile(r"^[0-9a-f]{8,32}$")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stat(policy: PathPolicy, raw: str, with_hash: bool = False) -> dict[str, Any]:
    path = policy.check(raw, must_exist=True)
    st = path.stat()
    out: dict[str, Any] = {
        "path": str(path),
        "name": path.name,
        "is_file": path.is_file(),
        "size": st.st_size if path.is_file() else None,
        "modified": _iso(st.st_mtime),
    }
    if with_hash and path.is_file():
        if st.st_size > TRANSFER_MAX:
            raise ValueError("Datei ist zu groß für eine Übertragung (max. 2 GB)")
        out["sha256"] = _sha256(path)
    return out


def read_chunk(policy: PathPolicy, raw: str, offset: int, length: int) -> dict[str, Any]:
    path = policy.check(raw, must_exist=True)
    if not path.is_file():
        raise IsADirectoryError(str(path))
    if offset < 0:
        raise ValueError("offset muss ≥ 0 sein")
    length = max(1, min(CHUNK_MAX, int(length)))
    size = path.stat().st_size
    with path.open("rb") as f:
        f.seek(offset)
        data = f.read(length)
    return {"offset": offset, "data": base64.b64encode(data).decode(), "eof": offset + len(data) >= size, "size": size}


def _free_name(target: Path) -> Path:
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for i in range(1, 1000):
        candidate = target.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Kein freier Name für {target.name}")


def write_chunk(
    policy: PathPolicy,
    upload_id: str,
    raw: str,
    offset: int,
    data_b64: str,
    final: bool,
    sha256: str | None = None,
    size: int | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    if not _UPLOAD_ID.match(upload_id or ""):
        raise ValueError("upload_id ungültig")
    target = policy.check(raw)
    if target.exists() and target.is_dir():
        raise IsADirectoryError(f"{target} ist ein Ordner – vollständigen Dateipfad angeben")
    policy.check(str(target.parent))
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(f".{target.name}.jarvis-part-{upload_id}")
    policy.check(str(part))
    data = base64.b64decode(data_b64 or "", validate=True)
    if len(data) > CHUNK_MAX:
        raise ValueError("Block zu groß")
    current = part.stat().st_size if part.exists() else 0
    if offset == 0 and current:
        part.unlink()
        current = 0
    if offset != current:
        raise ValueError(f"Block an falscher Stelle ({offset}, erwartet {current})")
    if current + len(data) > TRANSFER_MAX:
        part.unlink(missing_ok=True)
        raise ValueError("Übertragung zu groß (max. 2 GB)")
    with part.open("ab") as f:
        f.write(data)
    if not final:
        return {"received": current + len(data)}
    total = part.stat().st_size
    digest = _sha256(part)
    if (size is not None and total != int(size)) or not sha256 or digest != sha256:
        part.unlink(missing_ok=True)
        raise ValueError("Prüfsumme oder Größe stimmt nicht – Datei verworfen")
    final_path = target if overwrite else _free_name(target)
    os.replace(part, final_path)
    return {"path": str(final_path), "size": total, "sha256": digest}
