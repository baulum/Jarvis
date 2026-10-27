"""Change detection, the build queue and distribution of Android app updates.

Triggers: a finished coding job (``coding.updated`` → the ship flow has already merged and fetched), a periodic
check of ``origin/<branch>`` (pushes from elsewhere), the core's start, and Sir's buttons (check / build / retry /
rebuild an older release). Every trigger only *requests* a check; one worker handles them one after another:

- several triggers within ``debounce_s`` become one check, a trigger during a build becomes one check afterwards;
- the commit is resolved when the check runs, so the newest state is built, never a stale one;
- a build only starts when files of the app changed since the newest release (``changes.app_files``) – and not
  again for a commit that already failed unless the app changed since (or Sir presses "retry"); a failure
  because a tool was missing is retried by itself as soon as the tool is there.

Nothing here blocks the rest of Jarvis: the build is a subprocess, errors end up in the status (``snapshot``),
in the log file (``<log_dir>/android-build.log``) and as a notification – never as an exception elsewhere.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import secrets
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.config import REPO_ROOT
from jarvis.events import bus, spawn
from jarvis.mobile.ota.builder import APPLICATION_ID, ApkBuilder, BuildError, check_apk
from jarvis.mobile.ota.changes import APP_DIR, affects_app, app_files
from jarvis.mobile.ota.releases import Release, ReleaseStore, now_iso, sha256_file, write_json_atomic
from jarvis.mobile.ota.signing import SigningError, SigningManager
from jarvis.mobile.ota.toolchain import Runner, Toolchain, ToolchainError, detect
from jarvis.mobile.ota.versioning import VersionError, next_version_code, pubspec_version, version_name

logger = logging.getLogger(__name__)

ACTIVE = ("preparing", "building", "verifying", "publishing")
# failures that fix themselves once Sir installs/frees something: retried automatically when the check passes
RECOVERABLE = {"flutter_missing", "android_sdk_missing", "build_tools_missing", "jdk_missing", "disk_full"}
DEVICE_STATES = {
    "idle", "checking", "up_to_date", "downloading", "paused", "verifying", "ready", "waiting_user", "installing",
    "installed", "failed", "unsupported",
}  # fmt: skip
HISTORY = 20
INSTALL_LINK_MAX_USES = 20
STARTUP_DELAY_S = 45.0


@dataclass(slots=True)
class BuildRequest:
    reason: str
    force: bool = False  # skip the "is a build needed?" decision (manual build / retry / rebuild)
    commit: str | None = None  # explicit commit (rebuild of an older release); else the branch head
    rebuild_of: int | None = None
    actor: str = "system"


@dataclass(slots=True)
class InstallLink:
    expires: float
    uses: int = 0


class AppUpdateService:
    def __init__(self, app: Any, *, repo: Path = REPO_ROOT, runner: Runner | None = None) -> None:
        self.app = app
        self.cfg = app.settings.mobile.app_update
        self.repo = repo
        self.runner = runner or Runner()
        data_dir = Path(app.settings.paths.data_dir)
        self.dir = data_dir / "app-updates"
        self.releases = ReleaseStore(self.dir / "releases")
        self.signing = SigningManager(
            data_dir / "android-signing", self.cfg.keystore_password_secret, runner=self.runner
        )
        self.state_file = self.dir / "state.json"
        self.log_file = Path(app.settings.paths.log_dir) / "android-build.log"
        self.builder = ApkBuilder(repo, self.dir / "checkout", self.runner, self.log_file)
        self.state: dict[str, Any] = self._load()
        self.toolchain: dict[str, Any] = {"ok": None}
        self._pending: BuildRequest | None = None
        self._due = 0.0
        self._wake = asyncio.Event()
        self._busy = False
        self._tasks: list[asyncio.Task[Any]] = []
        self._links: dict[str, InstallLink] = {}
        self._last_report_event: dict[str, tuple[str, float]] = {}

    # ------------------------------------------------------------------ persistence
    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        data.setdefault("last_version_code", 0)
        data.setdefault("build", {"state": "idle"})
        data.setdefault("history", [])
        data.setdefault("devices", {})
        data.setdefault("last_failure", None)
        data.setdefault("last_check", None)
        if data["build"].get("state") in ACTIVE:  # the core stopped during a build: not Sir's fault, build again
            data["build"].update(
                state="failed",
                cause="interrupted",
                finished_at=now_iso(),
                message="Build durch einen Neustart von Jarvis unterbrochen – wird erneut geprüft.",
            )
        return data

    def _save(self) -> None:
        try:
            write_json_atomic(self.state_file, self.state)
        except OSError:
            logger.warning("App update state not writable", exc_info=True)

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._tasks.append(spawn(self._worker(), name="app-update-worker"))
        if self.cfg.enabled and self.cfg.auto_build:
            self._tasks.append(spawn(self._poll(), name="app-update-poll"))
            bus.subscribe("coding.updated", self._on_coding)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._tasks.clear()

    async def _poll(self) -> None:
        await asyncio.sleep(STARTUP_DELAY_S)  # let the core settle (and a self-restart finish) first
        while True:
            self.request(BuildRequest("regelmäßige Prüfung"), debounce=0)
            await asyncio.sleep(self.cfg.poll_interval_minutes * 60)

    async def _on_coding(self, ev: Any) -> None:
        if ev.data.get("status") == "completed":
            self.request(BuildRequest(f"Coding-Job {str(ev.data.get('job_id', ''))[:8]} abgeschlossen"))

    # ------------------------------------------------------------------ queue
    def request(self, req: BuildRequest, *, debounce: float | None = None) -> dict[str, Any]:
        """Queues a check/build (never two at once). A manual request replaces a waiting automatic one; an
        automatic one never replaces a waiting manual one."""
        if self._pending is None or req.force or not self._pending.force:
            self._pending = req
        wait = 0.0 if req.force else float(self.cfg.debounce_s if debounce is None else debounce)
        self._due = time.monotonic() + wait
        self._wake.set()
        return self.snapshot()

    async def _worker(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            while self._pending is not None:
                delay = self._due - time.monotonic()
                if delay > 0:  # debounce: a new trigger restarts the wait
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._wake.wait(), delay)
                    self._wake.clear()
                    continue
                req, self._pending = self._pending, None
                self._busy = True
                try:
                    await self.run(req)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # never kill the worker
                    logger.exception("App update run failed")
                    self._finish_failed(None, "internal", f"Interner Fehler: {type(e).__name__}: {e}"[:400])
                finally:
                    self._busy = False
                    await self._publish()

    # ------------------------------------------------------------------ git
    async def _git(self, *args: str, timeout_s: float = 60) -> str | None:
        code, out = await self.runner.run(["git", "-C", str(self.repo), *args], timeout_s=timeout_s)
        return out.strip() if code == 0 else None

    async def target_commit(self) -> str | None:
        if self.cfg.fetch and await self._git("remote", "get-url", "origin") is not None:
            await self._git("fetch", "--quiet", "origin", self.cfg.branch, timeout_s=120)  # offline: last known state
        for ref in (f"origin/{self.cfg.branch}", self.cfg.branch):
            out = await self._git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
            if out:
                return out.splitlines()[-1].strip()
        return None

    async def changed_files(self, old: str, new: str) -> list[str] | None:
        out = await self._git("diff", "--name-only", old, new)
        return None if out is None else [f for f in out.splitlines() if f.strip()]

    async def needs_build(self, target: str) -> tuple[bool, str]:
        """Is a new APK due for ``target``? (decision + reason for the status)"""
        newest = next(iter(self.releases.all()), None)
        if newest is None:
            return True, "Noch kein Release vorhanden – erste signierte Version"
        if newest.commit == target:
            return False, f"Release {newest.version_code} ist bereits auf diesem Stand"
        files = await self.changed_files(newest.commit, target)
        if not affects_app(files):
            return False, f"Keine Änderung an der App seit Release {newest.version_code}"
        fail = self.state.get("last_failure") or {}
        if fail.get("commit"):
            if fail.get("cause") in RECOVERABLE:
                return True, "Erneuter Versuch – die fehlende Voraussetzung wird neu geprüft"
            since = [] if fail["commit"] == target else await self.changed_files(fail["commit"], target)
            if since is not None and not affects_app(since):
                return False, (
                    f"Build für {fail['commit'][:7]} ist fehlgeschlagen, seitdem keine App-Änderung – "
                    "„Wiederholen“ baut erneut"
                )
        n = len(app_files(files)) if files is not None else None
        return True, f"{n} App-Datei(en) seit Release {newest.version_code} geändert" if n else "App geändert"

    async def changelog(self, old: str | None, new: str) -> list[dict[str, str]]:
        rng = f"{old}..{new}" if old else new
        out = await self._git("log", "--no-merges", "--format=%h%x09%s", "-n", "30", rng, "--", APP_DIR)
        rows = []
        for line in (out or "").splitlines():
            sha, _, subject = line.partition("\t")
            if sha.strip():
                rows.append({"commit": sha.strip(), "subject": subject.strip()[:200]})
        return rows

    # ------------------------------------------------------------------ one run
    def _killswitch(self) -> bool:
        return bool(getattr(getattr(self.app, "kill_switch", None), "engaged", False))

    async def run(self, req: BuildRequest) -> None:
        if self._killswitch():
            self.state["last_check"] = {"at": now_iso(), "decision": "skipped", "reason": "Not-Aus aktiv"}
            self._save()
            return
        target = req.commit or await self.target_commit()
        if not target:
            self.state["last_check"] = {"at": now_iso(), "decision": "skipped",
                                        "reason": f"Branch {self.cfg.branch} im Repository nicht gefunden"}  # fmt: skip
            self._save()
            return
        if not req.force:
            due, why = await self.needs_build(target)
            self.state["last_check"] = {"at": now_iso(), "target": target, "decision": "build" if due else "none",
                                        "reason": why, "trigger": req.reason}  # fmt: skip
            self._save()
            if not due:
                return
        else:
            self.state["last_check"] = {"at": now_iso(), "target": target, "decision": "build",
                                        "reason": req.reason, "trigger": req.reason}  # fmt: skip
        await self._build(req, target)

    def _set_build(self, **fields: Any) -> None:
        self.state["build"].update(fields)
        self._save()
        spawn(self._publish(), name="app-update-event")

    def _finish_failed(self, commit: str | None, cause: str, message: str, stage: str | None = None) -> None:
        b = self.state["build"]
        b.update(state="failed", cause=cause, stage=stage or b.get("state"), message=message, finished_at=now_iso())
        b["log_tail"] = self._log_tail()
        if commit and cause != "interrupted":
            self.state["last_failure"] = {"commit": commit, "cause": cause, "message": message, "at": now_iso()}
        self._history(b)
        self._save()

    def _history(self, b: dict[str, Any]) -> None:
        keep = ("state", "commit", "version_code", "version_name", "reason", "message", "cause", "started_at",
                "finished_at", "rebuild_of")  # fmt: skip
        self.state["history"] = ([{k: b.get(k) for k in keep}] + self.state["history"])[:HISTORY]

    async def _build(self, req: BuildRequest, commit: str) -> None:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.log_file.write_text(f"== {now_iso()} Build {commit} ({req.reason})\n", encoding="utf-8")
        self.state["build"] = {"state": "preparing", "commit": commit, "reason": req.reason, "actor": req.actor,
                               "rebuild_of": req.rebuild_of, "started_at": now_iso(),
                               "message": "Prüfe Werkzeuge und Signaturschlüssel …"}  # fmt: skip
        self._set_build()
        try:
            tc = await self._toolchain()
            free = shutil.disk_usage(self.dir.parent if not self.dir.exists() else self.dir).free / 1e9
            if free < self.cfg.min_free_gb:
                raise BuildError("preparing", f"Zu wenig Speicher auf dem Mac: {free:.1f} GB frei, mindestens "
                                 f"{self.cfg.min_free_gb:g} GB nötig", cause="disk_full")  # fmt: skip
            existing = self.releases.all()
            key = await self.signing.ensure(tc.keytool, existing[0].signing_cert_sha256 if existing else None)
            self._set_build(message=f"Checkout {commit[:7]} …")
            await self.builder.prepare(commit)
            pubspec = (self.builder.app_dir / "pubspec.yaml").read_text(encoding="utf-8")
            semver, pub_build = pubspec_version(pubspec)
            code = next_version_code(
                int(self.state["last_version_code"]), [r.version_code for r in existing], pub_build
            )
            name = version_name(semver, code, commit)
            self._set_build(state="building", version_code=code, version_name=name,
                            message=f"flutter build apk {name} …")  # fmt: skip
            apk = await self.builder.build(
                tc, key, version_code=code, version_name=name, target=self.cfg.target_platform,
                timeout_s=self.cfg.build_timeout_minutes * 60,
            )  # fmt: skip
            self._set_build(state="verifying", message="Prüfe Signatur, Paket und Version …")
            info = await self.builder.inspect(tc, apk)
            check_apk(info, version_code=code, version_name=name, fingerprint=key.fingerprint)
            self._set_build(state="publishing", message="Speichere Release …")
            previous = next((r for r in existing if not r.withdrawn), None)
            log = await self.changelog(previous.commit if previous else None, commit)
            if req.rebuild_of is not None:
                log.insert(
                    0, {"commit": commit[:7], "subject": f"Wiederherstellung des Stands von Release {req.rebuild_of}"}
                )
            rel = Release(
                application_id=APPLICATION_ID, version_code=code, version_name=name, commit=commit,
                sha256=await asyncio.to_thread(sha256_file, apk), size=apk.stat().st_size,
                signing_cert_sha256=key.fingerprint, file=f"jarvis-mobile-{code}.apk", created_at=now_iso(),
                branch=self.cfg.branch, min_sdk=info.min_sdk, target_sdk=info.target_sdk, changelog=log,
                rebuild_of=req.rebuild_of,
            )  # fmt: skip
            await asyncio.to_thread(self.releases.add, apk, rel)
        except ToolchainError as e:
            self.toolchain = {"ok": False, "cause": e.cause, "error": str(e), "checked_at": now_iso()}
            return await self._failed(commit, e.cause, str(e))
        except SigningError as e:
            return await self._failed(commit, e.cause, str(e))
        except BuildError as e:
            return await self._failed(commit, e.cause, str(e), e.stage)
        except (VersionError, OSError) as e:
            return await self._failed(commit, "build", f"{type(e).__name__}: {e}"[:400])
        self.state["last_version_code"] = max(int(self.state["last_version_code"]), code)
        self.state["last_failure"] = None
        protect = {
            int(d["version_code"]) for d in self.state["devices"].values() if str(d.get("version_code", "")).isdigit()
        }
        removed = self.releases.prune(self.cfg.keep_releases, protect)
        b = self.state["build"]
        pruned = f"; {len(removed)} alte(s) entfernt" if removed else ""
        b.update(
            state="ready",
            finished_at=now_iso(),
            sha256=rel.sha256,
            size=rel.size,
            message=f"Release {name} gebaut und geprüft{pruned}",
        )
        b.pop("log_tail", None)
        self._save()
        await self.distribute(rel)
        await self._notify("app_update.ready", f"Handy-App {name} bereit", self._release_text(rel), "info", rel)

    async def _failed(self, commit: str, cause: str, message: str, stage: str | None = None) -> None:
        self._finish_failed(commit, cause, message, stage)
        logger.warning("App update build failed (%s): %s", cause, message)
        await self._notify("app_update.failed", "Handy-App-Update fehlgeschlagen", message, "warning", None)

    async def _toolchain(self) -> Toolchain:
        from jarvis.connectors.generic import shell_allowlist

        tc = await detect(
            self.cfg,
            extra_path=getattr(self.app.settings.coding, "extra_path", None) or [],
            allowlist=shell_allowlist(self.app.permissions),
            runner=self.runner,
        )
        self.toolchain = {"ok": True, "checked_at": now_iso(), **tc.summary()}
        return tc

    async def check_toolchain(self) -> dict[str, Any]:
        try:
            await self._toolchain()
        except ToolchainError as e:
            self.toolchain = {"ok": False, "cause": e.cause, "error": str(e), "checked_at": now_iso()}
        return self.toolchain

    def _log_tail(self, lines: int = 25) -> str:
        try:
            text = self.log_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return "\n".join(text.splitlines()[-lines:])[-3000:]

    # ------------------------------------------------------------------ distribution
    async def distribute(self, rel: Release | None = None) -> dict[str, Any]:
        """Tells the paired phones about the newest release (encrypted push ``app_update``). Without push they
        find it themselves: at app start, when it returns to the foreground and every few hours."""
        rel = rel or self.releases.latest()
        if rel is None:
            return {"sent": 0, "message": "Kein Release vorhanden"}
        results: list[Any] = []
        try:
            results = await self.app.push.send_all(
                "app_update", {"version_code": rel.version_code, "version_name": rel.version_name}, ttl_s=3 * 86400
            )
        except Exception as e:
            logger.warning("App update push failed: %s", e)
        sent = sum(1 for r in results if r.ok)
        msg = (
            f"An {sent} Handy(s) per Push gemeldet"
            if sent
            else "Bereit – kein Push zugestellt; die App prüft beim Öffnen und alle paar Stunden selbst"
        )
        b = self.state["build"]
        if b.get("version_code") == rel.version_code and b.get("state") in ("ready", "distributed"):
            b.update(
                state="distributed", distributed_at=now_iso(), push_sent=sent, message=f"{rel.version_name}: {msg}"
            )
            self._history(b)
            self._save()
        await self._publish()
        return {"sent": sent, "message": msg, "version_code": rel.version_code}

    async def _notify(self, event: str, title: str, body: str, level: str, rel: Release | None) -> None:
        notifier = getattr(self.app, "notifier", None)
        if notifier is None:
            return
        data = {"version_code": rel.version_code, "version_name": rel.version_name} if rel else {}
        with contextlib.suppress(Exception):
            await notifier.notify(event, title, body[:600], level=level, data=data)

    @staticmethod
    def _release_text(rel: Release) -> str:
        lines = [f"• {c['subject']}" for c in rel.changelog[:5]]
        return f"{rel.size / 1e6:.1f} MB, Commit {rel.commit[:7]}." + ("\n" + "\n".join(lines) if lines else "")

    async def _publish(self) -> None:
        with contextlib.suppress(Exception):
            await bus.publish("app_update.status", **self.brief())

    # ------------------------------------------------------------------ manual actions
    def withdraw(self, code: int, withdrawn: bool, reason: str | None) -> Release:
        rel = self.releases.set_withdrawn(code, withdrawn, reason)
        spawn(self._publish(), name="app-update-event")
        return rel

    def rebuild(self, code: int, actor: str) -> dict[str, Any]:
        """Recovery: Android never installs a lower versionCode over a higher one (no downgrade without
        uninstalling), so "going back" means building the old commit again under a new, higher versionCode."""
        rel = self.releases.get(code)
        if rel is None:
            raise KeyError(code)
        return self.request(
            BuildRequest(
                f"Wiederherstellung von Release {code}", force=True, commit=rel.commit, rebuild_of=code, actor=actor
            )
        )

    # ------------------------------------------------------------------ phones
    def record_report(self, device_id: str, name: str, report: dict[str, Any]) -> None:
        state = report.get("state")
        if state not in DEVICE_STATES:
            state = "idle"
        entry = {
            **{k: v for k, v in report.items() if v is not None},
            "state": state,
            "name": name,
            "updated_at": now_iso(),
        }
        prev = self.state["devices"].get(device_id) or {}
        self.state["devices"][device_id] = entry
        if prev.get("state") != state or prev.get("version_code") != entry.get("version_code"):
            self._save()  # progress ticks stay in memory; state changes are persisted
        last = self._last_report_event.get(device_id)
        if last is None or last[0] != state or time.monotonic() - last[1] > 2:
            self._last_report_event[device_id] = (state, time.monotonic())
            spawn(self._publish(), name="app-update-event")

    # ------------------------------------------------------------------ first installation (no pairing yet)
    def create_install_link(self) -> tuple[str, float]:
        token = secrets.token_urlsafe(24)
        expires = time.time() + self.cfg.install_link_ttl_hours * 3600
        self._links = {h: lk for h, lk in self._links.items() if lk.expires > time.time()}
        self._links[_hash(token)] = InstallLink(expires)
        return token, expires

    def use_install_link(self, token: str, *, count: bool) -> bool:
        lk = self._links.get(_hash(token))
        if lk is None or lk.expires < time.time() or lk.uses >= INSTALL_LINK_MAX_USES:
            return False
        if count:
            lk.uses += 1
        return True

    # ------------------------------------------------------------------ status
    def brief(self) -> dict[str, Any]:
        b = self.state["build"]
        latest = self.releases.latest()
        return {
            "build_state": b.get("state"),
            "build_message": b.get("message"),
            "version_code": b.get("version_code"),
            "latest_version_code": latest.version_code if latest else None,
            "queued": self._pending is not None,
        }

    def snapshot(self) -> dict[str, Any]:
        rels = self.releases.all()
        latest = next((r for r in rels if not r.withdrawn), None)
        b = dict(self.state["build"])
        if b.get("state") in ACTIVE:
            b["log_tail"] = self._log_tail(12)
        return {
            "enabled": self.cfg.enabled,
            "auto_build": self.cfg.auto_build,
            "branch": self.cfg.branch,
            "application_id": APPLICATION_ID,
            "build": b,
            "queued": self._pending is not None,
            "queued_reason": self._pending.reason if self._pending else None,
            "busy": self._busy,
            "last_check": self.state.get("last_check"),
            "last_failure": self.state.get("last_failure"),
            "latest": latest.public() if latest else None,
            "releases": [r.public() for r in rels],
            "history": self.state["history"][:10],
            "devices": [{"device_id": k, **v} for k, v in self.state["devices"].items()],
            "signing": self.signing.info(),
            "toolchain": self.toolchain,
            "keep_releases": self.cfg.keep_releases,
            "log_file": str(self.log_file),
        }


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
