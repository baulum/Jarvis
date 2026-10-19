"""Hash-chained, append-only audit log.

Every entry stores ``hash = sha256(prev_hash + canonical_json(entry))``. ``verify_chain``
detects modified or deleted rows. Writes are serialised with a Postgres advisory lock so
the chain stays linear even with concurrent writers.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text

from jarvis.db import session_scope
from jarvis.db.models import AuditEntry
from jarvis.events import bus
from jarvis.logging_setup import redact

logger = logging.getLogger(__name__)
_LOCK_ID = 7_301_337
GENESIS = "0" * 64
_SENSITIVE_KEYS = {"password", "token", "secret", "api_key", "pin", "authorization", "auth_token"}


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if k.lower() in _SENSITIVE_KEYS else _scrub(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, str):
        return redact(value[:4000])
    return value


def _digest(
    prev_hash: str, ts: datetime, actor: str, action: str, target: str, outcome: str, details: dict[str, Any]
) -> str:
    canonical = json.dumps(
        {
            "ts": ts.isoformat(),
            "actor": actor,
            "action": action,
            "target": target,
            "outcome": outcome,
            "details": details,
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256((prev_hash + canonical).encode()).hexdigest()


class AuditUnavailable(RuntimeError):
    """A ``strict`` audit entry could not be written (the action must not run unrecorded)."""


async def audit(
    actor: str,
    action: str,
    *,
    target: str = "",
    outcome: str = "ok",
    details: dict[str, Any] | None = None,
    strict: bool = False,
) -> None:
    """Append an entry. ``strict``: raise :class:`AuditUnavailable` instead of only logging when it fails."""
    clean = _scrub(details or {})
    ts = datetime.now(UTC)
    try:
        async with session_scope() as s:
            await s.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": _LOCK_ID})
            prev = (await s.execute(select(AuditEntry.hash).order_by(AuditEntry.id.desc()).limit(1))).scalar()
            prev_hash = prev or GENESIS
            entry = AuditEntry(
                ts=ts,
                actor=actor,
                action=action,
                target=target[:300],
                outcome=outcome,
                details=clean,
                prev_hash=prev_hash,
                hash=_digest(prev_hash, ts, actor, action, target[:300], outcome, clean),
            )
            s.add(entry)
    except Exception:
        # Auditing must never silently disappear: log loudly, but don't crash the caller.
        logger.exception("AUDIT WRITE FAILED action=%s actor=%s", action, actor)
        if strict:
            raise AuditUnavailable("Audit-Protokoll nicht beschreibbar") from None
        return
    await bus.publish("audit.entry", actor=actor, action=action, target=target, outcome=outcome)


async def verify_chain(limit: int | None = None) -> dict[str, Any]:
    async with session_scope() as s:
        q = select(AuditEntry).order_by(AuditEntry.id.asc())
        if limit:
            q = q.limit(limit)
        rows = (await s.execute(q)).scalars().all()
    prev = GENESIS
    for row in rows:
        expected = _digest(prev, row.ts, row.actor, row.action, row.target, row.outcome, row.details)
        if row.prev_hash != prev or row.hash != expected:
            return {"valid": False, "broken_at": row.id, "checked": len(rows)}
        prev = row.hash
    return {"valid": True, "checked": len(rows)}
