"""Spoken commands that never need the language model (shared by the Mac voice daemon and phone calls)."""

from __future__ import annotations

import re

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
# Single words only count in a very short utterance ("Stopp.", "Tschüss Jarvis") – not in "das Ende vom Lied".
END_WORD = re.compile(r"\b(?:stopp|stop|ende|tschüss|tschüs|ciao|bye|auflegen|leg auf)\b", re.I)

# "Hey Jarvis, wake up", "Hey Jarvis, wach auf", "aufwachen" … – the wake-up routine (MP3 + programs), see
# jarvis.voice.wakeup. Matched on a normalised transcript (lower case, no punctuation) and only as the whole
# utterance, so "wach auf und mach den Kaffee" stays a normal request. The name and the verb tolerate the
# usual whisper slips ("Hey Javis, Wake-up!", "Wachauf.", "Jarvis, wach mal auf").
_NAME = r"(?:jarvis|javis|jarwis|jervis|jarviz|travis|dschawis|chavis|charvis)"
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


def is_end_conversation(text: str, max_len: int = 60) -> bool:
    if len(text) >= max_len:
        return False
    if END_CONVERSATION.search(text):
        return True
    return len(text.split()) <= 3 and END_WORD.search(text) is not None


def is_wake_up(text: str) -> bool:
    return len(text) < 60 and WAKE_UP.match(_normalise(text)) is not None
