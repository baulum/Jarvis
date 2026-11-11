"""Anrufstil, Regel 3: the movement check – yes / no / unknown from two location fixes, stale or missing → unknown,
nothing persisted, and the check before a call."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from jarvis.mobile.call_style import (
    NOTE_NO_FIX,
    NOTE_ONE_FIX,
    NOTE_STALE,
    max_sentences_for,
    motion_before_call,
)
from jarvis.mobile.location import LocationStore
from jarvis.mobile.motion import (
    MAX_FIX_AGE_MIN,
    MAX_FIX_GAP_S,
    MIN_FIX_GAP_S,
    MOVING_SPEED_KMH,
    Motion,
    Sample,
    detect_motion,
    distance_m,
)
from jarvis.mobile.push import PushResult

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
LAT, LON = 52.5200, 13.4050
DEG_LAT_M = 111_195.0  # metres per degree of latitude


def at(seconds_ago: float, north_m: float = 0.0, accuracy: float | None = 10.0, **kw: Any) -> Sample:
    return Sample(LAT + north_m / DEG_LAT_M, LON, NOW - timedelta(seconds=seconds_ago), accuracy, **kw)


def test_threshold_is_the_named_constant() -> None:
    assert MOVING_SPEED_KMH == 20.0
    assert MAX_FIX_AGE_MIN == 10


def test_distance_is_roughly_right() -> None:
    assert distance_m(at(0), at(0, north_m=1000)) == pytest.approx(1000, rel=0.01)


def test_fast_between_two_fixes_is_movement() -> None:
    # 1 km in 2 minutes = 30 km/h
    assert detect_motion(at(125, 0), at(5, 1000), NOW) is Motion.MOVING


def test_standing_still_is_no_movement() -> None:
    assert detect_motion(at(125, 0), at(5, 3), NOW) is Motion.STILL


def test_walking_pace_is_no_movement() -> None:
    # 150 m in 2 minutes = 4.5 km/h
    assert detect_motion(at(125, 0), at(5, 150), NOW) is Motion.STILL


def test_just_below_and_above_the_threshold() -> None:
    gap = 120.0
    per_m = MOVING_SPEED_KMH / 3.6 * gap  # metres in 120 s at exactly the threshold
    assert detect_motion(at(gap + 5, 0), at(5, per_m * 0.5), NOW) is Motion.STILL
    assert detect_motion(at(gap + 5, 0), at(5, per_m * 1.5), NOW) is Motion.MOVING


def test_gps_error_as_large_as_the_distance_is_unknown() -> None:
    # 150 m in 30 s could be 18 km/h or 33 km/h with ±50 m error each → no verdict
    prev, cur = at(35, 0, accuracy=50), at(5, 150, accuracy=50)
    assert detect_motion(prev, cur, NOW) is Motion.UNKNOWN


def test_missing_current_fix_is_unknown() -> None:
    assert detect_motion(at(125), None, NOW) is Motion.UNKNOWN
    assert detect_motion(None, None, NOW) is Motion.UNKNOWN


def test_stale_current_fix_is_unknown_even_if_it_looked_fast() -> None:
    old = (MAX_FIX_AGE_MIN + 1) * 60
    assert detect_motion(at(old + 120, 0), at(old, 5000), NOW) is Motion.UNKNOWN


def test_fix_just_inside_the_age_limit_still_counts() -> None:
    age = MAX_FIX_AGE_MIN * 60 - 1
    assert detect_motion(at(age + 120, 0), at(age, 1000), NOW) is Motion.MOVING


def test_single_fix_is_unknown() -> None:
    assert detect_motion(None, at(5), NOW) is Motion.UNKNOWN


def test_fixes_too_close_in_time_are_unknown() -> None:
    assert detect_motion(at(5 + MIN_FIX_GAP_S - 1, 0), at(5, 500), NOW) is Motion.UNKNOWN


def test_fixes_too_far_apart_are_unknown() -> None:
    assert detect_motion(at(5 + MAX_FIX_GAP_S + 1, 0), at(5, 1000), NOW) is Motion.UNKNOWN


def test_reported_speed_is_used_when_the_source_has_one() -> None:
    assert detect_motion(None, at(5, speed_mps=15.0), NOW) is Motion.MOVING  # 54 km/h
    assert detect_motion(None, at(5, speed_mps=1.0), NOW) is Motion.STILL
    old = Sample(LAT, LON, NOW - timedelta(minutes=30), speed_mps=15.0)
    assert detect_motion(None, old, NOW) is Motion.UNKNOWN  # even a reported speed says nothing about now


def test_naive_timestamps_are_read_as_utc() -> None:
    naive_prev = Sample(LAT, LON, (NOW - timedelta(seconds=125)).replace(tzinfo=None), 10)
    naive_cur = Sample(LAT + 1000 / DEG_LAT_M, LON, (NOW - timedelta(seconds=5)).replace(tzinfo=None), 10)
    assert detect_motion(naive_prev, naive_cur, NOW) is Motion.MOVING


def test_sample_repr_never_shows_coordinates() -> None:
    assert str(LAT) not in repr(at(5)) and str(LON) not in repr(at(5))


def test_only_a_certain_yes_switches_to_driving_mode() -> None:
    assert max_sentences_for(Motion.MOVING) == 1
    assert max_sentences_for(Motion.STILL) == 3
    assert max_sentences_for(Motion.UNKNOWN) == 3  # no conclusion → normal length, not the driving mode


# ------------------------------------------------------------------------------------------------ location store
DEV = uuid.uuid4()


class FakePush:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[str] = []

    async def send_all(self, kind: str, payload: dict[str, Any], **kw: Any) -> list[PushResult]:
        self.sent.append(kind)
        assert payload == {}  # the request carries no data
        return [PushResult(str(DEV), self.ok, None if self.ok else "UNREGISTERED")]


def store(push: FakePush | None = None) -> LocationStore:
    return LocationStore(push or FakePush())  # type: ignore[arg-type]


def put(st: LocationStore, minutes_ago: float, north_m: float = 0.0, accuracy: float = 10.0) -> None:
    t = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    assert st.update(DEV, LAT + north_m / DEG_LAT_M, LON, accuracy, t)


def test_store_without_a_fix_is_unknown() -> None:
    assert store().motion() is Motion.UNKNOWN


def test_store_with_one_fix_is_unknown() -> None:
    st = store()
    put(st, 1)
    assert st.motion() is Motion.UNKNOWN


def test_store_with_two_fast_fixes_is_moving() -> None:
    st = store()
    put(st, 4, 0)
    put(st, 1, 1500)  # 1.5 km in 3 minutes = 30 km/h
    assert st.motion() is Motion.MOVING


def test_store_with_two_equal_fixes_is_still() -> None:
    st = store()
    put(st, 4, 0)
    put(st, 1, 4)
    assert st.motion() is Motion.STILL


def test_store_stale_fix_is_unknown() -> None:
    st = store()
    put(st, 25, 0)
    put(st, 22, 1500)
    assert st.motion() is Motion.UNKNOWN


def test_store_ignores_noise_fixes_seconds_apart() -> None:
    st = store()
    put(st, 4, 0)
    put(st, 1, 1500)
    put(st, 0.95, 1520)  # 3 s later: must not replace the reference fix, else the speed would be computed over 3 s
    assert st.motion() is Motion.MOVING


def test_store_sharing_off_forgets_the_reference_fix() -> None:
    st = store()
    put(st, 4, 0)
    put(st, 1, 1500)
    st.clear(DEV)
    assert st.motion() is Motion.UNKNOWN
    put(st, 1, 0)
    assert st.motion() is Motion.UNKNOWN  # the old fix did not survive


def test_store_motion_needs_the_same_device() -> None:
    st = store()
    t1 = datetime.now(UTC) - timedelta(minutes=4)
    t2 = datetime.now(UTC) - timedelta(minutes=1)
    st.update(uuid.uuid4(), LAT, LON, 10, t1)
    st.update(uuid.uuid4(), LAT + 1500 / DEG_LAT_M, LON, 10, t2)
    assert st.motion() is Motion.UNKNOWN


def test_nothing_about_movement_is_persisted() -> None:
    """The store keeps two fixes in plain attributes – no file, no database, no log line."""
    import inspect

    import jarvis.mobile.call_style as cs
    import jarvis.mobile.location as loc
    import jarvis.mobile.motion as mo

    for module in (mo, cs):
        src = inspect.getsource(module)
        assert "open(" not in src and "write_text" not in src and "session_scope" not in src
    assert "_previous" in inspect.getsource(loc.LocationStore.__init__)
    st = store()
    put(st, 4, 0)
    put(st, 1, 100)
    assert str(LAT) not in repr(st._fix) and str(LAT) not in repr(st._previous)


# ------------------------------------------------------------------------------------------ check before a call
async def test_check_without_location_service_is_unknown_with_note() -> None:
    res = await motion_before_call(None)
    assert res.motion is Motion.UNKNOWN and res.note == NOTE_NO_FIX


async def test_check_without_any_fix_asks_the_phone_and_says_so() -> None:
    push = FakePush()
    res = await motion_before_call(store(push), wait_s=0.05)
    assert push.sent == ["location_request"]
    assert res.motion is Motion.UNKNOWN and res.note == NOTE_NO_FIX


async def test_check_with_stale_fix_says_so() -> None:
    st = store()
    put(st, MAX_FIX_AGE_MIN + 5, 0)
    res = await motion_before_call(st, wait_s=0.05)
    assert res.motion is Motion.UNKNOWN and res.note == NOTE_STALE
    assert "Standort" in NOTE_STALE and str(MAX_FIX_AGE_MIN) in NOTE_STALE


async def test_check_with_one_fresh_fix_does_not_guess() -> None:
    st = store()
    put(st, 0.2)
    res = await motion_before_call(st, wait_s=0.05)
    assert res.motion is Motion.UNKNOWN and res.note == NOTE_ONE_FIX


async def test_check_detects_movement_without_a_note() -> None:
    st = store()
    put(st, 4, 0)
    put(st, 0.5, 1500)
    res = await motion_before_call(st, wait_s=0.05)
    assert res.motion is Motion.MOVING and res.note is None


async def test_check_asks_for_a_fresh_fix_and_uses_the_answer() -> None:
    st = store()
    put(st, 4, 0)  # old enough to need a refresh
    put(st, 3, 10)

    def answer() -> None:
        t = datetime.now(UTC)
        st.update(DEV, LAT + 1500 / DEG_LAT_M, LON, 10, t)

    class AnsweringPush(FakePush):
        async def send_all(self, kind: str, payload: dict[str, Any], **kw: Any) -> list[PushResult]:
            asyncio.get_running_loop().call_later(0.01, answer)
            return await super().send_all(kind, payload, **kw)

    st.push = AnsweringPush()  # type: ignore[assignment]
    res = await motion_before_call(st, wait_s=1.0)
    assert res.motion is Motion.MOVING


async def test_check_never_raises_and_never_blocks_the_call() -> None:
    class Broken:
        def latest(self) -> None:
            raise RuntimeError("boom 52.52 13.40")

        def motion(self) -> Motion:
            raise RuntimeError("boom")

    res = await motion_before_call(Broken())
    assert res.motion is Motion.UNKNOWN and res.note == NOTE_NO_FIX


async def test_check_is_bounded_in_time() -> None:
    class Hanging:
        def latest(self) -> None:
            return None

        async def request_fresh(self, timeout_s: float) -> bool:
            await asyncio.sleep(30)
            return True

        def motion(self) -> Motion:
            return Motion.UNKNOWN

    t0 = asyncio.get_running_loop().time()
    res = await motion_before_call(Hanging(), wait_s=30, timeout_s=0.1)
    assert res.motion is Motion.UNKNOWN
    assert asyncio.get_running_loop().time() - t0 < 2
