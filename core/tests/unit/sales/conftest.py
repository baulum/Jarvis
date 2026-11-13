"""Test harness for the Kundensuche – nothing here touches the Lead-Tool or its database.

``FakeLeadTool`` stands in for the Lead-Tool's MCP server (``ExternalServer``): it lists the same tools with the same
annotations and answers with JSON in the exact shape of ``apps/mcp/src/queries.ts`` (German keys). The real
``MCPClientHub._register`` turns those tools into ``ext_pje_sales_ai_*`` specs, and Jarvis' approvals, ledger and audit
run for real on SQLite – so a redraft goes through the same executor path as in production.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import BigInteger, event
from sqlalchemy import types as sqltypes
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.sqlite import DATETIME as SQLITE_DATETIME
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

import jarvis.db as jdb
from jarvis.config import load_permissions
from jarvis.db.models import Approval, AuditEntry, ChatSession, ToolExecution
from jarvis.mcp.client import MCPClientHub
from jarvis.sales.api import require_sir, router
from jarvis.security.auth import Principal
from jarvis.security.guards import KillSwitch, rate_limiter
from jarvis.tools.approval_mode import ApprovalPolicy
from jarvis.tools.executor import ToolExecutor, ToolUserError
from jarvis.tools.registry import ToolRegistry

OWNER = Principal(id="desk-1", role="owner", channel="desktop", device_id=None)
CAMPAIGN_A = "11111111-1111-4111-8111-111111111111"
CAMPAIGN_B = "22222222-2222-4222-8222-222222222222"
LEAD_1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
LEAD_2 = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
STATUS_ENUM = ["DISCOVERED", "CHECKING", "AWAITING_APPROVAL", "CONTACTED", "DISQUALIFIED", "DRAFT_FAILED"]

MCP_CONFIG = """
servers:
  pje_sales_ai:
    description: Test
    enabled: true
    command: /bin/false
    web_url: http://127.0.0.1:3100/
    default_risk: medium
    risk_overrides:
      status: read
      find_industry: read
      list_campaigns: read
      get_campaign: read
      list_leads: read
      get_lead: read
      review_queue: read
      create_campaign: medium
      request_redraft: medium
"""


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_: Any, compiler: Any, **kw: Any) -> str:
    return "JSON"


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_: Any, compiler: Any, **kw: Any) -> str:
    return "INTEGER"


class AwareDateTime(SQLITE_DATETIME):
    def result_processor(self, dialect: Any, coltype: Any) -> Any:
        from datetime import UTC

        base = super().result_processor(dialect, coltype)

        def process(value: Any) -> Any:
            v = base(value) if base else value
            if v is not None and v.tzinfo is None:
                v = v.replace(tzinfo=UTC)
            return v

        return process


@pytest.fixture
async def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jarvis.db'}")
    engine.sync_engine.dialect.colspecs = {**engine.sync_engine.dialect.colspecs, sqltypes.DateTime: AwareDateTime}

    @event.listens_for(engine.sync_engine, "connect")
    def _fns(dbapi_conn: Any, _rec: Any) -> None:  # the audit chain's Postgres lock
        dbapi_conn.create_function("pg_advisory_xact_lock", 1, lambda _x: 0)

    tables = [Approval.__table__, ToolExecution.__table__, AuditEntry.__table__, ChatSession.__table__]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Approval.metadata.create_all(c, tables=tables))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(jdb, "_engine", engine)
    monkeypatch.setattr(jdb, "_sessionmaker", maker)
    yield maker
    await engine.dispose()


@pytest.fixture(autouse=True)
def fresh_rate_limits() -> Any:
    rate_limiter._hits.clear()
    yield
    rate_limiter._hits.clear()


# --------------------------------------------------------------------------- answers in the shape of queries.ts
def status_answer() -> dict[str, Any]:
    return {
        "outreachMode": "draft_only",
        "aufgaben": {"zurFreigabe": 1, "vorschauImPostausgang": 2, "eingegangeneAntworten": 0},
        "kampagnenNachStatus": {"done": 1, "processing": 1},
        "worker": [{"name": "worker", "online": True, "letztesLebenszeichen": "2026-11-13T10:00:00.000Z"}],
    }


def campaigns_answer() -> list[dict[str, Any]]:
    return [
        {
            "id": CAMPAIGN_B,
            "name": "Elektriker München",
            "status": "processing",
            "region": "München",
            "branche": "Elektriker",
            "zielanzahl": 50,
            "quellen": ["osm", "gmaps_scraper"],
            "letzterFehler": None,
            "erstellt": "2026-11-12T09:00:00.000Z",
            "leadsNachStatus": {"DISCOVERED": 3, "AWAITING_APPROVAL": 1, "DRAFT_FAILED": 1},
        },
        {
            "id": CAMPAIGN_A,
            "name": "Kfz-Werkstätten Augsburg",
            "status": "done",
            "region": "Augsburg",
            "branche": "Kfz-Werkstatt",
            "zielanzahl": 20,
            "quellen": ["osm"],
            "letzterFehler": "connect ECONNREFUSED postgres://pje:geheim123@127.0.0.1:5433/pje",
            "erstellt": "2026-11-10T09:00:00.000Z",
            "leadsNachStatus": {"CONTACTED": 2, "DISQUALIFIED": 4, "NEW_STATUS_FROM_FUTURE": 1},
        },
    ]


def campaign_answer(cid: str) -> dict[str, Any]:
    base = next(c for c in campaigns_answer() if c["id"] == cid)
    return {
        **base,
        "kanal": "email",
        "topN": 10,
        "budgetUsd": 5,
        "kostenUsd": 0.42,
        "quellenläufe": [
            {
                "quelle": "osm",
                "status": "succeeded",
                "treffer": 40,
                "neueFirmen": 30,
                "zusammengeführt": 10,
                "dauerMs": 1200,
                "fehler": None,
                "gestartet": "2026-11-12T09:00:01.000Z",
            }
        ],
    }


def lead_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": LEAD_1,
            "status": "AWAITING_APPROVAL",
            "lastError": None,
            "campaignId": CAMPAIGN_B,
            "campaignName": "Elektriker München",
            "name": "Elektro Huber GmbH",
            "website": "https://elektro-huber.example",
            "phone": "+49 89 000000",
            "email": "info@elektro-huber.example",
            "address": {"street": "Hauptstraße", "houseNumber": "5", "postcode": "80331", "city": "München"},
            "score": 78,
            "priorität": "A",
        },
        {
            "id": LEAD_2,
            "status": "DISCOVERED",
            "lastError": None,
            "campaignId": CAMPAIGN_B,
            "campaignName": "Elektriker München",
            "name": "Licht & Strom Maier",
            "website": None,
            "phone": None,
            "email": None,
            "address": {},
            "score": None,
            "priorität": None,
        },
    ]


def lead_answer(lid: str) -> dict[str, Any]:
    row = next(r for r in lead_rows() if r["id"] == lid)
    return {
        "id": lid,
        "kampagne": row["campaignName"],
        "status": row["status"],
        "letzterFehler": None,
        "firma": {
            "name": row["name"],
            "branche": "Elektriker",
            "website": row["website"],
            "telefon": row["phone"],
            "email": row["email"],
            "adresse": row["address"],
        },
        "score": {"wert": 78, "priorität": "A", "version": "v3", "details": {"rules": []}},
        "ausgewählterEntwurf": {
            "betreff": "Ihre Website",
            "text": "Guten Tag Herr Huber, …",
            "kanal": "email",
            "runde": 1,
            "belegIds": ["e1"],
        }
        if row["status"] == "AWAITING_APPROVAL"
        else None,
        "belege": [
            {
                "id": "e1",
                "art": "fact",
                "schlüssel": "website.https",
                "aussage": "Die Website nutzt kein HTTPS.",
                "quelle": "http://elektro-huber.example",
                "sicherheit": 0.9,
            }
        ],
        "verlauf": [
            {
                "von": "DRAFTING",
                "nach": "AWAITING_APPROVAL",
                "grund": "Entwurf fertig",
                "am": "2026-11-12T10:00:00.000Z",
            },
            {"von": None, "nach": "DISCOVERED", "grund": None, "am": "2026-11-12T09:00:00.000Z"},
        ],
    }


def tool(name: str, read_only: bool, schema: dict[str, Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        description=f"{name} (Test)",
        input_schema=schema or {"type": "object", "properties": {}},
        annotations=SimpleNamespace(read_only_hint=read_only),
    )


class FakeLeadTool:
    """The Lead-Tool's MCP server as the hub holds it (``ExternalServer``): ``session``, ``tools``, ``call``."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.name = "pje_sales_ai"
        self.cfg = cfg
        self.session: object | None = object()
        self.error: str | None = None
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail: dict[str, str] = {}
        uuid = {"type": "string", "format": "uuid"}
        self.tools = [
            tool("status", True),
            tool("list_campaigns", True),
            tool("get_campaign", True, {"type": "object", "properties": {"campaignId": uuid}}),
            tool(
                "list_leads",
                True,
                {
                    "type": "object",
                    "properties": {
                        "campaignId": uuid,
                        "status": {"type": "string", "enum": STATUS_ENUM},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                        "offset": {"type": "integer", "minimum": 0, "default": 0},
                    },
                },
            ),
            tool("export_leads", False),
            tool("get_lead", True, {"type": "object", "properties": {"leadId": uuid}}),
            tool("review_queue", True, {"type": "object", "properties": {"limit": {"type": "integer"}}}),
            tool("find_industry", True, {"type": "object", "properties": {"name": {"type": "string"}}}),
            tool(
                "create_campaign",
                False,
                {
                    "type": "object",
                    "properties": {
                        "region": {"type": "string"},
                        "industryKey": {"type": "string"},
                        "targetCount": {"type": "integer"},
                        "selectTopN": {"type": "integer"},
                        "channel": {"type": "string", "enum": ["email", "phone", "letter"]},
                        "budgetUsd": {"type": "number"},
                    },
                    "required": ["region", "industryKey"],
                },
            ),
            tool(
                "request_redraft",
                False,
                {
                    "type": "object",
                    "properties": {"leadId": uuid, "note": {"type": "string", "minLength": 5, "maxLength": 500}},
                    "required": ["leadId", "note"],
                },
            ),
        ]

    async def call(self, name: str, args: dict[str, Any]) -> str:
        self.calls.append((name, args))
        if name in self.fail:
            raise ToolUserError(self.fail[name])
        out: Any
        match name:
            case "status":
                out = status_answer()
            case "list_campaigns":
                out = campaigns_answer()
            case "get_campaign":
                if args["campaignId"] not in (CAMPAIGN_A, CAMPAIGN_B):
                    raise ToolUserError("Kampagne nicht gefunden.")
                out = campaign_answer(args["campaignId"])
            case "list_leads":
                rows = [
                    r
                    for r in lead_rows()
                    if (not args.get("status") or r["status"] == args["status"])
                    and (not args.get("campaignId") or r["campaignId"] == args["campaignId"])
                ]
                page = rows[args.get("offset", 0) : args.get("offset", 0) + args.get("limit", 50)]
                out = {
                    "gesamt": len(rows),
                    "offset": args.get("offset", 0),
                    "anzahl": len(page),
                    "weitere": max(0, len(rows) - args.get("offset", 0) - len(page)),
                    "leads": page,
                }
            case "get_lead":
                if args["leadId"] not in (LEAD_1, LEAD_2):
                    raise ToolUserError("Lead nicht gefunden.")
                out = lead_answer(args["leadId"])
            case "review_queue":
                out = [
                    {"id": LEAD_2, "firma": "B", "kampagne": "K", "score": None, "seit": "2026-11-12T09:00:00.000Z"},
                    {"id": LEAD_1, "firma": "A", "kampagne": "K", "score": 78, "seit": "2026-11-12T10:00:00.000Z"},
                ]
            case "find_industry":
                out = {"imKatalog": True, "treffer": [{"industryKey": "electrician", "label": "Elektriker"}]}
            case "request_redraft":
                out = {"leadId": args["leadId"], "hinweis": "Neu formulieren eingereiht."}
            case "create_campaign":
                out = {"campaignId": CAMPAIGN_A, "hinweis": "Lead-Suche eingereiht."}
            case _:
                raise ToolUserError(f"unbekannt: {name}")
        return json.dumps(out, ensure_ascii=False, indent=2)

    def calls_of(self, name: str) -> list[dict[str, Any]]:
        return [a for n, a in self.calls if n == name]


@pytest.fixture
async def sales(db: Any, tmp_path: Path) -> AsyncIterator[SimpleNamespace]:
    perms = load_permissions()
    registry = ToolRegistry(perms)
    policy = ApprovalPolicy(tmp_path / "approval-mode.json")
    kill = KillSwitch(tmp_path / "ks")
    executor = ToolExecutor(registry, perms, kill, policy)
    state = SimpleNamespace(permissions=perms, registry=registry, executor=executor, approval_policy=policy)
    cfg_path = tmp_path / "mcp_servers.yaml"
    cfg_path.write_text(MCP_CONFIG, encoding="utf-8")
    hub = MCPClientHub(state, cfg_path)  # type: ignore[arg-type]
    state.mcp_hub = hub
    fake = FakeLeadTool(hub.load_config()["pje_sales_ai"])
    hub.servers["pje_sales_ai"] = fake  # type: ignore[assignment]
    hub._register(fake)  # type: ignore[arg-type]  # the production registration: names, risk overrides, schemas

    app = FastAPI()
    app.include_router(router)
    app.state.jarvis = state
    app.dependency_overrides[require_sir] = lambda: OWNER
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield SimpleNamespace(
            client=client, fake=fake, state=state, hub=hub, executor=executor, policy=policy, kill=kill, app=app
        )
