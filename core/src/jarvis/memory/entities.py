"""Entity resolution and text consolidation for the memory engine (pure functions, no IO).

Why this exists: the extractor named the same customer "Kunde Müller Gebäudereinigung" in one
conversation and "Müller Gebäudereinigung" in the next; titles differed and the embeddings were
below the duplicate threshold, so two customer notes were created. Each follow-up then appended
a paraphrase of a sentence the note already contained ("**Ergänzung:** … für einen Betrag von
499 Euro"). Here names are reduced to a canonical key (role prefixes, legal forms, punctuation and
case removed) and new sentences are only added when they are not already covered.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

_PREFIXES = (
    "kunde",
    "kundin",
    "neukunde",
    "projekt",
    "projektstart",
    "website-projekt",
    "webseiten-projekt",
    "firma",
    "unternehmen",
    "herr",
    "frau",
    "person",
    "kontakt",
    "ansprechpartner",
    "ansprechpartnerin",
)
_LEGAL = {"gmbh", "ug", "ag", "kg", "ohg", "gbr", "e.k.", "ek", "mbh", "co", "haftungsbeschränkt", "inc", "ltd", "llc"}
_SPLIT_TITLE = re.compile(r"\s+[–—-]\s+|:\s+")
_SENT = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD = re.compile(r"[\wäöüß]+", re.I)

STOPWORDS = frozenset(
    """der die das den dem des ein eine einer eines einem einen und oder aber ist sind war waren wird werden hat
    haben hatte für fur mit von vom zu zum zur im in am an auf aus bei nach über unter vor wie was wer wo wann
    warum welche welcher welches ich du er sie es wir ihr mir dir mich dich uns euch mein dein sein ihr unser
    nicht kein keine noch schon auch nur sehr mal bitte jarvis sir hey ja nein dass daß als also dann denn doch
    so wenn weil this that the and for with""".split()
)


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"\s+", " ", text).strip()


def canonical_name(name: str, *, facets: bool = True) -> str:
    """Matching key for an entity name: 'Kunde Müller Gebäudereinigung GmbH' -> 'müller gebäudereinigung'.

    ``facets=True`` drops a " – suffix" ("Westside Taxi UG – Kunde und Geschäftsführung" is about the
    company). For projects the suffix names a different aspect ("Westside Taxi – Logo" vs "– Liefertermin")
    and must be kept (``facets=False``).
    """
    s = fold(name)
    if facets:
        s = _SPLIT_TITLE.split(s, maxsplit=1)[0]
    s = re.sub(r"\([^)]*\)", " ", s)
    words = [w for w in re.split(r"[\s,;]+", s) if w]
    while words and words[0].strip(".:") in _PREFIXES:
        words = words[1:]
    words = [w for w in words if w.strip(".") not in _LEGAL]
    return " ".join(w.strip(".,;:!?\"'") for w in words).strip()


def same_entity(a: str, b: str, *, facets: bool = True) -> bool:
    ka, kb = canonical_name(a, facets=facets), canonical_name(b, facets=facets)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    shorter, longer = sorted((ka, kb), key=len)
    # "westside taxi" vs "westside taxi ingolstadt": a whole-word prefix of at least two words
    if len(shorter.split()) >= 2 and (longer + " ").startswith(shorter + " "):
        return True
    return SequenceMatcher(None, ka, kb).ratio() >= 0.92


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text) if s and len(s.strip()) > 2]


def _norm_sentence(s: str) -> str:
    s = re.sub(r"\*\*[^*]+\*\*:?", " ", s)  # "**Ergänzung 2026-10-15:**"
    return " ".join(w for w in _WORD.findall(fold(s)) if w not in STOPWORDS)


def is_covered(sentence: str, existing: list[str], threshold: float = 0.8) -> bool:
    """True if ``sentence`` adds nothing to ``existing`` (equal, contained or a close paraphrase)."""
    n = _norm_sentence(sentence)
    if not n:
        return True
    words = set(n.split())
    for e in existing:
        en = _norm_sentence(e)
        if not en:
            continue
        if n in en:
            return True
        ew = set(en.split())
        if words and len(words & ew) / len(words) >= 0.9:
            return True
        if SequenceMatcher(None, n, en).ratio() >= threshold:
            return True
    return False


def new_information(content: str, existing_body: str) -> list[str]:
    """Sentences of ``content`` that are not yet in ``existing_body``."""
    have = sentences(existing_body)
    out: list[str] = []
    for s in sentences(content):
        if not is_covered(s, have + out):
            out.append(s)
    return out


def query_terms(query: str) -> list[str]:
    return [w for w in _WORD.findall(fold(query)) if len(w) >= 3 and w not in STOPWORDS][:12]


def best_sentences(content: str, terms: list[str], limit_chars: int) -> str:
    """Context compression: keep the sentences that mention the query terms (plus the first one)."""
    sents = sentences(re.sub(r"\n+Bezug:.*$", "", content, flags=re.S))
    if not sents:
        return content[:limit_chars]
    scored = []
    for i, s in enumerate(sents):
        low = fold(s)
        hits = sum(1 for t in terms if t in low)
        scored.append((hits, -i, s))
    keep = {sents[0]}
    for hits, _, s in sorted(scored, reverse=True):
        if hits == 0:
            break
        keep.add(s)
    out = []
    used = 0
    for s in sents:
        if s in keep and used + len(s) <= limit_chars:
            out.append(s)
            used += len(s) + 1
    return " ".join(out) or sents[0][:limit_chars]


_NUM = re.compile(r"\d+(?:[.,]\d+)*")


def lost_facts(old: str, new: str, names: list[str]) -> list[str]:
    """Facts of ``old`` missing from ``new``: numbers and the given names (title, aliases, entities)."""
    o = re.sub(r"\*\*[^*]+\*\*:?", " ", old)  # ignore "**Ergänzung 2026-10-15:**" markers
    new_f = fold(new)
    new_digits = {n.replace(",", ".") for n in _NUM.findall(new)}
    lost = [n for n in dict.fromkeys(_NUM.findall(o)) if n.replace(",", ".") not in new_digits]
    for name in dict.fromkeys(names):
        key = canonical_name(name, facets=False)
        if key and fold(name) in fold(o) and key not in new_f and fold(name) not in new_f:
            lost.append(name)
    return lost
