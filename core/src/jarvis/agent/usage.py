"""Claude subscription quota ("Kontingent") and local usage statistics.

The Claude Code CLI reports the real utilisation of the subscription windows (5 hours, 7 days) in a
``rate_limit_event`` during every turn; the result event carries the token counts. This tracker keeps the
latest window state plus per-day, per-model counters (``agent-usage.json``), warns before a limit is hit and
tells the model router how much pressure there is (so automatic choices can step down to a cheaper model).
Nothing here is estimated: the percentages are exactly what Anthropic reports.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jarvis.events import spawn

logger = logging.getLogger(__name__)
WINDOWS = ("five_hour", "seven_day")
WARN_LEVELS = (0.8, 0.95)
KEEP_DAYS = 35
_ALIASES = (("opus", "opus"), ("sonnet", "sonnet"), ("haiku", "haiku"))


def model_alias(model: str | None) -> str:
    low = (model or "").lower()
    return next((alias for key, alias in _ALIASES if key in low), "other")


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, UTC).isoformat() if ts else None


class UsageTracker:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.limits: dict[str, Any] = {}
        self.days: dict[str, dict[str, dict[str, int]]] = {}
        self.updated_at: float | None = None
        self._warned: set[str] = set()
        self.notify: Callable[[str, str, str], Awaitable[Any]] | None = None
        self.on_change: Callable[[dict[str, Any]], object] | None = None
        self._load()

    # ------------------------------------------------------------------ persistence
    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return
        self.limits = data.get("limits") or {}
        self.days = data.get("days") or {}
        self.updated_at = data.get("updated_at")
        self._warned = set(data.get("warned") or [])

    def _save(self) -> None:
        cutoff = (datetime.now(UTC) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
        self.days = {d: v for d, v in self.days.items() if d >= cutoff}
        tmp = self.path.with_suffix(".tmp")
        payload = {
            "limits": self.limits,
            "days": self.days,
            "updated_at": self.updated_at,
            "warned": sorted(self._warned)[-50:],
        }
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(self.path)

    # ------------------------------------------------------------------ input
    def on_rate_limit(self, info: dict[str, Any]) -> None:
        """``rate_limit_info`` of a ``rate_limit_event`` (CLI stream-json)."""
        windows: dict[str, Any] = {}
        for name, w in (info.get("unifiedWindows") or {}).items():
            if name in WINDOWS and isinstance(w, dict) and w.get("utilization") is not None:
                windows[name] = {"utilization": float(w["utilization"]), "resets_at": w.get("resetsAt")}
        if not windows and info.get("rateLimitType") in WINDOWS and info.get("utilization") is not None:
            windows[info["rateLimitType"]] = {
                "utilization": float(info["utilization"]),
                "resets_at": info.get("resetsAt"),
            }
        if not windows:
            return
        self.limits = {
            "windows": {**(self.limits.get("windows") or {}), **windows},
            "status": info.get("status", "allowed"),
            "overage": {"status": info.get("overageStatus"), "using": bool(info.get("isUsingOverage"))},
        }
        self.updated_at = time.time()
        self._save()
        self._emit()
        self._check_warnings()

    def on_result(self, model: str | None, usage: dict[str, Any] | None) -> None:
        if not usage:
            return
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        row = self.days.setdefault(day, {}).setdefault(
            model_alias(model),
            {"turns": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0},
        )
        row["turns"] += 1
        row["input_tokens"] += int(usage.get("input_tokens") or 0)
        row["output_tokens"] += int(usage.get("output_tokens") or 0)
        row["cache_read_tokens"] += int(usage.get("cache_read_input_tokens") or 0)
        row["cache_write_tokens"] += int(usage.get("cache_creation_input_tokens") or 0)
        self._save()
        self._emit()

    # ------------------------------------------------------------------ output
    def _window(self, name: str, now: float) -> dict[str, Any] | None:
        w = (self.limits.get("windows") or {}).get(name)
        if not w:
            return None
        resets = w.get("resets_at")
        expired = bool(resets and resets < now)  # the window rolled over since the last report
        return {
            "utilization": 0.0 if expired else round(float(w["utilization"]), 4),
            "resets_at": _iso(resets),
            "resets_in_s": max(0, int(resets - now)) if resets else None,
            "stale": expired,
        }

    def pressure(self) -> float:
        """Highest current utilisation (0..1) over all windows; 0 if unknown."""
        now = time.time()
        vals = [w["utilization"] for n in WINDOWS if (w := self._window(n, now)) is not None]
        return max(vals, default=0.0)

    def status(self) -> dict[str, Any]:
        now = time.time()
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        week_start = (datetime.now(UTC) - timedelta(days=6)).strftime("%Y-%m-%d")
        week: dict[str, dict[str, int]] = {}
        for day, models in self.days.items():
            if day < week_start:
                continue
            for m, row in models.items():
                acc = week.setdefault(m, {k: 0 for k in row})
                for k, v in row.items():
                    acc[k] = acc.get(k, 0) + v
        return {
            "known": bool(self.limits),
            "status": self.limits.get("status"),
            "windows": {n: self._window(n, now) for n in WINDOWS},
            "overage": self.limits.get("overage"),
            "updated_at": _iso(self.updated_at),
            "pressure": round(self.pressure(), 4),
            "today": self.days.get(today, {}),
            "week": week,
        }

    # ------------------------------------------------------------------ side effects
    def _emit(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change(self.status())
            except Exception:
                logger.debug("usage change callback failed", exc_info=True)

    def _check_warnings(self) -> None:
        if self.notify is None:
            return
        now = time.time()
        labels = {"five_hour": "5-Stunden-Fenster", "seven_day": "7-Tage-Fenster"}
        for name in WINDOWS:
            w = self._window(name, now)
            if not w or w["stale"]:
                continue
            for level in WARN_LEVELS:
                key = f"{name}:{(self.limits['windows'][name].get('resets_at') or 0)}:{level}"
                if w["utilization"] >= level and key not in self._warned:
                    self._warned.add(key)
                    pct = round(w["utilization"] * 100)
                    mins = (w["resets_in_s"] or 0) // 60
                    title = f"Claude-Kontingent bei {pct} %"
                    body = f"Das {labels[name]} ist zu {pct} % ausgeschöpft (Reset in {mins // 60} h {mins % 60} min)."
                    spawn(self.notify("warning" if level < 0.95 else "critical", title, body), name="usage-warning")
                    self._save()
