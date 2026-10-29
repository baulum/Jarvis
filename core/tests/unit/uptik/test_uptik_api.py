"""REST API: status, connector switch, accounts, upload into the inbox, validate, publish → approval, queue/history,
cancel, resume; the phone reaches the same endpoints through the gateway allowlist."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from jarvis.mobile.gateway import mobile_path_allowed
from jarvis.tools.registry import ToolContext
from jarvis.uptik.api import require_sir, router

from .conftest import OWNER, T0, approve, connected


@pytest.fixture
async def api(uptik: SimpleNamespace) -> AsyncIterator[SimpleNamespace]:
    async def configure(
        cid: str, *, actor: str, enabled: bool | None = None, settings: dict[str, Any] | None = None
    ) -> None:
        if enabled is not None:
            uptik.conn.enabled = enabled
        if settings:
            uptik.conn.settings = {**uptik.conn.settings, **settings}

    uptik.state.connectors.configure = configure
    app = FastAPI()
    app.include_router(router)
    app.state.jarvis = uptik.state
    app.dependency_overrides[require_sir] = lambda: OWNER
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        uptik.client = client
        yield uptik


async def test_connector_switch_accounts_and_publish_flow(api: SimpleNamespace) -> None:
    c = api.client
    r = await c.put("/api/uptik/connector", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert (await c.get("/api/uptik/accounts")).status_code == 409
    r = await c.put("/api/uptik/connector", json={"enabled": True, "settings": {"max_parallel": 1}})
    assert r.json()["active"] is True and r.json()["settings"]["max_parallel"] == 1

    r = await c.post("/api/uptik/accounts", json={"platform": "tiktok", "label": "PJE"})
    assert r.status_code == 201 and r.json()["status"] == "not_connected"
    r = await c.post(f"/api/uptik/accounts/{r.json()['id']}/connect")
    assert r.json()["status"] == "approval_required" and "anmelden" in r.json()["summary"]
    acct = await connected(api, "instagram_reels", "IG")
    r = await c.patch(f"/api/uptik/accounts/{acct['id']}", json={"autopost": True})
    assert r.status_code == 200 and r.json()["autopost"] is True

    r = await c.post("/api/uptik/uploads", params={"name": "../../Clip 1.mp4"}, content=b"\x00" * 2048)
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["source"] == "upload" and "/" not in item["name"] and item["name"].endswith("-Clip 1.mp4")
    assert (await c.post("/api/uptik/uploads", params={"name": "x.exe"}, content=b"MZ")).status_code == 415
    [listed] = (await c.get("/api/uptik/inbox")).json()
    assert listed["id"] == item["id"]

    body = {"video": f"inbox:{item['id']}", "caption": "Hallo", "hashtags": ["#web"], "accounts": [acct["id"]]}
    r = await c.post("/api/uptik/validate", json=body)
    assert r.json()["ok"] is True and r.json()["targets"][0]["label"] == "IG"
    r = await c.post("/api/uptik/validate", json={**body, "caption": "x" * 2300})
    assert r.json()["ok"] is False and "2200" in r.json()["problems"]

    r = await c.post("/api/uptik/publish", json={**body, "publish_at": "2026-11-02 20:00"})
    assert r.status_code == 200 and r.json()["status"] == "approval_required"
    assert "02.11.2026 20:00" in r.json()["summary"] and "IG" in r.json()["summary"]
    await api.executor.decide(uuid.UUID(r.json()["approval_id"]), True, OWNER)
    [q] = (await c.get("/api/uptik/queue")).json()
    assert q["status"] == "queued" and q["scheduled"] is True
    r = await c.post(f"/api/uptik/posts/{q['id']}/cancel")
    assert r.json()["status"] == "ok"
    [h] = (await c.get("/api/uptik/history")).json()
    assert h["status"] == "cancelled"
    detail = (await c.get(f"/api/uptik/posts/{q['id']}")).json()
    assert [e["kind"] for e in detail["events"]] == ["queued", "cancelled"]


async def test_resume_needs_confirmation(api: SimpleNamespace) -> None:
    acct = await connected(api, "tiktok", "PJE")
    api.web.plans[acct["id"]] = ["captcha"]
    res = await api.executor.execute(
        "uptik_publish_now", {"video": str(api.video), "caption": "x", "accounts": [acct["id"]]},
        ToolContext(principal=OWNER),
    )  # fmt: skip
    await approve(api, res)
    await api.svc.tick(T0)
    st = (await api.client.get("/api/uptik/status")).json()
    assert st["tripped"] == [acct["id"]]
    assert (await api.client.post(f"/api/uptik/accounts/{acct['id']}/resume", json={})).status_code == 422
    r = await api.client.post(f"/api/uptik/accounts/{acct['id']}/resume", json={"confirm": True, "requeue": True})
    assert r.status_code == 200 and r.json()["requeued"] == 1 and r.json()["account"]["circuit_state"] == "ok"


def test_phone_reaches_the_same_endpoints() -> None:
    aid = "0f8fad5b-d9cb-469f-a165-70867728950e"
    for method, path in [
        ("GET", "/api/uptik/status"), ("PUT", "/api/uptik/connector"), ("POST", "/api/uptik/publish"),
        ("POST", "/api/uptik/uploads"), ("GET", "/api/uptik/queue"), ("GET", "/api/uptik/history"),
        ("PATCH", f"/api/uptik/accounts/{aid}"), ("POST", f"/api/uptik/accounts/{aid}/resume"),
        ("POST", f"/api/uptik/posts/{aid}/cancel"),
    ]:  # fmt: skip
        assert mobile_path_allowed(method, path), (method, path)
    assert not mobile_path_allowed("POST", "/api/uptik/accounts/../../x/connect")
