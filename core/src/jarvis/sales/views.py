"""Pure translation of the Lead-Tool's MCP answers (German keys, written for a language model) into the stable shape
of ``/api/sales``. No I/O here – everything is unit-tested with the answers the Lead-Tool gives today.

Missing fields become ``None`` instead of failing: the MCP server is a separate project and may add or drop keys.
Status labels and stations mirror the Lead-Tool's ``leadStatusLabels`` (packages/core/src/lead-state.ts) and
``stages`` (apps/web/src/lib/stages.ts); the MCP server does not deliver labels. Unknown statuses keep their key.
"""

from __future__ import annotations

import re
from typing import Any

STATUS_LABELS: dict[str, str] = {
    "DISCOVERED": "Gefunden",
    "CHECKING": "Website-Check läuft",
    "PREQUALIFIED": "Vorqualifiziert",
    "RESEARCHING": "Recherche läuft",
    "AUDITED": "Recherchiert",
    "QUALIFIED": "Qualifiziert",
    "NOT_SELECTED": "Nicht ausgewählt",
    "DRAFTING": "Nachricht in Arbeit",
    "AWAITING_APPROVAL": "Wartet auf Freigabe",
    "APPROVED": "Freigegeben",
    "REJECTED": "Abgelehnt",
    "CONTACTED": "Kontaktiert",
    "REPLIED": "Geantwortet",
    "INTERESTED": "Interessiert",
    "NOT_INTERESTED": "Kein Interesse",
    "LATER": "Später",
    "MEETING": "Termin",
    "PROPOSAL": "Angebot",
    "WON": "Gewonnen",
    "LOST": "Verloren",
    "DISQUALIFIED": "Aussortiert",
    "DO_NOT_CONTACT": "Nicht kontaktieren",
    "CHECK_FAILED": "Check fehlgeschlagen",
    "RESEARCH_FAILED": "Recherche fehlgeschlagen",
    "AUDIT_FAILED": "Audit fehlgeschlagen",
    "DRAFT_FAILED": "Entwurf fehlgeschlagen",
    "CONTACT_INVALID": "Kein Kontaktweg",
    "SEND_FAILED": "Versand fehlgeschlagen",
    "BUDGET_EXCEEDED": "Budget überschritten",
}

# (key, label, statuses) – the stations a lead passes through, in pipeline order.
STAGES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("found", "Gefunden", ("DISCOVERED",)),
    ("check", "Website-Check", ("CHECKING",)),
    ("prequal", "Vorqualifiziert", ("PREQUALIFIED",)),
    ("research", "Recherche & Audit", ("RESEARCHING", "AUDITED")),
    ("qualified", "Qualifiziert", ("QUALIFIED", "NOT_SELECTED")),
    ("draft", "Entwurf", ("DRAFTING",)),
    ("review", "Freigabe", ("AWAITING_APPROVAL", "APPROVED")),
    ("contact", "Kontakt", ("CONTACTED", "REPLIED", "INTERESTED", "MEETING", "PROPOSAL", "WON")),
)
OUT_STATUSES = ("DISQUALIFIED", "DO_NOT_CONTACT", "REJECTED", "NOT_INTERESTED", "LOST", "LATER")
ERROR_STATUSES = (
    "CHECK_FAILED",
    "RESEARCH_FAILED",
    "AUDIT_FAILED",
    "DRAFT_FAILED",
    "CONTACT_INVALID",
    "SEND_FAILED",
    "BUDGET_EXCEEDED",
)
# campaigns.status in the Lead-Tool (packages/db/src/schema.ts) and its labels (apps/web/src/components/ui.tsx)
CAMPAIGN_STATUS_LABELS = {
    "created": "angelegt",
    "discovering": "sucht",
    "processing": "verarbeitet",
    "done": "fertig",
    "failed": "fehlgeschlagen",
}
CAMPAIGN_ACTIVE = ("created", "discovering", "processing")

_CREDENTIALS_IN_URL = re.compile(r"([a-z][a-z0-9+.-]*://)[^\s/@:]*:[^\s/@]*@", re.IGNORECASE)
_SECRET_PARAM = re.compile(r"((?:password|passwd|token|secret|api[_-]?key)=)[^\s&\"']+", re.IGNORECASE)


def scrub(message: str, limit: int = 300) -> str:
    """Error texts from the Lead-Tool may contain a connection string; user and password never leave the Core."""
    out = _CREDENTIALS_IN_URL.sub(r"\1***@", message)
    out = _SECRET_PARAM.sub(r"\1***", out)
    return out[:limit]


def status_label(status: Any) -> str:
    return STATUS_LABELS.get(str(status), str(status))


def stage_of(status: str) -> str | None:
    for key, _label, statuses in STAGES:
        if status in statuses:
            return key
    if status in OUT_STATUSES:
        return "out"
    if status in ERROR_STATUSES:
        return "error"
    return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _counts(value: Any) -> dict[str, int]:
    return {str(k): _int(v) for k, v in _dict(value).items()}


def address_line(address: Any) -> str:
    a = _dict(address)
    street = " ".join(str(a[k]) for k in ("street", "houseNumber") if a.get(k))
    city = " ".join(str(a[k]) for k in ("postcode", "city") if a.get(k))
    return ", ".join(p for p in (street, city) if p)


# --------------------------------------------------------------------------- status
def overview(raw: Any) -> dict[str, Any]:
    r = _dict(raw)
    tasks = _dict(r.get("aufgaben"))
    return {
        "outreach_mode": r.get("outreachMode"),
        "review": _int(tasks.get("zurFreigabe")),
        "outbox_preview": _int(tasks.get("vorschauImPostausgang")),
        "inbox": _int(tasks.get("eingegangeneAntworten")),
        "campaigns_by_status": _counts(r.get("kampagnenNachStatus")),
        "workers": [
            {
                "name": w.get("name"),
                "online": bool(w.get("online")),
                "last_seen": w.get("letztesLebenszeichen"),
            }
            for w in map(_dict, _list(r.get("worker")))
        ],
    }


def statuses(enum: list[str]) -> list[dict[str, Any]]:
    return [{"id": s, "label": status_label(s), "stage": stage_of(s)} for s in enum]


# --------------------------------------------------------------------------- campaigns
def campaign(raw: Any) -> dict[str, Any]:
    c = _dict(raw)
    by_status = _counts(c.get("leadsNachStatus"))
    status = c.get("status")
    return {
        "id": c.get("id"),
        "name": c.get("name"),
        "status": status,
        "status_label": CAMPAIGN_STATUS_LABELS.get(str(status), str(status)),
        "active": status in CAMPAIGN_ACTIVE,
        "region": c.get("region"),
        "industry": c.get("branche"),
        "target_count": c.get("zielanzahl"),
        "sources": [str(s) for s in _list(c.get("quellen"))],
        "last_error": scrub(c["letzterFehler"]) if isinstance(c.get("letzterFehler"), str) else None,
        "created_at": c.get("erstellt"),
        "leads_by_status": by_status,
        "lead_count": sum(by_status.values()),
        "in_review": by_status.get("AWAITING_APPROVAL", 0),
    }


def campaign_detail(raw: Any) -> dict[str, Any]:
    c = _dict(raw)
    return {
        **campaign(c),
        "channel": c.get("kanal"),
        "top_n": c.get("topN"),
        "budget_usd": c.get("budgetUsd"),
        "cost_usd": c.get("kostenUsd"),
        "source_runs": [
            {
                "source": r.get("quelle"),
                "status": r.get("status"),
                "hits": r.get("treffer"),
                "new_companies": r.get("neueFirmen"),
                "merged": r.get("zusammengeführt"),
                "duration_ms": r.get("dauerMs"),
                "error": scrub(r["fehler"]) if isinstance(r.get("fehler"), str) else None,
                "started_at": r.get("gestartet"),
            }
            for r in map(_dict, _list(c.get("quellenläufe")))
        ],
    }


# --------------------------------------------------------------------------- leads
def lead_row(raw: Any) -> dict[str, Any]:
    r = _dict(raw)
    status = str(r.get("status"))
    return {
        "id": r.get("id"),
        "status": status,
        "status_label": status_label(status),
        "stage": stage_of(status),
        "last_error": scrub(r["lastError"]) if isinstance(r.get("lastError"), str) else None,
        "campaign_id": r.get("campaignId"),
        "campaign_name": r.get("campaignName"),
        "name": r.get("name"),
        "website": r.get("website"),
        "phone": r.get("phone"),
        "email": r.get("email"),
        "address": address_line(r.get("address")),
        "score": r.get("score"),
        "priority": r.get("priorität"),
    }


def lead_page(raw: Any) -> dict[str, Any]:
    r = _dict(raw)
    leads = [lead_row(x) for x in _list(r.get("leads"))]
    return {
        "total": _int(r.get("gesamt")),
        "offset": _int(r.get("offset")),
        "count": len(leads),
        "more": _int(r.get("weitere")),
        "leads": leads,
    }


def lead_detail(raw: Any) -> dict[str, Any]:
    r = _dict(raw)
    status = str(r.get("status"))
    company = _dict(r.get("firma"))
    score = r.get("score")
    draft = r.get("ausgewählterEntwurf")
    return {
        "id": r.get("id"),
        "campaign_name": r.get("kampagne"),
        "status": status,
        "status_label": status_label(status),
        "stage": stage_of(status),
        "last_error": scrub(r["letzterFehler"]) if isinstance(r.get("letzterFehler"), str) else None,
        "company": {
            "name": company.get("name"),
            "industry": company.get("branche"),
            "website": company.get("website"),
            "phone": company.get("telefon"),
            "email": company.get("email"),
            "address": address_line(company.get("adresse")),
        },
        "score": (
            {
                "value": _dict(score).get("wert"),
                "priority": _dict(score).get("priorität"),
                "version": _dict(score).get("version"),
            }
            if isinstance(score, dict)
            else None
        ),
        "draft": (
            {
                "subject": _dict(draft).get("betreff"),
                "body": _dict(draft).get("text"),
                "channel": _dict(draft).get("kanal"),
                "iteration": _dict(draft).get("runde"),
                "evidence_ids": [str(x) for x in _list(_dict(draft).get("belegIds"))],
            }
            if isinstance(draft, dict)
            else None
        ),
        "evidence": [
            {
                "id": e.get("id"),
                "kind": e.get("art"),
                "key": e.get("schlüssel"),
                "claim": e.get("aussage"),
                "source_url": e.get("quelle"),
                "confidence": e.get("sicherheit"),
            }
            for e in map(_dict, _list(r.get("belege")))
        ],
        "history": [
            {
                "from": h.get("von"),
                "to": h.get("nach"),
                "from_label": status_label(h["von"]) if h.get("von") else None,
                "to_label": status_label(h.get("nach")),
                "reason": h.get("grund"),
                "at": h.get("am"),
            }
            for h in map(_dict, _list(r.get("verlauf")))
        ],
        # the Lead-Tool's MCP has no approve/send tool on purpose: these happen in the Lead-Tool itself
        "can_redraft": status == "AWAITING_APPROVAL",
    }


def review_item(raw: Any) -> dict[str, Any]:
    r = _dict(raw)
    return {
        "id": r.get("id"),
        "name": r.get("firma"),
        "campaign_name": r.get("kampagne"),
        "score": r.get("score"),
        "since": r.get("seit"),
    }


def industries(raw: Any) -> dict[str, Any]:
    r = _dict(raw)
    return {
        "in_catalog": bool(r.get("imKatalog")),
        "hint": r.get("hinweis"),
        "items": [{"key": t.get("industryKey"), "label": t.get("label")} for t in map(_dict, _list(r.get("treffer")))],
    }


# --------------------------------------------------------------------------- analytics
def analytics(campaigns: list[dict[str, Any]], ov: dict[str, Any] | None) -> dict[str, Any]:
    """Current state summed from what the Lead-Tool already reports (``campaign`` views + ``overview``)."""
    totals: dict[str, int] = {}
    for c in campaigns:
        for status, n in c["leads_by_status"].items():
            totals[status] = totals.get(status, 0) + n
    lead_total = sum(totals.values())
    order = [s for _k, _l, sts in STAGES for s in sts] + list(OUT_STATUSES) + list(ERROR_STATUSES)
    known = [s for s in order if totals.get(s)]
    unknown = sorted(s for s in totals if s not in order and totals[s])
    campaigns_by_status: dict[str, int] = {}
    for c in campaigns:
        key = str(c["status"])
        campaigns_by_status[key] = campaigns_by_status.get(key, 0) + 1
    return {
        "basis": f"Aktueller Stand aus {len(campaigns)} Kampagnen (das Lead-Tool liefert höchstens die 50 neuesten).",
        "lead_total": lead_total,
        "by_status": [
            {"status": s, "label": status_label(s), "stage": stage_of(s), "count": totals[s]} for s in known + unknown
        ],
        "stages": [
            {"key": key, "label": label, "count": sum(totals.get(s, 0) for s in sts)} for key, label, sts in STAGES
        ],
        "off_track": {
            "out": sum(totals.get(s, 0) for s in OUT_STATUSES),
            "error": sum(totals.get(s, 0) for s in ERROR_STATUSES),
        },
        "campaigns": {
            "total": len(campaigns),
            "by_status": [
                {"status": s, "label": CAMPAIGN_STATUS_LABELS.get(s, s), "count": n}
                for s, n in sorted(campaigns_by_status.items(), key=lambda kv: -kv[1])
            ],
        },
        "per_campaign": [
            {
                "id": c["id"],
                "name": c["name"],
                "status": c["status"],
                "status_label": c["status_label"],
                "leads": c["lead_count"],
                "in_review": c["in_review"],
                "contacted": sum(c["leads_by_status"].get(s, 0) for s in STAGES[-1][2]),
                "errors": sum(c["leads_by_status"].get(s, 0) for s in ERROR_STATUSES),
            }
            for c in campaigns
        ],
        "tasks": (
            {"review": ov["review"], "outbox_preview": ov["outbox_preview"], "inbox": ov["inbox"]} if ov else None
        ),
    }
