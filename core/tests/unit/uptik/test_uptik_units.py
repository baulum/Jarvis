"""Unit tests: queue (order, lease, restart recovery), retry policy, scheduling, per-platform validation, circuit
breaker detection and states, session encryption."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from jarvis.tools.executor import ToolUserError
from jarvis.uptik.circuit import LOGIN_REQUIRED, detect
from jarvis.uptik.model import PostContent, VideoInfo, normalize_hashtags
from jarvis.uptik.sessions import SessionVault
from jarvis.uptik.store import UptikStore
from jarvis.uptik.timing import RetryPolicy, at_local_time, iso, parse_publish_at
from jarvis.uptik.validation import OPTIONS, merged_options, options_schema, validate

T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


def row(group: str, account: str, run_at: datetime, **kw: Any) -> dict[str, Any]:
    return {
        "group_id": group, "account_id": account, "platform": "tiktok", "video_path": "/v.mp4", "video_name": "v.mp4",
        "video_size": 10, "run_at": iso(run_at), **kw,
    }  # fmt: skip


@pytest.fixture
async def store(tmp_path: Path) -> Any:
    s = UptikStore(tmp_path / "u.db")
    await s.open()
    yield s
    s.close()


# ------------------------------------------------------------------ queue
async def test_queue_order_due_and_idempotent_enqueue(store: UptikStore) -> None:
    later = await store.enqueue([row("g1", "a1", T0 + timedelta(hours=2), scheduled=True)])
    first = await store.enqueue([row("g2", "a1", T0), row("g2", "a2", T0)])
    same = await store.enqueue([row("g2", "a1", T0)])  # repeated approval/hand-over: no second post
    assert same[0]["id"] == first[0]["id"]
    assert len(await store.posts()) == 3

    due = await store.due(T0)
    assert [p["id"] for p in due] == [first[0]["id"], first[1]["id"]]  # insertion order at the same time
    assert later[0]["id"] not in [p["id"] for p in due]
    assert [p["id"] for p in await store.due(T0 + timedelta(hours=3))][-1] == later[0]["id"]


async def test_lease_is_exclusive_and_counts_attempts(store: UptikStore) -> None:
    [p] = await store.enqueue([row("g", "a", T0)])
    leased = await store.lease(p["id"], T0)
    assert leased and leased["status"] == "running" and leased["attempts"] == 1
    assert await store.lease(p["id"], T0) is None
    assert await store.due(T0) == []


async def test_restart_recovery_requeues_but_never_reposts_after_submit(tmp_path: Path) -> None:
    s = UptikStore(tmp_path / "u.db")
    await s.open()
    a, b = await s.enqueue([row("g", "a1", T0), row("g", "a2", T0)])
    await s.lease(a["id"], T0)
    await s.update_post(a["id"], stage="uploading")
    await s.lease(b["id"], T0)
    await s.update_post(b["id"], stage="submitting")
    s.close()  # the Core died here

    s2 = UptikStore(tmp_path / "u.db")
    rec = await s2.recover()
    assert rec == {"requeued": [a["id"]], "needs_review": [b["id"]]}
    assert (await s2.post(a["id"]))["status"] == "queued"
    assert (await s2.post(b["id"]))["status"] == "needs_review"
    assert [p["id"] for p in await s2.due(T0)] == [a["id"]]
    assert [e["kind"] for e in await s2.events(b["id"])][-1] == "recovered"
    s2.close()


async def test_cancel_only_before_publishing(store: UptikStore) -> None:
    a, b = await store.enqueue([row("g", "a1", T0), row("g", "a2", T0)])
    assert await store.cancel(a["id"])
    await store.lease(b["id"], T0)
    assert not await store.cancel(b["id"])  # running: too late


# ------------------------------------------------------------------ retry policy
def test_retry_policy_exponential_capped_and_bounded() -> None:
    p = RetryPolicy(max_attempts=4, base_s=60, factor=2, max_s=300, jitter=0)
    assert [p.delay(n) for n in (1, 2, 3, 4, 5)] == [60, 120, 240, 300, 300]
    assert p.should_retry(3) and not p.should_retry(4)
    j = RetryPolicy(base_s=100, jitter=0.1)
    assert j.delay(1, rand=lambda: 0.0) == pytest.approx(90) and j.delay(1, rand=lambda: 1.0) == pytest.approx(110)


# ------------------------------------------------------------------ scheduling (Europe/Berlin)
def test_publish_time_is_berlin_wall_clock() -> None:
    assert parse_publish_at("2026-11-02 18:30", T0) == datetime(2026, 11, 2, 17, 30, tzinfo=UTC)  # CET +1
    summer = datetime(2026, 7, 1, 8, 0, tzinfo=UTC)
    assert parse_publish_at("2026-07-01T18:30", summer) == datetime(2026, 7, 1, 16, 30, tzinfo=UTC)  # CEST +2
    assert parse_publish_at("2026-11-02T20:00:00+00:00", T0) == datetime(2026, 11, 2, 20, 0, tzinfo=UTC)


def test_publish_time_rejects_past_gap_far_future_and_garbage() -> None:
    with pytest.raises(ToolUserError, match="Vergangenheit"):
        parse_publish_at("2026-11-02 10:00", T0)  # = 09:00 UTC = now
    spring = datetime(2026, 3, 1, tzinfo=UTC)
    with pytest.raises(ToolUserError, match="Zeitumstellung"):
        parse_publish_at("2026-03-29 02:30", spring)
    with pytest.raises(ToolUserError, match="ein Jahr"):
        parse_publish_at("2028-01-01 10:00", T0)
    with pytest.raises(ToolUserError):
        parse_publish_at("morgen früh", T0)
    with pytest.raises(ToolUserError, match="Zeitzone"):
        parse_publish_at("2026-11-02T18:30:00.5", T0)


def test_ambiguous_autumn_hour_means_summer_time() -> None:
    now = datetime(2026, 10, 1, tzinfo=UTC)
    assert parse_publish_at("2026-10-25 02:30", now) == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)


def test_next_local_time() -> None:
    assert at_local_time("18:00", T0) == datetime(2026, 11, 2, 17, 0, tzinfo=UTC)
    assert at_local_time("08:00", T0) == datetime(2026, 11, 3, 7, 0, tzinfo=UTC)  # 10:00 Berlin already passed


# ------------------------------------------------------------------ validation per platform
def vid(name: str = "a.mp4", size: int = 10_000, dur: float | None = 20, w: int = 1080, h: int = 1920) -> VideoInfo:
    return VideoInfo(f"/x/{name}", name, size, "0" * 64, dur, w, h, True)


def test_validation_per_platform() -> None:
    ok = PostContent("Hallo Welt", ["#web"], "Titel")
    assert validate("tiktok", ok, vid()) == []
    assert validate("instagram_reels", ok, vid()) == []
    assert validate("youtube_shorts", ok, vid()) == []

    long = PostContent("x" * 2300)
    assert any("2200" in p for p in validate("tiktok", long, vid()))
    assert validate("youtube_shorts", long, vid()) == ["YouTube Shorts: Titel fehlt"]
    assert any("Titel hat" in p for p in validate("youtube_shorts", PostContent("x", title="t" * 101), vid()))
    assert any("spitzen" in p for p in validate("youtube_shorts", PostContent("x", title="<b>"), vid()))
    assert any("30" in p for p in validate("instagram_reels", PostContent("x", [f"#t{i}" for i in range(31)]), vid()))
    assert any("Caption fehlt" in p for p in validate("tiktok", PostContent(""), vid()))

    assert any(".webm" in p for p in validate("instagram_reels", ok, vid("a.webm")))
    assert any("180 s" in p for p in validate("instagram_reels", ok, vid(dur=200)))
    assert validate("tiktok", ok, vid(dur=200)) == []
    assert any("kürzer" in p for p in validate("tiktok", ok, vid(dur=1)))
    assert any("hochkant" in p for p in validate("youtube_shorts", ok, vid(w=1920, h=1080)))
    assert any("GB" in p for p in validate("instagram_reels", ok, vid(size=2 * 1024**3)))
    assert any("leer" in p for p in validate("tiktok", ok, vid(size=0)))
    no_track = vid()
    no_track.has_video = False
    assert any("Videospur" in p for p in validate("tiktok", ok, no_track))


def test_options_validation_and_defaults() -> None:
    assert merged_options("tiktok", {"visibility": "private"})["allow_comments"] is True
    bad = PostContent("x", options={"visibility": "everyone", "allow_comments": "nein", "foo": 1})
    problems = validate("tiktok", bad, None)
    assert len(problems) == 3
    assert set(options_schema()["properties"]) == set(OPTIONS)


def test_hashtags_normalized() -> None:
    assert normalize_hashtags("#Web, kmu  #web #Wolnzach") == ["#Web", "#kmu", "#Wolnzach"]
    assert normalize_hashtags(["#München", "ki_2026"]) == ["#München", "#ki_2026"]
    with pytest.raises(ToolUserError):
        normalize_hashtags(["#hallo-welt"])


# ------------------------------------------------------------------ circuit breaker
@pytest.mark.parametrize(
    ("url", "text", "statuses", "code"),
    [
        ("https://www.tiktok.com/tiktokstudio/upload", "Please complete the CAPTCHA", [], "captcha"),
        ("https://www.tiktok.com/tiktokstudio/upload", "Drag the slider to fit the puzzle", [], "captcha"),
        ("https://www.instagram.com/challenge/123/", "", [], "checkpoint"),
        ("https://www.instagram.com/", "Bestätige, dass du es bist", [], "checkpoint"),
        ("https://accounts.google.com/x", "2-Step Verification: enter the code we sent", [], "verification"),
        ("https://www.instagram.com/", "Gib den Code ein, den wir dir per SMS geschickt haben", [], "verification"),
        ("https://www.tiktok.com/", "Too many requests. Try again later.", [], "rate_limit"),
        ("https://www.instagram.com/", "Wir schränken bestimmte Aktivitäten ein", [], "unusual_activity"),
        ("https://www.tiktok.com/", "We noticed unusual activity", [], "unusual_activity"),
        ("https://www.tiktok.com/", "", [200, 429], "http_429"),
    ],
)
def test_roadblocks_are_detected(url: str, text: str, statuses: list[int], code: str) -> None:
    sig = detect(url, text, statuses)
    assert sig is not None and sig.code == code
    assert sig.error().error_class == "manual_review"


def test_normal_pages_do_not_trip() -> None:
    assert detect("https://www.tiktok.com/tiktokstudio/upload", "Upload video – Select file – Post", [200]) is None
    assert detect("https://studio.youtube.com/", "Kanal-Dashboard Videos hochladen", []) is None
    assert LOGIN_REQUIRED.error().code == "login_required"


async def test_circuit_trip_blocks_only_that_account_and_waits_for_sir(store: UptikStore) -> None:
    a1 = await store.create_account("tiktok", "PJE")
    a2 = await store.create_account("instagram_reels", "PJE IG")
    p1, p2 = await store.enqueue([row("g", a1["id"], T0), row("g2", a1["id"], T0 + timedelta(hours=1))])
    [p3] = await store.enqueue([row("g", a2["id"], T0)])
    sig = detect("", "captcha", [])
    assert sig is not None
    assert await store.trip(a1["id"], sig, p1["id"]) == 2
    acct = await store.account(a1["id"])
    assert acct and acct["circuit_state"] == "tripped" and acct["circuit_code"] == "captcha"
    assert {(await store.post(p["id"]))["status"] for p in (p1, p2)} == {"blocked"}
    assert (await store.post(p3["id"]))["status"] == "queued"
    assert [p["id"] for p in await store.due(T0 + timedelta(days=30))] == [p3["id"]]  # no timer re-opens it

    assert await store.reset_circuit(a1["id"], requeue=False, actor="desktop:x") == 0
    assert (await store.post(p1["id"]))["status"] == "blocked"
    await store.trip(a1["id"], sig)
    assert await store.reset_circuit(a1["id"], requeue=True, actor="desktop:x") == 2
    assert (await store.account(a1["id"]))["circuit_state"] == "ok"
    assert (await store.post(p1["id"]))["status"] == "queued"


# ------------------------------------------------------------------ session encryption
def test_session_vault_encrypts_binds_account_and_deletes(tmp_path: Path, secrets: dict[str, str]) -> None:
    vault = SessionVault(tmp_path / "sessions")
    aid, other = "11111111-2222-3333-4444-555555555555", "99999999-2222-3333-4444-555555555555"
    state = {"cookies": [{"name": "sessionid", "value": "TOP-SECRET-COOKIE-4711"}], "origins": []}
    vault.save(aid, state)
    blob = (tmp_path / "sessions" / f"{aid}.session").read_bytes()
    assert b"TOP-SECRET-COOKIE-4711" not in blob and b"sessionid" not in blob
    assert ((tmp_path / "sessions" / f"{aid}.session").stat().st_mode & 0o777) == 0o600
    assert "uptik-session-key" in secrets  # the key lives in the (OS) secret store, not next to the file
    assert vault.load(aid) == state

    (tmp_path / "sessions" / f"{other}.session").write_bytes(blob)  # copied to another account: does not open
    assert vault.load(other) is None
    secrets["uptik-session-key"] = "a-different-key"  # key rotated/lost: unreadable, no crash
    assert vault.load(aid) is None
    assert vault.delete(aid) and not vault.exists(aid) and vault.load(aid) is None
    with pytest.raises(ValueError):
        vault.save("../../etc/passwd", state)
    assert json.dumps(state)  # sanity
