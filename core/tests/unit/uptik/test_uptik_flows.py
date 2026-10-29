"""Integration with the fake browser: account login (simulated), video hand-over, immediate and scheduled fan-out
over several platforms through the approval system, errors with retries, circuit breaker on a simulated captcha,
disabled connector and kill switch. Never a real platform."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.events import bus
from jarvis.tools.registry import ToolContext
from jarvis.uptik.handoff import SocialBridge, VideoClip
from jarvis.uptik.service import SYSTEM

from .conftest import OWNER, T0, approve, connected


def ctx() -> ToolContext:
    return ToolContext(principal=OWNER)


async def publish(ns: SimpleNamespace, **args: Any) -> Any:
    return await ns.executor.execute("uptik_publish_now", {"video": str(ns.video), **args}, ctx())


# ------------------------------------------------------------------ account connection
async def test_connect_account_simulated_login_stores_encrypted_session(uptik: SimpleNamespace) -> None:
    res = await uptik.executor.execute("uptik_connect_account", {"platform": "tiktok", "label": "PJE Systems"}, ctx())
    assert res.status == "approval_required"  # connecting an account goes through the approvals
    assert await uptik.svc.accounts() == []
    out = await approve(uptik, res)
    assert out["status"] == "executed" and out["result"]["data"]["status"] == "login_started"
    [acct] = await uptik.svc.accounts()
    await uptik.svc._logins[acct["id"]]

    acct = await uptik.svc.account(acct["id"])
    assert acct["status"] == "connected" and acct["autopost"] is False  # autopost stays off
    [login] = uptik.web.sessions
    assert login.headless is False and login.state is None  # visible window, fresh – Sir types himself
    assert uptik.svc.vault.load(acct["id"])["cookies"][0]["value"] == uptik.web.cookie
    raw = (uptik.svc.base / "sessions" / f"{acct['id']}.session").read_bytes()
    assert uptik.web.cookie.encode() not in raw
    assert "uptik.login" in uptik.notifier.events()


async def test_login_timeout_and_unavailable_browser(uptik: SimpleNamespace) -> None:
    uptik.web.login_after_polls = 10**9
    uptik.conn.settings["login_timeout_s"] = 60
    a = await uptik.svc.create_account("instagram_reels", "IG")

    async def tick_clock() -> None:
        while not uptik.svc._logins[a["id"]].done():
            uptik.clock.advance(seconds=30)
            await asyncio.sleep(0)

    await uptik.svc.start_login(a["id"])
    await asyncio.gather(tick_clock(), uptik.svc._logins[a["id"]])
    acct = await uptik.svc.account(a["id"])
    assert acct["status"] == "login_failed" and "Zeitüberschreitung" in acct["last_error"]
    assert not uptik.svc.vault.exists(a["id"])

    uptik.web.runner_ok = False
    with pytest.raises(Exception, match="nicht möglich"):
        await uptik.svc.start_login(a["id"])


# ------------------------------------------------------------------ publish now / scheduled, fan-out
async def test_publish_now_fans_out_after_approval(uptik: SimpleNamespace) -> None:
    tt = await connected(uptik, "tiktok", "PJE TikTok")
    ig = await connected(uptik, "instagram_reels", "PJE Instagram")
    res = await publish(
        uptik, caption="Website in 7 Tagen", hashtags="#webdesign #kmu", platforms=["tiktok", "instagram_reels"],
        per_platform={"instagram_reels": {"caption": "Reel-Text"}}, options={"tiktok": {"allow_duet": True}},
    )  # fmt: skip
    assert res.status == "approval_required"
    assert await uptik.svc.store.posts() == []  # nothing queued before Sir approves
    out = await approve(uptik, res)
    assert out["status"] == "executed"
    posts = await uptik.svc.store.posts()
    assert {p["account_id"] for p in posts} == {tt["id"], ig["id"]} and {p["status"] for p in posts} == {"queued"}
    assert len({p["group_id"] for p in posts}) == 1

    processed = await uptik.svc.tick(T0)
    assert len(processed) == 2
    hist = await uptik.svc.store.posts(["published"])
    assert len(hist) == 2
    texts = {a[0]: a[3] for a in uptik.web.actions if a[1] == "insert_text"}
    assert texts["tiktok"] == "Website in 7 Tagen\n\n#webdesign #kmu"
    assert texts["instagram_reels"] == "Reel-Text\n\n#webdesign #kmu"
    assert ("tiktok", "upload", str(uptik.video)) in uptik.web.actions
    assert all(s.closed for s in uptik.web.sessions)
    assert uptik.notifier.events().count("uptik.published") == 2
    # a second tick does nothing; replaying the approval does not post twice
    assert await uptik.svc.tick(T0 + timedelta(minutes=5)) == []
    again = await uptik.executor.decide(res.approval_id, True, OWNER)
    assert again["status"] == "executed" and len(await uptik.svc.store.posts()) == 2


async def test_scheduled_post_runs_at_berlin_time(uptik: SimpleNamespace) -> None:
    await connected(uptik, "tiktok", "PJE")
    res = await uptik.executor.execute(
        "uptik_schedule_post",
        {"video": str(uptik.video), "caption": "Abendpost", "platforms": ["tiktok"], "publish_at": "2026-11-02 18:30"},
        ctx(),
    )
    ap = await approve(uptik, res)
    assert ap["result"]["data"]["scheduled_for"] == "02.11.2026 18:30 (Berlin)"
    [p] = await uptik.svc.store.posts()
    assert p["scheduled"] and p["run_at"].startswith("2026-11-02T17:30")
    assert await uptik.svc.tick(T0) == []
    assert await uptik.svc.tick(datetime(2026, 11, 2, 17, 29, tzinfo=UTC)) == []
    assert await uptik.svc.tick(datetime(2026, 11, 2, 17, 30, tzinfo=UTC)) == [p["id"]]
    assert (await uptik.svc.store.post(p["id"]))["status"] == "published"


async def test_youtube_only_when_enabled_and_needs_title(uptik: SimpleNamespace) -> None:
    with pytest.raises(Exception, match="nicht verfügbar"):
        await uptik.svc.create_account("youtube_shorts", "YT")
    uptik.conn.settings["youtube_shorts_enabled"] = True
    await connected(uptik, "youtube_shorts", "YT")
    res = await publish(uptik, caption="x", platforms=["youtube_shorts"])
    assert res.status == "error" and "Titel fehlt" in res.message
    res = await publish(uptik, caption="x", title="Website in 7 Tagen", platforms=["youtube_shorts"],
                        options={"youtube_shorts": {"visibility": "unlisted"}})  # fmt: skip
    await approve(uptik, res)
    await uptik.svc.tick(T0)
    [p] = await uptik.svc.store.posts(["published"])
    assert ("youtube_shorts", "click", 'tp-yt-paper-radio-button[name="UNLISTED"]') in uptik.web.actions
    assert p["platform"] == "youtube_shorts"


async def test_validation_fails_before_the_approval(uptik: SimpleNamespace, tmp_path: Path) -> None:
    await connected(uptik, "instagram_reels", "IG")
    res = await publish(uptik, caption="x" * 2201, platforms=["instagram_reels"])
    assert res.status == "error" and "2200" in res.message
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(b"x" * 100)
    res = await uptik.executor.execute("uptik_publish_now", {"video": str(outside), "caption": "x",
                                                             "platforms": ["instagram_reels"]}, ctx())  # fmt: skip
    assert res.status == "error" and "freigegebenen Ordnern" in res.message
    res = await publish(uptik, caption="x", platforms=["tiktok"])
    assert res.status == "error" and "Kein verbundenes TikTok" in res.message


# ------------------------------------------------------------------ errors and retries
async def test_retryable_error_backs_off_then_succeeds(uptik: SimpleNamespace) -> None:
    acct = await connected(uptik, "tiktok", "PJE")
    uptik.web.plans[acct["id"]] = ["flaky", "ok"]
    await approve(uptik, await publish(uptik, caption="x", platforms=["tiktok"]))
    [p] = await uptik.svc.tick(T0)
    p1 = await uptik.svc.store.post(p)
    assert p1["status"] == "retry_wait" and p1["attempts"] == 1 and "simuliert" in p1["last_error"]
    nxt = datetime.fromisoformat(p1["next_attempt_at"])
    assert timedelta(seconds=53) <= nxt - T0 <= timedelta(seconds=67)  # 60 s ± 10 %
    assert await uptik.svc.tick(T0 + timedelta(seconds=30)) == []
    assert await uptik.svc.tick(T0 + timedelta(minutes=2)) == [p]
    p2 = await uptik.svc.store.post(p)
    assert p2["status"] == "published" and p2["attempts"] == 2


async def test_retries_are_bounded_and_errors_sanitized(uptik: SimpleNamespace) -> None:
    acct = await connected(uptik, "tiktok", "PJE")
    uptik.web.plans[acct["id"]] = ["crash", "flaky", "flaky", "ok"]
    await approve(uptik, await publish(uptik, caption="x", platforms=["tiktok"]))
    now = T0
    for _ in range(3):
        await uptik.svc.tick(now)
        now += timedelta(hours=2)
    [p] = await uptik.svc.store.posts()
    assert p["status"] == "failed" and p["attempts"] == 3
    events = await uptik.svc.store.events(p["id"])
    assert all("SECRET123" not in (e["detail"] or "") for e in events)
    assert "uptik.failed" in uptik.notifier.events()
    assert await uptik.svc.tick(now + timedelta(days=1)) == []


async def test_unconfirmed_publish_is_never_retried(uptik: SimpleNamespace) -> None:
    acct = await connected(uptik, "instagram_reels", "IG")
    uptik.web.plans[acct["id"]] = ["unconfirmed"]
    await approve(uptik, await publish(uptik, caption="x", platforms=["instagram_reels"]))
    [pid] = await uptik.svc.tick(T0)
    p = await uptik.svc.store.post(pid)
    assert p["status"] == "needs_review" and p["stage"] == "submitting"
    assert await uptik.svc.tick(T0 + timedelta(days=1)) == []
    assert "uptik.needs_review" in uptik.notifier.events()
    done = await uptik.svc.resolve_review(pid, "published", "https://www.instagram.com/reel/abc/", "desktop:x")
    assert done["status"] == "published" and done["result_url"].endswith("/abc/")


# ------------------------------------------------------------------ circuit breaker
@pytest.mark.parametrize("roadblock", ["captcha", "http_429", "checkpoint"])
async def test_roadblock_trips_circuit_for_that_account_only(uptik: SimpleNamespace, roadblock: str) -> None:
    tt = await connected(uptik, "tiktok", "PJE TikTok")
    ig = await connected(uptik, "instagram_reels", "PJE IG")
    uptik.web.plans[tt["id"]] = [roadblock]
    await approve(uptik, await publish(uptik, caption="eins", accounts=[tt["id"], ig["id"]]))
    await approve(uptik, await publish(uptik, caption="zwei", accounts=[tt["id"]]))
    await uptik.svc.tick(T0)

    acct = await uptik.svc.account(tt["id"])
    assert acct["circuit_state"] == "tripped" and acct["circuit_code"] == roadblock
    tt_posts = await uptik.svc.store.posts(account_id=tt["id"])
    assert {p["status"] for p in tt_posts} == {"blocked"} and len(tt_posts) == 2
    [ig_post] = await uptik.svc.store.posts(account_id=ig["id"])
    assert ig_post["status"] == "published"  # the other account is not affected
    alert = next(i for i in uptik.notifier.items if i["event"] == "uptik.circuit")
    assert alert["level"] == "error" and "manuell" in alert["body"]
    # the adapter did not touch the roadblock page after detecting it (no click after the upload)
    tt_actions = [a for a in uptik.web.actions if a[0] == "tiktok"]
    upload_at = max(i for i, a in enumerate(tt_actions) if a[1] == "upload")
    assert not [a for a in tt_actions[upload_at + 1 :] if a[1] in ("click", "insert_text", "press")]

    sessions_before = len(uptik.web.sessions)
    assert await uptik.svc.tick(T0 + timedelta(days=7)) == []  # no automatic retry, no timer
    assert len(uptik.web.sessions) == sessions_before
    res = await publish(uptik, caption="drei", accounts=[tt["id"]])
    assert res.status == "error" and "angehalten" in res.message

    out = await uptik.svc.resume(tt["id"], "desktop:sir", requeue=True)  # Sir checked the account
    assert out["requeued"] == 2
    await uptik.svc.tick(T0 + timedelta(days=7))
    await uptik.svc.tick(T0 + timedelta(days=7, minutes=1))
    assert {p["status"] for p in await uptik.svc.store.posts(account_id=tt["id"])} == {"published"}


async def test_expired_login_trips_and_reconnect_resets(uptik: SimpleNamespace) -> None:
    acct = await connected(uptik, "tiktok", "PJE")
    uptik.web.plans[acct["id"]] = ["login_required"]
    await approve(uptik, await publish(uptik, caption="x", platforms=["tiktok"]))
    await uptik.svc.tick(T0)
    a = await uptik.svc.account(acct["id"])
    assert a["circuit_code"] == "login_required" and a["status"] == "not_connected"
    await uptik.svc.start_login(acct["id"])
    await uptik.svc._logins[acct["id"]]
    a = await uptik.svc.account(acct["id"])
    assert a["status"] == "connected" and a["circuit_state"] == "ok"
    [p] = await uptik.svc.store.posts()
    assert p["status"] == "blocked"  # Sir decides whether it still goes out


# ------------------------------------------------------------------ video hand-over
def clip(ns: SimpleNamespace, ref: str = "vid-1") -> VideoClip:
    return VideoClip(
        path=str(ns.video), source="social", source_ref=ref, title="Captcha für Kunden",
        caption="Allgemein", hashtags=["#web"],
        captions={"tiktok": {"caption": "TikTok-Text", "hashtags": ["#tt"]},
                  "instagram_reels": {"caption": "IG-Text", "hashtags": ["#ig"]}},
    )  # fmt: skip


async def test_video_handoff_off_inbox_and_autopost(uptik: SimpleNamespace) -> None:
    assert await uptik.svc.on_video_ready(clip(uptik)) is None  # hand-over is off by default
    uptik.conn.settings["handoff_enabled"] = True
    tt = await connected(uptik, "tiktok", "PJE")
    ig = await connected(uptik, "instagram_reels", "IG")

    out = await uptik.svc.on_video_ready(clip(uptik))
    assert out == {"inbox_id": out["inbox_id"], "queued": False}  # autopost off: only into the inbox
    assert len(await uptik.svc.store.inbox()) == 1 and await uptik.svc.store.posts() == []

    await uptik.svc.update_account(tt["id"], "desktop:sir", autopost=True)
    await uptik.svc.update_account(ig["id"], "desktop:sir", autopost=True)
    out = await uptik.svc.on_video_ready(clip(uptik, "vid-2"))
    assert out["status"] == "approval_required"  # autopost still needs the approval system
    assert await uptik.svc.store.posts() == []
    again = await uptik.svc.on_video_ready(clip(uptik, "vid-2"))
    assert again["approval_id"] == out["approval_id"]  # same clip again: same approval, nothing new
    await uptik.executor.decide(uuid.UUID(out["approval_id"]), True, OWNER)
    posts = {p["platform"]: p for p in await uptik.svc.store.posts()}
    assert posts["tiktok"]["caption"] == "TikTok-Text" and posts["tiktok"]["hashtags"] == ["#tt"]
    assert posts["instagram_reels"]["caption"] == "IG-Text" and posts["tiktok"]["source"] == "social"
    assert posts["tiktok"]["approval_id"] == out["approval_id"]


async def test_handoff_scheduled_time_and_never_raises(uptik: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    uptik.conn.settings.update(handoff_enabled=True, handoff_publish_time="18:00")
    tt = await connected(uptik, "tiktok", "PJE")
    await uptik.svc.update_account(tt["id"], "x", autopost=True)
    out = await uptik.svc.on_video_ready(clip(uptik))
    await uptik.executor.decide(uuid.UUID(out["approval_id"]), True, OWNER)
    [p] = await uptik.svc.store.posts()
    assert p["scheduled"] and p["run_at"].startswith("2026-11-02T17:00")

    async def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("executor down")

    monkeypatch.setattr(uptik.executor, "execute", boom)
    assert await uptik.svc.on_video_ready(clip(uptik, "vid-9")) is None  # production is never disturbed
    missing = clip(uptik, "vid-10")
    missing.path = "/does/not/exist.mp4"
    assert await uptik.svc.on_video_ready(missing) is None


async def test_social_bridge_hands_over_ready_videos(uptik: SimpleNamespace) -> None:
    vid = uuid.uuid4()
    row = SimpleNamespace(
        id=vid, status="ready", title="ERROR 404", file_name=uptik.video.name, duration_s=10.8,
        captions={"tiktok": {"text": "Fehler 404 🙈", "hashtags": ["#webdesign"]},
                  "instagram": {"text": "IG", "hashtags": ["#Webdesign"]},
                  "youtube": {"title": "ERROR 404", "description": "YT", "hashtags": ["#Shorts"]}},
    )  # fmt: skip

    async def get(_id: Any) -> Any:
        return row if _id == vid else None

    uptik.state.social = SimpleNamespace(
        repo=SimpleNamespace(get=get), file_path=lambda name: uptik.video.parent / name, library_dir=uptik.video.parent
    )
    uptik.conn.settings["handoff_enabled"] = True
    bridge = SocialBridge(uptik.svc)
    bridge.start()
    try:
        await bus.publish("social.updated", video_id=str(vid), status="ready")
        for _ in range(100):
            if await uptik.svc.store.inbox():
                break
            await asyncio.sleep(0.01)
        [item] = await uptik.svc.store.inbox()
        assert item["source"] == "social" and item["source_ref"] == str(vid)
        assert item["captions"]["tiktok"]["caption"] == "Fehler 404 🙈"
        assert item["captions"]["youtube_shorts"]["title"] == "ERROR 404"
    finally:
        bridge.stop()


# ------------------------------------------------------------------ disabled connector, kill switch
async def test_disabled_connector_does_nothing(uptik: SimpleNamespace) -> None:
    await connected(uptik, "tiktok", "PJE")
    await approve(uptik, await publish(uptik, caption="x", platforms=["tiktok"]))
    uptik.conn.enabled = False
    uptik.conn.settings["handoff_enabled"] = True
    res = await publish(uptik, caption="y", platforms=["tiktok"])
    assert res.status == "denied"
    assert (await uptik.executor.execute("uptik_status", {}, ctx())).status == "denied"
    assert await uptik.svc.tick(T0) == []
    assert await uptik.svc.on_video_ready(clip(uptik)) is None
    [p] = await uptik.svc.store.posts()
    assert p["status"] == "queued"  # waits until the connector is on again
    uptik.conn.enabled = True
    assert await uptik.svc.tick(T0) == [p["id"]]


async def test_kill_switch_stops_the_worker(uptik: SimpleNamespace) -> None:
    await connected(uptik, "tiktok", "PJE")
    await approve(uptik, await publish(uptik, caption="x", platforms=["tiktok"]))
    await uptik.kill.engage("test", "pytest")
    assert await uptik.svc.tick(T0) == []
    await uptik.kill.release("pytest")
    assert len(await uptik.svc.tick(T0)) == 1


async def test_restart_recovers_queue(uptik: SimpleNamespace) -> None:
    acct = await connected(uptik, "tiktok", "PJE")
    await approve(uptik, await publish(uptik, caption="x", platforms=["tiktok"]))
    [p] = await uptik.svc.store.posts()
    await uptik.svc.store.lease(p["id"], T0)  # the Core dies while uploading
    await uptik.svc.start()  # connector start after the restart
    assert (await uptik.svc.store.post(p["id"]))["status"] == "queued"
    await uptik.svc.stop()
    assert await uptik.svc.tick(T0) == [p["id"]]
    assert (await uptik.svc.account(acct["id"]))["last_used_at"]


async def test_secrets_stay_out_of_db_notifications_and_status(uptik: SimpleNamespace) -> None:
    acct = await connected(uptik, "tiktok", "PJE")
    await approve(uptik, await publish(uptik, caption="x", platforms=["tiktok"]))
    await uptik.svc.tick(T0)
    cookie = uptik.web.cookie
    for f in uptik.svc.base.rglob("*"):
        if f.is_file():
            assert cookie.encode() not in f.read_bytes(), f
    assert cookie not in repr(uptik.notifier.items)
    assert cookie not in repr(await uptik.svc.status())
    assert uptik.svc.vault.load(acct["id"]) is not None


async def test_system_principal_cannot_bypass(uptik: SimpleNamespace) -> None:
    await connected(uptik, "tiktok", "PJE")
    res = await uptik.executor.execute("uptik_publish_now", {"video": str(uptik.video), "caption": "x",
                                                             "platforms": ["tiktok"]}, ToolContext(principal=SYSTEM))  # fmt: skip
    assert res.status == "approval_required"
    assert await uptik.svc.store.posts() == []
