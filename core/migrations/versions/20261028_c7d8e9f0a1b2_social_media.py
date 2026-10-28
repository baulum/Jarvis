"""social media: video library with creative memory, publications per platform

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-10-28 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c7d8e9f0a1b2"
down_revision: str | None = "b6c7d8e9f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)


def jsonb(name: str, default: str = "{}") -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=default)


def upgrade() -> None:
    op.create_table(
        "social_videos",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="producing"),
        sa.Column("stage", sa.String(30), nullable=False, server_default="concept"),
        sa.Column("trigger", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("auto_date", sa.String(10), nullable=True, unique=True),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("category", sa.String(2), nullable=False, server_default=""),
        sa.Column("concept_key", sa.String(80), nullable=False, server_default=""),
        sa.Column("tone", sa.String(30), nullable=False, server_default=""),
        sa.Column("palette", sa.String(20), nullable=False, server_default=""),
        sa.Column("hook", sa.String(200), nullable=False, server_default=""),
        jsonb("concept"),
        sa.Column("script", sa.Text(), nullable=False, server_default=""),
        jsonb("storyboard"),
        jsonb("components", "[]"),
        jsonb("transitions", "[]"),
        jsonb("sounds", "[]"),
        jsonb("captions"),
        sa.Column("file_name", sa.String(200), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("duration_s", sa.Float(), nullable=False, server_default="0"),
        jsonb("qa"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        jsonb("log", "[]"),
        jsonb("email"),
        jsonb("metrics"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_social_videos_status", "social_videos", ["status"])
    op.create_index("ix_social_videos_task_id", "social_videos", ["task_id"])
    op.create_index("ix_social_videos_category", "social_videos", ["category"])
    op.create_index("ix_social_videos_concept_key", "social_videos", ["concept_key"])
    op.create_index("ix_social_videos_created_at", "social_videos", ["created_at"])

    op.create_table(
        "social_publications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("video_id", sa.Uuid(), sa.ForeignKey("social_videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="scheduled"),
        sa.Column("scheduled_at", TS, nullable=True),
        sa.Column("published_at", TS, nullable=True),
        sa.Column("post_id", sa.String(200), nullable=True),
        sa.Column("post_url", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("approval_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("video_id", "platform", name="uq_social_publications_video_platform"),
    )
    op.create_index("ix_social_publications_video_id", "social_publications", ["video_id"])
    op.create_index("ix_social_publications_status", "social_publications", ["status"])
    op.create_index("ix_social_publications_scheduled_at", "social_publications", ["scheduled_at"])


def downgrade() -> None:
    op.drop_table("social_publications")
    op.drop_table("social_videos")
