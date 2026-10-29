"""Test harness for the UpTik connector – nothing here ever reaches TikTok, Instagram or YouTube.

* The browser is ``FakeWeb``: a scripted stand-in for ``Runner``/``BrowserSession``. The real platform adapters run
  against it (every selector "exists" unless a scenario hides it), so their flows, the guard and the stages are
  exercised; time inside the fake advances only through ``wait`` (no real sleeping).
* Scenarios per platform and attempt: ``ok``, ``captcha``, ``http_429``, ``checkpoint``, ``flaky`` (network error),
  ``crash`` (unexpected exception), ``unconfirmed`` (no success marker after the final click), ``login_required``.
* Jarvis' approvals, ledger and audit run for real on SQLite; the connector's own store is its real SQLite file.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import BigInteger, event
from sqlalchemy import types as sqltypes
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.sqlite import DATETIME as SQLITE_DATETIME
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

import jarvis.db as jdb
from jarvis.config import PathsCfg, Settings, load_permissions
from jarvis.db.models import Approval, AuditEntry, ChatSession, ToolExecution
from jarvis.secrets import secret_store
from jarvis.security.auth import Principal
from jarvis.security.guards import KillSwitch, rate_limiter
from jarvis.tools.approval_mode import ApprovalPolicy
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.registry import ToolRegistry
from jarvis.uptik.adapters import default_adapters, instagram, tiktok, youtube
from jarvis.uptik.model import RetryableError
from jarvis.uptik.service import UptikService
from jarvis.uptik.tools import UptikConnector

OWNER = Principal(id="desk-1", role="owner", channel="desktop", device_id=None)
T0 = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
DONE_MARKERS = {*tiktok.DONE, *instagram.DONE, *youtube.DONE}


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_: Any, compiler: Any, **kw: Any) -> str:
    return "JSON"


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_: Any, compiler: Any, **kw: Any) -> str:
    return "INTEGER"


class AwareDateTime(SQLITE_DATETIME):
    def result_processor(self, dialect: Any, coltype: Any) -> Any:
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
    """The executor's limiter is process-global (60 calls/min, 30 high-risk/h per actor): every test here starts and
    ends with empty counters, so these tests neither hit the limit nor eat the budget of other test modules."""
    rate_limiter._hits.clear()
    yield
    rate_limiter._hits.clear()


@pytest.fixture
def secrets(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    store: dict[str, str] = {}
    monkeypatch.setattr(secret_store, "get", lambda name: store.get(name))
    monkeypatch.setattr(secret_store, "set", lambda name, value: store.__setitem__(name, value))
    monkeypatch.setattr(secret_store, "delete", lambda name: store.pop(name, None) is not None)
    monkeypatch.setattr(secret_store, "exists", lambda name: bool(store.get(name)))
    return store


@dataclass
class Recorder:
    items: list[dict[str, Any]] = field(default_factory=list)

    async def notify(self, event: str, title: str, body: str = "", **kw: Any) -> list[str]:
        self.items.append({"event": event, "title": title, "body": body, **kw})
        return ["desktop"]

    def events(self) -> list[str]:
        return [i["event"] for i in self.items]


class FakeSession:
    def __init__(
        self, web: FakeWeb, platform: str, behavior: str, state: dict[str, Any] | None, headless: bool
    ) -> None:
        self.web, self.platform, self.behavior, self.state, self.headless = web, platform, behavior, state, headless
        self._url = "about:blank"
        self._text = ""
        self._statuses: list[int] = []
        self.polls = 0
        self.closed = False
        web.sessions.append(self)

    def _adapter(self) -> Any:
        return self.web.adapters[self.platform]

    def act(self, *a: Any) -> None:
        self.web.actions.append((self.platform, *a))

    async def goto(self, url: str) -> None:
        self.act("goto", url)
        self._url = url
        if self.behavior == "login_required" and url == self._adapter().upload_url:
            self._url = self._adapter().login_url

    async def url(self) -> str:
        if self.behavior == "login" and self._url == self._adapter().login_url:
            self.polls += 1
            if self.polls > self.web.login_after_polls:  # Sir finished typing in the window
                self._url = self._adapter().upload_url
        return self._url

    async def text(self, selector: str = "body") -> str:
        return self._text

    async def exists(self, selector: str) -> bool:
        if self.behavior == "unconfirmed" and selector in DONE_MARKERS:
            return False
        return True

    async def wait_for(self, selector: str, timeout_ms: int = 15_000) -> bool:
        return await self.exists(selector)

    async def click(self, selector: str) -> None:
        self.act("click", selector)

    async def fill(self, selector: str, value: str) -> None:
        self.act("fill", selector, value)

    async def insert_text(self, selector: str, value: str) -> None:
        self.act("insert_text", selector, value)

    async def press(self, key: str) -> None:
        self.act("press", key)

    async def upload(self, selector: str, path: str) -> None:
        self.act("upload", path)
        if self.behavior == "flaky":
            raise RetryableError("Netzwerkfehler beim Hochladen (simuliert)", code="network")
        if self.behavior == "crash":
            raise RuntimeError("Target closed https://example.invalid/x?token=SECRET123")
        if self.behavior == "captcha":
            self._text = "Drag the puzzle piece – please verify the captcha"
        if self.behavior == "checkpoint":
            self._url = "https://www.instagram.com/challenge/xyz/"
        if self.behavior == "http_429":
            self._statuses.append(429)

    async def wait(self, ms: int) -> None:
        self.web.clock += ms / 1000

    async def statuses(self) -> list[int]:
        out, self._statuses = self._statuses, []
        return out

    async def storage_state(self) -> dict[str, Any]:
        return {"cookies": [{"name": "sessionid", "value": self.web.cookie, "domain": ".example"}], "origins": []}

    async def close(self) -> None:
        self.closed = True


class FakeRunner:
    def __init__(self, web: FakeWeb, account: dict[str, Any]) -> None:
        self.web, self.account = web, account
        self.kind = account.get("runner", "local")

    async def available(self) -> tuple[bool, str]:
        return (True, "") if self.web.runner_ok else (False, "Kein Browser (simuliert)")

    async def open(self, storage_state: dict[str, Any] | None, *, headless: bool) -> FakeSession:
        pf = self.account["platform"]
        if storage_state is None:
            return FakeSession(self.web, pf, "login", None, headless)
        plan = self.web.plans.get(self.account["id"]) or self.web.plans.get(pf) or []
        behavior = plan.pop(0) if plan else "ok"
        return FakeSession(self.web, pf, behavior, storage_state, headless)

    async def stage_video(self, path: Path) -> str:
        self.web.staged.append(str(path))
        return str(path)

    async def release_video(self, staged: str) -> None:
        return None


@dataclass
class FakeWeb:
    adapters: dict[str, Any]
    plans: dict[str, list[str]] = field(default_factory=dict)  # platform or account id → behaviours per attempt
    sessions: list[FakeSession] = field(default_factory=list)
    actions: list[tuple[Any, ...]] = field(default_factory=list)
    staged: list[str] = field(default_factory=list)
    clock: float = 0.0
    login_after_polls: int = 2
    runner_ok: bool = True
    cookie: str = "sess-cookie-VERY-SECRET-0815"

    def published(self, platform: str | None = None) -> list[FakeSession]:
        """Sessions that clicked a final publish button."""
        return [
            s for s in self.sessions if s.behavior not in ("login",) and (platform is None or s.platform == platform)
        ]


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> datetime:
        self.now += timedelta(**kw)
        return self.now


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    base = tmp_path / "jarvis"
    paths = PathsCfg(
        data_dir=base / "data", log_dir=base / "logs", vault_dir=base / "vault",
        workspaces_dir=base / "workspaces", projects_dir=base / "projects",
    )  # fmt: skip
    for p in (paths.data_dir, paths.log_dir):
        p.mkdir(parents=True, exist_ok=True)
    return Settings(paths=paths)


@pytest.fixture
def video(tmp_path: Path) -> Path:
    d = tmp_path / "videos"
    d.mkdir()
    p = d / "pje-reel.mp4"
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 4096)
    return p


async def fake_probe(path: Path) -> Any:
    from jarvis.uptik.validation import probe_video

    info = await probe_video(path, ffprobe="/nonexistent/ffprobe")
    info.duration_s, info.width, info.height, info.has_video = 12.0, 1080, 1920, True
    return info


@pytest.fixture
async def uptik(
    db: Any, settings: Settings, tmp_path: Path, secrets: dict[str, str], video: Path
) -> AsyncIterator[SimpleNamespace]:
    perms = load_permissions()
    registry = ToolRegistry(perms)
    policy = ApprovalPolicy(tmp_path / "approval-mode.json")
    kill = KillSwitch(tmp_path / "ks")
    executor = ToolExecutor(registry, perms, kill, policy)
    notifier = Recorder()
    clock = Clock(T0)
    adapters = default_adapters()
    web = FakeWeb(adapters)
    for a in adapters.values():
        a.now = lambda: web.clock
    state = SimpleNamespace(
        settings=settings, notifier=notifier, permissions=perms, registry=registry, executor=executor,
        approval_policy=policy, kill_switch=kill, connectors=SimpleNamespace(connectors={}, health={}),
        device_hub=None,
    )  # fmt: skip
    svc = UptikService(
        state,  # type: ignore[arg-type]
        adapters=adapters,
        runner_factory=lambda acct: FakeRunner(web, acct),  # type: ignore[arg-type,return-value]
        clock=clock,
        probe=fake_probe,
    )
    svc.login_poll_s = 0
    state.uptik = svc
    conn = UptikConnector(state)
    conn.enabled = True
    conn.settings = {"video_dirs": [str(video.parent)], "retry_base_s": 60}
    state.connectors.connectors["uptik"] = conn
    for spec in conn.tools():
        registry.register(spec)
    registry.set_connector_gate("uptik", lambda: conn.active)
    await svc.ensure_open()
    ns = SimpleNamespace(svc=svc, state=state, web=web, clock=clock, executor=executor, notifier=notifier,
                         conn=conn, video=video, secrets=secrets, kill=kill)  # fmt: skip
    yield ns
    await svc.stop()
    svc.store.close()


async def connected(ns: SimpleNamespace, platform: str, label: str) -> dict[str, Any]:
    """An account that went through the (simulated) login."""
    a = await ns.svc.create_account(platform, label, actor="test")
    await ns.svc.start_login(a["id"])
    await ns.svc._logins[a["id"]]
    return await ns.svc.account(a["id"])


async def approve(ns: SimpleNamespace, res: Any) -> dict[str, Any]:
    assert res.status == "approval_required", res.message
    return await ns.executor.decide(res.approval_id, True, OWNER)
