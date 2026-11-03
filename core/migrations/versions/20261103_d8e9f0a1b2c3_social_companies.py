"""social media: videos per company profile (company_id, one automatic video per company and day)

Existing videos belong to PJE Systems, the first profile (jarvis.social.companies).

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-11-03 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8e9f0a1b2c3"
down_revision: str | None = "c7d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _auto_date_unique() -> str | None:
    """Name of the old single-column unique constraint on auto_date (Postgres: social_videos_auto_date_key)."""
    insp = sa.inspect(op.get_bind())
    for uc in insp.get_unique_constraints("social_videos"):
        if uc.get("column_names") == ["auto_date"]:
            return uc.get("name")
    return None


def upgrade() -> None:
    op.add_column("social_videos", sa.Column("company_id", sa.String(60), nullable=False, server_default="pje-systems"))
    op.create_index("ix_social_videos_company_id", "social_videos", ["company_id"])
    old = _auto_date_unique()
    if old:
        op.drop_constraint(old, "social_videos", type_="unique")
    op.create_unique_constraint("uq_social_videos_company_auto_date", "social_videos", ["company_id", "auto_date"])


def downgrade() -> None:
    op.drop_constraint("uq_social_videos_company_auto_date", "social_videos", type_="unique")
    op.execute("UPDATE social_videos SET auto_date = NULL WHERE company_id <> 'pje-systems'")
    op.create_unique_constraint("social_videos_auto_date_key", "social_videos", ["auto_date"])
    op.drop_index("ix_social_videos_company_id", "social_videos")
    op.drop_column("social_videos", "company_id")
