"""REST API of the Kundensuche (``/api/sales``) – desktop app, browser client and phone use the same endpoints (Sir's
devices only). Reading translates the Lead-Tool's MCP answers; redrafting and starting a campaign run through the
ToolExecutor like the agent's calls (approvals, audit, kill switch, ledger). Approving or sending a draft does not
exist here: that stays in the Lead-Tool."""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from jarvis.api.deps import current_principal, get_state
from jarvis.app_state import AppState
from jarvis.db import session_scope
from jarvis.db.models import Approval
from jarvis.sales.service import SalesError, SalesService
from jarvis.security.auth import Principal

router = APIRouter(prefix="/api/sales")
StateDep = Depends(get_state)
PrincipalDep = Depends(current_principal)
SIR_ROLES = ("owner", "mobile_owner")
UUID_RE = r"^[0-9a-fA-F-]{36}$"
REQUEST_ID = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


async def require_sir(principal: Principal = PrincipalDep) -> Principal:
    if principal.role not in SIR_ROLES:
        raise HTTPException(403, "owner only")
    return principal


Sir = Depends(require_sir)


def _svc(st: AppState) -> SalesService:
    return SalesService(st)


def _http(e: SalesError) -> HTTPException:
    return HTTPException(e.status, e.message)


def _uuid(value: str) -> str:
    if not re.match(UUID_RE, value):
        raise HTTPException(422, "Ungültige ID")
    return value


# ------------------------------------------------------------------ reading
@router.get("/status")
async def status(st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    """Never fails for a missing connection: ``connected``/``error`` say why the area is empty."""
    return await _svc(st).status()


@router.get("/statuses")
async def statuses(st: AppState = StateDep, p: Principal = Sir) -> list[dict[str, Any]]:
    try:
        return await _svc(st).statuses()
    except SalesError as e:
        raise _http(e) from e


@router.get("/campaigns")
async def campaigns(st: AppState = StateDep, p: Principal = Sir) -> list[dict[str, Any]]:
    try:
        return await _svc(st).campaigns()
    except SalesError as e:
        raise _http(e) from e


@router.get("/campaigns/{cid}")
async def campaign(cid: str, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    try:
        return await _svc(st).campaign(_uuid(cid))
    except SalesError as e:
        raise _http(e) from e


@router.get("/leads")
async def leads(
    campaign_id: str | None = Query(default=None, pattern=UUID_RE),
    status: str | None = Query(default=None, pattern=r"^[A-Z_]{3,40}$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    st: AppState = StateDep,
    p: Principal = Sir,
) -> dict[str, Any]:
    """Status values are checked by the Lead-Tool's own enum (a newer Lead-Tool may know more than Jarvis' labels)."""
    try:
        return await _svc(st).leads(campaign_id=campaign_id, status=status, limit=limit, offset=offset)
    except SalesError as e:
        raise _http(e) from e


@router.get("/leads/{lid}")
async def lead(lid: str, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    try:
        return await _svc(st).lead(_uuid(lid))
    except SalesError as e:
        raise _http(e) from e


@router.get("/review")
async def review(
    limit: int = Query(default=50, ge=1, le=100), st: AppState = StateDep, p: Principal = Sir
) -> list[dict[str, Any]]:
    try:
        return await _svc(st).review(limit)
    except SalesError as e:
        raise _http(e) from e


@router.get("/outbox")
async def outbox(st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    try:
        return await _svc(st).outbox()
    except SalesError as e:
        raise _http(e) from e


@router.get("/analytics")
async def analytics(st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    try:
        return await _svc(st).analytics()
    except SalesError as e:
        raise _http(e) from e


@router.get("/industries")
async def industries(
    q: str = Query(min_length=2, max_length=60), st: AppState = StateDep, p: Principal = Sir
) -> dict[str, Any]:
    try:
        return await _svc(st).industries(q)
    except SalesError as e:
        raise _http(e) from e


# ------------------------------------------------------------------ acting (ToolExecutor only)
async def _act(st: AppState, p: Principal, action: str, args: dict[str, Any], request_id: str) -> dict[str, Any]:
    try:
        res = await _svc(st).act(p, action, args, request_id)
    except SalesError as e:
        raise _http(e) from e
    out = res.to_dict()
    out.pop("data", None)  # the MCP answer is wrapped for the model; the client re-reads the lead/campaign instead
    if res.status == "approval_required" and res.approval_id:
        async with session_scope() as s:
            ap = await s.get(Approval, res.approval_id)
            if ap is not None:
                out["summary"] = ap.summary
                out["risk"] = ap.risk
    if res.status in ("error", "denied"):
        raise HTTPException(400 if res.status == "error" else 403, res.message or "Fehler")
    return out


class RedraftBody(BaseModel):
    note: str = Field(min_length=5, max_length=500)
    request_id: str = REQUEST_ID


@router.post("/leads/{lid}/redraft")
async def redraft(lid: str, body: RedraftBody, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    """„Entwurf neu formulieren“ – the Lead-Tool's own action; the new draft waits for approval in the Lead-Tool."""
    args = {"leadId": _uuid(lid), "note": body.note.strip()}
    return await _act(st, p, "redraft", args, body.request_id)


class CampaignBody(BaseModel):
    region: str = Field(min_length=2, max_length=120)
    industry_key: str = Field(min_length=2, max_length=80)
    target_count: int = Field(default=50, ge=1, le=500)
    select_top_n: int = Field(default=10, ge=1, le=200)
    channel: Literal["email", "phone", "letter"] = "email"
    budget_usd: float | None = Field(default=None, ge=0, le=100)
    request_id: str = REQUEST_ID


@router.post("/campaigns")
async def create_campaign(body: CampaignBody, st: AppState = StateDep, p: Principal = Sir) -> dict[str, Any]:
    """Starts the Lead-Tool's lead search (external queries, later LLM costs) – the Lead-Tool's defaults for sources."""
    args: dict[str, Any] = {
        "region": body.region.strip(),
        "industryKey": body.industry_key,
        "targetCount": body.target_count,
        "selectTopN": body.select_top_n,
        "channel": body.channel,
    }
    if body.budget_usd is not None:
        args["budgetUsd"] = body.budget_usd
    return await _act(st, p, "create_campaign", args, body.request_id)
