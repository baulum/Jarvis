"""Publish times (Europe/Berlin) and the retry policy (exponential backoff with an upper bound)."""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from jarvis.tools.executor import ToolUserError

TZ = ZoneInfo("Europe/Berlin")
MIN_LEAD = timedelta(minutes=1)
MAX_AHEAD = timedelta(days=365)
_NAIVE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?$")


def iso(dt: datetime) -> str:
    """One fixed UTC format for the database: strings sort like the instants they stand for."""
    return dt.astimezone(UTC).isoformat(timespec="microseconds")


def from_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    dt = datetime.fromisoformat(raw)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def berlin(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%d.%m.%Y %H:%M")


def parse_publish_at(raw: str, now: datetime) -> datetime:
    """ "2026-11-02 18:30" (Berlin wall clock) or ISO 8601 with offset → UTC; at least 1 min, at most 1 year ahead.

    A wall-clock time that does not exist (spring DST gap, 02:00–03:00) is refused instead of shifted; an
    ambiguous one (autumn, 02:00–03:00 twice) means the first occurrence (summer time)."""
    text = (raw or "").strip()
    m = _NAIVE.match(text)
    if m:
        y, mo, d, h, mi, s = (int(g) if g else 0 for g in m.groups())
        try:
            local = datetime(y, mo, d, h, mi, s, tzinfo=TZ, fold=0)
        except ValueError:
            raise ToolUserError(f"Ungültiges Datum: {text}") from None
        roundtrip = local.astimezone(UTC).astimezone(TZ)
        if roundtrip.replace(tzinfo=None) != local.replace(tzinfo=None):
            raise ToolUserError(f"{text} gibt es in Berlin nicht (Zeitumstellung) – bitte eine andere Uhrzeit")
        when = local.astimezone(UTC)
    else:
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            raise ToolUserError("Zeitpunkt als „JJJJ-MM-TT HH:MM“ (Berlin) oder ISO 8601 angeben") from None
        if parsed.tzinfo is None:
            raise ToolUserError("Zeitpunkt ohne Zeitzone – „JJJJ-MM-TT HH:MM“ gilt als Berliner Zeit")
        when = parsed.astimezone(UTC)
    if when < now + MIN_LEAD:
        raise ToolUserError(f"Der Zeitpunkt {berlin(when)} liegt in der Vergangenheit oder zu knapp in der Zukunft")
    if when > now + MAX_AHEAD:
        raise ToolUserError("Zeitpunkt höchstens ein Jahr im Voraus")
    return when


def at_local_time(hhmm: str, now: datetime) -> datetime:
    """The next occurrence of HH:MM (Berlin) from ``now`` – today if still ahead, else tomorrow."""
    h, m = (int(x) for x in hhmm.split(":"))
    local = now.astimezone(TZ).replace(hour=h, minute=m, second=0, microsecond=0)
    if local.astimezone(UTC) < now + MIN_LEAD:
        local = (local + timedelta(days=1)).replace(hour=h, minute=m)
    return local.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """``max_attempts`` runs in total; after the n-th failed run the next one waits base·factor^(n−1), capped."""

    max_attempts: int = 3
    base_s: float = 120.0
    factor: float = 2.0
    max_s: float = 3600.0
    jitter: float = 0.1  # ± share of the delay, spreads retries of a fan-out apart

    def should_retry(self, attempts: int) -> bool:
        return attempts < self.max_attempts

    def delay(self, attempts: int, rand: Callable[[], float] = random.random) -> float:
        raw = min(self.base_s * self.factor ** max(attempts - 1, 0), self.max_s)
        return max(0.0, raw * (1 + self.jitter * (2 * rand() - 1)))
