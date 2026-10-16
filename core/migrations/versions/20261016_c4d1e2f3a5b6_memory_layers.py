"""memory layers: full-text index, confidence, access stats, aliases, typed relations, episodes

Revision ID: c4d1e2f3a5b6
Revises: b0ae8ff0495e
Create Date: 2026-10-16 12:30:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c4d1e2f3a5b6"
down_revision: str | None = "b0ae8ff0495e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("memory_items", sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"))
    op.add_column("memory_items", sa.Column("access_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("memory_items", sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "memory_items",
        sa.Column("aliases", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
    )
    # 'simple' keeps proper names intact (German stemming would mangle "Müller" / "Westside").
    op.execute(
        "ALTER TABLE memory_items ADD COLUMN search_tsv tsvector GENERATED ALWAYS AS ("
        "setweight(to_tsvector('simple', regexp_replace(coalesce(title, ''), '[-_/]', ' ', 'g')), 'A') || "
        "setweight(to_tsvector('german', regexp_replace(coalesce(content, ''), '[-_/]', ' ', 'g')), 'B') || "
        "setweight(to_tsvector('simple', regexp_replace(coalesce(content, ''), '[-_/]', ' ', 'g')), 'C')) STORED"
    )
    op.create_index("ix_memory_items_search_tsv", "memory_items", ["search_tsv"], postgresql_using="gin")
    op.add_column("memory_links", sa.Column("target_kind", sa.String(length=40), nullable=True))
    op.add_column("memory_links", sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"))
    op.add_column("chat_sessions", sa.Column("summarized_at", sa.DateTime(timezone=True), nullable=True))
    # Turns before this revision went through the old extractor: do not extract them a second time.
    op.execute("""UPDATE chat_messages SET meta = coalesce(meta, '{}'::jsonb) || '{"extracted": true}'::jsonb """
               "WHERE role = 'user'")


def downgrade() -> None:
    op.drop_column("chat_sessions", "summarized_at")
    op.drop_column("memory_links", "weight")
    op.drop_column("memory_links", "target_kind")
    op.drop_index("ix_memory_items_search_tsv", table_name="memory_items")
    op.drop_column("memory_items", "search_tsv")
    op.drop_column("memory_items", "aliases")
    op.drop_column("memory_items", "last_accessed_at")
    op.drop_column("memory_items", "access_count")
    op.drop_column("memory_items", "confidence")
