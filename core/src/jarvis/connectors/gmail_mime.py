"""Gmail MIME helpers: attachment discovery, filename sanitising and safe destination paths."""

from __future__ import annotations

import base64
import mimetypes
import re
from pathlib import Path
from typing import Any

from jarvis.tools.executor import ToolUserError

_EXT_FIX = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/heic": ".heic", "image/png": ".png"}
_MAX_NAME = 120


def b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _ext(mime: str) -> str:
    return _EXT_FIX.get(mime.lower()) or mimetypes.guess_extension(mime) or ".bin"


def _headers(part: dict[str, Any]) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in part.get("headers", []) or []}


def collect_attachments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Walk all MIME parts depth-first (= body order) and return attachment descriptors.

    Each entry: filename, mimeType, size, attachmentId, inline, contentId (+ internal ``data`` for
    parts that Gmail delivers inline in the message body instead of via attachmentId).
    """
    out: list[dict[str, Any]] = []
    counter = 0

    def walk(part: dict[str, Any]) -> None:
        nonlocal counter
        children = part.get("parts") or []
        mime = part.get("mimeType", "")
        body = part.get("body", {}) or {}
        hdr = _headers(part)
        name = part.get("filename") or ""
        disp = hdr.get("content-disposition", "").lower()
        cid = hdr.get("content-id", "").strip().strip("<>")
        is_text_body = mime in ("text/plain", "text/html") and not name and "attachment" not in disp
        has_content = bool(body.get("attachmentId") or body.get("data"))
        if not children and has_content and not is_text_body and not mime.startswith("multipart/"):
            counter += 1
            if not name:
                name = (
                    f"image_{counter}{_ext(mime)}" if mime.startswith("image/") else f"attachment_{counter}{_ext(mime)}"
                )
            entry = {
                "filename": name,
                "mimeType": mime,
                "size": body.get("size", 0),
                "attachmentId": body.get("attachmentId"),
                "inline": disp.startswith("inline") or (bool(cid) and "attachment" not in disp),
                "contentId": cid or None,
            }
            if body.get("data"):
                entry["data"] = body["data"]
            out.append(entry)
        for c in children:
            walk(c)

    walk(payload)
    return out


def public_view(att: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in att.items() if k != "data"}


def sanitize_filename(name: str, fallback: str = "datei") -> str:
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f]", "", name)
    name = re.sub(r"[^\w.\- ()äöüÄÖÜß]", "_", name).strip(" .")
    if not name:
        name = fallback
    if len(name) > _MAX_NAME:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: _MAX_NAME - len(ext) - 1] + "." + ext) if dot and len(ext) < 12 else name[:_MAX_NAME]
    return name


def resolve_dest_dir(dest_dir: str, roots: list[Path]) -> Path:
    """Expand and resolve ``dest_dir``; it must lie inside one of ``roots`` (symlinks/.. resolved)."""
    if not dest_dir or "\x00" in dest_dir:
        raise ToolUserError("Zielordner fehlt oder ist ungültig.")
    p = Path(dest_dir).expanduser()
    if not p.is_absolute():
        raise ToolUserError("Zielordner muss ein absoluter Pfad sein.")
    p = p.resolve()
    if not any(p.is_relative_to(r.resolve()) for r in roots):
        allowed = ", ".join(str(r) for r in roots)
        raise ToolUserError(f"Zielordner nicht erlaubt. Erlaubt: {allowed}")
    return p


def unique_path(directory: Path, filename: str) -> Path:
    """Return a path in ``directory`` that does not exist yet (appends _1, _2, ...)."""
    cand = directory / filename
    stem, suffix = cand.stem, cand.suffix
    i = 0
    while cand.exists():
        i += 1
        cand = directory / f"{stem}_{i}{suffix}"
    return cand


def write_new(directory: Path, filename: str, data: bytes) -> Path:
    """Create the file exclusively so an existing file is never overwritten (race-safe)."""
    while True:
        target = unique_path(directory, filename)
        try:
            with open(target, "xb") as f:
                f.write(data)
            return target
        except FileExistsError:
            continue
