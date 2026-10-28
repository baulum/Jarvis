"""Persistence of the video library and its creative memory (``social_videos``, ``social_publications``).

Portable SQL only: the same code runs on PostgreSQL and on SQLite in the unit tests."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from jarvis.db import session_scope
from jarvis.db.models import SocialPublication, SocialVideo
from jarvis.social.variety import Past

LOG_MAX = 80


class AlreadyProduced(Exception):
    """There already is an automatic video for this day (unique ``auto_date``)."""


def video_dict(v: SocialVideo, *, full: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(v.id),
        "status": v.status,
        "stage": v.stage,
        "trigger": v.trigger,
        "auto_date": v.auto_date,
        "parent_id": str(v.parent_id) if v.parent_id else None,
        "task_id": str(v.task_id) if v.task_id else None,
        "title": v.title,
        "category": v.category,
        "concept_key": v.concept_key,
        "tone": v.tone,
        "palette": v.palette,
        "hook": v.hook,
        "summary": (v.concept or {}).get("summary", ""),
        "file_name": v.file_name,
        "size": v.size,
        "duration_s": v.duration_s,
        "attempts": v.attempts,
        "error": v.error,
        "email": v.email or {},
        "qa_passed": (v.qa or {}).get("passed"),
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "updated_at": v.updated_at.isoformat() if v.updated_at else None,
    }
    if full:
        out.update(
            {
                "concept": v.concept or {},
                "script": v.script,
                "storyboard": v.storyboard or {},
                "components": v.components or [],
                "transitions": v.transitions or [],
                "sounds": v.sounds or [],
                "captions": v.captions or {},
                "qa": v.qa or {},
                "log": v.log or [],
                "metrics": v.metrics or {},
            }
        )
    return out


def publication_dict(p: SocialPublication) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "video_id": str(p.video_id),
        "platform": p.platform,
        "status": p.status,
        "scheduled_at": p.scheduled_at.isoformat() if p.scheduled_at else None,
        "published_at": p.published_at.isoformat() if p.published_at else None,
        "post_id": p.post_id,
        "post_url": p.post_url,
        "error": p.error,
        "approval_id": str(p.approval_id) if p.approval_id else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


class SocialRepo:
    def __init__(self, tz: str = "Europe/Berlin") -> None:
        self.tz = ZoneInfo(tz)

    def local_day(self, when: datetime) -> date:
        return when.astimezone(self.tz).date()

    async def create(self, **fields: Any) -> uuid.UUID:
        try:
            async with session_scope() as s:
                v = SocialVideo(**fields)
                s.add(v)
                await s.flush()
                return v.id
        except IntegrityError as e:
            if fields.get("auto_date"):
                raise AlreadyProduced(str(fields["auto_date"])) from e
            raise

    async def get(self, vid: uuid.UUID) -> SocialVideo | None:
        async with session_scope() as s:
            return await s.get(SocialVideo, vid)

    async def by_auto_date(self, day: str) -> SocialVideo | None:
        async with session_scope() as s:
            return (await s.execute(select(SocialVideo).where(SocialVideo.auto_date == day))).scalar_one_or_none()

    async def update(self, vid: uuid.UUID, **fields: Any) -> None:
        async with session_scope() as s:
            v = await s.get(SocialVideo, vid)
            if v is None:
                return
            for k, val in fields.items():
                setattr(v, k, val)
            v.updated_at = datetime.now(UTC)

    async def log(self, vid: uuid.UUID, msg: str, level: str = "info") -> None:
        async with session_scope() as s:
            v = await s.get(SocialVideo, vid)
            if v is None:
                return
            entry = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "level": level, "msg": msg[:600]}
            v.log = [*(v.log or []), entry][-LOG_MAX:]
            v.updated_at = datetime.now(UTC)

    async def list_videos(self, limit: int = 50, offset: int = 0, status: str | None = None) -> list[SocialVideo]:
        q = select(SocialVideo).order_by(SocialVideo.created_at.desc()).limit(limit).offset(offset)
        if status:
            q = q.where(SocialVideo.status == status)
        async with session_scope() as s:
            return list((await s.execute(q)).scalars())

    async def count(self) -> dict[str, int]:
        async with session_scope() as s:
            rows = (await s.execute(select(SocialVideo.status, func.count()).group_by(SocialVideo.status))).all()
        return {str(k): int(n) for k, n in rows}

    async def history(self, days: int = 45, now: datetime | None = None) -> list[Past]:
        """Creative memory: every finished video of the last ``days`` days (newest first)."""
        since = (now or datetime.now(UTC)) - timedelta(days=days)
        async with session_scope() as s:
            rows = (
                await s.execute(
                    select(SocialVideo)
                    .where(SocialVideo.status == "ready", SocialVideo.created_at >= since)
                    .order_by(SocialVideo.created_at.desc())
                )
            ).scalars()
            out = []
            for v in rows:
                board = v.storyboard or {}
                out.append(
                    Past(
                        day=date.fromisoformat(v.auto_date) if v.auto_date else self.local_day(v.created_at),
                        category=v.category,
                        concept_key=v.concept_key,
                        hook=v.hook,
                        tone=v.tone,
                        palette=v.palette,
                        music=str(board.get("music", "")),
                        components=list(v.components or []),
                        transitions=list(v.transitions or []),
                        title=v.title,
                        metrics=dict(v.metrics or {}),
                    )
                )
            return out

    # ------------------------------------------------------------------ publications
    async def publications(self, vid: uuid.UUID | None = None, limit: int = 50) -> list[SocialPublication]:
        q = select(SocialPublication).order_by(SocialPublication.updated_at.desc()).limit(limit)
        if vid is not None:
            q = q.where(SocialPublication.video_id == vid)
        async with session_scope() as s:
            return list((await s.execute(q)).scalars())

    async def upsert_publication(self, vid: uuid.UUID, platform: str, **fields: Any) -> SocialPublication:
        async with session_scope() as s:
            p = (
                await s.execute(
                    select(SocialPublication).where(
                        SocialPublication.video_id == vid, SocialPublication.platform == platform
                    )
                )
            ).scalar_one_or_none()
            if p is None:
                p = SocialPublication(video_id=vid, platform=platform)
                s.add(p)
            for k, val in fields.items():
                setattr(p, k, val)
            p.updated_at = datetime.now(UTC)
            await s.flush()
            return p

    async def due_publications(self, now: datetime) -> list[SocialPublication]:
        async with session_scope() as s:
            return list(
                (
                    await s.execute(
                        select(SocialPublication).where(
                            SocialPublication.status == "scheduled", SocialPublication.scheduled_at <= now
                        )
                    )
                ).scalars()
            )
