"""REST API: dashboard, library with streaming, signed links, settings (schedule time), production, gateway."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from jarvis.mobile.gateway import mobile_path_allowed, remote_path_allowed
from jarvis.security.auth import Principal
from jarvis.social import links
from jarvis.social.api import require_sir, router

from .conftest import OWNER
from .test_social_pipeline import ready_video


@pytest.fixture
async def api(social: SimpleNamespace) -> AsyncIterator[SimpleNamespace]:
    app = FastAPI()
    app.include_router(router)
    app.state.jarvis = social.state
    app.dependency_overrides[require_sir] = lambda: OWNER
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        social.client = client
        social.app = app
        yield social


async def test_status_and_settings_drive_the_schedule(api: SimpleNamespace) -> None:
    r = await api.client.get("/api/social/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["settings"]["autopost_enabled"] is False and body["settings"]["email_recipient"] == "paulhoflich@gmail.com"
    assert [p["platform"] for p in body["platforms"]] == ["instagram", "facebook", "tiktok", "youtube"]
    assert not any(p["will_autopost"] for p in body["platforms"])

    r = await api.client.put("/api/social/settings", json={"production_time": "09:15", "email_enabled": False, "publish_time": "18:00"})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["schedule"]["time"] == "09:15" and s["schedule"]["cron"] == "15 9 * * *"
    assert s["email_enabled"] is False and s["publish_time"] == "18:00"
    r = await api.client.put("/api/social/settings", json={"daily_enabled": False})
    assert r.json()["schedule"]["enabled"] is False
    assert (await api.client.put("/api/social/settings", json={"production_time": "25:00"})).status_code == 422
    assert (await api.client.put("/api/social/settings", json={"email_recipient": "kein-mail"})).status_code == 400


async def test_library_stream_and_signed_download(api: SimpleNamespace, tiny_video: Path) -> None:
    vid = await ready_video(api, tiny_video)
    r = await api.client.get("/api/social/videos")
    [item] = r.json()["videos"]
    assert item["id"] == str(vid) and item["captions"]["instagram"]["hashtags"]

    r = await api.client.get(f"/api/social/videos/{vid}")
    assert r.json()["email_preview"]["subject"].startswith("PJE Social · ")

    r = await api.client.get(f"/api/social/videos/{vid}/file", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100 and r.headers["content-type"] == "video/mp4"

    r = await api.client.post(f"/api/social/videos/{vid}/link", json={"hours": 2})
    path = r.json()["path"]
    unauth = FastAPI()
    unauth.include_router(router)
    unauth.state.jarvis = api.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=unauth), base_url="http://test") as anon:
        ok = await anon.get(path)
        assert ok.status_code == 200 and ok.content[:12] == tiny_video.read_bytes()[:12]
        assert "attachment" in ok.headers["content-disposition"]
        bad = await anon.get(path[:-3] + "xyz")
        assert bad.status_code == 404
        assert (await anon.get(f"/api/social/videos/{vid}/file")).status_code == 401  # library itself needs a login


def test_links_expire_and_cannot_be_forged(secrets: dict[str, str]) -> None:
    import uuid

    vid = uuid.uuid4()
    token = links.sign(vid, 60)
    assert links.verify(token) == vid
    assert links.verify(token, now=time.time() + 120) is None
    payload, mac = token.split(".")
    other = links.sign(uuid.uuid4(), 60).split(".")[0]
    assert links.verify(f"{other}.{mac}") is None
    assert links.verify("garbage") is None


async def test_produce_regenerate_and_manual_post(api: SimpleNamespace, tiny_video: Path) -> None:
    r = await api.client.post("/api/social/produce", json={"hint": "Wetterbericht"})
    assert r.status_code == 200 and api.state.tasks.created[0]["params"]["hint"] == "Wetterbericht"
    vid = await ready_video(api, tiny_video)
    r = await api.client.post(f"/api/social/videos/{vid}/regenerate", json={})
    assert api.state.tasks.created[1]["params"]["parent_id"] == str(vid)
    r = await api.client.post(f"/api/social/videos/{vid}/publications/instagram/manual", json={"post_url": "https://www.instagram.com/reel/abc"})
    assert r.json()["status"] == "published"
    r = await api.client.post(f"/api/social/videos/{vid}/publish", json={"platform": "tiktok"})
    assert r.status_code == 200 and r.json()["status"] == "approval_required"  # never published without approval


async def test_only_sirs_devices(social: SimpleNamespace) -> None:
    app = FastAPI()
    app.include_router(router)
    app.state.jarvis = social.state
    for role, code in (("viewer", 403), ("mobile_owner", 200)):
        app.dependency_overrides.clear()
        from jarvis.api.deps import current_principal

        app.dependency_overrides[current_principal] = lambda role=role: Principal(id="d", role=role, channel="x")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            assert (await c.get("/api/social/settings")).status_code == code


def test_gateway_paths() -> None:
    vid = "0b9c3e5e-1c2d-4e5f-8a9b-0c1d2e3f4a5b"
    assert mobile_path_allowed("GET", "/api/social/status")
    assert mobile_path_allowed("GET", f"/api/social/videos/{vid}/file")
    assert mobile_path_allowed("POST", f"/api/social/videos/{vid}/publish")
    assert mobile_path_allowed("PUT", "/api/social/settings")
    assert not mobile_path_allowed("POST", "/api/social/oauth/instagram/start")  # connecting happens at the desktop
    assert not remote_path_allowed("GET", "/api/social/oauth/instagram/callback")
