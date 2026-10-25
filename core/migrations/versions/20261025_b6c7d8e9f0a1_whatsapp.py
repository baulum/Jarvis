"""whatsapp connector: chats, contacts, messages, stored files, send ledger, suggestions

Revision ID: b6c7d8e9f0a1
Revises: a3b4c5d6e7f8
Create Date: 2026-10-25 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b6c7d8e9f0a1"
down_revision: str | None = "a3b4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)


def jsonb(name: str, default: str = "{}") -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=default)


def upgrade() -> None:
    op.create_table(
        "whatsapp_chats",
        sa.Column("jid", sa.String(200), primary_key=True),
        sa.Column("name", sa.String(300), nullable=False, server_default=""),
        sa.Column("is_group", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("unread_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_message_at", TS, nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("muted_until", TS, nullable=True),
        jsonb("meta"),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_whatsapp_chats_last_message_at", "whatsapp_chats", ["last_message_at"])

    op.create_table(
        "whatsapp_contacts",
        sa.Column("jid", sa.String(200), primary_key=True),
        sa.Column("lid", sa.String(200), nullable=True),
        sa.Column("name", sa.String(300), nullable=False, server_default=""),
        sa.Column("notify", sa.String(300), nullable=False, server_default=""),
        sa.Column("verified_name", sa.String(300), nullable=False, server_default=""),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_whatsapp_contacts_lid", "whatsapp_contacts", ["lid"])

    op.create_table(
        "whatsapp_messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("chat_jid", sa.String(200), nullable=False),
        sa.Column("msg_id", sa.String(128), nullable=False),
        sa.Column("from_me", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sender_jid", sa.String(200), nullable=False, server_default=""),
        sa.Column("sender_name", sa.String(300), nullable=False, server_default=""),
        sa.Column("ts", TS, nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="text"),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("quoted_msg_id", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default=""),
        sa.Column("forwarded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("edited_at", TS, nullable=True),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("read_by_me", sa.Boolean(), nullable=False, server_default=sa.false()),
        jsonb("media"),
        sa.Column("media_ref", sa.Text(), nullable=True),
        jsonb("extra"),
        sa.Column("source", sa.String(10), nullable=False, server_default="live"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("chat_jid", "msg_id", name="uq_whatsapp_messages_chat_msg"),
    )
    op.create_index("ix_whatsapp_messages_chat_ts", "whatsapp_messages", ["chat_jid", "ts"])
    op.create_index("ix_whatsapp_messages_msg_id", "whatsapp_messages", ["msg_id"])
    op.create_index("ix_whatsapp_messages_ts", "whatsapp_messages", ["ts"])
    # full-text search over message texts (whatsapp_search_messages); 'simple' = language independent
    op.execute("CREATE INDEX ix_whatsapp_messages_fts ON whatsapp_messages USING gin (to_tsvector('simple', text))")

    op.create_table(
        "whatsapp_files",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("chat_jid", sa.String(200), nullable=False),
        sa.Column("msg_id", sa.String(128), nullable=False),
        sa.Column("sender_jid", sa.String(200), nullable=False, server_default=""),
        sa.Column("sender_name", sa.String(300), nullable=False, server_default=""),
        sa.Column("chat_name", sa.String(300), nullable=False, server_default=""),
        sa.Column("file_name", sa.String(300), nullable=False),
        sa.Column("location", sa.String(160), nullable=False, server_default="server"),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("mime", sa.String(120), nullable=False, server_default="application/octet-stream"),
        sa.Column("ext", sa.String(12), nullable=False, server_default=""),
        sa.Column("category", sa.String(20), nullable=False, server_default="document"),
        sa.Column("size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("duplicate_of", sa.Uuid(), nullable=True),
        sa.Column("text_excerpt", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_whatsapp_files_chat_jid", "whatsapp_files", ["chat_jid"])
    op.create_index("ix_whatsapp_files_msg_id", "whatsapp_files", ["msg_id"])
    op.create_index("ix_whatsapp_files_sha256", "whatsapp_files", ["sha256"])
    op.create_index("ix_whatsapp_files_created_at", "whatsapp_files", ["created_at"])
    # search index for stored files: name, chat, sender and extracted content
    op.execute(
        "CREATE INDEX ix_whatsapp_files_fts ON whatsapp_files USING gin (to_tsvector('simple', "
        "file_name || ' ' || chat_name || ' ' || sender_name || ' ' || text_excerpt))"
    )

    op.create_table(
        "whatsapp_sends",
        sa.Column("send_key", sa.String(200), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("chat_jid", sa.String(200), nullable=False),
        sa.Column("chat_name", sa.String(300), nullable=False, server_default=""),
        sa.Column("kind", sa.String(20), nullable=False),
        jsonb("files", "[]"),
        sa.Column("text_len", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        jsonb("msg_ids", "[]"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(120), nullable=False, server_default=""),
        sa.Column("approval_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_whatsapp_sends_fingerprint", "whatsapp_sends", ["fingerprint"])
    op.create_index("ix_whatsapp_sends_chat_jid", "whatsapp_sends", ["chat_jid"])
    op.create_index("ix_whatsapp_sends_created_at", "whatsapp_sends", ["created_at"])

    op.create_table(
        "whatsapp_suggestions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("chat_jid", sa.String(200), nullable=False),
        sa.Column("msg_id", sa.String(128), nullable=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("due_at", TS, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        jsonb("applied"),
        sa.Column("remind_at", TS, nullable=True),
        sa.Column("reminded_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("decided_at", TS, nullable=True),
    )
    op.create_index("ix_whatsapp_suggestions_chat_jid", "whatsapp_suggestions", ["chat_jid"])
    op.create_index("ix_whatsapp_suggestions_due_at", "whatsapp_suggestions", ["due_at"])
    op.create_index("ix_whatsapp_suggestions_remind_at", "whatsapp_suggestions", ["remind_at"])
    op.create_index("ix_whatsapp_suggestions_status", "whatsapp_suggestions", ["status"])
    op.create_index("ix_whatsapp_suggestions_created_at", "whatsapp_suggestions", ["created_at"])


def downgrade() -> None:
    for table in (
        "whatsapp_suggestions",
        "whatsapp_sends",
        "whatsapp_files",
        "whatsapp_messages",
        "whatsapp_contacts",
        "whatsapp_chats",
    ):
        op.drop_table(table)
