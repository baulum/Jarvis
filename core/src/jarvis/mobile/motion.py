"""Is Sir moving (in the car, on the train)? Derived from two location fixes – pure function, no I/O, no storage.

The phone reports latitude, longitude, accuracy and the time of the measurement – *no speed* (see
``jarvis.mobile.location``). So movement is the average speed between two fixes. The answer is deliberately
three-valued: whenever the data cannot support "yes" or "no" (no fix, stale fix, only one fix, fixes too close in
time or too far apart, GPS error as large as the distance) the answer is ``unknown`` – never a guess. Callers treat
``unknown`` like standing still for the length of the reply, but must not claim that Sir is not moving.

Coordinates never leave this module: nothing here logs, stores or returns them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

# From this average speed on Sir counts as "in motion" (walking ≈ 5 km/h, cycling ≈ 15-20 km/h, car/train above).
MOVING_SPEED_KMH = 20.0
# A fix older than this says nothing about now (same limit as the "stale" note of ``mobile_get_location``).
MAX_FIX_AGE_MIN = 10
# Two fixes closer in time than this are GPS noise, further apart than this average over too long a stretch
# (a drive that ended minutes ago would look slow).
MIN_FIX_GAP_S = 30.0
MAX_FIX_GAP_S = MAX_FIX_AGE_MIN * 60.0
# Used when the phone reported no accuracy: a typical outdoor GPS error.
DEFAULT_ACCURACY_M = 30.0
EARTH_RADIUS_M = 6_371_000.0


class Motion(StrEnum):
    MOVING = "yes"
    STILL = "no"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Sample:
    """One measurement. ``repr=False`` on the coordinates: an accidental log line never shows them."""

    latitude: float = field(repr=False)
    longitude: float = field(repr=False)
    measured_at: datetime
    accuracy_m: float | None = field(default=None, repr=False)
    speed_mps: float | None = field(default=None, repr=False)  # only if the source ever delivers one


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo is not None else t.replace(tzinfo=UTC)


def distance_m(a: Sample, b: Sample) -> float:
    """Great-circle distance (haversine)."""
    p1, p2 = math.radians(a.latitude), math.radians(b.latitude)
    dp = p2 - p1
    dl = math.radians(b.longitude - a.longitude)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def fix_is_current(current: Sample | None, now: datetime | None = None) -> bool:
    """True if there is a fix and it is not older than ``MAX_FIX_AGE_MIN``."""
    if current is None:
        return False
    age_s = ((_aware(now) if now else datetime.now(UTC)) - _aware(current.measured_at)).total_seconds()
    return age_s <= MAX_FIX_AGE_MIN * 60


def detect_motion(
    previous: Sample | None,
    current: Sample | None,
    now: datetime | None = None,
    *,
    threshold_kmh: float = MOVING_SPEED_KMH,
) -> Motion:
    """``yes`` / ``no`` / ``unknown`` – see the module docstring for when it is ``unknown``."""
    if not fix_is_current(current, now):
        return Motion.UNKNOWN  # missing or stale: no conclusion about now
    assert current is not None
    threshold_mps = threshold_kmh / 3.6
    if current.speed_mps is not None and current.speed_mps >= 0:
        return Motion.MOVING if current.speed_mps >= threshold_mps else Motion.STILL
    if previous is None:
        return Motion.UNKNOWN
    gap_s = (_aware(current.measured_at) - _aware(previous.measured_at)).total_seconds()
    if gap_s < MIN_FIX_GAP_S or gap_s > MAX_FIX_GAP_S:
        return Motion.UNKNOWN
    dist = distance_m(previous, current)
    jitter = (previous.accuracy_m if previous.accuracy_m is not None else DEFAULT_ACCURACY_M) + (
        current.accuracy_m if current.accuracy_m is not None else DEFAULT_ACCURACY_M
    )
    # The true distance lies somewhere in [dist - jitter, dist + jitter]. Only a verdict that holds for the whole
    # range is given.
    slowest = max(0.0, dist - jitter) / gap_s
    fastest = (dist + jitter) / gap_s
    if slowest >= threshold_mps:
        return Motion.MOVING
    if fastest < threshold_mps:
        return Motion.STILL
    return Motion.UNKNOWN
