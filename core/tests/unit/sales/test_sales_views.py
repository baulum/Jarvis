"""Data layer of the Kundensuche: translating the Lead-Tool's MCP answers (no I/O)."""

from __future__ import annotations

from jarvis.sales import views

from .conftest import (
    CAMPAIGN_A,
    CAMPAIGN_B,
    LEAD_1,
    LEAD_2,
    campaign_answer,
    campaigns_answer,
    lead_answer,
    lead_rows,
    status_answer,
)


def test_overview_maps_tasks_campaigns_and_workers() -> None:
    ov = views.overview(status_answer())
    assert ov == {
        "outreach_mode": "draft_only",
        "review": 1,
        "outbox_preview": 2,
        "inbox": 0,
        "campaigns_by_status": {"done": 1, "processing": 1},
        "workers": [{"name": "worker", "online": True, "last_seen": "2026-11-13T10:00:00.000Z"}],
    }


def test_overview_tolerates_missing_and_wrong_types() -> None:
    ov = views.overview({"aufgaben": {"zurFreigabe": "viele"}, "worker": "kaputt"})
    assert ov["review"] == 0 and ov["outbox_preview"] == 0 and ov["workers"] == [] and ov["campaigns_by_status"] == {}
    assert views.overview(None)["outreach_mode"] is None


def test_campaign_counts_labels_and_scrubbed_error() -> None:
    b, a = (views.campaign(c) for c in campaigns_answer())
    assert b["id"] == CAMPAIGN_B and b["status_label"] == "verarbeitet" and b["active"] is True
    assert b["lead_count"] == 5 and b["in_review"] == 1 and b["industry"] == "Elektriker"
    assert b["sources"] == ["osm", "gmaps_scraper"] and b["target_count"] == 50
    assert a["status_label"] == "fertig" and a["active"] is False
    # a database URL with user and password from the Lead-Tool never reaches a client
    assert a["last_error"] is not None and "geheim123" not in a["last_error"] and "pje:" not in a["last_error"]
    assert "postgres://***@127.0.0.1:5433/pje" in a["last_error"]


def test_campaign_detail_has_source_runs_and_costs() -> None:
    d = views.campaign_detail(campaign_answer(CAMPAIGN_A))
    assert d["channel"] == "email" and d["top_n"] == 10 and d["cost_usd"] == 0.42 and d["budget_usd"] == 5
    assert d["source_runs"] == [
        {
            "source": "osm",
            "status": "succeeded",
            "hits": 40,
            "new_companies": 30,
            "merged": 10,
            "duration_ms": 1200,
            "error": None,
            "started_at": "2026-11-12T09:00:01.000Z",
        }
    ]


def test_lead_page_and_rows() -> None:
    page = views.lead_page({"gesamt": 7, "offset": 5, "anzahl": 2, "weitere": 0, "leads": lead_rows()})
    assert (page["total"], page["offset"], page["count"], page["more"]) == (7, 5, 2, 0)
    first, second = page["leads"]
    assert first["status_label"] == "Wartet auf Freigabe" and first["stage"] == "review"
    assert first["address"] == "Hauptstraße 5, 80331 München" and first["priority"] == "A" and first["score"] == 78
    assert second["address"] == "" and second["score"] is None and second["stage"] == "found"


def test_lead_detail_with_draft_evidence_and_history() -> None:
    d = views.lead_detail(lead_answer(LEAD_1))
    assert d["company"]["name"] == "Elektro Huber GmbH" and d["company"]["phone"] == "+49 89 000000"
    assert d["score"] == {"value": 78, "priority": "A", "version": "v3"}
    assert d["draft"] == {
        "subject": "Ihre Website",
        "body": "Guten Tag Herr Huber, …",
        "channel": "email",
        "iteration": 1,
        "evidence_ids": ["e1"],
    }
    assert d["evidence"][0]["claim"] == "Die Website nutzt kein HTTPS." and d["evidence"][0]["kind"] == "fact"
    assert (
        d["history"][0]["from_label"] == "Nachricht in Arbeit" and d["history"][0]["to_label"] == "Wartet auf Freigabe"
    )
    assert d["history"][1]["from"] is None and d["history"][1]["from_label"] is None
    assert d["can_redraft"] is True
    other = views.lead_detail(lead_answer(LEAD_2))
    assert other["draft"] is None and other["can_redraft"] is False


def test_unknown_status_keeps_its_key() -> None:
    assert views.status_label("NEW_STATUS_FROM_FUTURE") == "NEW_STATUS_FROM_FUTURE"
    assert views.stage_of("NEW_STATUS_FROM_FUTURE") is None
    assert views.stage_of("DISQUALIFIED") == "out" and views.stage_of("SEND_FAILED") == "error"


def test_statuses_follow_the_given_enum() -> None:
    out = views.statuses(["AWAITING_APPROVAL", "X_NEW"])
    assert out == [
        {"id": "AWAITING_APPROVAL", "label": "Wartet auf Freigabe", "stage": "review"},
        {"id": "X_NEW", "label": "X_NEW", "stage": None},
    ]


def test_every_label_belongs_to_a_stage_or_off_track() -> None:
    for status in views.STATUS_LABELS:
        assert views.stage_of(status) is not None, status


def test_analytics_sums_current_state() -> None:
    camps = [views.campaign(c) for c in campaigns_answer()]
    a = views.analytics(camps, views.overview(status_answer()))
    assert a["lead_total"] == 12
    stages = {s["key"]: s["count"] for s in a["stages"]}
    assert stages["found"] == 3 and stages["review"] == 1 and stages["contact"] == 2
    assert a["off_track"] == {"out": 4, "error": 1}
    by_status = [s["status"] for s in a["by_status"]]
    # pipeline order first, statuses of a newer Lead-Tool at the end
    assert by_status == [
        "DISCOVERED",
        "AWAITING_APPROVAL",
        "CONTACTED",
        "DISQUALIFIED",
        "DRAFT_FAILED",
        "NEW_STATUS_FROM_FUTURE",
    ]
    assert a["campaigns"]["total"] == 2
    assert {c["status"]: c["label"] for c in a["campaigns"]["by_status"]} == {
        "processing": "verarbeitet",
        "done": "fertig",
    }
    per = {c["id"]: c for c in a["per_campaign"]}
    assert per[CAMPAIGN_B]["in_review"] == 1 and per[CAMPAIGN_B]["errors"] == 1
    assert per[CAMPAIGN_A]["contacted"] == 2
    assert a["tasks"] == {"review": 1, "outbox_preview": 2, "inbox": 0}
    assert "2 Kampagnen" in a["basis"]


def test_analytics_empty() -> None:
    a = views.analytics([], None)
    assert a["lead_total"] == 0 and a["by_status"] == [] and a["tasks"] is None
    assert all(s["count"] == 0 for s in a["stages"])


def test_scrub_removes_credentials_and_secret_parameters() -> None:
    msg = "fail postgresql://user:p%40ss@db.example:5432/x?sslmode=require&password=abc token=xyz OK"
    out = views.scrub(msg)
    assert "p%40ss" not in out and "abc" not in out and "xyz" not in out
    assert "postgresql://***@db.example:5432/x" in out and out.endswith("OK")
    assert len(views.scrub("x" * 1000)) == 300


def test_industries() -> None:
    out = views.industries(
        {
            "imKatalog": False,
            "treffer": [{"industryKey": "custom:imker", "label": "Imker"}],
            "hinweis": "Eigene Branche",
        }
    )
    assert out == {"in_catalog": False, "hint": "Eigene Branche", "items": [{"key": "custom:imker", "label": "Imker"}]}
