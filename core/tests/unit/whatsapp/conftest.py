"""Test harness for the WhatsApp connector – no WhatsApp account, no network, no Postgres.

* The real repository and the real ToolExecutor run on SQLite (aiosqlite): a few compile hooks map the Postgres
  types of the shared schema (JSONB, BIGINT primary keys) and ``pg_advisory_xact_lock`` exists as a no-op.
* ``FakeSidecar`` answers the sidecar's IPC methods like the Node process would (deterministic message ids from the
  idempotency key, downloads written to the requested path, receipts as events).
* ``FakeHub`` routes device commands into the real Device Agent file actions (device_agent/src) over a temp folder
  that plays "Sir's Mac": transfers between server and device use the real chunking/hash code on both ends.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import BigInteger, event
from sqlalchemy import types as sqltypes
from sqlalchemy.dialects.sqlite import DATETIME as SQLITE_DATETIME
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

import jarvis.db as jdb
from jarvis.config import PathsCfg, Settings, load_permissions
from jarvis.db.models import WHATSAPP_TABLES, Approval, AuditEntry, ChatSession, ToolExecution
from jarvis.security.auth import Principal
from jarvis.security.guards import KillSwitch
from jarvis.tools.approval_mode import ApprovalPolicy
from jarvis.tools.executor import ToolExecutor
from jarvis.tools.registry import ToolRegistry
from jarvis.whatsapp.repo import WhatsAppRepo
from jarvis.whatsapp.sending import message_id_for
from jarvis.whatsapp.service import WhatsAppService

REPO_ROOT = Path(__file__).resolve().parents[4]


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_: Any, compiler: Any, **kw: Any) -> str:
    return "JSON"


@compiles(BigInteger, "sqlite")
def _bigint_sqlite(type_: Any, compiler: Any, **kw: Any) -> str:
    return "INTEGER"  # rowid alias → autoincrement like BIGSERIAL


class AwareDateTime(SQLITE_DATETIME):
    """SQLite has no time zones: values are stored as UTC and come back timezone-aware, like from Postgres."""

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
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'wa.db'}")
    engine.sync_engine.dialect.colspecs = {**engine.sync_engine.dialect.colspecs, sqltypes.DateTime: AwareDateTime}

    @event.listens_for(engine.sync_engine, "connect")
    def _fns(dbapi_conn: Any, _rec: Any) -> None:
        dbapi_conn.create_function("pg_advisory_xact_lock", 1, lambda _x: 0)

    tables = [*WHATSAPP_TABLES, Approval.__table__, ToolExecution.__table__, AuditEntry.__table__, ChatSession.__table__]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Approval.metadata.create_all(c, tables=tables))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(jdb, "_engine", engine)
    monkeypatch.setattr(jdb, "_sessionmaker", maker)
    yield maker
    await engine.dispose()


# ---------------------------------------------------------------------------------------------- fakes
class FakeSidecar:
    """Behaves like whatsapp-sidecar on the IPC level."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.running = True
        self.restarts = 0
        self.last_error: str | None = None
        self.info = {"baileys": "6.7.24"}
        self.download_data: bytes | Exception = b""
        self.updated_raw: str | None = None
        self.send_error: Exception | None = None
        self.sends: dict[str, dict[str, Any]] = {}
        self.ack: int | None = 2  # status the "server" confirms with (2 = SERVER_ACK)
        self.on_event: Any = None

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def request(self, method: str, params: dict[str, Any] | None = None, **_: Any) -> Any:
        params = params or {}
        self.calls.append((method, params))
        if method == "send":
            if self.send_error:
                raise self.send_error
            key = params["idempotencyKey"]
            if key in self.sends:  # the sidecar's own idempotency
                return self.sends[key]
            mid = message_id_for(key)
            res = {
                "messageId": mid,
                "chatJid": params["jid"],
                "timestamp": 1_760_000_000,
                "status": 1,
                "message": {
                    "id": mid, "chatJid": params["jid"], "fromMe": True, "senderJid": "me", "timestamp": 1_760_000_000,
                    "kind": "text" if params["kind"] == "text" else ("voice" if params.get("ptt") else params["kind"]),
                    "text": params.get("text") or params.get("caption") or "", "isGroup": False, "status": 1,
                },
            }  # fmt: skip
            self.sends[key] = res
            if self.ack is not None and self.on_event is not None:
                status = self.ack

                async def receipt() -> None:
                    await asyncio.sleep(0.01)
                    await self.on_event(
                        "message_updates", {"updates": [{"chatJid": params["jid"], "id": mid, "fromMe": True, "status": status}]}
                    )

                asyncio.get_running_loop().create_task(receipt())
            return res
        if method == "download":
            if isinstance(self.download_data, Exception):
                raise self.download_data
            data = self.download_data
            Path(params["destPath"]).write_bytes(data)
            out: dict[str, Any] = {"path": params["destPath"], "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            if self.updated_raw:
                out["updatedRaw"] = self.updated_raw
            return out
        if method == "markRead":
            return {"marked": len(params.get("keys") or [])}
        if method == "checkNumber":
            return {"exists": True, "jid": f"{params['phone']}@s.whatsapp.net"}
        if method == "connect":
            return {"started": True, **({"pairingCode": "ABCD-EFGH"} if params.get("method") == "pairing" else {})}
        if method == "fetchHistory":
            return {"requestId": "req-1"}
        return {"ok": True}


def load_device_agent() -> tuple[Any, Any]:
    src = str(REPO_ROOT / "device_agent" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    files = importlib.import_module("jarvis_device.actions.files")
    security = importlib.import_module("jarvis_device.security")
    return files, security


class FakeHub:
    """A connected Device Agent ("Mac") whose file commands run the real agent code on a temp folder."""

    def __init__(self, root: Path, name: str = "Mac") -> None:
        files, security = load_device_agent()
        self.files = files
        self.policy = security.PathPolicy([root])
        self.root = root
        self.name = name
        self.calls: list[str] = []

    def online(self) -> list[Any]:
        return [SimpleNamespace(name=self.name)]

    def describe(self) -> list[dict[str, Any]]:
        return [{"name": self.name, "online": True, "platform": "macos"}]

    async def call(self, device: str | None, action: str, args: dict[str, Any] | None = None, timeout_s: float | None = None) -> Any:
        a = dict(args or {})
        self.calls.append(action)
        if isinstance(a.get("path"), str) and not Path(a["path"]).is_absolute():
            a["path"] = str(self.root / a["path"])  # relative = the device's home folder
        if device not in (None, self.name):
            raise RuntimeError(f"Unbekanntes Gerät {device}")
        if action == "files.stat":
            return self.files.stat(self.policy, a["path"], bool(a.get("hash")))
        if action == "files.read_chunk":
            return self.files.read_chunk(self.policy, a["path"], int(a["offset"]), int(a["length"]))
        if action == "files.write_chunk":
            return self.files.write_chunk(
                self.policy, a["upload_id"], a["path"], int(a["offset"]), a.get("data") or "", bool(a.get("final")),
                a.get("sha256"), a.get("size"), bool(a.get("overwrite")),
            )  # fmt: skip
        if action == "files.search":
            return self.files.search(self.policy, a["query"], None, int(a.get("limit") or 20))
        raise RuntimeError(f"unexpected action {action}")


@dataclass
class Recorder:
    items: list[Any] = field(default_factory=list)

    async def notify(self, event: str, title: str, body: str = "", **kw: Any) -> list[str]:
        self.items.append({"event": event, "title": title, "body": body, **kw})
        return ["desktop"]


class FakeMemory:
    def __init__(self) -> None:
        self.saved: list[dict[str, Any]] = []

    async def remember(self, title: str, content: str, kind: str = "note", **kw: Any) -> dict[str, Any]:
        self.saved.append({"title": title, "content": content, "kind": kind, **kw})
        return {"id": f"mem-{len(self.saved)}", "status": "created"}


class FakeSTT:
    def __init__(self, text: str = "Bitte schick mir bis Freitag das Angebot.") -> None:
        self.text = text
        self.paths: list[Path] = []

    async def healthy(self) -> bool:
        return True

    async def transcribe_file(self, path: Path) -> str:
        self.paths.append(path)
        assert path.is_file()
        return self.text


class FakeOneShot:
    def __init__(self, answer: str = "Ein Logo-Entwurf in Blau. Text: „ACME GmbH“.") -> None:
        self.answer = answer
        self.prompts: list[tuple[str, Path]] = []

    async def run(self, prompt: str, *, read_dir: Path | None = None, timeout_s: float = 120, **_: Any) -> str:
        assert read_dir is not None and read_dir.is_dir()
        files = [p.name for p in read_dir.iterdir()]
        self.prompts.append((prompt, read_dir))
        return f"{self.answer} (gelesen: {', '.join(sorted(files))})"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    base = tmp_path / "jarvis"
    return Settings(
        paths=PathsCfg(
            data_dir=base / "data", log_dir=base / "logs", vault_dir=base / "vault",
            workspaces_dir=base / "workspaces", projects_dir=base / "projects",
        )  # fmt: skip
    )


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    for d in ("Desktop", "Documents", "Downloads"):
        (h / d).mkdir(parents=True)
    return h


@pytest.fixture
async def wa(db: Any, settings: Settings, home: Path, tmp_path: Path, secret_key: str) -> AsyncIterator[SimpleNamespace]:
    """A WhatsAppService over SQLite with fakes for everything outside the Core."""
    for p in (settings.paths.data_dir, settings.paths.log_dir, settings.paths.projects_dir, settings.paths.workspaces_dir):
        p.mkdir(parents=True, exist_ok=True)
    perms = load_permissions()
    registry = ToolRegistry(perms)
    policy = ApprovalPolicy(tmp_path / "approval-mode.json")
    executor = ToolExecutor(registry, perms, KillSwitch(tmp_path), policy)
    device_root = tmp_path / "mac-home"
    (device_root / "Desktop").mkdir(parents=True)
    state = SimpleNamespace(
        settings=settings,
        notifier=Recorder(),
        memory=FakeMemory(),
        stt=FakeSTT(),
        oneshot=FakeOneShot(),
        agent=object(),
        registry=registry,
        executor=executor,
        approval_policy=policy,
        device_hub=FakeHub(device_root),
        connectors=SimpleNamespace(check=_noop_check, connectors={}),
        llm_answers=[],
    )

    async def structured_llm(system: str, prompt: str, schema: dict[str, Any]) -> Any:
        state.llm_prompts = [*getattr(state, "llm_prompts", []), prompt]
        return state.llm_answers.pop(0) if state.llm_answers else {"items": []}

    state.structured_llm = structured_llm
    sidecar = FakeSidecar()
    svc = WhatsAppService(state, repo=WhatsAppRepo(), sidecar=sidecar)  # type: ignore[arg-type]
    svc.home = home
    sidecar.on_event = svc.on_event
    state.whatsapp = svc
    svc._ensure_dirs()
    svc.status = {"state": "open", "has_session": True, "me": {"jid": "4915100000000@s.whatsapp.net", "name": "Sir"}}
    yield SimpleNamespace(svc=svc, state=state, sidecar=sidecar, home=home, device_root=device_root, executor=executor,
                          registry=registry, policy=policy)  # fmt: skip


async def _noop_check(cid: str) -> None:
    return None


@pytest.fixture
def secret_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = "unit-test-session-key-" + "k" * 40
    monkeypatch.setenv("JARVIS_SECRET_WHATSAPP_SESSION_KEY", key)
    return key


OWNER = Principal(id="desk-1", role="owner", channel="desktop", device_id=None)


def incoming(mid: str, text: str = "", *, chat: str = "4917000000001@s.whatsapp.net", kind: str = "text",
             sender: str | None = None, name: str = "Jonathan", ts: int = 1_760_000_000, **extra: Any) -> dict[str, Any]:  # fmt: skip
    return {
        "id": mid, "chatJid": chat, "fromMe": False, "senderJid": sender or chat, "senderName": name,
        "timestamp": ts, "kind": kind, "text": text, "isGroup": chat.endswith("@g.us"), **extra,
    }  # fmt: skip


def media_message(mid: str, kind: str, *, file_name: str | None = None, mimetype: str, size: int = 1000,
                  sha_b64: str | None = None, caption: str = "", **kw: Any) -> dict[str, Any]:  # fmt: skip
    media: dict[str, Any] = {"mimetype": mimetype, "fileLength": size}
    if file_name:
        media["fileName"] = file_name
    if sha_b64:
        media["fileSha256"] = sha_b64
    raw = '{"key":{"id":"%s"},"message":{"documentMessage":{"url":"https://mmg.whatsapp.net/x"}}}' % mid
    return incoming(mid, caption, kind=kind, media=media, raw=raw, **kw)
