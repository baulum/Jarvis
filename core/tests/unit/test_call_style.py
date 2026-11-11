"""Anrufstil: what is spoken on a call to Sir's phone – technical content removed, at most 3 sentences (1 on the
move), the rules in the prompt, and nothing of it leaks into the chat."""

import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

import jarvis
from jarvis.agent.llm import AgentEvent
from jarvis.agent.persona import ERROR_PHRASE, turn_instructions
from jarvis.mobile.call_style import (
    CALL_STYLE_RULES,
    MAX_SENTENCES,
    MAX_SENTENCES_DRIVING,
    CallReplyFilter,
    call_style_block,
    clean_call_text,
    speakable,
    split_sentences,
)
from jarvis.mobile.location import LocationStore
from jarvis.mobile.motion import Motion
from jarvis.mobile.voice_agent import CallVoiceAgent

# ------------------------------------------------------------------------------- technical content is removed


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Job 1963367e ist fertig.", "Der Auftrag ist fertig."),
        ("Der Coding-Job a3f9c21d ist durch.", "Der Auftrag ist durch."),
        ("Die Aufgabe 8f3a91c2d0 läuft noch.", "Die Aufgabe läuft noch."),
        ("Die Sitzung 0b5e9c1a-77d2-4f3b-9a10-5c2d8e6f1a44 ist beendet.", "Die Sitzung ist beendet."),
        ("Ich habe config.yaml angepasst.", "Ich habe eine Datei angepasst."),
        ("Das steht in config.yaml drin.", "Das steht in einer Datei drin."),
        ("Geändert wurde /Users/paul/Dev/app/main.py gestern.", "Geändert wurde eine Datei gestern."),
        ("Es liegt in ~/Documents/Kunden/seite.", "Es liegt in."),
        ("Der Stand steht auf C:\\Users\\Paul\\notes.txt.", "Der Stand steht auf eine Datei."),
        ("Siehe https://vercel.com/pje/projekt?x=1 dafür.", "Siehe dafür."),
        ("Die Sache mit Next.js ist erledigt.", "Die Sache mit Next.js ist erledigt."),
        ("Es kam HTTP 500 zurück.", "Es kam Fehler zurück."),
        ("Der Server meldet Fehlercode 503 und ein ValueError.", "Der Server meldet Fehler und ein Fehler."),
        ("Status 200 und alles gut.", "Status 200 und alles gut."),
        ("Das Ergebnis ist Exit-Code 1.", "Das Ergebnis ist."),
        ("Der Lauf ist unter mobile_call_owner angelaufen.", "Der Lauf ist unter angelaufen."),
        ("Fertig seit 2026-11-03T14:30:00+01:00.", "Fertig seit 3. November um 14 Uhr 30."),
        ("Fertig seit 2026-11-03T13:30:00Z.", "Fertig seit 3. November um 14 Uhr 30."),
        ("Der Termin ist am 2026-11-03.", "Der Termin ist am 3. November."),
        (
            "Das dauert ca. fünf Minuten, z. B. wegen des Uploads.",
            "Das dauert etwa fünf Minuten, zum Beispiel wegen des Uploads.",
        ),
        ("Das passt bzw. klappt usw.", "Das passt beziehungsweise klappt und so weiter"),
        ("Die URL ist in der DB.", "Die Adresse ist in der Datenbank."),
        ("Der Speicher hat 12 GB frei.", "Der Speicher hat 12 Gigabyte frei."),
        ("**Wichtig:** alles läuft 🙂", "Wichtig: alles läuft"),
        ("# Ergebnis", "Ergebnis"),
        ("J.A.R.V.I.S. ist bereit.", "Jarvis ist bereit."),
    ],
)
def test_technical_content_is_replaced_or_left_out(raw: str, expected: str) -> None:
    assert clean_call_text(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "Ich nutze Claude Haiku 5.5 mit Effort medium dafür.",
        "Das Modell claude-haiku-5-5 antwortet.",
        "Laut Opus 5.5 geht das.",
        "Starte npm run build --force und prüfe es.",
        "Mach git push origin main danach.",
        "Der Commit a3f9c21 ist drin, Hash 9b1c4d7e2f ebenfalls.",
        "Fehler ECONNREFUSED beim Abruf, Adresse 0x7fff5fbff8c0.",
        "Das Skript stuerzt mit KeyError ab.",
        "Zeitstempel 1730642400 und 1730642400123 sind raus.",
        "Die JSON und HTML Dateien sind bereit.",
    ],
)
def test_no_technical_token_survives(raw: str) -> None:
    out = clean_call_text(raw)
    forbidden = re.compile(
        r"claude|haiku|opus|effort|npm|git |--force|a3f9c21|9b1c4d7e2f|ECONN|0x7f|KeyError|1730642400|JSON|HTML|\d{6,}",
        re.IGNORECASE,
    )
    assert not forbidden.search(out), out
    assert out  # something sensible stays


@pytest.mark.parametrize(
    "text",
    [
        "Guten Abend, Sir. Ich habe die Seite fertig.",
        "Es kostet 3,5 Prozent mehr als gestern.",
        "Um 14 Uhr 30 beginnt Ihr Termin.",
        "Am 3. November ist alles bereit.",
        "Der Auftrag für die Website ist fertig.",
        "Soll ich Ihnen die Einzelheiten schicken?",
    ],
)
def test_plain_german_is_left_alone(text: str) -> None:
    assert clean_call_text(text) == text
    assert clean_call_text(clean_call_text(text)) == text  # idempotent


# ----------------------------------------------------------------------------------------- sentence limits


def test_reply_longer_than_three_sentences_is_cut_to_three() -> None:
    text = "Das ist eins. Das ist zwei. Das ist drei. Das ist vier. Das ist fünf."
    assert speakable(text) == "Das ist eins. Das ist zwei. Das ist drei."
    assert MAX_SENTENCES == 3


def test_shorter_reply_keeps_all_its_sentences() -> None:
    assert speakable("Alles klar. Ich kümmere mich darum.") == "Alles klar. Ich kümmere mich darum."


def test_driving_mode_speaks_exactly_one_sentence_core_statement_first() -> None:
    text = "Der Auftrag ist fertig. Die Seite ist online. Soll ich Ihnen die Einzelheiten schicken?"
    assert speakable(text, MAX_SENTENCES_DRIVING) == "Der Auftrag ist fertig."


def test_lists_and_markdown_become_plain_sentences_and_count_as_sentences() -> None:
    text = "Das ist erledigt:\n- Erstens die Seite\n- Zweitens der Text\n- Drittens das Bild\n- Viertens der Rest"
    out = speakable(text)
    assert "-" not in out and "\n" not in out
    assert len(split_sentences(out)[0]) <= MAX_SENTENCES
    assert speakable(text, 1) == "Das ist erledigt:"


def test_abbreviations_and_ordinals_do_not_end_a_sentence() -> None:
    assert len(split_sentences("Das dauert ca. 5 Minuten, z. B. wegen des Uploads. Dann ist es fertig.")[0]) == 2
    assert len(split_sentences("Am 3. November kommt es an. Danach ruhe.")[0]) == 2
    assert len(split_sentences("Es kostet 3.5 Euro. Das ist günstig.")[0]) == 2


def test_empty_after_cleaning_counts_as_no_sentence() -> None:
    assert speakable("1963367e. Der Auftrag ist fertig.", 1) == "Der Auftrag ist fertig."


def test_streaming_gives_the_same_result_as_the_whole_text() -> None:
    text = "Der Job 1963367e ist fertig. Die Seite ist **online**. Details folgen. Noch ein Satz."
    whole = speakable(text)
    f = CallReplyFilter()
    out = "".join(f.feed(ch) for ch in text) + f.flush()
    assert out.strip() == whole == f.spoken_text()
    assert f.truncated


def test_streaming_speaks_a_sentence_as_soon_as_it_is_complete() -> None:
    f = CallReplyFilter()
    assert f.feed("Der Auftrag ist fertig") == ""
    assert f.feed(". Die Seite") == "Der Auftrag ist fertig. "
    assert f.feed(" ist online.") == ""
    assert f.flush() == "Die Seite ist online. "


def test_nothing_is_spoken_after_the_budget_is_used() -> None:
    f = CallReplyFilter(1)
    assert f.feed("Erster Satz. Zweiter Satz. ") == "Erster Satz. "
    assert f.feed("Dritter Satz.") == ""
    assert f.flush() == ""
    assert f.truncated and f.spoken_text() == "Erster Satz."


def test_a_tool_call_starts_a_new_budget_so_the_answer_is_not_eaten() -> None:
    f = CallReplyFilter(1)
    said = f.feed("Ich schaue kurz nach. ")
    said += f.end_segment()
    said += f.feed("Der Auftrag ist fertig. Noch mehr.")
    said += f.flush()
    assert said.strip() == "Ich schaue kurz nach. Der Auftrag ist fertig."


def test_code_blocks_are_never_spoken() -> None:
    f = CallReplyFilter()
    out = f.feed("Ich habe es repariert. ```bash\nnpm run build\n``` Alles läuft.") + f.flush()
    assert "npm" not in out and "```" not in out
    assert out.strip() == "Ich habe es repariert. Alles läuft."
    f2 = CallReplyFilter()
    out2 = f2.feed("Fertig. ```python\nprint(1)") + f2.flush()
    assert out2.strip() == "Fertig."


# ----------------------------------------------------------------------------------------------- the prompt


def test_prompt_contains_the_rules_for_every_mode() -> None:
    for motion in Motion:
        block = call_style_block(motion)
        assert CALL_STYLE_RULES in block
        for word in ("IDs", "Modellnamen", "Pfade", "Befehle", "Fehlercodes", "Abkürzungen", "Listen", "Emojis"):
            assert word in block
        assert "Sie-Anrede" in block


def test_prompt_driving_mode_asks_for_one_sentence_core_first_and_details_later() -> None:
    block = call_style_block(Motion.MOVING)
    assert "EIN Satz" in block and "Kernaussage zuerst" in block and "Nachricht" in block
    assert "höchstens 1 Satz" in block


def test_prompt_at_rest_and_unknown_use_normal_length() -> None:
    assert "ein bis drei Sätze" in call_style_block(Motion.STILL)
    unknown = call_style_block(Motion.UNKNOWN)
    assert "nicht auf Bewegung" in unknown and "weißt du nicht" in unknown and "höchstens 3 Satz" in unknown


# ---------------------------------------------------------------------------------- chat stays untouched


@pytest.mark.parametrize("channel", ["chat", "desktop", "voice", "telegram"])
def test_other_channels_get_no_call_style(channel: str) -> None:
    text = turn_instructions(channel)
    assert "Anrufstil" not in text
    assert "IDs" not in text


def test_phone_instructions_of_the_persona_are_unchanged_the_style_comes_via_call_context() -> None:
    text = turn_instructions("phone")
    assert "maximal zwei bis drei kurze Sätze" in text and "Anrufstil" not in text
    assert "Anrufstil" in turn_instructions("phone", call_context=call_style_block(Motion.STILL))


def test_chat_orchestrator_and_coding_do_not_use_the_call_style() -> None:
    root = Path(jarvis.__file__).parent
    files = [*(root / "agent").glob("*.py"), *(root / "connectors").glob("coding*.py"), *(root / "jobs").glob("*.py")]
    assert files
    for f in files:
        assert "call_style" not in f.read_text(encoding="utf-8"), f


# ------------------------------------------------------------------- a whole turn of a call (voice agent)
DEV = uuid.uuid4()
AUDIO = np.zeros(16000, dtype=np.int16)
LAT, LON = 52.52, 13.405
DEG = 111_195.0


class FakeSpeech:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.fixed: list[str] = []
        self.events: list[str] = []

    async def prepare(self) -> None:
        pass

    def text(self, t: str) -> None:
        self.parts.append(t)
        self.events.append("text")

    def say(self, t: str) -> None:
        self.fixed.append(t)

    def tool_started(self) -> None:
        self.events.append("tool_started")

    def tool_finished(self, ok: bool) -> None:
        self.events.append("tool_finished")

    async def finish(self) -> dict[str, Any]:
        return {}

    async def cancel(self) -> None:
        pass

    @property
    def spoken(self) -> str:
        return "".join(self.parts).strip()


class FakeOrchestrator:
    def __init__(self, chunks: list[Any], error: str | None = None) -> None:
        self.chunks = chunks
        self.error = error
        self.call_context = ""

    async def handle(self, principal: Any, channel: str, text: str, **kw: Any) -> Any:
        assert channel == "phone"
        self.call_context = kw["call_context"]
        out = []
        for c in self.chunks:
            if isinstance(c, AgentEvent):
                await kw["on_event"](c)
            else:
                out.append(c)
                await kw["on_event"](AgentEvent("delta", text=c))
        return SimpleNamespace(text="".join(out), error=self.error)


class NoPush:
    async def send_all(self, *a: Any, **kw: Any) -> list[Any]:
        return []


def make_agent(chunks: list[Any], location: LocationStore | None, error: str | None = None):
    agent: Any = CallVoiceAgent.__new__(CallVoiceAgent)
    orch = FakeOrchestrator(chunks, error)
    agent.state = SimpleNamespace(orchestrator=orch, personality=SimpleNamespace(owner_title="Sir"), location=location)
    agent.call_id = uuid.uuid4()
    agent.principal = object()
    agent.session_id = uuid.uuid4()
    agent.greeting = None
    agent.call_context = {}
    agent.metrics = {"turns": 0, "barge_ins": 0}
    agent.transcript = []
    agent._prepared = None
    agent._speech = None
    agent._last_location_request = float("-inf")
    speech = FakeSpeech()
    said: list[tuple[str, str]] = []

    async def noop(*a: Any, **kw: Any) -> None:
        pass

    async def transcript(role: str, text: str) -> None:
        said.append((role, text))

    agent.set_state = noop
    agent.send = noop
    agent._transcript = transcript
    agent._drain = noop
    agent._new_speech = lambda: speech
    return agent, speech, orch, said


def driving_store() -> LocationStore:
    st = LocationStore(NoPush())  # type: ignore[arg-type]
    st.update(DEV, LAT, LON, 10, datetime.now(UTC) - timedelta(minutes=4))
    st.update(DEV, LAT + 2000 / DEG, LON, 10, datetime.now(UTC) - timedelta(seconds=6))  # ~30 km/h
    assert st.motion() is Motion.MOVING
    return st


def resting_store() -> LocationStore:
    st = LocationStore(NoPush())  # type: ignore[arg-type]
    st.update(DEV, LAT, LON, 10, datetime.now(UTC) - timedelta(minutes=4))
    st.update(DEV, LAT + 3 / DEG, LON, 10, datetime.now(UTC) - timedelta(seconds=6))
    assert st.motion() is Motion.STILL
    return st


LONG_TECHNICAL = (
    "Der Job 1963367e ist fertig. Die Änderung steckt im Commit a3f9c21 und in /Users/paul/Dev/app/main.py. "
    "Claude Haiku 5.5 hat es gebaut. Alles läuft bei HTTP 200. Soll ich noch etwas tun?"
)


async def test_turn_in_motion_speaks_one_sentence_without_technical_content() -> None:
    agent, speech, orch, said = make_agent([LONG_TECHNICAL[:40], LONG_TECHNICAL[40:]], driving_store())
    await agent._turn(AUDIO, "Wie steht es um die Website?")
    assert speech.spoken == "Der Auftrag ist fertig."
    assert said[-1] == ("jarvis", "Der Auftrag ist fertig.")  # the transcript shows what was said
    assert "EIN Satz" in orch.call_context and "Anrufstil" in orch.call_context
    assert agent.metrics["motion"] == "yes" and agent.metrics["style_cuts"] == 1


async def test_turn_at_rest_speaks_at_most_three_sentences_cleaned() -> None:
    agent, speech, orch, said = make_agent([LONG_TECHNICAL], resting_store())
    await agent._turn(AUDIO, "Wie steht es um die Website?")
    spoken = speech.spoken
    assert len(split_sentences(spoken)[0]) == 3
    for token in ("1963367e", "a3f9c21", "/Users", "main.py", "Haiku", "Claude"):
        assert token not in spoken
    assert spoken.startswith("Der Auftrag ist fertig.")
    assert said[-1] == ("jarvis", spoken)
    assert agent.metrics["motion"] == "no" and "ein bis drei Sätze" in orch.call_context


async def test_turn_with_unknown_motion_uses_normal_length_and_does_not_assume_driving() -> None:
    agent, speech, orch, _ = make_agent(["Eins. Zwei. Drei. Vier."], LocationStore(NoPush()))  # type: ignore[arg-type]
    await agent._turn(AUDIO, "Erzähl mal.")
    assert speech.spoken == "Eins. Zwei. Drei."
    assert agent.metrics["motion"] == "unknown" and "nicht auf Bewegung" in orch.call_context


async def test_turn_without_location_service_keeps_the_motion_from_before_the_call() -> None:
    agent, speech, _, _ = make_agent(["Eins. Zwei."], None)
    agent.motion = Motion.MOVING
    await agent._turn(AUDIO, "Erzähl mal.")
    assert speech.spoken == "Eins."


async def test_turn_answer_after_a_tool_is_spoken_even_in_motion() -> None:
    chunks: list[Any] = [
        "Ich schaue nach. Das dauert kurz. ",
        AgentEvent("tool_started", data={"name": "coding_status"}),
        AgentEvent("tool_completed", data={"name": "coding_status"}),
        "Der Auftrag ist fertig. Die Seite ist online.",
    ]
    agent, speech, _, _ = make_agent(chunks, driving_store())
    await agent._turn(AUDIO, "Stand?")
    assert speech.spoken == "Ich schaue nach. Der Auftrag ist fertig."
    assert speech.events.index("tool_started") < len(speech.events) - 1  # the answer comes after the tool start


async def test_turn_error_says_a_short_phrase_not_a_technical_message() -> None:
    agent, speech, _, said = make_agent([], resting_store(), error="HTTP 500 TypeError in /srv/app/core.py")
    await agent._turn(AUDIO, "Mach etwas.")
    assert speech.fixed == [ERROR_PHRASE]
    assert said[-1] == ("jarvis", ERROR_PHRASE)
    assert not re.search(r"500|TypeError|core\.py", " ".join(t for _, t in said))


async def test_vague_confirmation_without_question_is_one_question_on_the_move() -> None:
    agent, speech, orch, _ = make_agent([], driving_store())

    async def record_turn(*a: Any, **kw: Any) -> None:
        pass

    orch.record_turn = record_turn
    await agent._answer_vague_confirmation("ja")
    assert speech.fixed == ["Worauf beziehen Sie sich, Sir?"]


async def test_vague_confirmation_at_rest_keeps_its_normal_length() -> None:
    agent, speech, orch, _ = make_agent([], resting_store())

    async def record_turn(*a: Any, **kw: Any) -> None:
        pass

    orch.record_turn = record_turn
    await agent._answer_vague_confirmation("ja")
    assert len(split_sentences(speech.fixed[0])[0]) == 3
