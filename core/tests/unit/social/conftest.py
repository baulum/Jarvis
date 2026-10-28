"""Test harness for the social video feature – no Claude, no Remotion, no Gmail, no Postgres.

* The real repository, scheduler, task engine and ToolExecutor run on SQLite (aiosqlite) with the same compile
  hooks as the WhatsApp tests (JSONB → JSON, timezone-aware datetimes).
* Secrets live in memory (``secrets`` fixture), the LLM is a queue of canned answers, Gmail is a recorder.
* ``tiny_video`` renders a real 9 s 1080×1920 H.264/AAC file with FFmpeg (skipped if FFmpeg is missing) so the
  QA runs against an actual render.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC
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
from jarvis.db.models import (
    SOCIAL_TABLES,
    Approval,
    AuditEntry,
    ChatSession,
    Schedule,
    Task,
    TaskStep,
    ToolExecution,
)
from jarvis.secrets import secret_store
from jarvis.security.auth import Principal
from jarvis.security.guards import KillSwitch
from jarvis.social.service import SocialMediaService
from jarvis.social.tools import social_tools
from jarvis.tools.approval_mode import ApprovalPolicy
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.registry import ToolRegistry


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
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'social.db'}")
    engine.sync_engine.dialect.colspecs = {**engine.sync_engine.dialect.colspecs, sqltypes.DateTime: AwareDateTime}

    @event.listens_for(engine.sync_engine, "connect")
    def _fns(dbapi_conn: Any, _rec: Any) -> None:
        dbapi_conn.create_function("pg_advisory_xact_lock", 1, lambda _x: 0)

    tables = [
        *SOCIAL_TABLES,
        Approval.__table__,
        ToolExecution.__table__,
        AuditEntry.__table__,
        ChatSession.__table__,
        Schedule.__table__,
        Task.__table__,
        TaskStep.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Approval.metadata.create_all(c, tables=tables))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(jdb, "_engine", engine)
    monkeypatch.setattr(jdb, "_sessionmaker", maker)
    yield maker
    await engine.dispose()


@pytest.fixture
def secrets(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    store: dict[str, str] = {}
    monkeypatch.setattr(secret_store, "get", lambda name: store.get(name))
    monkeypatch.setattr(secret_store, "set", lambda name, value: store.__setitem__(name, value))
    monkeypatch.setattr(secret_store, "delete", lambda name: store.pop(name, None) is not None)
    monkeypatch.setattr(secret_store, "exists", lambda name: bool(store.get(name)))
    return store


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


@dataclass
class Recorder:
    items: list[dict[str, Any]] = field(default_factory=list)

    async def notify(self, event: str, title: str, body: str = "", **kw: Any) -> list[str]:
        self.items.append({"event": event, "title": title, "body": body, **kw})
        return ["desktop"]


class FakeGoogle:
    """The Gmail part of the Google connector: records what would be sent."""

    def __init__(self) -> None:
        self.enabled = True
        self.active = True
        self.settings: dict[str, Any] = {"account_email": "paulhoflich@gmail.com"}
        self.sent: list[dict[str, Any]] = []

    async def send_mail(self, to: str, subject: str, body: str, attachments: Any = None, draft: bool = False, html: str | None = None) -> Any:
        self.sent.append({"to": to, "subject": subject, "body": body, "attachments": attachments, "html": html})
        return {"id": f"msg-{len(self.sent)}"}


class FakeTasks:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create(self, playbook: str, params: dict[str, Any], principal: Any, **kw: Any) -> Any:
        self.created.append({"playbook": playbook, "params": params, **kw})
        return SimpleNamespace(id=f"task-{len(self.created)}", title="PJE Systems Daily Social Video")


OWNER = Principal(id="desk-1", role="owner", channel="desktop", device_id=None)
SYSTEM = Principal(id="scheduler", role="system", channel="scheduler")


@pytest.fixture
async def social(db: Any, settings: Settings, tmp_path: Path, secrets: dict[str, str]) -> AsyncIterator[SimpleNamespace]:
    perms = load_permissions()
    registry = ToolRegistry(perms)
    policy = ApprovalPolicy(tmp_path / "approval-mode.json")
    executor = ToolExecutor(registry, perms, KillSwitch(tmp_path), policy)
    google = FakeGoogle()
    state = SimpleNamespace(
        settings=settings,
        notifier=Recorder(),
        permissions=perms,
        registry=registry,
        executor=executor,
        approval_policy=policy,
        kill_switch=KillSwitch(tmp_path / "ks"),
        connectors=SimpleNamespace(connectors={"google": google}, health={}),
        tasks=FakeTasks(),
        agent=object(),
        llm_answers=[],
    )
    from jarvis.jobs.scheduler import Scheduler

    state.scheduler = Scheduler(state)  # type: ignore[arg-type]
    svc = SocialMediaService(state)  # type: ignore[arg-type]
    svc.deps = {"ok": True, "problems": []}
    state.social = svc
    for spec in social_tools(svc):
        registry.register(spec)
    yield SimpleNamespace(svc=svc, state=state, google=google, executor=executor, registry=registry, policy=policy, secrets=secrets)


@pytest.fixture(scope="session")
def tiny_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """10.8 s (the length of ``board()``), 1080×1920, 30 fps, H.264 yuv420p + AAC (a tone at ≈ −16 LUFS) – a real file for ffprobe/QA."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg nicht installiert")
    out = tmp_path_factory.mktemp("video") / "tiny.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=10.8",
         "-f", "lavfi", "-i", "sine=f=440:d=10.8:sample_rate=48000", "-af", "volume=6dB",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "40", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)],
        check=True,
    )  # fmt: skip
    return out


@pytest.fixture(scope="session")
def silent_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if shutil.which("ffmpeg") is None:
        pytest.skip("FFmpeg nicht installiert")
    out = tmp_path_factory.mktemp("video") / "silent.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=720x1280:r=25:d=10.8",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "10.8",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "40", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)],
        check=True,
    )  # fmt: skip
    return out


def board(**over: Any) -> dict[str, Any]:
    """A valid storyboard (10 s, five scenes) to modify per test."""
    b: dict[str, Any] = {
        "version": 1,
        "title": "Captcha für Kunden von 2011",
        "category": "B",
        "tone": "humorvoll",
        "palette": "ink",
        "music": "pulse",
        "scenes": [
            {"id": "s1", "component": "error_dialog", "duration": 1.8, "transition": "cut",
             "props": {"severity": "critical", "title": "ERROR 404", "message": "Kundenvertrauen nicht gefunden.", "buttons": ["Beheben"]},
             "sfx": [{"sound": "error_buzz", "at": 0.1}]},
            {"id": "s2", "component": "kinetic_type", "duration": 1.6, "transition": "glitch",
             "props": {"lines": [{"text": "Eigener Code."}, {"text": "Kein Baukasten.", "accent": True}], "style": "slam"}},
            {"id": "s3", "component": "split_screen", "duration": 2.2, "transition": "wipe",
             "props": {"left_label": "Baukasten.", "right_label": "Echter Code."}},
            {"id": "s4", "component": "fact", "duration": 2.6, "transition": "zoom", "surface": "accent",
             "props": {"fact_id": "price_from", "kicker": "Festpreis"}},
            {"id": "s5", "component": "end_card", "duration": 2.6, "transition": "iris",
             "props": {"headline": "Lass uns bauen.", "button": "Projekt anfragen", "url": "pje-systems.de"},
             "sfx": [{"sound": "success", "at": 0.4}]},
        ],
    }  # fmt: skip
    b.update(over)
    return b


def captions_ok() -> dict[str, Any]:
    return {
        "instagram": {"text": "Dein Kundenvertrauen ist nicht weg, nur schlecht verlinkt 😉 Projekt anfragen auf pje-systems.de", "hashtags": ["#Webdesign", "#Wolnzach", "#Handwerk"]},
        "tiktok": {"text": "Fehler 404 im echten Leben 🙈 Projekt anfragen: pje-systems.de", "hashtags": ["#webdesign", "#kleinunternehmen", "#website"]},
        "facebook": {"text": "Eigenes Layout, echter Code, Festpreis ab 499 € einmalig. Projekt anfragen auf pje-systems.de.", "hashtags": ["#Webdesign", "#Wolnzach", "#München"]},
        "youtube": {"title": "ERROR 404: Kundenvertrauen nicht gefunden", "description": "Websites, gebaut. Nicht geklickt. Projekt anfragen auf pje-systems.de", "hashtags": ["#Shorts", "#Webdesign"]},
    }  # fmt: skip
