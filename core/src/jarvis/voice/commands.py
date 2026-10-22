"""Spoken commands that never need the language model (shared by the Mac voice daemon and phone calls)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from difflib import SequenceMatcher

# Explicit phrases: "beende das Gespräch", "Gespräch beenden", "danke, das war's" … → end the conversation and go back
# to waiting for the wake word.
END_CONVERSATION = re.compile(
    r"\b(?:danke,? das war'?s|das wär'?s|das war alles|auf wiederhören|"
    r"(?:beende|beenden|beendest)\w*\s+(?:(?:bitte|jetzt|mal|doch)\s+)*(?:das|dieses|unser|unsere|die|den|diesen)\s+"
    r"(?:gespräch|unterhaltung|konversation|anruf|telefonat)\w*|"
    r"(?:gespräch|unterhaltung|konversation|anruf|telefonat)\s+(?:bitte\s+|jetzt\s+)?(?:beenden|beende|ende)|"
    r"(?:das\s+)?gespräch\s+(?:ist\s+)?(?:zu ende|vorbei|beendet)|"
    r"thanks,? that'?s all|end (?:the |this )?(?:conversation|call))\b",
    re.I,
)
# Single words only count in a very short utterance ("Tschüss Jarvis") – not in "das Ende vom Lied".
# "Stopp" is deliberately not one of them: it only silences Jarvis (``is_stop_command``), it does not end the call.
END_WORD = re.compile(r"\b(?:ende|tschüss|tschüs|ciao|bye|auflegen|leg auf)\b", re.I)

# "Hey Jarvis, wake up", "Hey Jarvis, wach auf", "aufwachen" … – the wake-up routine (MP3 + programs), see
# jarvis.voice.wakeup. Matched on a normalised transcript (lower case, no punctuation) and only as the whole
# utterance, so "wach auf und mach den Kaffee" stays a normal request. The name and the verb tolerate the
# usual whisper slips ("Hey Javis, Wake-up!", "Wachauf.", "Jarvis, wach mal auf").
_NAMES = ("jarvis", "javis", "jarwis", "jervis", "jarviz", "travis", "dschawis", "chavis", "charvis")
_NAME = "(?:" + "|".join(_NAMES) + ")"
_WAKE_UP_CORE = (
    r"(?:wake|wakes|waik|weik|wäk|wek)\s?(?:up|app)|wakey\s+wakey|rise\s+and\s+shine|"
    r"wach[et]?\s?(?:(?:mal|doch|endlich|jetzt|bitte)\s+)*auf|aufwachen|werde\s+wach|aufstehen"
)
WAKE_UP = re.compile(
    rf"^(?:(?:hey|hi|he|hay|ey|hallo|okay|ok)\s+)?(?:{_NAME}\s+)?(?:{_WAKE_UP_CORE})"
    rf"(?:\s+(?:{_NAME}|sir|bitte|please|jetzt))*$"
)


def _normalise(text: str) -> str:
    text = text.lower().replace("'", "").replace("’", "").replace("`", "")
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


# Stop command: "Stopp", "Jarvis, stopp!", "Hör auf", "Sei still", "Das reicht" … – Jarvis falls silent and drops the
# running answer, nothing is sent to the agent and the conversation stays open. Only counts as the WHOLE utterance
# (name, politeness and filler words aside): "stopp den Timer in zehn Minuten" is a normal request.
_STOP_FILLER = frozenset(
    """hey hi hallo okay ok sir bitte please mal doch jetzt schon einfach kurz sofort es das dir damit it that thats
    now""".split()
) | frozenset(_NAMES)
_STOP_WORDS = frozenset(
    """stopp stop stopp stoppp halt moment warte genug reicht ruhe still schweig abbruch abbrechen cancel enough
    quiet wait""".split()
)
_STOP_PHRASES = frozenset(
    {
        "hör auf",
        "höre auf",
        "hört auf",
        "sei still",
        "sei leise",
        "sei ruhig",
        "nicht weiter",
        "nicht weitersprechen",
        "nicht weiter sprechen",
        "sprich nicht weiter",
        "hör auf zu reden",
        "hör auf zu sprechen",
        "be quiet",
        "stop talking",
        "shut up",
    }
)


def is_stop_command(text: str, max_words: int = 6) -> bool:
    """Is the utterance nothing but "stop speaking"? (see above; ``is_end_conversation`` is the other command)"""
    words = _normalise(text).split()
    if not words or len(words) > max_words + 3:
        return False
    core = [w for w in words if w not in _STOP_FILLER]
    if not core or len(core) > max_words:
        return False
    return all(w in _STOP_WORDS for w in core) or " ".join(core) in _STOP_PHRASES


def is_end_conversation(text: str, max_len: int = 60) -> bool:
    if len(text) >= max_len:
        return False
    if END_CONVERSATION.search(text):
        return True
    return len(text.split()) <= 3 and END_WORD.search(text) is not None


def is_echo(text: str, spoken: Iterable[str], min_chars: int = 8, min_words: int = 4) -> bool:
    """Is the recognised text (a part of) something Jarvis said a moment ago? His own voice coming back through the
    microphone must never be answered as if Sir had said it (that would loop). ``spoken``: his last utterances.

    A fragment of fewer than ``min_words`` words counts only if it is a near copy of a whole utterance – "Mail
    senden" after "Soll ich die Mail senden?" is Sir's answer, not an echo."""
    heard = _normalise(text)
    if len(heard) < min_chars:
        return False
    for said_raw in spoken:
        said = _normalise(said_raw)
        if not said:
            continue
        if len(heard.split()) >= min_words and heard in said:
            return True
        if SequenceMatcher(None, heard, said).ratio() > 0.75:
            return True
    return False


def is_wake_up(text: str) -> bool:
    return len(text) < 60 and WAKE_UP.match(_normalise(text)) is not None
