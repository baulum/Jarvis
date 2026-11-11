"""Anrufstil – how Jarvis sounds on a call to Sir's phone. The one place for every call-only speech rule.

Applies to the voice calls of the J.A.R.V.I.S. app (chat channel ``phone``: ``mobile_call_owner``,
``mobile_call_when``, calls Sir starts in the app). The chat, the coding jobs, the Mac voice daemon and the model
choice are not touched by anything in here.

Two layers, because a prompt is only a request:

1. **Prompt** (``call_style_block``): the rules for the model, plus whether Sir is moving (driving mode).
2. **Enforcement** (``CallReplyFilter`` / ``speakable``): whatever the model writes is cleaned (IDs, paths, model
   names, commands, error codes, machine timestamps, abbreviations, Markdown, emojis) and cut to the allowed
   number of sentences before it is spoken. What cannot be replaced safely by an everyday word is left out.

Movement: ``motion_before_call`` (before ringing) and ``LocationStore.motion`` (every turn) use
``jarvis.mobile.motion``; nothing about the location is stored or spoken.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from jarvis.mobile.location import FRESH_ENOUGH_S, STALE_AFTER_MIN
from jarvis.mobile.motion import MOVING_SPEED_KMH, Motion

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------------------------------------- limits
MAX_SENTENCES = 3  # at rest: one to three sentences per answer
MAX_SENTENCES_DRIVING = 1  # in the car / in motion: only the core statement
MOTION_REFRESH_WAIT_S = 5.0  # before ringing: how long to wait for a fresh fix from the phone
MOTION_CHECK_TIMEOUT_S = 8.0  # hard cap for the whole check, ringing must not wait longer
LOCATION_REFRESH_EVERY_S = 60.0  # during a call: ask the phone for a new fix at most this often
FALLBACK_SUBJECT = "Eine Nachricht für Sie"  # ring display / car head unit, when nothing speakable is left

_BERLIN = ZoneInfo("Europe/Berlin")


def max_sentences_for(motion: Motion) -> int:
    """Driving mode only for a certain ``yes``. ``no`` and ``unknown`` get the normal length."""
    return MAX_SENTENCES_DRIVING if motion == Motion.MOVING else MAX_SENTENCES


# ---------------------------------------------------------------------------------------------------------- prompt
CALL_STYLE_RULES = (
    "Anrufstil (verbindlich): Sir hört dich nur, er sieht nichts. Sprich wie ein Mensch am Telefon: kurze, einfache "
    "Sätze in deutscher Umgangssprache, Sie-Anrede, im gewohnten Jarvis-Stil. Keine Fachbegriffe, wo ein Alltagswort "
    "reicht. Keine Aufzählungen, keine Listen, keine Markdown-Zeichen, keine Emojis. Nenne am Telefon nie IDs, "
    "Hashes, Modellnamen, Dateinamen, Pfade, Befehle, Fehlercodes, Abkürzungen oder Zeitangaben im Maschinenformat; "
    "die Stimme kann sie nicht sprechen. Sag stattdessen, worum es geht („Der Auftrag für die Website ist fertig.“, "
    "„um halb vier“). Koordinaten liest du nie vor. Geht etwas schief, sag kurz und verständlich, dass es nicht "
    "geklappt hat und wie es weitergeht – nie eine Fehlermeldung oder einen Code."
)
_STYLE_MOVING = (
    "Sir ist gerade unterwegs (Auto oder Bewegung): Sprich nur das Nötigste. Genau EIN Satz, die Kernaussage zuerst, "
    "keine Rückfragenketten (höchstens eine kurze Rückfrage), keine Erklärungen. Wichtiges sofort. Details liest du "
    "nicht vor, sondern bietest sie später als Nachricht an („Die Einzelheiten schicke ich Ihnen als Nachricht.“)."
)
_STYLE_STILL = "Sir ist gerade nicht in Bewegung: normale Länge, ein bis drei Sätze, freundlich und klar."
_STYLE_UNKNOWN = (
    "Ob Sir gerade unterwegs ist, weißt du nicht (Standort fehlt oder ist zu alt). Schließe nicht auf Bewegung; "
    "antworte in einem bis drei kurzen Sätzen. Fragt Sir danach, sag ehrlich in einem einfachen Satz, dass du es "
    "gerade nicht weißt."
)


def call_style_block(motion: Motion) -> str:
    """The style instructions for one turn of a call (after the general phone instructions)."""
    mode = {Motion.MOVING: _STYLE_MOVING, Motion.STILL: _STYLE_STILL}.get(motion, _STYLE_UNKNOWN)
    return f"{CALL_STYLE_RULES}\n{mode} (Gesprochen werden höchstens {max_sentences_for(motion)} Satz/Sätze.)"


# --------------------------------------------------------------------------------------------- motion before a call
NOTE_NO_FIX = (
    "Hinweis: Vom Handy liegt kein Standort vor (Freigabe aus oder Handy gerade nicht erreichbar). Ob Sir unterwegs "
    "ist, weißt du nicht – sag ihm das in der Antwort kurz und ehrlich."
)
NOTE_STALE = (
    f"Hinweis: Der letzte Standort des Handys ist älter als {STALE_AFTER_MIN} Minuten. Ob Sir unterwegs ist, weißt du "
    "nicht – sag ihm das in der Antwort kurz und ehrlich."
)
NOTE_ONE_FIX = (
    "Hinweis: Es gibt erst eine aktuelle Standortmessung, deshalb lässt sich nicht sagen, ob Sir unterwegs ist. "
    "Sag ihm das in der Antwort kurz und ehrlich."
)


@dataclass(frozen=True, slots=True)
class MotionCheck:
    """Result of the check before a call. ``note`` (only when ``motion`` is unknown) is for the agent's answer to Sir;
    it never contains location data."""

    motion: Motion
    note: str | None = None


async def motion_before_call(
    location: Any, *, wait_s: float = MOTION_REFRESH_WAIT_S, timeout_s: float = MOTION_CHECK_TIMEOUT_S
) -> MotionCheck:
    """Rule 3: before ringing, look at the phone's location (``location`` = the ``LocationStore`` behind
    ``mobile_get_location``). A missing or older fix is refreshed with a push that carries no data; the movement is
    derived from the last two fixes in memory.

    Unlike the tool it does not do the address lookup: the coordinates stay in the core and do not go to
    Nominatim for this. Never raises – the call must not fail because of this check."""
    if location is None:
        return MotionCheck(Motion.UNKNOWN, NOTE_NO_FIX)
    try:
        fix = location.latest()
        if fix is None or fix.age_minutes() * 60 > FRESH_ENOUGH_S:
            await asyncio.wait_for(location.request_fresh(wait_s), timeout_s)
    except Exception as e:  # TimeoutError included; no details (could carry location data), only the type
        logger.info("Movement check before call: no fresh location (%s)", type(e).__name__)
    try:
        fix = location.latest()
        motion = location.motion()
    except Exception as e:
        logger.warning("Movement check before call failed (%s)", type(e).__name__)
        return MotionCheck(Motion.UNKNOWN, NOTE_NO_FIX)
    if motion != Motion.UNKNOWN:
        return MotionCheck(motion)
    if fix is None:
        return MotionCheck(motion, NOTE_NO_FIX)
    if fix.age_minutes() > STALE_AFTER_MIN:
        return MotionCheck(motion, NOTE_STALE)
    return MotionCheck(motion, NOTE_ONE_FIX)


# --------------------------------------------------------------------------------------------------- text cleaning
_MONTHS = "Januar Februar März April Mai Juni Juli August September Oktober November Dezember".split()
_MONTH_RE = "|".join(_MONTHS)

_FENCE = re.compile(r"```.*?```", re.DOTALL)
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"')\]]+", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

_ISO_DATETIME = re.compile(
    r"\b(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?(?![\w:])"
)
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_EPOCH = re.compile(r"\b1[5-9]\d{8}(?:\d{3})?\b")

_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_PATH_WITH_FILE = re.compile(
    r"(?<![\w/\\:.@-])(?:[A-Za-z]:\\|~/|\.{1,2}/|/)?(?:[\w.@+-]+[/\\])+[\w.@+-]+\.[A-Za-z][A-Za-z0-9]{0,5}\b"
)
_PATH_ABS = re.compile(r"(?<![\w/\\:.@-])(?:[A-Za-z]:\\|~/|/)(?:[\w.@+-]+[/\\]?)+")
_FILE_EXT = (
    "py ts tsx js jsx mjs json md ya?ml sh env log sql txt dart kt html? css toml lock csv xml ini cfg conf plist "
    "gradle apk dmg zip png jpe?g pdf mp4 mov"
).replace(" ", "|")
_FILENAME = re.compile(rf"(?<![\w/@-])([\w-]*(?:\.[\w-]+)*)\.(?:{_FILE_EXT})\b", re.IGNORECASE)
_PRODUCT_JS = frozenset({"node", "next", "nuxt", "vue", "three", "chart", "express", "alpine", "ember", "d3"})

_MODEL = re.compile(
    r"\bclaude[-\s][\w.\-]*\d[\w.\-]*|\b(?:gpt|gemini|llama|mistral|qwen|whisper)[-\s]?\d[\w.\-]*"
    r"|\b(?:Claude\s+)?(?:Haiku|Sonnet|Opus|Fable)(?:[\s-]+\d+(?:[.,-]\d+)*)?\b|\bEffort\s+(?:low|medium|high|xhigh|max)\b",
    re.IGNORECASE,
)
_COMMAND = re.compile(
    r"\b(?:git|npm|npx|pnpm|yarn|pip3?|pytest|docker|curl|sudo|brew)\s+"
    r"(?:(?:run|exec)(?:\s+[\w:.-]+)?|push|pull|commit|install|build|test|start|status|checkout|merge|rebase|clone"
    r"|add|ps|up|down|logs?|diff|reset|stash|restart|publish|dev|deploy|-{1,2}[\w-]+)\b"
    r"(?:\s+(?:-{1,2}[\w=.-]+|[\w.-]*[./\\][\w./\\-]*))*"
)
_FLAG = re.compile(r"(?<!\S)--[a-z][\w-]+(?:=\S+)?")
_ERR_WORD_CODE = re.compile(
    r"\b(?:(?:Fehlercode|Fehler|Error[\s-]?code|Error)\s*[:#]?\s*(?:[A-Z]{0,6}[-_]?)?[1-5]\d\d"
    r"|Status(?:code)?\s*[:#]?\s*[45]\d\d)\b"
)
_HTTP_CODE = re.compile(r"\bHTTP[\s/-]*(?:\d\.\d\s+)?[1-5]\d\d\b")
_EXIT_CODE = re.compile(r"\bExit[\s-]?code\s*[:#]?\s*-?\d+\b", re.IGNORECASE)
_EXC_CLASS = re.compile(r"\b(?:[A-Z][a-z0-9]+)+(?:Error|Exception)\b|\bE(?:CONN|NOENT|ACCES|PERM|TIMEDOUT)[A-Z]*\b")
_UPPER_SNAKE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
_HEX_LITERAL = re.compile(r"\b0x[0-9a-fA-F]+\b")

_ID_AFTER_WORD = re.compile(
    r"\b(?P<w>Job|Auftrag|Aufgabe|Task|Commit|Branch|Anruf|Call|Session|Sitzung|Version|Build|Release|Ticket|Run|Lauf)"
    r"(?:[\s-]*ID)?[\s:#=-]*#?(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{5,64}\b"
)
_ID_LABEL = re.compile(r"\b(?:\w+-)?ID\b[\s:#=]*(?=[0-9a-fA-F-]*\d)[0-9a-fA-F-]{4,}")
_HASH = re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,64}\b")
_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_CALLABLE = re.compile(r"\b[\w.]+\(\)")

_DATIVE_FILE = re.compile(r"\b(in|aus|von|bei|mit|nach|seit)\s+eine Datei\b", re.IGNORECASE)
_JOB_START = re.compile(r"^(?:Coding-)?Job\b")
_JOB = re.compile(r"\b(?:Coding-)?Jobs\b|\b(?:Coding-)?Job\b")

# (pattern, replacement) – everyday words only where grammar and meaning stay safe, otherwise left out
_ABBREVIATIONS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p, re.IGNORECASE), r_)
    for p, r_ in (
        (r"\bz\.\s?B\.", "zum Beispiel"),
        (r"\bd\.\s?h\.", "das heißt"),
        (r"\bu\.\s?a\.", "unter anderem"),
        (r"\bz\.\s?T\.", "zum Teil"),
        (r"\bu\.\s?U\.", "unter Umständen"),
        (r"\bo\.\s?ä\.", "oder ähnlich"),
        (r"\bi\.\s?d\.\s?R\.", "in der Regel"),
        (r"\bbzw\.", "beziehungsweise"),
        (r"\bca\.", "etwa"),
        (r"\busw\.", "und so weiter"),
        (r"\betc\.", "und so weiter"),
        (r"\bevtl\.", "eventuell"),
        (r"\bggf\.", "gegebenenfalls"),
        (r"\binkl\.", "inklusive"),
        (r"\bbzgl\.", "bezüglich"),
        (r"\bvgl\.", "vergleiche"),
        (r"\bNr\.", "Nummer"),
        (r"\bmax\.", "höchstens"),
        (r"\bmind\.", "mindestens"),
        (r"\bkm/h\b", "Kilometer pro Stunde"),
    )
)
_EVERYDAY: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p), r_)
    for p, r_ in (
        (r"\bJ\.A\.R\.V\.I\.S\.?", "Jarvis"),
        (r"\bURLs\b", "Adressen"),
        (r"\bURL\b", "Adresse"),
        (r"\bAPIs\b", "Schnittstellen"),
        (r"\bAPI\b", "Schnittstelle"),
        (r"\bDB\b", "Datenbank"),
        (r"\bUI\b", "Oberfläche"),
        (r"\bCPU\b", "Prozessor"),
        (r"\bRAM\b", "Arbeitsspeicher"),
        (r"\bGB\b", "Gigabyte"),
        (r"\bMB\b", "Megabyte"),
        (r"(?<=\d)\s?ms\b", " Millisekunden"),
    )
)
# Technical abbreviations nobody can say aloud meaningfully and no everyday word replaces safely: left out.
_UNSAYABLE = re.compile(
    r"\b(?:JSON|HTML|CSS|SDK|CLI|SSH|DNS|FCM|STT|TTS|LLM|SSE|YAML|SQL|HTTPS?|TLS|SSL|JWT|CORS|CSV|XML|RLS|ORM|DOM|GPU"
    r"|TCP|UDP|VPN|CDN|CI/CD|SIGTERM|SIGKILL|stdout|stderr)\b"
)

_EMOJI = re.compile("[\U0001f000-\U0001faff←-⇿⌀-⏿☀-➿⬀-⯿️‍]", re.UNICODE)
_MD_LEAD = re.compile(rf"^\s*(?:#{{1,6}}\s*|>\s*|[-*•·]\s+|\d+[.)]\s+(?!(?:{_MONTH_RE})\b))")
_MD_INLINE = re.compile(r"\*{1,3}|~~|`+|(?<!\w)_+|_+(?!\w)|\|")


def _spoken_time(h: int, m: int) -> str:
    return f"{h} Uhr" if m == 0 else f"{h} Uhr {m}"


def _iso_datetime_to_speech(m: re.Match[str]) -> str:
    y, mo, d, h, mi = (int(m.group(i)) for i in range(1, 6))
    tz = m.group(6)
    try:
        if tz is None:
            dt = datetime(y, mo, d, h, mi)
        else:
            if tz == "Z":
                zone = UTC
            else:
                sign = -1 if tz[0] == "-" else 1
                digits = tz[1:].replace(":", "")
                zone = timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))
            dt = datetime(y, mo, d, h, mi, tzinfo=zone).astimezone(_BERLIN)
    except ValueError:
        return ""
    return f"{dt.day}. {_MONTHS[dt.month - 1]} um {_spoken_time(dt.hour, dt.minute)}"


def _iso_date_to_speech(m: re.Match[str]) -> str:
    try:
        y, mo, d = (int(m.group(i)) for i in range(1, 4))
        datetime(y, mo, d)
    except ValueError:
        return ""
    return f"{d}. {_MONTHS[mo - 1]}"


def _drop(m: re.Match[str]) -> str:
    """Remove a match, but keep the sentence punctuation it swallowed at its end ("… unter /srv/app." → "… unter.")."""
    tail = m.group(0)[len(m.group(0).rstrip(".,;:!?")) :]
    return " " + tail[-1:] if tail else " "


def _file_or_product(m: re.Match[str]) -> str:
    name = m.group(1).lower()
    return m.group(0) if name in _PRODUCT_JS and m.group(0).lower().endswith(".js") else "eine Datei"


def _spell_out(words: str) -> Callable[[re.Match[str]], str]:
    """Replacement for an abbreviation that keeps a capital at the start ("Z. B." → "Zum Beispiel")."""

    def repl(m: re.Match[str]) -> str:
        return words[:1].upper() + words[1:] if m.group(0)[:1].isupper() else words

    return repl


def _error_word(m: re.Match[str]) -> str:
    return "Fehler"


def _tidy(t: str) -> str:
    t = re.sub(r"\(\s*[,;:–-]*\s*\)", " ", t)  # parentheses that held only something removed
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", t)
    t = re.sub(r"([,;:])\s*([.!?])", r"\2", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    t = re.sub(r"^[\s,;:–-]+", "", t)
    return t


def clean_call_text(text: str) -> str:
    """One sentence (or any short text) made fit for the voice: technical content replaced by everyday words or
    removed, formatting gone. Pure function; plain German sentences come back unchanged."""
    t = _FENCE.sub(" ", text)
    t = _LINK.sub(r"\1", t)
    t = _URL.sub(_drop, t)
    t = _EMAIL.sub(" ", t)
    t = _ISO_DATETIME.sub(_iso_datetime_to_speech, t)
    t = _ISO_DATE.sub(_iso_date_to_speech, t)
    t = _EPOCH.sub(" ", t)
    t = _UUID.sub(" ", t)
    t = _PATH_WITH_FILE.sub("eine Datei", t)
    t = _PATH_ABS.sub(_drop, t)
    t = _FILENAME.sub(_file_or_product, t)
    t = _DATIVE_FILE.sub(r"\1 einer Datei", t)
    t = _MODEL.sub(" ", t)
    t = _COMMAND.sub(" ", t)
    t = _FLAG.sub(" ", t)
    t = _HTTP_CODE.sub("Fehler", t)
    t = _ERR_WORD_CODE.sub(_error_word, t)
    t = _EXIT_CODE.sub(" ", t)
    t = _EXC_CLASS.sub("Fehler", t)
    t = _UPPER_SNAKE.sub(" ", t)
    t = _HEX_LITERAL.sub(" ", t)
    t = _ID_AFTER_WORD.sub(lambda m: m.group("w"), t)
    t = _ID_LABEL.sub(" ", t)
    t = _HASH.sub(" ", t)
    t = _SNAKE.sub(" ", t)
    t = _CALLABLE.sub(" ", t)
    for pattern, repl in _ABBREVIATIONS:
        t = pattern.sub(_spell_out(repl), t)
    for pattern, repl in _EVERYDAY:
        t = pattern.sub(repl, t)
    t = _UNSAYABLE.sub(" ", t)
    t = _EMOJI.sub("", t)
    t = _MD_LEAD.sub("", t)
    t = _MD_INLINE.sub("", t)
    t = _JOB_START.sub("Der Auftrag", t.lstrip())
    t = _JOB.sub(lambda m: "Aufträge" if m.group(0).endswith("s") else "Auftrag", t)
    t = _tidy(t)
    return t[:1].upper() + t[1:] if t else t


# ------------------------------------------------------------------------------------------------ sentence handling
_ABBREV_TOKENS = frozenset(
    "bzw ca usw etc evtl ggf inkl bzgl vgl nr max mind dr prof str tel ggü sog ev z.b d.h u.a".split()
)
_TERMINATORS = ".!?…;"
_CLOSERS = "\"'“”„)]»«"


def _is_abbreviation(sentence_so_far: str) -> bool:
    """The period ending ``sentence_so_far`` is no sentence end ("z. B.", "ca.", "am 3.", "J.A.R.V.I.S.")."""
    words = sentence_so_far.split()
    if not words:
        return False
    token = words[-1].strip(_CLOSERS).rstrip(".")
    if len(token) == 1 and token.isalpha():
        return True
    if token.isdigit() and len(token) <= 2:
        return True  # German ordinal: "am 3. November"
    if re.fullmatch(r"(?:\w\.)+\w", token):
        return True
    return token.lower() in _ABBREV_TOKENS


def split_sentences(buf: str, *, final: bool = True) -> tuple[list[str], str]:
    """Splits at the end of a sentence (. ! ? … ; and line breaks – list items count as sentences). A boundary is
    only certain once the next word has begun, so with ``final=False`` the unfinished tail is returned as ``rest``;
    with ``final=True`` it is the last sentence."""
    out: list[str] = []
    start = i = 0
    n = len(buf)
    while i < n:
        c = buf[i]
        if c == "\n":
            if buf[start:i].strip():
                out.append(buf[start:i].strip())
            start = i = i + 1
            continue
        if c not in _TERMINATORS:
            i += 1
            continue
        j = i
        while j + 1 < n and (buf[j + 1] in _TERMINATORS or buf[j + 1] in _CLOSERS):
            j += 1
        if j + 1 >= n:
            break  # nothing after the mark yet: the sentence may continue ("3.5", "e.V.")
        if not buf[j + 1].isspace():
            i = j + 1
            continue
        m = j + 1
        while m < n and buf[m].isspace() and buf[m] != "\n":
            m += 1
        if m >= n:
            break
        if buf[m] == "\n":
            i = m  # the line break ends the sentence on the next round
            continue
        nxt = buf[m]
        is_period = buf[i] == "."
        if is_period and (nxt.islower() or _is_abbreviation(buf[start : j + 1])):
            i = j + 1
            continue
        out.append(buf[start : j + 1].strip())
        start = i = m
    rest = buf[start:]
    if final:
        if rest.strip():
            out.append(rest.strip())
        rest = ""
    return out, rest


class CallReplyFilter:
    """Streams the model's reply through the call rules: complete sentences are cleaned (``clean_call_text``) and
    handed on, everything beyond ``max_sentences`` is dropped.

    The sentence budget applies per text segment: a tool call (``end_segment``) starts a new one, so a short "Moment,
    ich schaue nach." before a tool does not eat the answer after it. A sentence is spoken once it is complete –
    speech starts a sentence later than with raw streaming, in exchange nothing technical is ever spoken."""

    def __init__(self, max_sentences: int = MAX_SENTENCES) -> None:
        self.max_sentences = max(1, max_sentences)
        self.truncated = False  # something was cut off
        self._buf = ""
        self._count = 0
        self._spoken: list[str] = []

    def feed(self, delta: str) -> str:
        if self._count >= self.max_sentences:
            self.truncated = self.truncated or bool(delta.strip())
            return ""
        self._buf += delta
        return self._take(final=False)

    def end_segment(self) -> str:
        """A tool starts: what is pending is spoken now; the next text gets a fresh budget."""
        out = self._take(final=True)
        self._count = 0
        return out

    def flush(self) -> str:
        """End of the reply."""
        return self._take(final=True)

    def spoken_text(self) -> str:
        """Everything that was handed on – what Sir actually heard (for the transcript)."""
        return " ".join(self._spoken).strip()

    def _take(self, final: bool) -> str:
        buf = _FENCE.sub(" ", self._buf)
        hold = ""
        fence = buf.find("```")
        if fence != -1:  # an open code block: nothing from there on is speakable
            buf, hold = buf[:fence], buf[fence:]
        sentences, rest = split_sentences(buf, final=final)
        self._buf = "" if final else rest + hold
        out: list[str] = []
        for s in sentences:
            if self._count >= self.max_sentences:
                self.truncated = True
                self._buf = ""
                break
            cleaned = clean_call_text(s)
            if re.search(r"\w", cleaned):
                out.append(cleaned)
                self._count += 1
        if not out:
            return ""
        self._spoken.extend(out)
        return " ".join(out) + " "


def speakable(text: str, max_sentences: int = MAX_SENTENCES) -> str:
    """A finished text (call message, ring subject, fixed phrase) as it may be spoken: cleaned and cut to
    ``max_sentences``. Empty string if nothing speakable is left."""
    f = CallReplyFilter(max_sentences)
    f.feed(text)
    f.flush()
    return f.spoken_text()


__all__ = [
    "CALL_STYLE_RULES",
    "FALLBACK_SUBJECT",
    "LOCATION_REFRESH_EVERY_S",
    "MAX_SENTENCES",
    "MAX_SENTENCES_DRIVING",
    "MOVING_SPEED_KMH",
    "CallReplyFilter",
    "MotionCheck",
    "call_style_block",
    "clean_call_text",
    "max_sentences_for",
    "motion_before_call",
    "speakable",
    "split_sentences",
]
