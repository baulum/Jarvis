"""What a call Jarvis placed is about: the open question Sir is answering, and what it refers to.

A call is a new conversation on the phone channel. Without this context, Sir's "ja, mach das" arrives with nothing
to refer to. The context is stored with the call (``CallSession.context``, keyed by the call id), loaded by the
call's voice agent when the session starts, and handed to the agent on every turn of the call.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from jarvis.db import session_scope
from jarvis.db.models import CallSession

# Short answers that only say yes (or "do it") and name nothing. Only meaningful with an open question.
_CONFIRMATIONS = frozenset(
    "ja jo jawohl jawoll yes ok okay oke gerne gern klar genau passt sicher einverstanden".split()
)
_DO_IT = frozenset({"mach", "machs", "tu", "tue"})
_FILLER = frozenset({"bitte", "das", "es", "s", "doch", "einfach", "mal", "dann", "so", "also", "sehr", "los", "dies"})


def build_call_context(question: str, *, reason: str, refs: dict[str, Any] | None = None) -> dict[str, Any]:
    """The context stored with a call. ``refs`` name what the question is about (task, job, approval, alert, chat)."""
    return {
        "question": question.strip()[:2000],
        "reason": reason[:60],
        "refs": {k: str(v)[:200] for k, v in (refs or {}).items() if v},
    }


def open_question(context: dict[str, Any] | None) -> str | None:
    """The question Jarvis asked Sir on this call, or None when the call has no open question."""
    question = str((context or {}).get("question") or "").strip()
    return question or None


def is_vague_confirmation(text: str) -> bool:
    """A short yes-type answer that names nothing ("ja", "Ja bitte mach das"). It needs a question to refer to."""
    words = re.findall(r"\w+", text.lower())
    if not words or len(words) > 6:
        return False
    if not all(w in _CONFIRMATIONS | _DO_IT | _FILLER for w in words):
        return False
    return any(w in _CONFIRMATIONS | _DO_IT for w in words)


def call_context_block(context: dict[str, Any] | None) -> str:
    """Instructions for a turn of the call: the question Sir answers, or the honest statement that there is none."""
    question = open_question(context)
    if question is None:
        return (
            "Kontext dieses Anrufs: Ich kenne hier keine offene Rückfrage an Sir. Bezieht sich Sir auf etwas, das du "
            "nicht siehst, sag das ehrlich und frag konkret nach, statt so zu tun, als wäre es bekannt. Auf eine vage "
            "Bestätigung („ja“, „mach das“) hin führst du nichts mit Außenwirkung aus."
        )
    lines = [f"Kontext dieses Anrufs: Du hast Sir in diesem Anruf gefragt: „{question}“"]
    refs = (context or {}).get("refs") or {}
    if refs:
        lines.append("Bezug: " + ", ".join(f"{k}={v}" for k, v in refs.items()) + ".")
    lines.append(
        "Antwortet Sir darauf zustimmend („ja“, „mach das“), bezieht sich das auf genau diese Frage: Nenne vor einer "
        "Aktion mit Außenwirkung konkret, was du tust. Ist die Antwort auf etwas anderes bezogen oder unklar, frag "
        "konkret nach und führe nichts aus."
    )
    return "\n".join(lines)


async def load_call_context(call_id: uuid.UUID) -> dict[str, Any]:
    async with session_scope() as s:
        row = await s.get(CallSession, call_id)
        return dict(row.context or {}) if row is not None else {}
