"""File actions below the allowed roots (``PathPolicy``): list, read, write, search by name, move, mkdir."""

from __future__ import annotations

import asyncio
import fnmatch
import os
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


async def run_sync(fn: Any, *args: Any) -> Any:
    return await asyncio.to_thread(fn, *args)
