import pytest

from jarvis.voice.commands import is_end_conversation, is_wake_up
from jarvis.voice.speech import pronounceable, speakable


@pytest.mark.parametrize(
    "text",
    [
        "beende das Gespräch",
        "Jarvis, beende bitte das Gespräch.",
        "Gespräch beenden",
        "das Gespräch ist zu Ende",
        "beende dieses Telefonat",
        "Stopp.",
        "Tschüss Jarvis",
        "auflegen",
        "danke, das war's",
        "end the conversation",
    ],
)
def test_end_conversation_phrases(text: str) -> None:
    assert is_end_conversation(text)


@pytest.mark.parametrize(
    "text",
    [
        "Das Ende vom Lied ist, dass wir verkaufen",
        "Ich beende das Projekt morgen",
        "Wie spät ist es",
        "stopp den Timer in zehn Minuten bitte",
    ],
)
def test_normal_sentences_do_not_end_the_conversation(text: str) -> None:
    assert not is_end_conversation(text)


@pytest.mark.parametrize(
    "text",
    [
        "wach auf",
        "Jarvis, wach auf!",
        "Hey Jarvis wach auf",
        "wake up",
        "aufwachen",
        # the two documented commands, as whisper writes them
        "Hey Jarvis, wake up.",
        "Hey Jarvis, wach auf.",
        "HEY JARVIS, WAKE UP!",
        "hey jarvis wach auf",
        # slight transcription deviations
        "Hey Javis, Wake-up!",
        "Hey, Jarvis. Wakeup.",
        "Wake up, Jarvis.",
        "Wachauf!",
        "Jarvis, wach' auf.",
        "Jarvis, wach mal auf",
        "Hey Jervis, wacht auf",
        "Wach auf, Sir.",
        "Hey Jarvis … wake up?",
    ],
)
def test_wake_up_phrases(text: str) -> None:
    assert is_wake_up(text)


@pytest.mark.parametrize(
    "text",
    [
        "wach auf und mach den Kaffee",
        "ich wache um sieben auf",
        "Wie spät ist es",
        "Jarvis, mach auf",
        "make up",
        "Hey Jarvis",
        "wake up call um sieben einrichten",
        "Erinnere mich morgen ans Aufwachen",
    ],
)
def test_wake_up_is_strict(text: str) -> None:
    assert not is_wake_up(text)


@pytest.mark.parametrize(
    "text",
    ["J.A.R.V.I.S. hier.", "Hier ist J.A.R.V.I.S", "j.a.r.v.i.s. ruft an", "J. A. R. V. I. S. bereit"],
)
def test_name_is_never_spelled_out(text: str) -> None:
    out = speakable(text)
    assert "Jarvis" in out and "J.A" not in out and "." not in out.replace("hier.", "hier")


def test_pronounceable_keeps_other_text() -> None:
    assert pronounceable("Guten Tag, Sir. Ich bin Jarvis.") == "Guten Tag, Sir. Ich bin Jarvis."


def test_builtin_wakeup_sound_is_a_real_waveform() -> None:
    import numpy as np

    from jarvis.voice.daemon import PLAY_RATE, power_up

    pcm = np.frombuffer(power_up(), dtype=np.int16)
    assert abs(len(pcm) - int(PLAY_RATE * 1.6)) < 5
    assert 3000 < np.abs(pcm).max() < 20000  # audible but never clipping
    assert np.abs(pcm[:200]).max() < np.abs(pcm[20000:20400]).max()  # fades in instead of clicking
