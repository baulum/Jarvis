"""Structured JSON logging.

Logs go to ``<log_dir>/core.jsonl`` (rotated) and to an in-memory ring buffer that the
Live-Logs view of the desktop app reads. Secrets must never be passed to loggers; as a
safety net, values that look like tokens are redacted.
"""

from __future__ import annotations

import collections
import json
import logging
import logging.handlers
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REDACT = re.compile(
    r"(?i)(bearer\s+[a-z0-9._\-]{12,}|sk-[a-z0-9]{12,}|ghp_[a-z0-9]{20,}|xox[abp]-[a-z0-9-]{10,}"
    r"|\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"
    r"|\bj(?:dv|at|rt)_[A-Za-z0-9._-]{20,}|AIza[0-9A-Za-z_-]{30,}|\bAC[0-9a-f]{32}\b)"
)


def redact(text: str) -> str:
    return _REDACT.sub("[REDACTED]", text)


# Tool arguments that carry a password Sir typed (e.g. coding_supabase_password): never shown or stored as is.
_SECRET_ARG_KEYS = {"password"}


def hide_secret_args(value: Any) -> Any:
    """Tool input with password fields replaced (for the chat timeline and events)."""
    if isinstance(value, dict):
        return {k: "[verborgen]" if k in _SECRET_ARG_KEYS else hide_secret_args(v) for k, v in value.items()}
    if isinstance(value, list):
        return [hide_secret_args(v) for v in value]
    return value


class RingBuffer(logging.Handler):
    def __init__(self, capacity: int = 2000) -> None:
        super().__init__()
        self._buf: collections.deque[dict[str, Any]] = collections.deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._listeners: list[Any] = []

    def emit(self, record: logging.LogRecord) -> None:
        entry = _record_to_dict(record)
        with self._lock:
            self._buf.append(entry)
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(entry)
            except Exception:  # noqa: S110 - a listener must never break logging
                pass

    def recent(self, limit: int = 200, level: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._buf)
        if level:
            threshold = logging.getLevelName(level.upper())
            if isinstance(threshold, int):
                items = [i for i in items if logging.getLevelName(i["level"]) >= threshold]
        return items[-limit:]

    def subscribe(self, cb: Any) -> None:
        with self._lock:
            self._listeners.append(cb)


def _record_to_dict(record: logging.LogRecord) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
        "level": record.levelname,
        "logger": record.name,
        "msg": redact(record.getMessage()),
    }
    extra = getattr(record, "data", None)
    if isinstance(extra, dict):
        entry["data"] = json.loads(redact(json.dumps(extra, default=str)))
    if record.exc_info:
        entry["exc"] = redact(logging.Formatter().formatException(record.exc_info))
    return entry


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(_record_to_dict(record), ensure_ascii=False)


ring = RingBuffer()


def setup_logging(log_dir: Path, level: str = "INFO") -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "core.jsonl", maxBytes=10_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(JsonFormatter())
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(file_handler)
    root.addHandler(console)
    root.addHandler(ring)
    for noisy in ("httpx", "httpcore", "uvicorn.access", "watchfiles", "mcp", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log(logger: logging.Logger, level: int, msg: str, **data: Any) -> None:
    logger.log(level, msg, extra={"data": data} if data else None)
