"""Status model of tasks (A6).

Stored values stay what they were (no data migration, old clients keep working); new values are added:

=================  =====================================================================================
stored             canonical phase (``phase()``, spec: QUEUED … COMPLETED)
=================  =====================================================================================
queued             queued
running            running
retrying           retrying      – a transient error, backoff running (agent tasks)
waiting_approval   waiting_for_user
waiting_input      waiting_for_user
waiting_external   waiting_external  (extra: waits for a coding job etc.)
paused             paused            (extra: Sir paused it)
blocked            blocked       – limit reached / real blocker; ``retry`` continues it
failed             failed
cancelled          cancelled
completed          completed     – only after the completion check (agent tasks)
=================  =====================================================================================
"""

from __future__ import annotations

QUEUED = "queued"
RUNNING = "running"
RETRYING = "retrying"
WAITING_APPROVAL = "waiting_approval"
WAITING_INPUT = "waiting_input"
WAITING_EXTERNAL = "waiting_external"
PAUSED = "paused"
BLOCKED = "blocked"
FAILED = "failed"
CANCELLED = "cancelled"
COMPLETED = "completed"

TERMINAL = frozenset({COMPLETED, FAILED, CANCELLED})
# a worker holds the task (lease + heartbeat) in these states
EXECUTING = frozenset({RUNNING, RETRYING})
ACTIVE = frozenset({QUEUED, RUNNING, RETRYING, PAUSED, WAITING_APPROVAL, WAITING_INPUT, WAITING_EXTERNAL})
WAITING_FOR_USER = frozenset({WAITING_APPROVAL, WAITING_INPUT})

_PHASE = {WAITING_APPROVAL: "waiting_for_user", WAITING_INPUT: "waiting_for_user"}


def phase(status: str) -> str:
    """Canonical phase of a stored status (old and new values alike)."""
    return _PHASE.get(status, status)
