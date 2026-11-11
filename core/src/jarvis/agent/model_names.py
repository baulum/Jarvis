"""Erkennung von Modellnamen in Freitext (Chat, Diktat, Telefon).

Erkannt werden die Familien Haiku, Sonnet und Opus in allen gängigen Schreibweisen – auch buchstabiert
(``h.a.i.k.u``, ``h a i k u``, ``h-a-i-k-u``) – samt direkt angehängter Version (``5.5``, ``5,5``, ``fünf komma fünf``,
``fünf punkt fünf``). Das Modul ist rein syntaktisch: Es rät nie. Ob eine Version bekannt ist, entscheidet
:data:`KNOWN_VERSION`; Familien ohne Zahl gelten als die aktuelle Version.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

KNOWN_VERSION = "5.5"

FAMILY_SPELLINGS: dict[str, tuple[str, ...]] = {
    "haiku": ("haiku", "hayku"),
    "sonnet": ("sonnet", "sonett"),
    "opus": ("opus",),
}

# between the letters of a spelled-out name: spaces, dots, hyphens, underscores, middle dots
_LETTER_SEP = r"[ \t .\-_·•]*"
_SEP_CHARS = re.compile(r"[ \t .\-_·•]")


def _spell(word: str) -> str:
    return _LETTER_SEP.join(re.escape(c) for c in word)


#: one capturing group; matches "haiku" as well as "h.a.i.k.u" / "h a i k u" / "h-a-i-k-u"
FAMILY_PATTERN = "(" + "|".join(_spell(w) for words in FAMILY_SPELLINGS.values() for w in words) + ")"

_NUMBER_WORDS = {
    "null": 0, "eins": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "fuenf": 5, "funf": 5,
    "sechs": 6, "sieben": 7, "acht": 8, "neun": 9,
}  # fmt: skip
_NUM = r"(?:\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")"
_DECIMAL_SEP = r"(?:[.,\-]|[ \t]+(?:komma|punkt|point|dot)[ \t]+)"
#: "5.5", "5,5", "5-5", "5 komma 5", "fünf punkt fünf" – or a bare integer (digits only, never a number word)
VERSION_PATTERN = r"(?:" + _NUM + _DECIMAL_SEP + _NUM + r"|\d{1,2})(?![\w])"

_VERSION_AFTER = re.compile(r"[ \t \-]*(" + VERSION_PATTERN + ")", re.IGNORECASE)
_VERSION_BEFORE = re.compile(r"(?<![\w.,])(" + VERSION_PATTERN + r")[ \t \-]*$", re.IGNORECASE)
_FAMILY_RE = re.compile(r"(?<![\w])" + FAMILY_PATTERN + r"(?![\w])", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ModelMention:
    family: str  # haiku | sonnet | opus
    version: str | None  # normalised ("5.5") or None when no number stands next to the name
    start: int
    end: int
    spelled_out: bool  # written with separators between the letters ("h.a.i.k.u")

    @property
    def version_known(self) -> bool:
        return self.version is None or self.version == KNOWN_VERSION


def family_of(raw: str) -> str | None:
    """"H.a.i.k.u" → "haiku"; None for anything that is not a model family."""
    w = _SEP_CHARS.sub("", raw.lower())
    for family, spellings in FAMILY_SPELLINGS.items():
        if w in spellings:
            return family
    return None


def version_of(raw: str) -> str | None:
    """"5,5" / "fünf komma fünf" → "5.5"; "5" → "5"; None if unparseable."""
    parts = [p for p in re.split(_DECIMAL_SEP, raw.strip().lower()) if p != ""]
    values: list[int] = []
    for p in parts:
        if p.isdigit():
            values.append(int(p))
        elif p in _NUMBER_WORDS:
            values.append(_NUMBER_WORDS[p])
        else:
            return None
    if len(values) == 1:
        return str(values[0])
    if len(values) == 2:
        return f"{values[0]}.{values[1]}"
    return None


def adjacent_version(text: str, start: int, end: int) -> tuple[str | None, int, int]:
    """Version written right after (``Haiku 5.5``) or, if decimal, right before (``5.5 Haiku``) the span
    ``text[start:end]``. Returns (version, new_start, new_end); the span grows to include the version."""
    probe = end + 1 if text[end : end + 1] == "." and "." in text[start:end] else end  # "H. A. I. K. U. 5.5"
    after = _VERSION_AFTER.match(text, probe)
    if after:
        return version_of(after.group(1)), start, after.end()
    before = _VERSION_BEFORE.search(text[:start])
    if before:
        v = version_of(before.group(1))
        if v is not None and "." in v:  # a lone number before the name is usually a count ("5 Haiku-Gedichte")
            return v, before.start(1), end
    return None, start, end


def find_mentions(text: str) -> list[ModelMention]:
    out: list[ModelMention] = []
    for m in _FAMILY_RE.finditer(text):
        family = family_of(m.group(1))
        if family is None:
            continue
        version, start, end = adjacent_version(text, m.start(), m.end())
        out.append(ModelMention(family, version, start, end, bool(_SEP_CHARS.search(m.group(1)))))
    return out


def parse_spec(value: str) -> ModelMention | None:
    """A tool argument such as "haiku", "H.a.i.k.u 5,5" or "Opus": exactly one mention, nothing else."""
    mentions = find_mentions(value)
    if len(mentions) != 1:
        return None
    mention = mentions[0]
    leftover = value[: mention.start] + value[mention.end :]
    return mention if not re.search(r"\w", leftover.replace("claude", "", 1).lower()) else None
