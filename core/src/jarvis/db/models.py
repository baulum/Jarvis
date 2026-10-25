"""Database schema (PostgreSQL + pgvector). Changes require an Alembic migration."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBED_DIM = 1024


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSONB, list[Any]: JSONB}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# --------------------------------------------------------------------------- identity
class Device(Base, TimestampMixin):
    __tablename__ = "devices"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(40))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


# --------------------------------------------------------------------------- conversations
class ChatSession(Base, TimestampMixin):
    __tablename__ = "chat_sessions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    channel: Mapped[str] = mapped_column(String(30))  # desktop | voice | telegram | phone | api
    actor_role: Mapped[str] = mapped_column(String(40))
    actor_id: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(200), default="")
    tainted: Mapped[bool] = mapped_column(Boolean, default=False)
    taint_sources: Mapped[list[Any]] = mapped_column(default=list)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)
    hermes_session_id: Mapped[str | None] = mapped_column(String(120))
    summarized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # episodic memory written
    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.created_at"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # user | assistant | system | tool
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    session: Mapped[ChatSession] = relationship(back_populates="messages")


class ChatEvent(Base):
    """One item of a conversation's timeline (message segment, tool call, process step, turn) in event order.

    ``seq`` is gapless-ish and strictly increasing per session; the UI sorts by it. ``ts`` is the creation time in
    milliseconds since the epoch. Items are upserted while they change (tool finished, segment closed)."""

    __tablename__ = "chat_events"
    __table_args__ = (UniqueConstraint("session_id", "seq"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True)
    turn_id: Mapped[uuid.UUID | None] = mapped_column()
    seq: Mapped[int] = mapped_column(Integer)
    ts: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(20))  # message | tool | process | turn
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# --------------------------------------------------------------------------- tasks
class Task(Base, TimestampMixin):
    __tablename__ = "tasks"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(60))  # playbook name | "custom"
    status: Mapped[str] = mapped_column(String(30), index=True, default="queued")
    # queued | running | paused | waiting_approval | waiting_input | completed | failed | cancelled
    params: Mapped[dict[str, Any]] = mapped_column(default=dict)
    context: Mapped[dict[str, Any]] = mapped_column(default=dict)  # step outputs shared across steps
    result: Mapped[dict[str, Any]] = mapped_column(default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    pending_question: Mapped[str | None] = mapped_column(Text)
    actor_role: Mapped[str] = mapped_column(String(40), default="owner")
    actor_id: Mapped[str] = mapped_column(String(120), default="")
    session_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    lease_owner: Mapped[str | None] = mapped_column(String(80))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    steps: Mapped[list[TaskStep]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="TaskStep.idx"
    )


class TaskStep(Base, TimestampMixin):
    __tablename__ = "task_steps"
    __table_args__ = (UniqueConstraint("task_id", "idx"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    # pending | running | waiting_approval | waiting_input | completed | skipped | failed
    output: Mapped[dict[str, Any]] = mapped_column(default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    approval_id: Mapped[uuid.UUID | None] = mapped_column()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    task: Mapped[Task] = relationship(back_populates="steps")


# --------------------------------------------------------------------------- approvals & tool ledger
class Approval(Base, TimestampMixin):
    __tablename__ = "approvals"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    tool: Mapped[str] = mapped_column(String(120), index=True)
    args: Mapped[dict[str, Any]] = mapped_column(default=dict)
    summary: Mapped[str] = mapped_column(Text)
    risk: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), index=True, default="pending")
    # pending | approved | rejected | expired | executed | failed
    requested_by_role: Mapped[str] = mapped_column(String(40))
    requested_by: Mapped[str] = mapped_column(String(120))
    channel: Mapped[str] = mapped_column(String(30))
    session_id: Mapped[uuid.UUID | None] = mapped_column()
    task_step_id: Mapped[uuid.UUID | None] = mapped_column()
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    decided_by: Mapped[str | None] = mapped_column(String(120))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any]] = mapped_column(default=dict)


class ToolExecution(Base):
    """Idempotency ledger: a write action with the same key is executed at most once."""

    __tablename__ = "tool_executions"
    idempotency_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    tool: Mapped[str] = mapped_column(String(120), index=True)
    args_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))  # running | succeeded | failed
    result: Mapped[dict[str, Any]] = mapped_column(default=dict)
    actor: Mapped[str] = mapped_column(String(120))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --------------------------------------------------------------------------- audit
class AuditEntry(Base):
    """Append-only, hash-chained audit log (tamper evident)."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(120), index=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    target: Mapped[str] = mapped_column(String(300), default="")
    outcome: Mapped[str] = mapped_column(String(30))
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


# --------------------------------------------------------------------------- scheduler / notifications
class Schedule(Base, TimestampMixin):
    __tablename__ = "schedules"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    cron: Mapped[str] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(60), default="Europe/Berlin")
    action: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"type": "prompt"|"tool"|"playbook", ...}
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_status: Mapped[str | None] = mapped_column(String(30))


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    event: Mapped[str] = mapped_column(String(80))
    level: Mapped[str] = mapped_column(String(20), default="info")
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    delivered: Mapped[list[Any]] = mapped_column(default=list)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ConnectorConfig(Base, TimestampMixin):
    __tablename__ = "connector_configs"
    connector_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)  # non-secret settings only
    last_health: Mapped[dict[str, Any]] = mapped_column(default=dict)


# --------------------------------------------------------------------------- memory
class MemoryItem(Base, TimestampMixin):
    __tablename__ = "memory_items"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    path: Mapped[str] = mapped_column(String(500), unique=True)  # relative path inside the vault
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(40), index=True)
    # preference | project | customer | process | decision | recurring | technical | person | note | conversation
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM))
    sensitivity: Mapped[str] = mapped_column(String(20), default="normal")  # normal | sensitive
    status: Mapped[str] = mapped_column(String(20), index=True, default="active")
    # active | pending (awaiting user confirmation) | superseded
    superseded_by: Mapped[uuid.UUID | None] = mapped_column()
    source: Mapped[dict[str, Any]] = mapped_column(default=dict)
    entities: Mapped[list[Any]] = mapped_column(default=list)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")
    access_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    aliases: Mapped[list[Any]] = mapped_column(default=list, server_default="[]")
    # Generated by Postgres (title weight A, content B/C); read-only for the ORM.
    search_tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('simple', regexp_replace(coalesce(title, ''), '[-_/]', ' ', 'g')), 'A') || "
            "setweight(to_tsvector('german', regexp_replace(coalesce(content, ''), '[-_/]', ' ', 'g')), 'B') || "
            "setweight(to_tsvector('simple', regexp_replace(coalesce(content, ''), '[-_/]', ' ', 'g')), 'C')",
            persisted=True,
        ),
        deferred=True,
    )
    __table_args__ = (
        Index("ix_memory_items_search_tsv", "search_tsv", postgresql_using="gin"),
        Index(
            "ix_memory_items_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class MemoryLink(Base):
    """Knowledge graph edge between two notes (derived from [[wiki links]] and extraction)."""

    __tablename__ = "memory_links"
    __table_args__ = (UniqueConstraint("source_path", "target_title", "relation"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_path: Mapped[str] = mapped_column(String(500), index=True)
    target_title: Mapped[str] = mapped_column(String(300), index=True)
    relation: Mapped[str] = mapped_column(String(60), default="links_to")
    target_kind: Mapped[str | None] = mapped_column(String(40))  # person | company | project ... for entity nodes
    weight: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")


# --------------------------------------------------------------------------- telephony / coding / usage
class Call(Base, TimestampMixin):
    __tablename__ = "calls"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(30))
    provider_call_id: Mapped[str | None] = mapped_column(String(120), index=True)
    direction: Mapped[str] = mapped_column(String(10))  # inbound | outbound
    from_number: Mapped[str] = mapped_column(String(40), default="")
    to_number: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(30), default="initiated")
    authenticated: Mapped[bool] = mapped_column(Boolean, default=False)
    purpose: Mapped[str] = mapped_column(Text, default="")
    transcript: Mapped[list[Any]] = mapped_column(default=list)
    session_id: Mapped[uuid.UUID | None] = mapped_column()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CodingJob(Base, TimestampMixin):
    __tablename__ = "coding_jobs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(300))
    prompt: Mapped[str] = mapped_column(Text)
    repo_path: Mapped[str] = mapped_column(String(500))
    worktree_path: Mapped[str] = mapped_column(String(500), default="")
    branch: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(30), index=True, default="queued")
    # queued | running | paused | completed | failed | cancelled
    pid: Mapped[int | None] = mapped_column(Integer)
    log_path: Mapped[str] = mapped_column(String(500), default="")
    claude_session_id: Mapped[str | None] = mapped_column(String(80))
    result: Mapped[dict[str, Any]] = mapped_column(default=dict)
    test_command: Mapped[str | None] = mapped_column(String(300))
    task_id: Mapped[uuid.UUID | None] = mapped_column()
    model: Mapped[str | None] = mapped_column(String(80))  # full model id the job runs with
    effort: Mapped[str | None] = mapped_column(String(20))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageRecord(Base):
    __tablename__ = "usage_records"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(120))
    purpose: Mapped[str] = mapped_column(String(60))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0)


# --------------------------------------------------------------------------- mobile companion
class MobileDevice(Base, TimestampMixin):
    """A paired smartphone: device-bound key (Android Keystore), push registration, app metadata."""

    __tablename__ = "mobile_devices"
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), default="android")
    model: Mapped[str] = mapped_column(String(120), default="")
    app_version: Mapped[str] = mapped_column(String(40), default="")
    public_key: Mapped[str] = mapped_column(Text)  # SPKI DER, base64 (EC P-256, non-exportable on the phone)
    push_key_id: Mapped[str] = mapped_column(String(40), default="")  # version of the payload encryption key
    push_token: Mapped[str | None] = mapped_column(Text)
    push_token_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ip: Mapped[str | None] = mapped_column(String(64))


class MobileRefreshToken(Base):
    """Refresh tokens rotate on every use; re-use of a spent token revokes the whole family."""

    __tablename__ = "mobile_refresh_tokens"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    family: Mapped[uuid.UUID] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class AlertDispatch(Base, TimestampMixin):
    """One decision of the notification & calling engine (dedupe key, channel, outcome)."""

    __tablename__ = "alert_dispatches"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    event_key: Mapped[str] = mapped_column(String(200), index=True)
    category: Mapped[str] = mapped_column(String(30))
    level: Mapped[str] = mapped_column(String(20), default="info")
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    decision: Mapped[str] = mapped_column(String(20))  # none | silent | push | call
    reason: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(30), default="pending")
    # pending | sent | undeliverable | duplicate | suppressed | snoozed | call_* | failed
    deep_link: Mapped[str] = mapped_column(String(300), default="")
    actions: Mapped[list[Any]] = mapped_column(default=list)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)
    delivered_to: Mapped[list[Any]] = mapped_column(default=list)
    call_id: Mapped[uuid.UUID | None] = mapped_column()
    remind_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class CallSession(Base, TimestampMixin):
    """A VoIP call between Sir's phone and Jarvis (LiveKit room)."""

    __tablename__ = "call_sessions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    direction: Mapped[str] = mapped_column(String(20))  # to_owner (Jarvis rings Sir) | to_jarvis (Sir calls)
    device_id: Mapped[uuid.UUID | None] = mapped_column()  # device that answered / placed the call
    room: Mapped[str] = mapped_column(String(120), unique=True)
    status: Mapped[str] = mapped_column(String(20), index=True, default="ringing")
    # ringing | connecting | active | ended | missed | declined | failed | cancelled
    reason: Mapped[str] = mapped_column(String(60), default="")  # agent | rule:<category> | user | test
    message: Mapped[str] = mapped_column(Text, default="")
    event_key: Mapped[str | None] = mapped_column(String(200), index=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str] = mapped_column(String(60), default="")
    transcript: Mapped[list[Any]] = mapped_column(default=list)
    session_id: Mapped[uuid.UUID | None] = mapped_column()
    metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # why Jarvis rings: the open question and what it refers to (jarvis.mobile.call_context)
    context: Mapped[dict[str, Any]] = mapped_column(default=dict)


# --------------------------------------------------------------------------- WhatsApp connector
# Portable column types: the same repository code runs on PostgreSQL (production) and SQLite (unit tests).
class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware datetimes on every backend (SQLite returns naive values otherwise)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value


PortableJSON = JSON().with_variant(JSONB(), "postgresql")
PortableBigInt = BigInteger().with_variant(Integer(), "sqlite")


class WhatsAppChat(Base):
    __tablename__ = "whatsapp_chats"
    jid: Mapped[str] = mapped_column(String(200), primary_key=True)
    name: Mapped[str] = mapped_column(String(300), default="")
    is_group: Mapped[bool] = mapped_column(Boolean, default=False)
    unread_count: Mapped[int] = mapped_column(Integer, default=0)
    last_message_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    muted_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    meta: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class WhatsAppContact(Base):
    __tablename__ = "whatsapp_contacts"
    jid: Mapped[str] = mapped_column(String(200), primary_key=True)
    lid: Mapped[str | None] = mapped_column(String(200), index=True)
    name: Mapped[str] = mapped_column(String(300), default="")  # Sir's address book name
    notify: Mapped[str] = mapped_column(String(300), default="")  # the name the contact set themselves
    verified_name: Mapped[str] = mapped_column(String(300), default="")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class WhatsAppMessage(Base):
    """One WhatsApp message. (chat_jid, msg_id) is unique: replays, history syncs and our own echo never
    create a second row."""

    __tablename__ = "whatsapp_messages"
    __table_args__ = (
        UniqueConstraint("chat_jid", "msg_id", name="uq_whatsapp_messages_chat_msg"),
        Index("ix_whatsapp_messages_chat_ts", "chat_jid", "ts"),
    )
    id: Mapped[int] = mapped_column(PortableBigInt, primary_key=True, autoincrement=True)
    chat_jid: Mapped[str] = mapped_column(String(200))
    msg_id: Mapped[str] = mapped_column(String(128), index=True)
    from_me: Mapped[bool] = mapped_column(Boolean, default=False)
    sender_jid: Mapped[str] = mapped_column(String(200), default="")
    sender_name: Mapped[str] = mapped_column(String(300), default="")
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="text")
    text: Mapped[str] = mapped_column(Text, default="")
    quoted_msg_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="")  # pending|server_ack|delivered|read|played|error
    forwarded: Mapped[bool] = mapped_column(Boolean, default=False)
    edited_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    read_by_me: Mapped[bool] = mapped_column(Boolean, default=False)
    media: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict)
    media_ref: Mapped[str | None] = mapped_column(Text)  # AES-GCM encrypted original message (download key)
    extra: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict)  # location, poll, reactions, …
    source: Mapped[str] = mapped_column(String(10), default="live")  # live | history | sent
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class WhatsAppFile(Base):
    """A file received via WhatsApp and stored by Jarvis (server or a device), with integrity data."""

    __tablename__ = "whatsapp_files"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    chat_jid: Mapped[str] = mapped_column(String(200), index=True)
    msg_id: Mapped[str] = mapped_column(String(128), index=True)
    sender_jid: Mapped[str] = mapped_column(String(200), default="")
    sender_name: Mapped[str] = mapped_column(String(300), default="")
    chat_name: Mapped[str] = mapped_column(String(300), default="")
    file_name: Mapped[str] = mapped_column(String(300))
    location: Mapped[str] = mapped_column(String(160), default="server")  # server | device:<name>
    path: Mapped[str] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    ext: Mapped[str] = mapped_column(String(12), default="")
    category: Mapped[str] = mapped_column(String(20), default="document")  # image|video|audio|document|archive|other
    size: Mapped[int] = mapped_column(PortableBigInt, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column()
    text_excerpt: Mapped[str] = mapped_column(Text, default="")  # search index for the content
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class WhatsAppSend(Base):
    """Outgoing message ledger: delivery status, and the guard against sending the same thing twice."""

    __tablename__ = "whatsapp_sends"
    send_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)  # recipient + content hash
    chat_jid: Mapped[str] = mapped_column(String(200), index=True)
    chat_name: Mapped[str] = mapped_column(String(300), default="")
    kind: Mapped[str] = mapped_column(String(20))
    files: Mapped[list[Any]] = mapped_column(PortableJSON, default=list)  # [{name, type, size, sha256}]
    text_len: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending | uploading | sent | server_ack | delivered | read | played | failed
    msg_ids: Mapped[list[Any]] = mapped_column(PortableJSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(120), default="")
    approval_id: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class WhatsAppSuggestion(Base):
    """Something Jarvis noticed in a chat (task, appointment, invoice …). Nothing is applied before Sir accepts."""

    __tablename__ = "whatsapp_suggestions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    chat_jid: Mapped[str] = mapped_column(String(200), index=True)
    msg_id: Mapped[str | None] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(30))  # task|appointment|reminder|invoice|change_request|project_info
    title: Mapped[str] = mapped_column(String(300))
    detail: Mapped[str] = mapped_column(Text, default="")
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending|accepted|dismissed
    applied: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict)
    remind_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)  # set when Sir accepts
    reminded_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


WHATSAPP_TABLES = [
    WhatsAppChat.__table__,
    WhatsAppContact.__table__,
    WhatsAppMessage.__table__,
    WhatsAppFile.__table__,
    WhatsAppSend.__table__,
    WhatsAppSuggestion.__table__,
]
