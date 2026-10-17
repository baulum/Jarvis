"""mobile companion: paired phones, rotating refresh tokens, alert dispatches, VoIP call sessions

Revision ID: d7e8f9a0b1c2
Revises: c4d1e2f3a5b6
Create Date: 2026-10-17 10:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d7e8f9a0b1c2"
down_revision: str | None = "c4d1e2f3a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "mobile_devices",
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("app_version", sa.String(40), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("push_key_id", sa.String(40), nullable=False),
        sa.Column("push_token", sa.Text(), nullable=True),
        sa.Column("push_token_updated_at", TS, nullable=True),
        sa.Column("last_ip", sa.String(64), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_index("ix_mobile_devices_created_at", "mobile_devices", ["created_at"])
    op.create_table(
        "mobile_refresh_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("family", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("used_at", TS, nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_mobile_refresh_tokens_device_id", "mobile_refresh_tokens", ["device_id"])
    op.create_index("ix_mobile_refresh_tokens_family", "mobile_refresh_tokens", ["family"])
    op.create_table(
        "alert_dispatches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_key", sa.String(200), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("level", sa.String(20), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("deep_link", sa.String(300), nullable=False),
        sa.Column("actions", JSONB, nullable=False),
        sa.Column("data", JSONB, nullable=False),
        sa.Column("delivered_to", JSONB, nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=True),
        sa.Column("remind_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_index("ix_alert_dispatches_event_key", "alert_dispatches", ["event_key"])
    op.create_index("ix_alert_dispatches_remind_at", "alert_dispatches", ["remind_at"])
    op.create_index("ix_alert_dispatches_created_at", "alert_dispatches", ["created_at"])
    op.create_table(
        "call_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("direction", sa.String(20), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=True),
        sa.Column("room", sa.String(120), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(60), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("event_key", sa.String(200), nullable=True),
        sa.Column("answered_at", TS, nullable=True),
        sa.Column("ended_at", TS, nullable=True),
        sa.Column("end_reason", sa.String(60), nullable=False),
        sa.Column("transcript", JSONB, nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("metrics", JSONB, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_index("ix_call_sessions_status", "call_sessions", ["status"])
    op.create_index("ix_call_sessions_event_key", "call_sessions", ["event_key"])
    op.create_index("ix_call_sessions_created_at", "call_sessions", ["created_at"])


def downgrade() -> None:
    op.drop_table("call_sessions")
    op.drop_table("alert_dispatches")
    op.drop_table("mobile_refresh_tokens")
    op.drop_table("mobile_devices")
