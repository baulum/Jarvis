"""The question a call is about: its block for the agent, vague confirmations, and no action on a bare "ja"."""

import uuid
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from jarvis.agent.persona import turn_instructions
from jarvis.mobile.call_context import build_call_context, call_context_block, is_vague_confirmation, open_question
from jarvis.mobile.voice_agent import CallVoiceAgent

QUESTION = "Neue Google-Bewertung von Westside Taxi UG. Soll ich den Antwortentwurf veröffentlichen?"


@pytest.mark.parametrize("text", ["ja", "Ja.", "Ja bitte mach das", "mach das bitte", "mach's", "okay", "jo, passt"])
def test_bare_confirmations_are_vague(text: str) -> None:
    assert is_vague_confirmation(text)


@pytest.mark.parametrize(
    "text",
    [
        "bitte",  # no confirmation in it
        "nein",
        "Veröffentliche den Entwurf",
        "Ja, schick die Antwort an Westside",  # names what to do
        "Ja, Westside Taxi veröffentlichen",
        "Wie spät ist es?",
    ],
)
def test_answers_that_name_something_are_not_vague(text: str) -> None:
    assert not is_vague_confirmation(text)


def test_block_names_the_open_question_and_its_reference() -> None:
    ctx = build_call_context(QUESTION, reason="alert:task", refs={"task_id": "t-1", "alert_id": None})
    block = call_context_block(ctx)
    assert open_question(ctx) == QUESTION
    assert QUESTION in block
    assert "task_id=t-1" in block
    assert "alert_id" not in block  # empty references are not stored


def test_call_without_open_question_says_so_instead_of_pretending() -> None:
    assert open_question({}) is None
    assert open_question(None) is None
    block = call_context_block({})
    assert "keine offene Rückfrage" in block
    assert "ehrlich" in block


def test_turn_instructions_carry_the_call_context() -> None:
    ctx = build_call_context(QUESTION, reason="agent")
    instructions = turn_instructions("phone", call_context=call_context_block(ctx))
    assert "Kanal: phone" in instructions
    assert QUESTION in instructions
    assert "Kontext dieses Anrufs" not in turn_instructions("desktop")  # only calls carry it


class FakeOrchestrator:
    def __init__(self) -> None:
        self.handled: list[dict[str, Any]] = []
        self.recorded: list[tuple[str, str]] = []

    async def handle(self, principal: Any, channel: str, text: str, **kw: Any) -> Any:
        self.handled.append({"channel": channel, "text": text, **kw})
        return SimpleNamespace(text="Erledigt, Sir.", error=None)

    async def record_turn(self, session_id: uuid.UUID, text: str, reply: str, meta: Any = None) -> None:
        self.recorded.append((text, reply))


class FakeSpeech:
    async def prepare(self) -> None:
        pass

    def text(self, _: str) -> None:
        pass

    def say(self, _: str) -> None:
        pass

    def tool_started(self) -> None:
        pass

    def tool_finished(self, _: bool) -> None:
        pass

    async def finish(self) -> dict[str, Any]:
        return {}

    async def cancel(self) -> None:
        pass


def call_agent(question: str | None) -> tuple[Any, FakeOrchestrator, list[str]]:
    """A voice agent without room or speech engine: turns run for real, only the audio output is recorded."""
    agent: Any = CallVoiceAgent.__new__(CallVoiceAgent)
    orch = FakeOrchestrator()
    spoken: list[str] = []

    async def set_state(value: str, detail: str | None = None) -> None:
        pass

    async def transcript(role: str, text: str) -> None:
        agent.transcript.append({"role": role, "text": text, "ts": 0})

    async def speak_fixed(text: str) -> None:
        spoken.append(text)

    async def drain() -> None:
        pass

    agent.state = SimpleNamespace(orchestrator=orch, personality=SimpleNamespace(owner_title="Sir"))
    agent.call_id = uuid.uuid4()
    agent.principal = SimpleNamespace()
    agent.session_id = uuid.uuid4()
    agent.call_context = build_call_context(question, reason="agent") if question else {}
    agent.transcript = []
    agent.metrics = {"turns": 0, "barge_ins": 0}
    agent.set_state = set_state
    agent._transcript = transcript
    agent._speak_fixed = speak_fixed
    agent._drain = drain
    agent._new_speech = lambda: FakeSpeech()
    agent._prepared = None
    agent._speech = None
    agent._turn_task = None
    return agent, orch, spoken


async def test_vague_yes_without_open_question_triggers_no_agent_action() -> None:
    agent, orch, spoken = call_agent(None)
    await agent._turn(np.zeros(1, dtype=np.int16), "Ja bitte mach das")
    assert orch.handled == []  # the agent never sees the "ja", so it cannot publish, send or call anything
    assert len(spoken) == 1 and "Worauf beziehen Sie sich" in spoken[0]  # a concrete question instead
    assert orch.recorded == [("Ja bitte mach das", spoken[0])]


async def test_vague_yes_with_open_question_reaches_the_agent_with_that_question() -> None:
    agent, orch, spoken = call_agent(QUESTION)
    await agent._turn(np.zeros(1, dtype=np.int16), "Ja bitte mach das")
    assert spoken == []
    assert len(orch.handled) == 1
    handled = orch.handled[0]
    assert handled["channel"] == "phone" and handled["text"] == "Ja bitte mach das"
    assert QUESTION in handled["call_context"]


async def test_concrete_request_without_open_question_still_reaches_the_agent() -> None:
    agent, orch, _ = call_agent(None)
    await agent._turn(np.zeros(1, dtype=np.int16), "Schick die Antwort an Westside Taxi")
    assert len(orch.handled) == 1
    assert "keine offene Rückfrage" in orch.handled[0]["call_context"]  # honest about the missing context
