"""coding job model and effort

Revision ID: e1f2a3b4c5d6
Revises: d7e8f9a0b1c2
Create Date: 2026-10-20 09:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "d7e8f9a0b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("coding_jobs", sa.Column("model", sa.String(length=80), nullable=True))
    op.add_column("coding_jobs", sa.Column("effort", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("coding_jobs", "effort")
    op.drop_column("coding_jobs", "model")
