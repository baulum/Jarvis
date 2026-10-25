"""Who is "Jonathan"? Fuzzy resolution of a name, number or chat id to exactly one WhatsApp chat.

Names are compared case- and accent-insensitively (``Müller`` = ``mueller``), against the address book name, the
contact's own name, a business name and group subjects. Exactly one clear hit is used; otherwise the caller gets
a ranked candidate list and asks Sir – a message never goes to a guessed recipient.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from jarvis.whatsapp.files import unicode_fold

CLEAR = 0.9  # a hit at least this good …
MARGIN = 0.08  # … and this much better than the next one is unambiguous
SHOW = 0.55  # candidates below this are not offered


@dataclass(slots=True)
class Candidate:
    jid: str
    names: list[str]
    is_group: bool = False
    last_ts: float = 0.0
    score: float = 0.0
    matched: str = ""

    @property
    def label(self) -> str:
        return self.names[0] if self.names else phone_from_jid(self.jid) or self.jid

    def public(self) -> dict[str, Any]:
        out: dict[str, Any] = {"chat_id": self.jid, "name": self.label, "is_group": self.is_group}
        others = [n for n in self.names[1:] if n != self.label]
        if others:
            out["also_known_as"] = others[:3]
        return out


@dataclass(slots=True)
class Resolution:
    match: Candidate | None
    candidates: list[Candidate] = field(default_factory=list)
    unknown_number: str | None = None  # a phone number that is no known chat (must be checked with WhatsApp)


def phone_from_jid(jid: str) -> str:
    user = jid.split("@", 1)[0].split(":", 1)[0]
    return user if jid.endswith("@s.whatsapp.net") and user.isdigit() else ""


def normalize(text: str) -> str:
    t = unicode_fold(text)
    t = re.sub(r"[^\w\s]", " ", t)
    return " ".join(t.split())


def _phone(query: str) -> str | None:
    if re.fullmatch(r"[+\d][\d\s()./-]{5,}", query.strip()):
        digits = re.sub(r"\D", "", query)
        if query.strip().startswith("00"):
            digits = digits[2:]
        if 7 <= len(digits) <= 15:
            return digits
    return None


def _score(query: str, name: str) -> float:
    q, n = normalize(query), normalize(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    q_tokens, n_tokens = q.split(), n.split()
    if len(q_tokens) == 1 and q_tokens[0] in n_tokens:
        return 0.93 if n_tokens[0] == q_tokens[0] else 0.9  # "Jonathan" ↔ "Jonathan Weber"
    if set(q_tokens) <= set(n_tokens):
        return 0.92
    if n.startswith(q) and len(q) >= 3:
        return 0.86
    if any(t.startswith(q) for t in n_tokens) and len(q) >= 3:
        return 0.82
    ratio = SequenceMatcher(None, q, n).ratio()
    token_best = max((SequenceMatcher(None, q, t).ratio() for t in n_tokens), default=0.0)
    return max(ratio, token_best * 0.9)


def resolve(query: str, entries: list[dict[str, Any]], *, prefer_groups: bool | None = None) -> Resolution:
    q = (query or "").strip()
    if not q:
        return Resolution(None)
    cands = [
        Candidate(
            str(e["jid"]), [n for n in e.get("names", []) if n], bool(e.get("is_group")), float(e.get("last_ts") or 0)
        )
        for e in entries
    ]
    if "@" in q:  # a chat id
        exact = next((c for c in cands if c.jid == q), None)
        if exact is not None:
            exact.score = 1.0
            return Resolution(exact, [exact])
        if re.fullmatch(r"[\w.:-]+@(s\.whatsapp\.net|g\.us|lid)", q):
            return Resolution(Candidate(q, [], q.endswith("@g.us"), score=1.0))
        return Resolution(None)
    digits = _phone(q)
    if digits:
        hits = [c for c in cands if phone_from_jid(c.jid) == digits or phone_from_jid(c.jid).endswith(digits[-9:])]
        if len(hits) == 1:
            hits[0].score = 1.0
            return Resolution(hits[0], hits)
        if len(hits) > 1:
            return Resolution(None, hits[:5])
        return Resolution(None, [], unknown_number=digits)
    for c in cands:
        best, matched = 0.0, ""
        for n in c.names:
            sc = _score(q, n)
            if sc > best:
                best, matched = sc, n
        if prefer_groups is not None and c.is_group != prefer_groups:
            best *= 0.9
        c.score, c.matched = best, matched
    ranked = sorted((c for c in cands if c.score >= SHOW), key=lambda c: (-c.score, -c.last_ts))
    if not ranked:
        return Resolution(None, [])
    top = ranked[0]
    second = ranked[1].score if len(ranked) > 1 else 0.0
    if top.score >= CLEAR and top.score - second >= MARGIN:
        return Resolution(top, ranked[:5])
    # two equally good name matches, but only one is a real person/business with a conversation: still ask
    return Resolution(None, ranked[:5])
