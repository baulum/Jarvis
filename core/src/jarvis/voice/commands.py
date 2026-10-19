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

# "wach auf", "Jarvis wach auf", "aufwachen", "wake up" – the staged wake-up (sound + apps), see voice.wakeup.
WAKE_UP = re.compile(
    r"^\W*(?:(?:hey\s+)?jarvis\W+)?(?:wach(?:\s+mal)?\s+auf|aufwachen|werde\s+wach|wake\s+up|rise\s+and\s+shine|"
    r"aufstehen)\b\W*(?:jarvis|sir|bitte)?\W*$",
    re.I,
)


def is_end_conversation(text: str, max_len: int = 60) -> bool:
    if len(text) >= max_len:
        return False
    if END_CONVERSATION.search(text):
        return True
    return len(text.split()) <= 3 and END_WORD.search(text) is not None


def is_wake_up(text: str) -> bool:
    return len(text) < 40 and WAKE_UP.match(text) is not None
