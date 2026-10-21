"""Call greeting (short, once), idempotent watches, no re-ring from inside a call, push targets."""

import json
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.agent.persona import TZ, LeadingGreetingFilter, call_opening, short_greeting
from jarvis.mobile.alerts import Alert, alert_target
from jarvis.mobile.calls import CallService
from jarvis.mobile.tools import mobile_tools
from jarvis.mobile.voice_agent import CallVoiceAgent
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext

P = SimpleNamespace(owner_title="Sir", greetings={"afternoon": "Guten Tag, Sir. Alle Systeme laufen."})
AFTERNOON = datetime(2026, 10, 17, 14, 0, tzinfo=TZ)


def test_greeting_is_short_without_status_report() -> None:
    assert short_greeting(P, AFTERNOON) == "Guten Tag, Sir."  # type: ignore[arg-type]
    assert "Systeme" not in call_opening(P, None, AFTERNOON)  # type: ignore[arg-type]


def test_message_greeting_is_not_doubled() -> None:
    msg = "Guten Tag, Sir. Wie besprochen, rufe ich Sie zurück, weil der Coding-Job fertig ist."
    out = call_opening(P, msg, AFTERNOON)  # type: ignore[arg-type]
    assert out == "Guten Tag, Sir. Wie besprochen, rufe ich Sie zurück, weil der Coding-Job fertig ist."
    assert out.count("Guten Tag") == 1
    plain = call_opening(P, "Die Website ist fertig.", AFTERNOON)  # type: ignore[arg-type]
    assert plain == "Guten Tag, Sir. Die Website ist fertig."


@pytest.mark.parametrize(
    "message",
    [
        "Hallo Sir, der Build ist fertig.",  # greeting and content in one sentence: the content must survive
        "Guten Abend Sir – der Build ist fertig.",
        "Sir, der Build ist fertig.",  # "Guten Tag, Sir. Sir, …" would repeat the address
        "sir: der Build ist fertig.",
        "Guten Tag, Sir. Sir, der Build ist fertig.",
    ],
)
def test_opening_keeps_the_message_and_says_the_address_once(message: str) -> None:
    assert call_opening(P, message, AFTERNOON) == "Guten Tag, Sir. Der Build ist fertig."  # type: ignore[arg-type]


def test_opening_leaves_words_that_only_look_like_greetings() -> None:
    assert call_opening(P, "Halloween-Deko ist bestellt.", AFTERNOON).endswith("Halloween-Deko ist bestellt.")  # type: ignore[arg-type]
    assert call_opening(P, "Sirius ist online.", AFTERNOON).endswith("Sirius ist online.")  # type: ignore[arg-type]


def _stream(chunks: list[str]) -> tuple[str, list[str]]:
    f = LeadingGreetingFilter("Sir")
    released = [f.feed(c) for c in chunks]
    tail = f.flush()
    return "".join(released) + tail, released


def test_reply_opening_with_a_greeting_is_trimmed_while_streaming() -> None:
    text, _ = _stream(["Gu", "ten A", "bend, ", "Sir. ", "Der Build", " ist fertig."])
    assert text.strip() == "Der Build ist fertig."
    text, _ = _stream(["Hallo Sir, der", " Build ist fertig."])
    assert text == "Der Build ist fertig."
    text, _ = _stream(["Noch wach, Sir?"])
    assert text == ""


def test_reply_without_greeting_is_not_held_back() -> None:
    text, released = _stream(["Sehr wohl", ", Sir.", " Ich kümmere mich darum."])
    assert released[0] == "Sehr wohl"  # passed on at once: no extra latency
    assert text == "Sehr wohl, Sir. Ich kümmere mich darum."
    text, released = _stream(["G", "erne, Sir."])
    assert released == ["", "Gerne, Sir."] and text == "Gerne, Sir."
    text, _ = _stream(["Guten Tagesausflug", " gehabt?"])
    assert text == "Guten Tagesausflug gehabt?"


def _service(tmp_path: Path) -> CallService:
    state = SimpleNamespace(
        settings=SimpleNamespace(
            paths=SimpleNamespace(data_dir=tmp_path),
            mobile=SimpleNamespace(calls=SimpleNamespace()),
        )
    )
    return CallService(state)  # type: ignore[arg-type]


def test_watch_fires_only_once(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    svc._save_watches([{"id": "w1", "type": "coding_job", "target": "j1", "message": "x"}])
    first = svc._claim_watches(lambda w: w["target"] == "j1")
    second = svc._claim_watches(lambda w: w["target"] == "j1")
    assert [w["id"] for w in first] == ["w1"] and second == []
    assert json.loads((tmp_path / "call-watches.json").read_text()) == []
    # even if the file were restored (e.g. by a stale writer), the claimed id never fires again
    svc._save_watches([{"id": "w1", "type": "coding_job", "target": "j1", "message": "x"}])
    assert svc._claim_watches(lambda w: True) == []


async def test_call_owner_is_refused_inside_a_call(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    sid = uuid.uuid4()
    svc.agents[uuid.uuid4()] = SimpleNamespace(session_id=sid)  # type: ignore[assignment]
    tools = {t.name: t for t in mobile_tools(SimpleNamespace(calls=svc))}  # type: ignore[arg-type]
    ctx = ToolContext(principal=None, session_id=sid)  # type: ignore[arg-type]
    with pytest.raises(ToolUserError, match="bereits im Gespräch"):
        await tools["mobile_call_owner"].handler(ctx, {"message": "Hallo Sir"})


def test_echo_of_own_speech_is_recognised() -> None:
    agent: Any = CallVoiceAgent.__new__(CallVoiceAgent)
    agent.transcript = [
        {"role": "jarvis", "text": "Guten Tag, Sir. Alle Systeme laufen.", "ts": 0},
    ]
    assert agent._is_echo("Guten Tag, Sir, alle Systeme laufen")
    assert agent._is_echo("alle Systeme laufen")
    assert not agent._is_echo("Wie ist der Stand beim Coding-Job?")
    assert not agent._is_echo("ja")


def test_push_target_prefers_job_over_task() -> None:
    a = Alert("task", "t", data={"task_id": "t1", "job_id": "j1"})
    assert alert_target(a) == ("job", "j1")
    assert alert_target(Alert("task", "t", data={"task_id": "t1"})) == ("task", "t1")
    assert alert_target(Alert("approval", "t", data={"approval_id": "a1"})) == ("approval", "a1")
    assert alert_target(Alert("system", "t")) == ("", "")
