"""The connector's engine: accounts and their login, preparing posts, the queue worker, the circuit breaker and the
video hand-over.

Flow of a post: ``prepare`` (tool ``prepare`` hook, before the approval: resolve video and accounts, validate per
platform, parse the time) → approval (``uptik_publish_now`` / ``uptik_schedule_post`` are risk *high*) → ``enqueue``
(one row per account = fan-out, ``run_at`` now or later) → worker ``tick`` (every few seconds; one post per account at
a time, up to ``max_parallel`` accounts at once) → adapter in a browser of the account's runner → published | retry
with backoff | failed | blocked (circuit breaker) | needs_review (outcome unknown after the final click).

The worker never runs while the connector is off or the kill switch is engaged. A tripped account stays stopped
until Sir resumes it in the app – no timer, no automatic retry.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jarvis.events import bus, spawn
from jarvis.security.audit import audit
from jarvis.security.auth import Principal
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext
from jarvis.uptik.adapters import PlatformAdapter, default_adapters
from jarvis.uptik.circuit import LOGIN_REQUIRED, BlockSignal
from jarvis.uptik.model import (
    CONNECTOR_ID,
    PLATFORM_LABEL,
    PLATFORMS,
    SUBMITTING,
    ManualReviewError,
    PermanentError,
    PostContent,
    PublishJob,
    RetryableError,
    UptikError,
    VideoInfo,
    normalize_hashtags,
)
from jarvis.uptik.runners import DeviceRunner, LocalRunner, Runner
from jarvis.uptik.sessions import SessionVault
from jarvis.uptik.store import UptikStore
from jarvis.uptik.timing import RetryPolicy, at_local_time, berlin, from_iso, iso, parse_publish_at
from jarvis.uptik.validation import merged_options, probe_video, validate

if TYPE_CHECKING:
    from jarvis.app_state import AppState
    from jarvis.uptik.handoff import VideoClip

logger = logging.getLogger(__name__)

SYSTEM = Principal(id="uptik", role="system", channel="scheduler")
DEFAULT_SETTINGS: dict[str, Any] = {
    "runner": "local",
    "device": "",
    "device_staging_dir": "~/Downloads/Jarvis-UpTik",
    "headless": False,
    "browser_path": "",
    "max_attempts": 3,
    "retry_base_s": 120,
    "max_parallel": 2,
    "handoff_enabled": False,
    "handoff_publish_time": "",
    "youtube_shorts_enabled": False,
    "login_timeout_s": 600,
    "video_dirs": ["~/Movies", "~/Videos", "~/Downloads", "~/Desktop"],
}
PUBLISH_TIMEOUT_S = 45 * 60
VIDEO_EXT = (".mp4", ".mov", ".webm")
_URL_QUERY = re.compile(r"(https?://[^\s?#]+)[?#][^\s]*")
_LONG_TOKEN = re.compile(r"[A-Za-z0-9_\-]{40,}")


def safe_message(msg: str) -> str:
    """Error text for logs/UI: no query strings, no long tokens, short."""
    return _LONG_TOKEN.sub("[…]", _URL_QUERY.sub(r"\1", str(msg)))[:300]


class UptikService:
    def __init__(
        self,
        state: AppState,
        *,
        store: UptikStore | None = None,
        vault: SessionVault | None = None,
        adapters: dict[str, PlatformAdapter] | None = None,
        runner_factory: Callable[[dict[str, Any]], Runner] | None = None,
        clock: Callable[[], datetime] | None = None,
        probe: Callable[[Path], Any] | None = None,
    ) -> None:
        self.state = state
        self.base = state.settings.paths.data_dir / "uptik"
        self.store = store or UptikStore(self.base / "uptik.db")
        self.vault = vault or SessionVault(self.base / "sessions")
        self.uploads_dir = self.base / "uploads"
        self.adapters = adapters or default_adapters()
        self.runner_factory = runner_factory
        self.clock = clock or (lambda: datetime.now(UTC))
        self.probe = probe or probe_video
        self.extra_video_roots: list[Path] = []  # e.g. the social video library (registered by the hand-over bridge)
        self.poll_s = 10.0
        self.login_poll_s = 3.0
        self._worker: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._locks: dict[str, asyncio.Lock] = {}
        self._running: set[str] = set()
        self._logins: dict[str, asyncio.Task[None]] = {}
        self._opened = False

    # ------------------------------------------------------------------ configuration
    def connector(self) -> Any:
        return self.state.connectors.connectors.get(CONNECTOR_ID)

    def active(self) -> bool:
        c = self.connector()
        return bool(c is not None and c.active)

    @property
    def settings(self) -> dict[str, Any]:
        c = self.connector()
        return {**DEFAULT_SETTINGS, **((c.settings if c is not None else {}) or {})}

    def platforms(self) -> list[str]:
        return [p for p in PLATFORMS if p != "youtube_shorts" or self.settings["youtube_shorts_enabled"]]

    def policy(self) -> RetryPolicy:
        st = self.settings
        return RetryPolicy(max_attempts=int(st["max_attempts"]), base_s=float(st["retry_base_s"]))

    def runner_for(self, account: dict[str, Any]) -> Runner:
        if self.runner_factory is not None:
            return self.runner_factory(account)
        st = self.settings
        if account.get("runner") == "device":
            return DeviceRunner(self.state.device_hub, account.get("device") or st["device"], st["device_staging_dir"])
        return LocalRunner(st["browser_path"])

    # ------------------------------------------------------------------ lifecycle (connector start/stop)
    async def ensure_open(self) -> None:
        if not self._opened:
            await self.store.open()
            self._opened = True

    async def start(self) -> None:
        await self.ensure_open()
        rec = await self.store.recover()
        if rec["needs_review"]:
            await self._notify(
                "uptik.needs_review",
                "UpTik: Veröffentlichung unklar",
                f"{len(rec['needs_review'])} Beitrag/Beiträge wurden beim Neustart gerade veröffentlicht. "
                "Bitte auf der Plattform prüfen und in der Historie als veröffentlicht oder fehlgeschlagen markieren.",
                level="warning",
            )
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._loop(), name="uptik-worker")

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._worker
            self._worker = None
        for t in self._logins.values():
            t.cancel()
        self._logins.clear()

    def wake(self) -> None:
        self._wake.set()

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("UpTik worker tick failed")
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self.poll_s)

    # ------------------------------------------------------------------ accounts
    def _account_view(self, a: dict[str, Any]) -> dict[str, Any]:
        out = {
            k: a[k]
            for k in (
                "id",
                "platform",
                "label",
                "handle",
                "runner",
                "device",
                "status",
                "autopost",
                "circuit_state",
                "circuit_code",
                "circuit_reason",
                "circuit_at",
                "last_error",
                "connected_at",
                "last_used_at",
                "created_at",
            )
        }
        out["platform_label"] = PLATFORM_LABEL.get(a["platform"], a["platform"])
        out["session_saved"] = self.vault.exists(a["id"])
        out["session_saved_at"] = self.vault.saved_at(a["id"])
        out["login_running"] = a["id"] in self._logins and not self._logins[a["id"]].done()
        return out

    async def accounts(self) -> list[dict[str, Any]]:
        await self.ensure_open()
        return [self._account_view(a) for a in await self.store.accounts()]

    async def account(self, aid: str) -> dict[str, Any]:
        await self.ensure_open()
        a = await self.store.account(aid)
        if a is None:
            raise ToolUserError("Unbekanntes Konto")
        return a

    async def create_account(
        self, platform: str, label: str, runner: str | None = None, device: str | None = None, actor: str = ""
    ) -> dict[str, Any]:
        await self.ensure_open()
        if platform not in self.platforms():
            raise ToolUserError(f"Plattform {platform} ist nicht verfügbar")
        label = label.strip()[:80]
        if not label:
            raise ToolUserError("Name des Kontos fehlt")
        runner = runner or self.settings["runner"]
        if runner not in ("local", "device"):
            raise ToolUserError("runner: local oder device")
        a = await self.store.create_account(platform, label, runner, (device or self.settings["device"] or "")[:120])
        await audit(actor or "system", "uptik.account.create", target=a["id"], details={"platform": platform})
        await self._changed(account_id=a["id"])
        return self._account_view(a)

    async def update_account(self, aid: str, actor: str, **fields: Any) -> dict[str, Any]:
        a = await self.account(aid)
        allowed = {k: v for k, v in fields.items() if k in ("label", "runner", "device", "autopost") and v is not None}
        if "runner" in allowed and allowed["runner"] not in ("local", "device"):
            raise ToolUserError("runner: local oder device")
        if allowed.get("autopost") and a["status"] != "connected":
            raise ToolUserError("Autopost erst nach erfolgreicher Anmeldung des Kontos")
        row = await self.store.update_account(aid, **allowed)
        await audit(actor, "uptik.account.update", target=aid, details={"fields": sorted(allowed)})
        await self._changed(account_id=aid)
        return self._account_view(row or a)

    async def delete_account(self, aid: str, actor: str) -> None:
        await self.account(aid)
        if aid in self._running:
            raise ToolUserError("Für dieses Konto läuft gerade eine Veröffentlichung")
        self.vault.delete(aid)
        await self.store.delete_account(aid)
        await audit(actor, "uptik.account.delete", target=aid)
        await self._changed(account_id=aid)

    async def disconnect(self, aid: str, actor: str) -> dict[str, Any]:
        await self.account(aid)
        self.vault.delete(aid)
        row = await self.store.update_account(aid, status="not_connected", autopost=False, connected_at=None)
        await audit(actor, "uptik.account.disconnect", target=aid)
        await self._changed(account_id=aid)
        return self._account_view(row or {})

    async def resume(self, aid: str, actor: str, requeue: bool = False) -> dict[str, Any]:
        """Sir checked the account on the platform and closes the circuit breaker again."""
        a = await self.account(aid)
        n = await self.store.reset_circuit(aid, requeue=requeue, actor=actor)
        await audit(
            actor,
            "uptik.circuit.reset",
            target=aid,
            details={"requeue": requeue, "requeued": n, "code": a.get("circuit_code")},
        )
        await self._changed(account_id=aid)
        self.wake()
        return {"account": self._account_view(await self.account(aid)), "requeued": n}

    # ------------------------------------------------------------------ login (Sir logs in himself)
    async def start_login(self, aid: str) -> dict[str, Any]:
        a = await self.account(aid)
        if a["platform"] not in self.platforms():
            raise ToolUserError(f"{PLATFORM_LABEL[a['platform']]} ist nicht aktiviert")
        task = self._logins.get(aid)
        if task is not None and not task.done():
            return {"status": "login_running", "account_id": aid}
        runner = self.runner_for(a)
        ok, why = await runner.available()
        if not ok:
            await self.store.update_account(aid, status="login_failed", last_error=why)
            raise ToolUserError(f"Anmeldung nicht möglich: {why}")
        await self.store.update_account(aid, status="connecting", last_error=None)
        self._logins[aid] = spawn(self._login(a, runner), name=f"uptik-login-{aid[:8]}")
        await self._changed(account_id=aid)
        where = "auf diesem Rechner" if a["runner"] == "local" else f"auf {a['device'] or 'dem Gerät'}"
        return {
            "status": "login_started",
            "account_id": aid,
            "message": f"Ein Browserfenster mit der Anmeldeseite von {PLATFORM_LABEL[a['platform']]} öffnet sich "
            f"{where}. Bitte dort selbst anmelden – Jarvis sieht und speichert kein Passwort.",
        }

    async def _login(self, a: dict[str, Any], runner: Runner) -> None:
        adapter = self.adapters[a["platform"]]
        aid = a["id"]
        session = None
        try:
            session = await runner.open(None, headless=False)  # always visible: Sir types in it
            await session.goto(adapter.login_url)
            deadline = self.clock() + timedelta(seconds=int(self.settings["login_timeout_s"]))
            while not await adapter.is_logged_in(session):
                if self.clock() >= deadline:
                    raise PermanentError("Zeitüberschreitung bei der Anmeldung", code="login_timeout")
                await asyncio.sleep(self.login_poll_s)
            if not await adapter.confirm_login(session):
                raise PermanentError("Nach der Anmeldung ist die Upload-Seite nicht erreichbar", code="login_check")
            handle = await adapter.handle(session)
            self.vault.save(aid, await session.storage_state())
            fresh = await self.store.update_account(
                aid,
                status="connected",
                connected_at=iso(self.clock()),
                last_error=None,
                **({"handle": handle} if handle else {}),
            )
            if fresh and fresh["circuit_state"] == "tripped" and fresh["circuit_code"] == LOGIN_REQUIRED.code:
                await self.store.reset_circuit(aid, requeue=False, actor="login")
            await audit("system", "uptik.account.connected", target=aid, details={"platform": a["platform"]})
            await self._notify(
                "uptik.login",
                f"{PLATFORM_LABEL[a['platform']]} verbunden",
                f"Konto „{a['label']}“ ist angemeldet. Autopost bleibt aus, bis du ihn für das Konto einschaltest.",
            )
        except asyncio.CancelledError:
            await self.store.update_account(aid, status="not_connected", last_error="Anmeldung abgebrochen")
            raise
        except Exception as e:
            msg = e.args[0] if isinstance(e, UptikError) else f"Anmeldung fehlgeschlagen ({type(e).__name__})"
            await self.store.update_account(aid, status="login_failed", last_error=safe_message(msg))
            await self._notify(
                "uptik.login",
                f"{PLATFORM_LABEL[a['platform']]}: Anmeldung fehlgeschlagen",
                safe_message(msg),
                level="warning",
            )
        finally:
            if session is not None:
                with contextlib.suppress(Exception):
                    await session.close()
            await self._changed(account_id=aid)

    async def check_account(self, aid: str) -> dict[str, Any]:
        """Open the upload page with the stored session (headless) and update the connection state."""
        a = await self.account(aid)
        state = self.vault.load(aid)
        if state is None:
            row = await self.store.update_account(aid, status="not_connected")
            return self._account_view(row or a)
        runner = self.runner_for(a)
        ok, why = await runner.available()
        if not ok:
            raise ToolUserError(why)
        session = await runner.open(state, headless=True)
        try:
            await session.goto(self.adapters[a["platform"]].upload_url)
            await session.wait(2_000)
            logged_in = await self.adapters[a["platform"]].is_logged_in(session)
            if logged_in:
                self.vault.save(aid, await session.storage_state())
        finally:
            with contextlib.suppress(Exception):
                await session.close()
        row = await self.store.update_account(
            aid,
            status="connected" if logged_in else "not_connected",
            last_error=None if logged_in else "Sitzung abgelaufen – neu anmelden",
        )
        await self._changed(account_id=aid)
        return self._account_view(row or a)

    # ------------------------------------------------------------------ preparing and queueing posts
    def video_roots(self) -> list[Path]:
        roots = [self.uploads_dir, *self.extra_video_roots]
        roots += [Path(str(d)).expanduser() for d in self.settings.get("video_dirs") or []]
        return [r.resolve() for r in roots]

    async def resolve_video(self, ref: str) -> VideoInfo:
        ref = (ref or "").strip()
        if ref.startswith("inbox:"):
            item = await self.store.inbox_item(ref.removeprefix("inbox:"))
            if item is None:
                raise ToolUserError("Video aus dem Eingang nicht gefunden")
            path = Path(item["path"])
        elif ref.startswith("upload:"):
            name = Path(ref.removeprefix("upload:")).name
            path = self.uploads_dir / name
        else:
            path = Path(ref).expanduser()
            if not path.is_absolute():
                raise ToolUserError("Video als absoluter Pfad, „inbox:<id>“ oder „upload:<name>“ angeben")
        try:
            real = path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise ToolUserError("Videodatei nicht gefunden") from None
        if not real.is_file():
            raise ToolUserError("Das ist keine Datei")
        if not any(real.is_relative_to(r) for r in self.video_roots()):
            raise ToolUserError("Videos nur aus freigegebenen Ordnern (Uploads, Eingang, Videobibliothek, video_dirs)")
        if real.suffix.lower() not in VIDEO_EXT:
            raise ToolUserError(f"Kein Videoformat ({', '.join(VIDEO_EXT)})")
        info: VideoInfo = await self.probe(real)
        return info

    async def resolve_targets(self, a: dict[str, Any]) -> list[dict[str, Any]]:
        accounts = await self.store.accounts()
        by_id = {x["id"]: x for x in accounts}
        chosen: list[dict[str, Any]] = []
        for ref in a.get("accounts") or []:
            hit = by_id.get(ref) or next((x for x in accounts if x["label"].lower() == str(ref).lower()), None)
            if hit is None:
                raise ToolUserError(f"Unbekanntes Konto „{ref}“")
            chosen.append(hit)
        for pf in a.get("platforms") or []:
            if any(x["platform"] == pf for x in chosen):
                continue
            cands = [x for x in accounts if x["platform"] == pf and x["status"] == "connected"]
            if not cands:
                raise ToolUserError(f"Kein verbundenes {PLATFORM_LABEL.get(pf, pf)}-Konto")
            if len(cands) > 1:
                names = ", ".join(f"{x['label']} ({x['id'][:8]})" for x in cands)
                raise ToolUserError(f"Mehrere {PLATFORM_LABEL[pf]}-Konten – bitte accounts angeben: {names}")
            chosen.append(cands[0])
        if not chosen:
            raise ToolUserError("Keine Zielkonten – accounts oder platforms angeben")
        seen: set[str] = set()
        out = []
        for x in chosen:
            if x["id"] in seen:
                continue
            seen.add(x["id"])
            if x["platform"] not in self.platforms():
                raise ToolUserError(f"{PLATFORM_LABEL[x['platform']]} ist nicht aktiviert")
            if x["status"] != "connected":
                raise ToolUserError(f"Konto „{x['label']}“ ist nicht angemeldet")
            if x["circuit_state"] != "ok":
                raise ToolUserError(
                    f"Konto „{x['label']}“ ist angehalten ({x['circuit_reason']}) – manuelle Prüfung nötig"
                )
            out.append(x)
        return out

    def content_for(self, a: dict[str, Any], platform: str) -> PostContent:
        over = (a.get("per_platform") or {}).get(platform) or {}
        tags = over.get("hashtags") if over.get("hashtags") is not None else a.get("hashtags")
        return PostContent(
            caption=str(over.get("caption") if over.get("caption") is not None else a.get("caption") or ""),
            hashtags=normalize_hashtags(tags),
            title=str(over.get("title") or a.get("title") or ""),
            options=merged_options(platform, (a.get("options") or {}).get(platform)),
        )

    async def prepare(self, a: dict[str, Any], *, scheduled: bool) -> dict[str, Any]:
        """Server-side check before the approval: the approval shows exactly what will be queued."""
        await self.ensure_open()
        now = self.clock()
        out = {k: v for k, v in a.items() if k not in ("targets", "publish_at_utc")}
        video = await self.resolve_video(str(a.get("video") or ""))
        targets = await self.resolve_targets(a)
        problems: list[str] = []
        for t in targets:
            problems += validate(t["platform"], self.content_for(a, t["platform"]), video)
        if problems:
            raise ToolUserError("Nicht veröffentlichbar: " + "; ".join(dict.fromkeys(problems)))
        if scheduled:
            when = parse_publish_at(str(a.get("publish_at") or ""), now)
            out["publish_at_utc"] = iso(when)
            out["publish_at_label"] = berlin(when) + " (Berlin)"
        out.update(
            video=video.path,
            video_name=video.name,
            video_size=video.size,
            video_sha256=video.sha256,
            video_duration=video.duration_s,
            targets=[{"account_id": t["id"], "platform": t["platform"], "label": t["label"]} for t in targets],
        )
        out["accounts"] = [t["id"] for t in targets]
        out.pop("platforms", None)
        return out

    async def enqueue(self, a: dict[str, Any], ctx: ToolContext, *, scheduled: bool) -> dict[str, Any]:
        if not a.get("targets") or not a.get("video_sha256"):
            a = await self.prepare(a, scheduled=scheduled)
        path = Path(a["video"])
        if not path.is_file() or path.stat().st_size != int(a["video_size"]):
            raise ToolUserError("Die Videodatei wurde seit der Freigabe verändert oder entfernt")
        targets = await self.resolve_targets({"accounts": [t["account_id"] for t in a["targets"]]})
        now = self.clock()
        run_at = from_iso(a.get("publish_at_utc")) if scheduled else now
        assert run_at is not None
        group = str(ctx.approval_id or ctx.idempotency_key or uuid.uuid4())
        rows = []
        for t in targets:
            c = self.content_for(a, t["platform"])
            rows.append(
                dict(
                    group_id=group,
                    account_id=t["id"],
                    platform=t["platform"],
                    video_path=str(path),
                    video_name=a["video_name"],
                    video_size=a["video_size"],
                    video_sha256=a["video_sha256"],
                    video_duration=a.get("video_duration"),
                    caption=c.caption,
                    hashtags=c.hashtags,
                    title=c.title,
                    options=c.options,
                    run_at=iso(run_at),
                    scheduled=scheduled,
                    max_attempts=self.policy().max_attempts,
                    source=a.get("source") or "manual",
                    source_ref=a.get("source_ref"),
                    approval_id=str(ctx.approval_id) if ctx.approval_id else None,
                    requested_by=ctx.principal.label,
                )
            )
        posts = await self.store.enqueue(rows)
        await self._changed()
        self.wake()
        return {
            "group_id": group,
            "scheduled_for": berlin(run_at) + " (Berlin)" if scheduled else None,
            "posts": [
                {"id": p["id"], "platform": p["platform"], "account_id": p["account_id"], "status": p["status"]}
                for p in posts
            ],
        }

    async def cancel(self, pid: str, actor: str) -> dict[str, Any]:
        await self.ensure_open()
        p = await self.store.post(pid)
        if p is None:
            raise ToolUserError("Unbekannter Beitrag")
        if not await self.store.cancel(pid):
            raise ToolUserError(f"Beitrag ist {p['status']} – nur wartende oder angehaltene lassen sich abbrechen")
        await audit(actor, "uptik.post.cancel", target=pid)
        await self._changed(post_id=pid)
        return {"id": pid, "status": "cancelled"}

    async def resolve_review(self, pid: str, outcome: str, url: str | None, actor: str) -> dict[str, Any]:
        """Sir looked on the platform: a post with unknown outcome was published (or not)."""
        p = await self.store.post(pid)
        if p is None or p["status"] != "needs_review":
            raise ToolUserError("Nur Beiträge mit Status „prüfen“ lassen sich so abschließen")
        if outcome not in ("published", "failed"):
            raise ToolUserError("outcome: published oder failed")
        row = await self.store.update_post(
            pid,
            status=outcome,
            result_url=url or p.get("result_url"),
            last_error=None if outcome == "published" else "Von Sir als nicht veröffentlicht markiert",
        )
        await self.store.event(pid, p["account_id"], "reviewed", f"{actor}: {outcome}")
        await self._changed(post_id=pid)
        return row or {}

    # ------------------------------------------------------------------ worker
    async def tick(self, now: datetime | None = None) -> list[str]:
        """Run every due post that may run now. Returns the ids that were processed."""
        if not self.active() or self.state.kill_switch.engaged:
            return []
        await self.ensure_open()
        now = now or self.clock()
        due = await self.store.due(now)
        picked: list[dict[str, Any]] = []
        busy = set(self._running)
        for p in due:
            if p["account_id"] in busy:
                continue
            busy.add(p["account_id"])
            picked.append(p)
            if len(picked) >= max(1, int(self.settings["max_parallel"])):
                break
        await asyncio.gather(*(self._process(p, now) for p in picked))
        return [p["id"] for p in picked]

    async def _process(self, post: dict[str, Any], now: datetime) -> None:
        aid = post["account_id"]
        lock = self._locks.setdefault(aid, asyncio.Lock())
        async with lock:
            self._running.add(aid)
            try:
                await self._run_post(post, now)
            finally:
                self._running.discard(aid)

    async def _run_post(self, post: dict[str, Any], now: datetime) -> None:
        acct = await self.store.account(post["account_id"])
        if acct is None:
            await self.store.update_post(post["id"], status="cancelled", last_error="Konto entfernt")
            return
        if acct["circuit_state"] != "ok":
            await self.store.update_post(
                post["id"],
                status="blocked",
                error_class="manual_review",
                last_error=acct["circuit_reason"] or "Konto angehalten",
            )
            return
        leased = await self.store.lease(post["id"], now)
        if leased is None:
            return
        post = leased
        stage = {"value": "start"}

        async def on_stage(value: str) -> None:
            stage["value"] = value
            await self.store.update_post(post["id"], stage=value)

        await self._changed(post_id=post["id"], status="running")
        try:
            result = await asyncio.wait_for(self._attempt(post, acct, on_stage), PUBLISH_TIMEOUT_S)
        except ManualReviewError as e:
            await self._tripped(post, acct, BlockSignal(e.code, str(e), e.code), stage["value"])
            return
        except UptikError as e:
            await self._failed(post, acct, e, stage["value"], now)
            return
        except TimeoutError:
            await self._failed(
                post, acct, RetryableError("Zeitüberschreitung beim Veröffentlichen"), stage["value"], now
            )
            return
        except Exception as e:
            logger.warning("UpTik post %s failed: %s", post["id"], type(e).__name__)
            err = RetryableError(f"Browserfehler ({type(e).__name__}): {safe_message(str(e))}", code="browser")
            await self._failed(post, acct, err, stage["value"], now)
            return
        await self.store.update_post(
            post["id"], status="published", stage="done", result_url=result.url, last_error=None, error_class=None
        )
        await self.store.update_account(acct["id"], last_used_at=iso(self.clock()))
        await self.store.event(post["id"], acct["id"], "published", result.url or "")
        await audit(
            "system",
            "uptik.post.published",
            target=post["id"],
            details={"platform": post["platform"], "account": acct["id"], "approval": post["approval_id"]},
        )
        await self._notify(
            "uptik.published",
            f"Veröffentlicht auf {PLATFORM_LABEL[post['platform']]}",
            f"„{post['video_name']}“ auf {acct['label']}." + (f" {result.url}" if result.url else ""),
            data={"post_id": post["id"]},
        )
        await self._changed(post_id=post["id"], status="published")

    async def _attempt(self, post: dict[str, Any], acct: dict[str, Any], on_stage: Callable[[str], Any]) -> Any:
        path = Path(post["video_path"])
        if not path.is_file() or path.stat().st_size != int(post["video_size"]):
            raise PermanentError("Videodatei fehlt oder wurde verändert", code="video_missing")
        session_state = self.vault.load(acct["id"])
        if session_state is None:
            raise LOGIN_REQUIRED.error()
        runner = self.runner_for(acct)
        ok, why = await runner.available()
        if not ok:
            raise RetryableError(f"Browser nicht verfügbar: {why}", code="runner")
        adapter = self.adapters[post["platform"]]
        staged = await runner.stage_video(path)
        session = await runner.open(session_state, headless=bool(self.settings["headless"]))
        login_lost = False
        try:
            job = PublishJob(
                post["id"],
                acct["id"],
                post["platform"],
                staged,
                PostContent(post["caption"], list(post["hashtags"]), post["title"], dict(post["options"])),
                acct.get("handle") or "",
            )
            return await adapter.publish(session, job, on_stage)
        except ManualReviewError as e:
            login_lost = e.code == LOGIN_REQUIRED.code
            raise
        finally:
            if not login_lost:
                with contextlib.suppress(Exception):
                    self.vault.save(acct["id"], await session.storage_state())
            with contextlib.suppress(Exception):
                await session.close()
            with contextlib.suppress(Exception):
                await runner.release_video(staged)

    async def _tripped(self, post: dict[str, Any], acct: dict[str, Any], signal: BlockSignal, stage: str) -> None:
        after_submit = stage == SUBMITTING
        await self.store.update_post(
            post["id"],
            status="needs_review" if after_submit else "blocked",
            error_class="manual_review",
            last_error=str(signal.label),
        )
        blocked = await self.store.trip(acct["id"], signal, post["id"])
        if signal.code == LOGIN_REQUIRED.code:
            await self.store.update_account(acct["id"], status="not_connected", autopost=False)
        await audit(
            "system",
            "uptik.circuit.trip",
            target=acct["id"],
            outcome=signal.code,
            details={"post": post["id"], "blocked": blocked},
        )
        await self._notify(
            "uptik.circuit",
            f"UpTik: {PLATFORM_LABEL[acct['platform']]}-Konto „{acct['label']}“ angehalten",
            f"{signal.label}. Die Warteschlange dieses Kontos ist gestoppt ({blocked + 1} Beitrag/Beiträge), es gibt "
            "keine automatischen Wiederholungen. Bitte das Konto selbst im Browser prüfen und danach in Jarvis › "
            "UpTik „Konto freigeben“ wählen."
            + (" Der aktuelle Beitrag wurde evtl. schon veröffentlicht." if after_submit else ""),
            level="error",
            data={"account_id": acct["id"], "post_id": post["id"], "code": signal.code},
        )
        await bus.publish("uptik.circuit", account_id=acct["id"], code=signal.code)
        await self._changed(post_id=post["id"], account_id=acct["id"])

    async def _failed(
        self, post: dict[str, Any], acct: dict[str, Any], err: UptikError, stage: str, now: datetime
    ) -> None:
        msg = safe_message(str(err))
        if stage == SUBMITTING:
            await self.store.update_post(post["id"], status="needs_review", error_class=err.error_class, last_error=msg)
            await self.store.event(post["id"], acct["id"], "needs_review", msg)
            await self._notify(
                "uptik.needs_review",
                f"{PLATFORM_LABEL[post['platform']]}: Veröffentlichung unklar",
                f"„{post['video_name']}“ auf {acct['label']}: {msg}. Nicht automatisch wiederholt "
                "(Gefahr eines Doppelposts) – bitte auf der Plattform prüfen.",
                level="warning",
                data={"post_id": post["id"]},
            )
        elif err.error_class == "retryable" and self.policy().should_retry(int(post["attempts"])):
            wait = self.policy().delay(int(post["attempts"]))
            nxt = now + timedelta(seconds=wait)
            await self.store.update_post(
                post["id"],
                status="retry_wait",
                stage="",
                next_attempt_at=iso(nxt),
                error_class="retryable",
                last_error=msg,
            )
            await self.store.event(
                post["id"], acct["id"], "retry", f"Versuch {post['attempts']}: {msg}; nächster {berlin(nxt)}"
            )
        else:
            await self.store.update_post(post["id"], status="failed", error_class=err.error_class, last_error=msg)
            await self.store.event(post["id"], acct["id"], "failed", msg)
            await self._notify(
                "uptik.failed",
                f"{PLATFORM_LABEL[post['platform']]}: Veröffentlichung fehlgeschlagen",
                f"„{post['video_name']}“ auf {acct['label']}: {msg}",
                level="warning",
                data={"post_id": post["id"]},
            )
        await self._changed(post_id=post["id"])

    # ------------------------------------------------------------------ hand-over from the video production
    async def on_video_ready(self, clip: VideoClip) -> dict[str, Any] | None:
        """Narrow entry point for finished clips. Never raises: a connector problem must not touch the production.

        Off unless ``handoff_enabled``. The clip lands in the inbox; for accounts with autopost on (default off) the
        post goes to the approval system like any other – nothing is published without it."""
        try:
            if not self.active() or not self.settings["handoff_enabled"]:
                return None
            await self.ensure_open()
            path = Path(clip.path)
            if not path.is_file():
                return None
            item = await self.store.add_inbox(
                source=clip.source,
                source_ref=clip.source_ref,
                path=str(path),
                name=path.name,
                size=path.stat().st_size,
                duration=clip.duration_s,
                title=clip.title,
                captions=clip.captions,
            )
            await self._changed()
            targets = [
                a
                for a in await self.store.accounts()
                if a["autopost"]
                and a["status"] == "connected"
                and a["circuit_state"] == "ok"
                and a["platform"] in self.platforms()
                and (not clip.platforms or a["platform"] in clip.platforms)
            ]
            if not targets:
                return {"inbox_id": item["id"], "queued": False}
            args: dict[str, Any] = {
                "video": f"inbox:{item['id']}",
                "caption": clip.caption,
                "hashtags": clip.hashtags,
                "title": clip.title[:100],
                "per_platform": {pf: c for pf, c in clip.captions.items() if pf in PLATFORMS},
                "accounts": [a["id"] for a in targets],
                "source": clip.source,
                "source_ref": clip.source_ref,
            }
            tool = "uptik_publish_now"
            hhmm = str(self.settings.get("handoff_publish_time") or "")
            if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", hhmm):
                tool = "uptik_schedule_post"
                when = at_local_time(hhmm, self.clock())
                args["publish_at"] = when.astimezone(UTC).isoformat()
            ctx = ToolContext(principal=SYSTEM, idempotency_key=f"uptik-handoff:{clip.source}:{clip.source_ref}")
            res = await self.state.executor.execute(tool, args, ctx)
            await bus.publish("uptik.handoff", inbox_id=item["id"], status=res.status)
            return {
                "inbox_id": item["id"],
                "queued": res.status in ("ok", "approval_required", "duplicate"),
                "status": res.status,
                "approval_id": str(res.approval_id) if res.approval_id else None,
            }
        except Exception:
            logger.exception("UpTik hand-over failed (video production is not affected)")
            return None

    # ------------------------------------------------------------------ status
    async def status(self) -> dict[str, Any]:
        await self.ensure_open()
        accounts = await self.accounts()
        runner_ok, runner_why = await self.runner_for({"runner": "local", "device": ""}).available()
        return {
            "enabled": bool(self.connector() and self.connector().enabled),
            "active": self.active(),
            "kill_switch": bool(self.state.kill_switch.engaged),
            "platforms": [{"id": p, "label": PLATFORM_LABEL[p]} for p in self.platforms()],
            "settings": {k: v for k, v in self.settings.items() if k != "browser_path"},
            "local_runner": {"ok": runner_ok, "reason": runner_why},
            "accounts": accounts,
            "tripped": [a["id"] for a in accounts if a["circuit_state"] != "ok"],
            "counts": await self.store.counts(),
        }

    # ------------------------------------------------------------------ helpers
    async def _notify(
        self, event: str, title: str, body: str, *, level: str = "info", data: dict[str, Any] | None = None
    ) -> None:
        try:
            await self.state.notifier.notify(event, title, body, level=level, data=data or {})
        except Exception:
            logger.warning("UpTik notification %s failed", event, exc_info=True)

    async def _changed(self, **data: Any) -> None:
        await bus.publish("uptik.updated", **{k: v for k, v in data.items() if v is not None})
