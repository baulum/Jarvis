"""When a chat request becomes a persistent task (orchestrator hooks), and that system-written turns are not parsed as
commands."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from jarvis.agent.orchestrator import Orchestrator, TurnResult
from jarvis.config import AgentTaskCfg
from jarvis.security.auth import Principal

OWNER = Principal(id="m", role="mobile_owner", channel="mobile")
LONG = (
    "Suche zuerst alle offenen Rechnungen von Müller heraus, erstelle dann eine Übersicht als PDF und schicke sie "
    "anschließend an den Steuerberater. Prüfe danach, ob alle Beträge stimmen und fasse das Ergebnis zusammen."
)


class FakeTasks:
    def __init__(self, cfg: AgentTaskCfg) -> None:
        self.cfg = cfg
        self.started: list[dict[str, Any]] = []

    def should_background(self, channel: str, text: str, *, has_files: bool = False) -> bool:
        from jarvis.jobs.agent_task import looks_long_running

        c = self.cfg
        return (
            c.enabled
            and c.auto_background
            and channel == "mobile"
            and not has_files
            and looks_long_running(text, c.auto_min_chars)
        )

    async def start(self, goal: str, principal: Any, **kw: Any) -> Any:
        self.started.append({"goal": goal, **kw})
        return SimpleNamespace(id=uuid.uuid4(), title="Rechnungsübersicht")


def orch(cfg: AgentTaskCfg | None = None) -> tuple[Orchestrator, FakeTasks]:
    cfg = cfg or AgentTaskCfg()
    tasks = FakeTasks(cfg)
    state = SimpleNamespace(
        agent_tasks=tasks,
        settings=SimpleNamespace(agent=SimpleNamespace(tasks=cfg)),
        model_router=SimpleNamespace(choose=lambda text, channel, sid, override=None: ("sonnet", "Standard")),
    )
    return Orchestrator(state), tasks  # type: ignore[arg-type]


async def test_a_multi_step_request_from_the_phone_starts_a_task_and_answers_at_once() -> None:
    o, tasks = orch()
    sess = SimpleNamespace(id=uuid.uuid4())
    msg = await o._background_confirmation(sess, OWNER, "mobile", LONG, False, ["Müller"])  # type: ignore[arg-type]
    assert msg and "im Hintergrund" in msg and "Rechnungsübersicht" in msg
    assert tasks.started[0]["origin_session"] == sess.id and tasks.started[0]["project_hints"] == ["Müller"]
    assert tasks.started[0]["goal"] == LONG  # the full request, nothing summarised away


async def test_simple_questions_files_calls_and_the_desktop_are_not_touched() -> None:
    o, tasks = orch()
    sess = SimpleNamespace(id=uuid.uuid4())
    assert await o._background_confirmation(sess, OWNER, "mobile", "Wie spät ist es?", False, None) is None  # type: ignore[arg-type]
    assert await o._background_confirmation(sess, OWNER, "mobile", LONG, True, None) is None  # type: ignore[arg-type]
    assert await o._background_confirmation(sess, OWNER, "phone", LONG, False, None) is None  # type: ignore[arg-type]
    assert await o._background_confirmation(sess, OWNER, "desktop", LONG, False, None) is None  # type: ignore[arg-type]
    guest = Principal(id="g", role="guest", channel="mobile")
    assert await o._background_confirmation(sess, guest, "mobile", LONG, False, None) is None  # type: ignore[arg-type]
    assert not tasks.started
    off, tasks_off = orch(AgentTaskCfg(auto_background=False))
    assert await off._background_confirmation(sess, OWNER, "mobile", LONG, False, None) is None  # type: ignore[arg-type]
    assert not tasks_off.started


def _result(error: str | None, done: int) -> TurnResult:
    tools = [{"event": "tool_completed", "name": f"t{i}"} for i in range(done)]
    return TurnResult(uuid.uuid4(), "Das hat leider nicht funktioniert", tools, error=error)


async def test_a_turn_that_died_after_real_work_goes_on_as_a_task() -> None:
    o, tasks = orch()
    sess = SimpleNamespace(id=uuid.uuid4())
    note = await o._continue_in_background(
        sess,
        OWNER,
        "mobile",
        "Mach das",
        _result("Zeitüberschreitung nach 15 Minuten", 5),
        None,  # type: ignore[arg-type]
    )
    assert note and "im Hintergrund weiter" in note
    goal = tasks.started[0]["goal"]
    assert goal.startswith("Mach das") and "5 Werkzeugaufrufen" in goal and "wiederhole keine" in goal


async def test_no_continuation_for_cancel_calls_little_work_or_permanent_errors() -> None:
    o, tasks = orch()
    sess = SimpleNamespace(id=uuid.uuid4())
    for ch, err, n in (
        ("mobile", "cancelled", 5),
        ("phone", "Zeitüberschreitung", 5),
        ("mobile", "Zeitüberschreitung", 1),
        ("mobile", "ungültiger Parameter", 5),
        ("mobile", "Claude Code ist nicht angemeldet (im Terminal `claude` → /login).", 5),
    ):
        assert await o._continue_in_background(sess, OWNER, ch, "x", _result(err, n), None) is None  # type: ignore[arg-type]
    assert not tasks.started


async def test_system_written_turns_are_never_parsed_as_model_commands() -> None:
    o, _ = orch()
    sess = SimpleNamespace(id=uuid.uuid4())
    goal = "Nimm Opus für die Analyse und schreibe danach den Bericht"
    model, _, confirmation, text = await o._model_for_turn(sess, OWNER, "mobile", goal, internal=True)  # type: ignore[arg-type]
    assert confirmation is None and text == goal and model == "sonnet"  # not a switch command, nothing consumed
