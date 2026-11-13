"""REST API of the Kundensuche against a fake Lead-Tool MCP server: reading, filters, errors, and the two actions
through the real ToolExecutor (approval mode, kill switch, ledger). Nothing here writes to any Lead-Tool data."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from jarvis.mobile.gateway import mobile_path_allowed
from jarvis.sales.api import router
from jarvis.security.auth import Principal

from .conftest import CAMPAIGN_A, CAMPAIGN_B, LEAD_1, LEAD_2, OWNER, STATUS_ENUM

MISSING = "99999999-9999-4999-8999-999999999999"


# ------------------------------------------------------------------ smoke: campaign list and status load
async def test_smoke_campaign_list_and_status_load(sales: SimpleNamespace) -> None:
    c = sales.client
    st = await c.get("/api/sales/status")
    assert st.status_code == 200, st.text
    body = st.json()
    assert body["connected"] is True and body["error"] is None and body["web_url"] == "http://127.0.0.1:3100"
    assert body["capabilities"] == {"read": True, "redraft": True, "create_campaign": True, "outbox_list": False}
    assert body["overview"]["review"] == 1 and body["overview"]["workers"][0]["online"] is True

    camps = await c.get("/api/sales/campaigns")
    assert camps.status_code == 200, camps.text
    assert [x["id"] for x in camps.json()] == [CAMPAIGN_B, CAMPAIGN_A]
    # only read tools were called, nothing that changes Lead-Tool data
    assert {n for n, _ in sales.fake.calls} <= {"status", "list_campaigns"}


# ------------------------------------------------------------------ reading
async def test_campaign_detail_and_not_found(sales: SimpleNamespace) -> None:
    c = sales.client
    r = await c.get(f"/api/sales/campaigns/{CAMPAIGN_A}")
    assert r.status_code == 200 and r.json()["source_runs"][0]["hits"] == 40
    r = await c.get(f"/api/sales/campaigns/{MISSING}")
    assert r.status_code == 404 and r.json()["detail"] == "Kampagne nicht gefunden."
    assert (await c.get("/api/sales/campaigns/nicht-eine-id")).status_code == 422


async def test_leads_filters_are_passed_to_the_lead_tool(sales: SimpleNamespace) -> None:
    c = sales.client
    r = await c.get("/api/sales/leads", params={"campaign_id": CAMPAIGN_B, "status": "AWAITING_APPROVAL", "limit": 10})
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["total"] == 1 and [x["id"] for x in page["leads"]] == [LEAD_1]
    assert sales.fake.calls_of("list_leads")[-1] == {
        "limit": 10,
        "offset": 0,
        "campaignId": CAMPAIGN_B,
        "status": "AWAITING_APPROVAL",
    }
    r = await c.get("/api/sales/leads", params={"limit": 1, "offset": 1})
    assert r.json()["offset"] == 1 and r.json()["count"] == 1 and r.json()["more"] == 0
    assert "campaignId" not in sales.fake.calls_of("list_leads")[-1]
    assert (await c.get("/api/sales/leads", params={"limit": 500})).status_code == 422
    assert (await c.get("/api/sales/leads", params={"status": "drop table"})).status_code == 422


async def test_lead_detail_review_statuses_outbox_analytics(sales: SimpleNamespace) -> None:
    c = sales.client
    d = (await c.get(f"/api/sales/leads/{LEAD_1}")).json()
    assert d["draft"]["subject"] == "Ihre Website" and d["can_redraft"] is True
    assert (await c.get(f"/api/sales/leads/{MISSING}")).status_code == 404

    q = (await c.get("/api/sales/review", params={"limit": 5})).json()
    assert [x["id"] for x in q] == [LEAD_2, LEAD_1]  # the Lead-Tool's order (best score first) is kept as delivered
    assert sales.fake.calls_of("review_queue")[-1] == {"limit": 5}

    statuses = (await c.get("/api/sales/statuses")).json()
    assert [s["id"] for s in statuses] == STATUS_ENUM  # from the Lead-Tool's own schema
    assert statuses[2]["label"] == "Wartet auf Freigabe"

    ob = (await c.get("/api/sales/outbox")).json()
    assert (
        ob["available"] is False
        and ob["preview_count"] == 2
        and ob["items"] == []
        and ob["outreach_mode"] == "draft_only"
    )

    an = (await c.get("/api/sales/analytics")).json()
    assert an["lead_total"] == 12 and an["tasks"]["review"] == 1

    ind = (await c.get("/api/sales/industries", params={"q": "Elektro"})).json()
    assert ind["items"] == [{"key": "electrician", "label": "Elektriker"}]
    assert sales.fake.calls_of("find_industry")[-1] == {"name": "Elektro"}


async def test_lead_tool_errors_are_scrubbed(sales: SimpleNamespace) -> None:
    sales.fake.fail["list_campaigns"] = "password authentication failed: postgres://pje:geheim123@localhost:5433/pje"
    r = await sales.client.get("/api/sales/campaigns")
    assert r.status_code == 502
    assert "geheim123" not in r.text and "pje:" not in r.text


async def test_not_connected_and_not_configured(sales: SimpleNamespace, tmp_path: object) -> None:
    c = sales.client
    sales.fake.session = None
    sales.fake.error = "McpError: Connection closed (postgres://u:pw@h/db)"
    st = (await c.get("/api/sales/status")).json()
    assert st["connected"] is False and st["overview"] is None and "pw@" not in st["error"]
    assert st["capabilities"]["redraft"] is False
    assert (await c.get("/api/sales/campaigns")).status_code == 503
    r = await c.post(f"/api/sales/leads/{LEAD_1}/redraft", json={"note": "Bitte kürzer", "request_id": "req-00000001"})
    assert r.status_code == 503
    assert sales.fake.calls_of("request_redraft") == []

    del sales.hub.servers["pje_sales_ai"]
    sales.hub.path.write_text("servers: {}\n", encoding="utf-8")
    st = (await c.get("/api/sales/status")).json()
    assert st["configured"] is False and st["enabled"] is False and "nicht eingerichtet" in st["error"]
    assert (await c.get("/api/sales/campaigns")).status_code == 409


async def test_disabled_read_tool_counts_as_unavailable(sales: SimpleNamespace) -> None:
    sales.state.registry.is_enabled = lambda spec: spec.name != "ext_pje_sales_ai_list_campaigns"
    assert (await sales.client.get("/api/sales/campaigns")).status_code == 503
    assert sales.fake.calls_of("list_campaigns") == []


# ------------------------------------------------------------------ acting (ToolExecutor only)
async def test_redraft_runs_through_executor_with_ledger(sales: SimpleNamespace) -> None:
    c = sales.client
    body = {"note": "Bitte kürzer und ohne Preis", "request_id": "req-00000001"}
    r = await c.post(f"/api/sales/leads/{LEAD_1}/redraft", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok" and r.json()["tool"] == "ext_pje_sales_ai_request_redraft"
    assert "data" not in r.json()
    assert sales.fake.calls_of("request_redraft") == [{"leadId": LEAD_1, "note": "Bitte kürzer und ohne Preis"}]

    # the same submission again (e.g. a retried request): the ledger answers, the Lead-Tool is not called twice
    r = await c.post(f"/api/sales/leads/{LEAD_1}/redraft", json=body)
    assert r.json()["status"] == "duplicate"
    assert len(sales.fake.calls_of("request_redraft")) == 1

    # a new submission with the same text is a new request
    r = await c.post(f"/api/sales/leads/{LEAD_1}/redraft", json={**body, "request_id": "req-00000002"})
    assert r.json()["status"] == "ok"
    assert len(sales.fake.calls_of("request_redraft")) == 2


async def test_redraft_waits_for_approval_in_strict_mode(sales: SimpleNamespace) -> None:
    sales.policy.update({"mode": "block_everything"})
    r = await sales.client.post(
        f"/api/sales/leads/{LEAD_1}/redraft", json={"note": "Bitte kürzer", "request_id": "req-00000003"}
    )
    assert r.status_code == 200 and r.json()["status"] == "approval_required", r.text
    assert r.json()["risk"] == "medium" and "request_redraft" in r.json()["summary"]
    assert sales.fake.calls_of("request_redraft") == []  # nothing happens before Sir approves

    res = await sales.executor.decide(uuid.UUID(r.json()["approval_id"]), True, OWNER)
    assert res["status"] == "executed"
    assert sales.fake.calls_of("request_redraft") == [{"leadId": LEAD_1, "note": "Bitte kürzer"}]


async def test_custom_deny_and_kill_switch_block_actions(sales: SimpleNamespace) -> None:
    c = sales.client
    sales.policy.update({"mode": "custom", "tool_overrides": {"ext_pje_sales_ai_*": "deny"}})
    r = await c.post(f"/api/sales/leads/{LEAD_1}/redraft", json={"note": "Bitte kürzer", "request_id": "req-00000004"})
    assert r.status_code == 403
    sales.policy.update({"mode": "yolo", "tool_overrides": {}})
    await sales.kill.engage("Test", "test")
    r = await c.post(f"/api/sales/leads/{LEAD_1}/redraft", json={"note": "Bitte kürzer", "request_id": "req-00000005"})
    assert r.status_code == 403 and "Not-Aus" in r.json()["detail"]
    assert sales.fake.calls_of("request_redraft") == []
    # reading stays possible during the kill switch
    assert (await c.get("/api/sales/campaigns")).status_code == 200


async def test_redraft_validation(sales: SimpleNamespace) -> None:
    c = sales.client
    assert (
        await c.post(f"/api/sales/leads/{LEAD_1}/redraft", json={"note": "kurz", "request_id": "req-00000006"})
    ).status_code == 422
    assert (
        await c.post(f"/api/sales/leads/{LEAD_1}/redraft", json={"note": "Bitte kürzer", "request_id": "x"})
    ).status_code == 422
    assert (
        await c.post("/api/sales/leads/kaputt/redraft", json={"note": "Bitte kürzer", "request_id": "req-00000007"})
    ).status_code == 422
    assert sales.fake.calls_of("request_redraft") == []


async def test_create_campaign_maps_arguments(sales: SimpleNamespace) -> None:
    body = {
        "region": " Augsburg ",
        "industry_key": "electrician",
        "target_count": 30,
        "select_top_n": 5,
        "channel": "email",
        "budget_usd": 3,
        "request_id": "req-00000008",
    }
    r = await sales.client.post("/api/sales/campaigns", json=body)
    assert r.status_code == 200 and r.json()["status"] == "ok", r.text
    assert sales.fake.calls_of("create_campaign") == [
        {
            "region": "Augsburg",
            "industryKey": "electrician",
            "targetCount": 30,
            "selectTopN": 5,
            "channel": "email",
            "budgetUsd": 3.0,
        }
    ]
    bad = {**body, "target_count": 5000, "request_id": "req-00000009"}
    assert (await sales.client.post("/api/sales/campaigns", json=bad)).status_code == 422


# ------------------------------------------------------------------ access
async def test_only_sir_reaches_the_api(sales: SimpleNamespace) -> None:
    from jarvis.api.deps import current_principal

    app = FastAPI()
    app.include_router(router)
    app.state.jarvis = sales.state
    for role, expected in (("viewer", 403), ("operator", 403), ("mobile_owner", 200)):
        app.dependency_overrides[current_principal] = lambda r=role: Principal(id="x", role=r, channel="test")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/sales/campaigns")).status_code == expected, role


def test_phone_gateway_allows_the_sales_paths() -> None:
    allowed = [
        ("GET", "/api/sales/status"),
        ("GET", "/api/sales/statuses"),
        ("GET", "/api/sales/campaigns"),
        ("GET", f"/api/sales/campaigns/{CAMPAIGN_A}"),
        ("GET", "/api/sales/leads"),
        ("GET", f"/api/sales/leads/{LEAD_1}"),
        ("GET", "/api/sales/review"),
        ("GET", "/api/sales/outbox"),
        ("GET", "/api/sales/analytics"),
        ("GET", "/api/sales/industries"),
        ("POST", f"/api/sales/leads/{LEAD_1}/redraft"),
        ("POST", "/api/sales/campaigns"),
    ]
    for method, path in allowed:
        assert mobile_path_allowed(method, path), (method, path)
    for method, path in [
        ("DELETE", f"/api/sales/leads/{LEAD_1}"),
        ("POST", f"/api/sales/leads/{LEAD_1}/approve"),
        ("POST", f"/api/sales/leads/{LEAD_1}"),
        ("GET", "/api/sales/../system/settings"),
    ]:
        assert not mobile_path_allowed(method, path), (method, path)


def test_real_app_mounts_the_sales_router() -> None:
    import inspect

    import jarvis.main as main

    assert main.sales_router is router
    assert "app.include_router(sales_router)" in inspect.getsource(main.create_app)
    paths = {getattr(r, "path", "") for r in router.routes}
    assert {"/api/sales/status", "/api/sales/campaigns", "/api/sales/leads/{lid}/redraft"} <= paths
