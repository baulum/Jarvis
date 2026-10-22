import pytest

from jarvis.voice.commands import is_echo, is_end_conversation, is_stop_command, is_wake_up
from jarvis.voice.speech import pronounceable, speakable


@pytest.mark.parametrize(
    "text",
    [
        "beende das Gespräch",
        "Jarvis, beende bitte das Gespräch.",
        "Gespräch beenden",
        "das Gespräch ist zu Ende",
        "beende dieses Telefonat",
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
        "Stopp.",  # only silences Jarvis, see is_stop_command
        "Jarvis, stopp!",
    ],
)
def test_normal_sentences_do_not_end_the_conversation(text: str) -> None:
    assert not is_end_conversation(text)


@pytest.mark.parametrize(
    "text",
    [
        "Stopp.",
        "Stop!",
        "Jarvis stopp",
        "Hey Jarvis, stopp!",
        "Jarvis, stopp, stopp, stopp.",
        "Stopp bitte",
        "Halt.",
        "Hör auf",
        "Hör bitte auf damit!",
        "Jarvis, hör mal auf.",
        "Sei still",
        "Das reicht.",
        "Es reicht jetzt",
        "Genug.",
        "Moment!",
        "Warte",
        "Stop it, Jarvis",
        "That's enough",
        "be quiet",
    ],
)
def test_stop_command_phrases(text: str) -> None:
    assert is_stop_command(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Jarvis",
        "Hey Jarvis",
        "stopp den Timer in zehn Minuten bitte",
        "Stopp, ich meinte eigentlich Dienstag",
        "Halt die Termine am Freitag frei",
        "Warte, ich habe noch eine Frage zur Rechnung von Müller",
        "Das reicht mir als Antwort, danke",
        "Wie spät ist es",
        "Beende das Gespräch",
        "Tschüss",
    ],
)
def test_stop_command_is_strict(text: str) -> None:
    assert not is_stop_command(text)


def test_is_echo_catches_jarvis_own_voice_but_not_sirs_short_answer() -> None:
    said = ["Guten Tag, Sir, alle Systeme laufen. Soll ich die Mail an Müller jetzt senden?"]
    assert is_echo("alle Systeme laufen. Soll ich die Mail", said)
    assert is_echo("Guten Tag Sir alle Systeme laufen Soll ich die Mail an Müller jetzt senden", said)
    assert not is_echo("Mail senden", said)  # fragment of an answer, too short to be a copy
    assert not is_echo("Ja, bitte an Müller senden", said)
    assert not is_echo("Wie ist der Stand beim Coding-Job?", said)
    assert not is_echo("Stopp", said)
    assert is_echo("alle Systeme laufen", said, min_words=1)  # the phone call's looser rule (greeting via speaker)
    assert not is_echo("alle Systeme laufen", [])


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
