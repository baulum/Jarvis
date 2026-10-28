"""Social media service: settings, the daily schedule, the production pipeline (as playbook steps), e-mail and
publishing. Everything runs on the existing building blocks: the scheduler starts the playbook
``social_daily_video`` (task engine: persisted steps, leases, resume after restarts), e-mail and publishing go
through the ToolExecutor (policy, approvals, idempotency ledger, audit)."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
import wave
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar
from zoneinfo import ZoneInfo

from sqlalchemy import select

from jarvis.db import session_scope
from jarvis.db.models import Schedule
from jarvis.events import bus, spawn
from jarvis.jobs.engine import Playbook, StepContext, StepDef, StepFail, StepSkip
from jarvis.security.audit import audit
from jarvis.social import captions as cap
from jarvis.social import emailer, links, qa
from jarvis.social import storyboard as sb
from jarvis.social.brand import load_brand, load_inspiration
from jarvis.social.concept import ConceptRejected, generate, revise_storyboard
from jarvis.social.media import MediaError, loudness, probe
from jarvis.social.platforms import CONNECTOR_ID, PLATFORMS, PlatformConnector
from jarvis.social.render import RenderError, Tools, check_dependencies, render_video
from jarvis.social.repo import AlreadyProduced, SocialRepo, publication_dict, video_dict
from jarvis.tools.executor import ToolUserError

if TYPE_CHECKING:
    from jarvis.app_state import AppState

logger = logging.getLogger(__name__)
PLAYBOOK = "social_daily_video"
SCHEDULE_NAME = "PJE Systems Daily Social Video"
DAILY_KEY = "social-daily"


def cron_for(hhmm: str) -> str:
    h, m = hhmm.split(":")
    return f"{int(m)} {int(h)} * * *"


def time_of(cron: str) -> str:
    parts = cron.split()
    try:
        return f"{int(parts[1]):02d}:{int(parts[0]):02d}"
    except (IndexError, ValueError):
        return ""


class SocialSettings:
    """Sir's switches (``<data_dir>/social-media.json``). Daily on/off and time live in the schedule itself."""

    DEFAULTS: ClassVar[dict[str, Any]] = {
        "email_enabled": True,
        "email_recipient": "",
        "publish_time": None,
        "autopost_enabled": False,
        "platforms": {p: {"autopost": False} for p in PLATFORMS},
        "voiceover": False,
        "hint": "",
    }

    def __init__(self, path: Path, owner_email: str) -> None:
        self.path = path
        self.owner_email = owner_email

    def load(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.warning("social-media.json unreadable – defaults")
        out = {**json.loads(json.dumps(self.DEFAULTS)), **{k: v for k, v in data.items() if k in self.DEFAULTS}}
        out["platforms"] = {
            p: {"autopost": bool((data.get("platforms") or {}).get(p, {}).get("autopost", False))} for p in PLATFORMS
        }
        out["email_recipient"] = out["email_recipient"] or self.owner_email
        return out

    def save(self, changes: dict[str, Any]) -> dict[str, Any]:
        cur = self.load()
        for k, v in changes.items():
            if k == "platforms" and isinstance(v, dict):
                for p, val in v.items():
                    if p in PLATFORMS and isinstance(val, dict) and "autopost" in val:
                        cur["platforms"][p]["autopost"] = bool(val["autopost"])
            elif k in self.DEFAULTS:
                cur[k] = v
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        return cur


class SocialMediaService:
    def __init__(self, state: AppState, repo: SocialRepo | None = None) -> None:
        self.state = state
        self.cfg = state.settings.social_media
        self.tz = ZoneInfo(self.cfg.timezone)
        self.repo = repo or SocialRepo(self.cfg.timezone)
        self.settings = SocialSettings(state.settings.paths.data_dir / "social-media.json", self.cfg.owner_email)
        self.library_dir = self.cfg.library_dir or (state.settings.paths.data_dir / "social-videos")
        self.work_root = state.settings.paths.data_dir / "social-work"
        self.deps: dict[str, Any] = {"ok": None, "problems": [], "checked_at": None}
        self._ticker: asyncio.Task[None] | None = None
        self.progress: dict[str, Any] = {}

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        if not self.cfg.enabled:
            return
        self.library_dir.mkdir(parents=True, exist_ok=True)
        try:
            await self.ensure_schedule()
        except Exception:
            logger.exception("Social schedule could not be ensured")
        spawn(self.refresh_dependencies(), name="social-deps")
        self._ticker = asyncio.create_task(self._tick_loop())

    async def stop(self) -> None:
        if self._ticker:
            self._ticker.cancel()

    async def refresh_dependencies(self) -> dict[str, Any]:
        try:
            res = await check_dependencies(self.cfg)
        except Exception as e:
            res = {"ok": False, "problems": [f"Prüfung fehlgeschlagen: {e}"]}
        res["checked_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        self.deps = res
        if not res.get("ok"):
            logger.warning("Social video dependencies missing: %s", res.get("problems"))
        return res

    # ------------------------------------------------------------------ schedule
    async def schedule(self) -> Schedule | None:
        async with session_scope() as s:
            rows = (await s.execute(select(Schedule))).scalars().all()
        return next((r for r in rows if (r.action or {}).get("playbook") == PLAYBOOK), None)

    async def ensure_schedule(self) -> Schedule:
        sched = await self.schedule()
        if sched is not None:
            return sched
        action = {
            "type": "playbook",
            "playbook": PLAYBOOK,
            "params": {"trigger": "schedule"},
            "daily_key": DAILY_KEY,  # Scheduler: idempotency key per local day → one task per day
            "catch_up": "same_day",  # Scheduler: a missed run is only made up on the same day
        }
        return await self.state.scheduler.create(
            SCHEDULE_NAME, cron_for(self.cfg.default_time), action, "social", tz=self.cfg.timezone
        )

    async def schedule_info(self) -> dict[str, Any]:
        sched = await self.schedule()
        if sched is None:
            return {"exists": False, "enabled": False, "time": self.cfg.default_time, "timezone": self.cfg.timezone}
        return {
            "exists": True,
            "id": str(sched.id),
            "name": sched.name,
            "enabled": sched.enabled,
            "time": time_of(sched.cron),
            "cron": sched.cron,
            "timezone": sched.timezone,
            "next_run_at": sched.next_run_at.isoformat() if sched.next_run_at and sched.enabled else None,
            "last_run_at": sched.last_run_at.isoformat() if sched.last_run_at else None,
            "last_status": sched.last_status,
        }

    async def set_production(
        self, actor: str, *, enabled: bool | None = None, time: str | None = None
    ) -> dict[str, Any]:
        from jarvis.jobs.scheduler import next_run

        sched = await self.ensure_schedule()
        async with session_scope() as s:
            row = await s.get(Schedule, sched.id)
            assert row is not None
            if time is not None:
                row.cron = cron_for(time)
            if enabled is not None:
                row.enabled = enabled
            row.next_run_at = next_run(row.cron, row.timezone)
        await audit(actor, "social.schedule", target=str(sched.id), details={"enabled": enabled, "time": time})
        await bus.publish("schedule.updated", id=str(sched.id))
        return await self.schedule_info()

    # ------------------------------------------------------------------ settings & status
    def runtime_settings(self) -> dict[str, Any]:
        return self.settings.load()

    def platform(self, pf: str) -> PlatformConnector | None:
        conn = self.state.connectors.connectors.get(CONNECTOR_ID[pf])
        return conn if isinstance(conn, PlatformConnector) else None

    def can_autopost(self, pf: str, settings: dict[str, Any] | None = None) -> bool:
        """Autopost only if globally on AND on for this platform AND the connector is enabled AND connected."""
        st = settings or self.settings.load()
        conn = self.platform(pf)
        return bool(
            st.get("autopost_enabled")
            and st.get("platforms", {}).get(pf, {}).get("autopost")
            and conn is not None
            and conn.enabled
            and conn.connected
        )

    def platforms_status(self) -> list[dict[str, Any]]:
        st = self.settings.load()
        out = []
        for pf in PLATFORMS:
            conn = self.platform(pf)
            base = conn.public_status() if conn else {"platform": pf, "enabled": False, "connected": False}
            h = self.state.connectors.health.get(CONNECTOR_ID[pf])
            out.append(
                {
                    **base,
                    "autopost": st["platforms"][pf]["autopost"],
                    "will_autopost": self.can_autopost(pf, st),
                    "health": h.to_dict() if h else None,
                }
            )
        return out

    async def status(self) -> dict[str, Any]:
        latest = await self.repo.list_videos(limit=6)
        last_ready = next((v for v in latest if v.status == "ready"), None)
        failed = [v for v in latest if v.status == "failed"][:3]
        pubs = await self.repo.publications(limit=10)
        return {
            "enabled": self.cfg.enabled,
            "dependencies": self.deps,
            "schedule": await self.schedule_info(),
            "settings": self.runtime_settings(),
            "owner_email": self.cfg.owner_email,
            "producing": [video_dict(v, full=False) for v in latest if v.status == "producing"],
            "last_video": video_dict(last_ready, full=False) if last_ready else None,
            "recent_errors": [
                {"id": str(v.id), "title": v.title, "error": v.error, "at": v.updated_at.isoformat()} for v in failed
            ],
            "recent_publications": [publication_dict(p) for p in pubs],
            "counts": await self.repo.count(),
            "progress": self.progress,
        }

    def file_path(self, file_name: str | None) -> Path | None:
        if not file_name or "/" in file_name or "\\" in file_name or file_name.startswith("."):
            return None
        p = (self.library_dir / file_name).resolve()
        return p if p.is_file() and p.parent == self.library_dir.resolve() else None

    def public_base(self) -> str:
        return (self.cfg.public_base_url or self.state.settings.server.public_base_url or "").rstrip("/")

    def download_url(self, vid: uuid.UUID, hours: int | None = None) -> str | None:
        base = self.public_base()
        if not base:
            return None
        token = links.sign(vid, (hours or self.cfg.link_ttl_hours) * 3600)
        return f"{base}/api/social/download/{token}"

    # ------------------------------------------------------------------ start a production
    async def produce(self, principal: Any, *, hint: str = "", parent_id: uuid.UUID | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"trigger": "regenerate" if parent_id else "manual", "hint": hint[:500]}
        if parent_id:
            params["parent_id"] = str(parent_id)
        task = await self.state.tasks.create(PLAYBOOK, params, principal)
        return {"task_id": str(task.id), "title": task.title}

    # ------------------------------------------------------------------ pipeline steps
    def _today(self) -> date:
        return datetime.now(self.tz).date()

    async def _video_id(self, ctx: StepContext) -> uuid.UUID:
        if ctx.context.get("duplicate"):
            raise StepSkip(ctx.context["duplicate"])
        vid = ctx.context.get("video_id")
        if not vid:
            raise StepFail("Kein Video zu dieser Aufgabe")
        return uuid.UUID(vid)

    async def _fail(self, vid: uuid.UUID, stage: str, message: str) -> StepFail:
        await self.repo.update(vid, status="failed", stage=stage, error=message[:2000])
        await self.repo.log(vid, message, "error")
        await bus.publish("social.updated", video_id=str(vid), status="failed")
        await self.state.notifier.notify(
            "social.failed", "Social-Video fehlgeschlagen", message[:300], level="warning", data={"video_id": str(vid)}
        )
        return StepFail(message[:500])

    async def step_start(self, ctx: StepContext) -> dict[str, Any]:
        if ctx.context.get("video_id"):
            return {"video_id": ctx.context["video_id"]}
        trigger = str(ctx.params.get("trigger") or "manual")
        day = self._today().isoformat()
        fields: dict[str, Any] = {"trigger": trigger, "task_id": ctx.task_id, "status": "producing", "stage": "concept"}
        if ctx.params.get("parent_id"):
            fields["parent_id"] = uuid.UUID(str(ctx.params["parent_id"]))
        if trigger == "schedule":
            fields["auto_date"] = day
        try:
            vid = await self.repo.create(**fields)
        except AlreadyProduced:
            ctx.context["duplicate"] = f"Für {day} gibt es schon ein automatisches Video – nichts doppelt produziert"
            raise StepSkip(ctx.context["duplicate"]) from None
        ctx.context["video_id"] = str(vid)
        await self.repo.log(vid, f"Produktion gestartet ({trigger})")
        await bus.publish("social.updated", video_id=str(vid), status="producing")
        if not self.deps.get("ok"):
            await self.refresh_dependencies()
        if not self.deps.get("ok"):
            raise await self._fail(
                vid, "concept", "Abhängigkeiten fehlen: " + "; ".join(self.deps.get("problems") or [])
            )
        return {"video_id": str(vid), "day": day}

    async def step_concept(self, ctx: StepContext) -> dict[str, Any]:
        vid = await self._video_id(ctx)
        v = await self.repo.get(vid)
        if v is not None and v.storyboard:
            return {"title": v.title, "category": v.category}
        brand = load_brand()
        history = await self.repo.history()
        st = self.settings.load()
        hint = str(ctx.params.get("hint") or st.get("hint") or "")
        try:
            res = await generate(
                self._ask_concept,
                brand,
                load_inspiration(),
                history,
                self._today(),
                hint=hint,
                voiceover=bool(st.get("voiceover")),
            )
        except ConceptRejected as e:
            for n in e.notes:
                await self.repo.log(vid, n, "warning")
            raise await self._fail(vid, "concept", str(e)) from e
        except Exception as e:
            raise await self._fail(vid, "concept", f"Konzept konnte nicht erstellt werden: {e}") from e
        c, board = res.concept, res.storyboard
        summ = sb.summary(board)
        await self.repo.update(
            vid,
            title=str(c.get("title", ""))[:200],
            category=str(c.get("category", "")),
            concept_key=str(c.get("key", ""))[:80],
            tone=str(c.get("tone", "")),
            palette=str(board.get("palette", "")),
            hook=str(c.get("hook", ""))[:200],
            concept=c,
            script=res.script,
            storyboard=board,
            components=summ["components"],
            transitions=summ["transitions"],
            sounds=summ["sounds"],
            stage="render",
        )
        for n in res.notes:
            await self.repo.log(vid, n, "warning")
        await self.repo.log(
            vid, f"Konzept „{c.get('title')}“ ({c.get('category')}, {c.get('tone')}) nach {res.attempts} Versuch(en)"
        )
        return {"title": c.get("title"), "category": c.get("category"), "attempts": res.attempts}

    async def step_storyboard(self, ctx: StepContext) -> dict[str, Any]:
        vid = await self._video_id(ctx)
        v = await self.repo.get(vid)
        assert v is not None
        check = sb.validate(v.storyboard, fact_ids=load_brand().fact_ids)
        if not check.ok:
            raise await self._fail(vid, "concept", "Storyboard ungültig: " + "; ".join(check.problems))
        out: dict[str, Any] = {"scenes": len(v.storyboard["scenes"]), "seconds": sb.total_seconds(v.storyboard)}
        if self.settings.load().get("voiceover") and v.storyboard.get("voiceover"):
            work = self.work_root / str(vid)
            try:
                await self._voiceover(str(v.storyboard["voiceover"]), work / "voiceover.wav")
                out["voiceover"] = True
            except Exception as e:
                await self.repo.log(vid, f"Voiceover übersprungen: {e}", "warning")
        return out

    async def step_render(self, ctx: StepContext) -> dict[str, Any]:
        vid = await self._video_id(ctx)
        v = await self.repo.get(vid)
        assert v is not None
        if v.file_name and self.file_path(v.file_name):
            return {"skipped": "schon in der Bibliothek"}
        work = self.work_root / str(vid)
        out = work / "video.mp4"
        await self._render(vid, v.storyboard, work, out, attempt=1)
        return {"file": str(out)}

    async def _render(
        self, vid: uuid.UUID, board: dict[str, Any], work: Path, out: Path, *, attempt: int, gain: float = 0.0
    ) -> dict[str, Any]:
        await self.repo.update(vid, stage="render", attempts=attempt)
        await self.repo.log(vid, f"Rendern (Versuch {attempt})")
        self.progress = {"video_id": str(vid), "stage": "render", "percent": 0}

        def on_progress(pct: int) -> None:
            self.progress = {"video_id": str(vid), "stage": "render", "percent": pct}

        voice = work / "voiceover.wav"
        try:
            res = await render_video(
                self.cfg,
                board,
                work,
                out,
                voiceover=voice if voice.is_file() else None,
                progress=on_progress,
                loud_gain_db=gain,
            )
        except (RenderError, MediaError) as e:
            raise await self._fail(vid, "render", str(e)) from e
        finally:
            self.progress = {}
        pr = res["probe"]
        await self.repo.log(
            vid,
            f"Gerendert: {pr['width']}×{pr['height']}, {pr['duration']:.2f} s, {pr['size'] / 1048576:.1f} MB",
        )
        return res

    async def step_qa(self, ctx: StepContext) -> dict[str, Any]:
        vid = await self._video_id(ctx)
        v = await self.repo.get(vid)
        assert v is not None
        if v.file_name and self.file_path(v.file_name):
            return {"passed": (v.qa or {}).get("passed"), "skipped": "schon geprüft"}
        work = self.work_root / str(vid)
        video = work / "video.mp4"
        board = dict(v.storyboard)
        brand = load_brand()
        attempt = max(1, v.attempts)
        gain = 0.0
        while True:
            await self.repo.update(vid, stage="qa")
            report = await self.run_qa(video, board, work / f"frames-{attempt}", brand.fact_ids)
            await self.repo.update(vid, qa=report.to_dict())
            failed = [c["label"] + (f" ({c['detail']})" if c["detail"] else "") for c in report.checks if not c["ok"]]
            if report.passed:
                await self.repo.log(vid, f"Qualitätskontrolle bestanden ({len(report.checks)} Prüfungen)")
                return {"passed": True, "attempts": attempt}
            await self.repo.log(vid, "QA: " + "; ".join(failed)[:500], "warning")
            if attempt >= self.cfg.max_render_attempts:
                raise await self._fail(
                    vid, "qa", "Qualitätskontrolle nach " + f"{attempt} Versuchen nicht bestanden: " + "; ".join(failed)
                )
            only_vision_unavailable = all(c["id"] == "vision" for c in report.checks if not c["ok"])
            if only_vision_unavailable:
                raise await self._fail(
                    vid, "qa", "Bildprüfung nicht möglich (Claude nicht verfügbar) – Video nicht versandt"
                )
            # automatic corrections, then render again
            if "spelling" in report.fixes and report.spelling_fixes:
                board, n = qa.apply_spelling(board, report.spelling_fixes)
                await self.repo.log(vid, f"{n} Rechtschreibkorrektur(en) übernommen")
            if "audio_gain" in report.fixes:
                lufs = float((report.probe.get("loudness") or {}).get("integrated_lufs", -14.0))
                gain = gain - 2.0 if lufs > qa.LUFS_RANGE[1] else gain + 3.0
            if "storyboard" in report.fixes:
                issues = failed + [i.get("detail", "") for i in report.issues if i.get("severity") == "blocker"]
                try:
                    fixed = await revise_storyboard(self._ask_concept, brand, board, issues)
                except Exception as e:
                    raise await self._fail(vid, "qa", f"Korrektur des Storyboards fehlgeschlagen: {e}") from e
                check = sb.validate(fixed, fact_ids=brand.fact_ids)
                if check.ok:
                    board = fixed
                    await self.repo.log(vid, "Storyboard nach QA überarbeitet")
                else:
                    await self.repo.log(vid, "Überarbeitung verworfen: " + "; ".join(check.problems)[:300], "warning")
            summ = sb.summary(board)
            await self.repo.update(
                vid,
                storyboard=board,
                components=summ["components"],
                transitions=summ["transitions"],
                sounds=summ["sounds"],
            )
            attempt += 1
            await self._render(vid, board, work, video, attempt=attempt, gain=gain)

    async def run_qa(self, video: Path, board: dict[str, Any], frames_dir: Path, fact_ids: set[str]) -> qa.QaReport:
        tools = Tools.resolve(self.cfg)
        assert tools.ffmpeg and tools.ffprobe
        report = qa.QaReport()
        info = await probe(tools.ffprobe, video)
        loud = await loudness(tools.ffmpeg, video)
        qa.technical(report, info, loud, board, int(self.cfg.max_video_mb * 1024 * 1024))
        qa.textual(report, board, fact_ids)
        await qa.visual(
            report,
            tools.ffmpeg,
            video,
            board,
            frames_dir,
            self._ask_vision if getattr(self.state, "agent", None) is not None else None,
        )
        report.probe = {**info.to_dict(), "loudness": loud.to_dict()}
        return report

    async def step_captions(self, ctx: StepContext) -> dict[str, Any]:
        vid = await self._video_id(ctx)
        v = await self.repo.get(vid)
        assert v is not None
        if v.captions and cap.validate(v.captions, self._allowed_prices()).ok:
            return {"skipped": "schon erstellt"}
        await self.repo.update(vid, stage="captions")
        brand = load_brand()
        concept = {**(v.concept or {}), "title": v.title, "tone": v.tone}
        problems: list[str] = []
        for _ in range(3):
            raw = await self._ask_concept(
                "Du schreibst Social-Media-Captions für PJE Systems.",
                cap.prompt(concept, v.script, brand.raw, brand.active_offers(self._today()), problems),
                cap.CAPTIONS_SCHEMA,
            )
            result = cap.clean(raw if isinstance(raw, dict) else {})
            check = cap.validate(result, self._allowed_prices())
            if check.ok:
                await self.repo.update(vid, captions=result)
                await self.repo.log(vid, "Captions und Hashtags erstellt")
                return {"platforms": list(result)}
            problems = check.problems
            await self.repo.log(vid, "Captions abgelehnt: " + "; ".join(problems)[:300], "warning")
        raise await self._fail(vid, "captions", "Captions verletzen Regeln: " + "; ".join(problems))

    def _allowed_prices(self) -> set[str]:
        brand = load_brand()
        texts = brand.active_offers(self._today()) + [f["value"] + " " + f["label"] for f in brand.facts]
        return {p for t in texts for p in cap.prices_in(t)}

    async def step_library(self, ctx: StepContext) -> dict[str, Any]:
        vid = await self._video_id(ctx)
        v = await self.repo.get(vid)
        assert v is not None
        if v.file_name and self.file_path(v.file_name):
            return {"file_name": v.file_name}
        work = self.work_root / str(vid)
        src = work / "video.mp4"
        if not src.is_file():
            raise await self._fail(vid, "library", "Gerendertes Video fehlt im Arbeitsordner")
        name = f"{self._today().isoformat()}_{(v.concept_key or 'video')[:40]}_{str(vid)[:8]}.mp4"
        self.library_dir.mkdir(parents=True, exist_ok=True)
        dst = self.library_dir / name
        shutil.copyfile(src, dst)
        tools = Tools.resolve(self.cfg)
        info = await probe(tools.ffprobe or "ffprobe", dst)
        qa_report = dict(v.qa or {})
        qa_report["probe"] = info.to_dict()
        await self.repo.update(
            vid,
            file_name=name,
            size=info.size,
            duration_s=info.duration,
            qa=qa_report,
            status="ready",
            stage="email",
            error=None,
        )
        await self.repo.log(vid, f"In der Bibliothek gespeichert: {name}")
        shutil.rmtree(work, ignore_errors=True)
        await bus.publish("social.updated", video_id=str(vid), status="ready")
        await self.state.notifier.notify(
            "social.ready",
            f"Neues Social-Video: {v.title}",
            (v.concept or {}).get("summary", "")[:300],
            data={"video_id": str(vid)},
        )
        return {"file_name": name, "size": info.size}

    async def step_email(self, ctx: StepContext) -> dict[str, Any]:
        vid = await self._video_id(ctx)
        st = self.settings.load()
        if not st.get("email_enabled"):
            raise StepSkip("E-Mail-Versand ist ausgeschaltet")
        res = await ctx.tool("social_email_video", {"video_id": str(vid)})
        return dict(res or {})

    async def step_publish(self, ctx: StepContext) -> dict[str, Any]:
        vid = await self._video_id(ctx)
        st = self.settings.load()
        targets = [pf for pf in PLATFORMS if self.can_autopost(pf, st)]
        if not targets:
            await self.repo.update(vid, stage="done")
            raise StepSkip("Autopost aus oder keine Plattform verbunden und aktiviert")
        when = self._publish_at(st)
        out: dict[str, Any] = {}
        for pf in targets:
            if when is not None and when > datetime.now(UTC):
                await self.repo.upsert_publication(vid, pf, status="scheduled", scheduled_at=when)
                out[pf] = {"status": "scheduled", "at": when.isoformat()}
                continue
            out[pf] = await ctx.tool(
                "social_publish_video", {"video_id": str(vid), "platform": pf}, key=f"publish-{pf}"
            )
        await self.repo.update(vid, stage="done")
        return out

    def _publish_at(self, st: dict[str, Any]) -> datetime | None:
        t = st.get("publish_time")
        if not t:
            return None
        h, m = (int(x) for x in str(t).split(":"))
        local = datetime.now(self.tz).replace(hour=h, minute=m, second=0, microsecond=0)
        return local.astimezone(UTC)

    # ------------------------------------------------------------------ e-mail & publishing (tool handlers)
    async def send_email(self, vid: uuid.UUID, to: str) -> dict[str, Any]:
        v = await self.repo.get(vid)
        if v is None or v.status != "ready":
            raise ToolUserError("Video nicht gefunden oder noch nicht fertig")
        path = self.file_path(v.file_name)
        if path is None:
            raise ToolUserError("Videodatei fehlt in der Bibliothek")
        google = self.state.connectors.connectors.get("google")
        if google is None or not google.active:
            raise ToolUserError("Google (Gmail) ist nicht eingerichtet – Connectoren › Google")
        attach = path.stat().st_size <= self.cfg.email_attachment_max_mb * 1024 * 1024
        url = None if attach else self.download_url(vid)
        mail = self.build_mail(v, attachment=path.name if attach else None, download_url=url)
        files = [(path.name, "video/mp4", path.read_bytes())] if attach else None
        res = await google.send_mail(to, mail.subject, mail.text, attachments=files, html=mail.html)  # type: ignore[attr-defined]
        info = {
            "status": "sent",
            "to": to,
            "sent_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "message_id": (res or {}).get("id"),
            "attached": attach,
            "subject": mail.subject,
        }
        await self.repo.update(vid, email=info)
        await self.repo.log(vid, f"E-Mail an {to} gesendet ({'mit Anhang' if attach else 'mit Download-Link'})")
        await bus.publish("social.updated", video_id=str(vid), status="ready")
        return info

    def build_mail(
        self,
        v: Any,
        *,
        attachment: str | None,
        download_url: str | None,
        publications: list[dict[str, Any]] | None = None,
    ) -> emailer.Mail:
        return emailer.build(
            video_dict(v),
            day=datetime.now(self.tz),
            attachment=attachment,
            download_url=download_url,
            link_hours=self.cfg.link_ttl_hours,
            publications=publications or [],
            autopost_enabled=bool(self.settings.load().get("autopost_enabled")),
        )

    async def publish(self, vid: uuid.UUID, pf: str) -> dict[str, Any]:
        v = await self.repo.get(vid)
        if v is None or v.status != "ready":
            raise ToolUserError("Video nicht gefunden oder noch nicht fertig")
        conn = self.platform(pf)
        if conn is None or not conn.enabled or not conn.connected:
            raise ToolUserError(f"{pf} ist nicht verbunden oder deaktiviert (Social Media › Connector)")
        path = self.file_path(v.file_name)
        if path is None:
            raise ToolUserError("Videodatei fehlt in der Bibliothek")
        entry = (v.captions or {}).get(pf) or {}
        text = cap.full_text(pf, entry)
        title = entry.get("title") or v.title
        await self.repo.upsert_publication(vid, pf, status="publishing", error=None)
        try:
            res = await conn.publish(path, text, title)
        except ToolUserError as e:
            await self.repo.upsert_publication(vid, pf, status="failed", error=str(e)[:500])
            await self.repo.log(vid, f"{pf}: {e}", "error")
            raise
        await self.repo.upsert_publication(
            vid,
            pf,
            status="published",
            published_at=datetime.now(UTC),
            post_id=res.get("post_id"),
            post_url=res.get("url"),
        )
        await self.repo.log(vid, f"Veröffentlicht auf {pf}" + (f": {res.get('url')}" if res.get("url") else ""))
        await bus.publish("social.updated", video_id=str(vid), status="published", platform=pf)
        return res

    async def mark_manual(self, vid: uuid.UUID, pf: str, post_url: str | None, actor: str) -> dict[str, Any]:
        p = await self.repo.upsert_publication(
            vid,
            pf,
            status="published" if post_url else "manual",
            published_at=datetime.now(UTC) if post_url else None,
            post_url=post_url,
        )
        await audit(actor, "social.manual_post", target=str(vid), details={"platform": pf})
        return publication_dict(p)

    # ------------------------------------------------------------------ background: scheduled posts, metrics
    async def _tick_loop(self) -> None:
        n = 0
        while True:
            try:
                await self._run_due_publications()
                if n % 360 == 0:  # every 6 h: performance numbers from the official APIs
                    await self._collect_metrics()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Social ticker failed")
            n += 1
            await asyncio.sleep(60)

    async def _run_due_publications(self) -> None:
        if self.state.kill_switch.engaged:
            return
        from jarvis.jobs.scheduler import SCHEDULER
        from jarvis.tools.registry import ToolContext

        for p in await self.repo.due_publications(datetime.now(UTC)):
            await self.repo.upsert_publication(p.video_id, p.platform, status="publishing")
            ctx = ToolContext(principal=SCHEDULER, idempotency_key=f"social-publish:{p.video_id}:{p.platform}")
            res = await self.state.executor.execute(
                "social_publish_video", {"video_id": str(p.video_id), "platform": p.platform}, ctx
            )
            if res.status == "approval_required":
                await self.repo.upsert_publication(
                    p.video_id, p.platform, status="waiting_approval", approval_id=res.approval_id
                )
            elif res.status not in ("ok", "duplicate"):
                await self.repo.upsert_publication(p.video_id, p.platform, status="failed", error=res.message[:500])

    async def _collect_metrics(self) -> None:
        since = datetime.now(UTC) - timedelta(days=14)
        for p in await self.repo.publications(limit=100):
            if p.status != "published" or not p.post_id or not p.published_at or p.published_at < since:
                continue
            conn = self.platform(p.platform)
            if conn is None or not conn.active:
                continue
            try:
                m = await conn.metrics(p.post_id)
            except Exception as e:
                logger.info("Metrics %s failed: %s", p.platform, e)
                continue
            if m:
                v = await self.repo.get(p.video_id)
                if v is not None:
                    await self.repo.update(
                        p.video_id,
                        metrics={
                            **(v.metrics or {}),
                            p.platform: {**m, "at": datetime.now(UTC).isoformat(timespec="seconds")},
                        },
                    )

    # ------------------------------------------------------------------ LLM + TTS adapters
    async def _ask_concept(self, system: str, prompt: str, schema: dict[str, Any]) -> Any:
        return await self.state.oneshot.run(
            prompt, schema=schema, system=system, model=self.cfg.concept_model, timeout_s=420
        )

    async def _ask_vision(self, prompt: str, frames_dir: Path) -> Any:
        return await self.state.oneshot.run(
            prompt, schema=qa.VISION_SCHEMA, read_dir=frames_dir, model=self.cfg.qa_model, timeout_s=420
        )

    async def _voiceover(self, text: str, out: Path) -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        rate = 24000
        pcm = bytearray()
        async for chunk in self.state.tts.stream(text, rate):
            pcm += chunk
        if not pcm:
            raise RuntimeError("TTS lieferte keinen Ton")
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(bytes(pcm))


def playbook(svc: SocialMediaService) -> Playbook:
    return Playbook(
        name=PLAYBOOK,
        title="PJE Systems Daily Social Video",
        title_template="PJE Systems Daily Social Video",
        description="Entwickelt ein neues Kurzvideo für PJE Systems (Idee, Skript, Storyboard), rendert es mit "
        "Remotion, prüft es, schreibt Captions, legt es in der Bibliothek ab, schickt es Sir per E-Mail und "
        "veröffentlicht es optional.",
        params_schema={
            "type": "object",
            "properties": {
                "trigger": {"enum": ["schedule", "manual", "regenerate"], "default": "manual"},
                "hint": {"type": "string", "maxLength": 500},
                "parent_id": {"type": "string"},
            },
        },
        steps=[
            StepDef("Tageslauf prüfen", svc.step_start),
            StepDef("Idee entwickeln", svc.step_concept),
            StepDef("Skript und Storyboard prüfen", svc.step_storyboard),
            StepDef("Video rendern", svc.step_render),
            StepDef("Qualitätskontrolle", svc.step_qa),
            StepDef("Captions und Hashtags", svc.step_captions),
            StepDef("In Bibliothek speichern", svc.step_library),
            StepDef("E-Mail an Sir", svc.step_email),
            StepDef("Veröffentlichen (optional)", svc.step_publish),
        ],
    )
