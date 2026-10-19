"""Sir's last known phone location – in memory only: no history, no database, no log line, no audit entry.

The app reports a fix only while Sir has enabled location sharing in the app (and when Jarvis asks for one);
switching sharing off deletes the stored fix. Jarvis asks for a fresh fix with an end-to-end encrypted push
that carries no data besides its type. A core restart forgets the fix – the next request fetches a new one.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jarvis.mobile.push import PushService

logger = logging.getLogger(__name__)
STALE_AFTER_MIN = 10
FRESH_ENOUGH_S = 120  # younger fixes are used as they are, older ones trigger a request to the phone
REQUEST_WAIT_S = 12.0
MAX_CLOCK_SKEW = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class LocationFix:
    # repr=False: an accidental log/f-string of the object never shows coordinates
    latitude: float = field(repr=False)
    longitude: float = field(repr=False)
    accuracy_m: float | None = field(repr=False)
    measured_at: datetime
    device_id: uuid.UUID

    def age_minutes(self, now: datetime | None = None) -> float:
        return max(0.0, ((now or datetime.now(UTC)) - self.measured_at).total_seconds() / 60)


class LocationStore:
    def __init__(self, push: PushService) -> None:
        self.push = push
        self._fix: LocationFix | None = None
        self._sharing_off = False  # the phone said "sharing off" since the last fix (until restart)
        self._changed = asyncio.Event()

    def latest(self) -> LocationFix | None:
        return self._fix

    def update(
        self, device_id: uuid.UUID, latitude: float, longitude: float, accuracy_m: float | None, measured_at: datetime
    ) -> bool:
        """Keeps the newest fix only. Returns False for a fix older than the stored one (out-of-order)."""
        now = datetime.now(UTC)
        if measured_at > now + MAX_CLOCK_SKEW:
            raise ValueError("timestamp in the future")
        measured_at = min(measured_at, now)
        if self._fix is not None and measured_at < self._fix.measured_at:
            return False
        self._fix = LocationFix(latitude, longitude, accuracy_m, measured_at, device_id)
        self._sharing_off = False
        self._changed.set()
        self._changed = asyncio.Event()
        return True

    def clear(self, device_id: uuid.UUID, *, sharing_off: bool = True) -> None:
        """Sir switched sharing off on this phone (or unpaired it): forget its fix."""
        if self._fix is not None and self._fix.device_id == device_id:
            self._fix = None
        self._sharing_off = self._sharing_off or sharing_off

    async def request_fresh(self, timeout_s: float = REQUEST_WAIT_S) -> bool:
        """Asks the paired phones for a current fix and waits briefly for it. True if a push went out."""
        waiter = self._changed
        try:
            results = await self.push.send_all("location_request", {}, high_priority=True, ttl_s=120)
        except Exception as e:  # push problems must not break the tool; no payload details in the log
            logger.warning("Location request push failed: %s", type(e).__name__)
            return False
        if not any(r.ok for r in results):
            return False
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(waiter.wait(), timeout_s)
        return True

    async def current(self) -> dict[str, Any]:
        """What ``mobile_get_location`` returns: the last fix with its age, plus a note if stale or missing."""
        fix = self._fix
        requested = False
        if fix is None or fix.age_minutes() * 60 > FRESH_ENOUGH_S:
            requested = await self.request_fresh()
            fix = self._fix
        return describe(fix, requested=requested, sharing_off=self._sharing_off)


def describe(
    fix: LocationFix | None, *, requested: bool, sharing_off: bool, now: datetime | None = None
) -> dict[str, Any]:
    if fix is None:
        if sharing_off:
            why = "Sir hat die Standortfreigabe in der J.A.R.V.I.S.-App ausgeschaltet."
        elif requested:
            why = (
                "Das Handy hat auf die Standortanfrage nicht rechtzeitig geantwortet (Freigabe aus, Ortung "
                "deaktiviert oder Handy offline)."
            )
        else:
            why = (
                "Das Handy ist per Push nicht erreichbar (kein gekoppeltes Handy mit Push-Registrierung oder "
                "Firebase fehlt)."
            )
        return {
            "available": False,
            "note": f"Kein Standort vorhanden. {why} Sir kann die Freigabe in der App unter "
            "Einstellungen › Standortfreigabe einschalten.",
        }
    age = fix.age_minutes(now)
    out: dict[str, Any] = {
        "available": True,
        "latitude": round(fix.latitude, 6),
        "longitude": round(fix.longitude, 6),
        "accuracy_m": round(fix.accuracy_m) if fix.accuracy_m is not None else None,
        "measured_at": fix.measured_at.isoformat(),
        "age_minutes": round(age, 1),
        "stale": age > STALE_AFTER_MIN,
    }
    if age > STALE_AFTER_MIN:
        out["note"] = (
            f"Achtung: Der Standort ist {round(age)} Minuten alt (älter als {STALE_AFTER_MIN} Minuten) und "
            "möglicherweise nicht mehr aktuell – das Handy hat keinen frischeren gemeldet."
        )
    return out
