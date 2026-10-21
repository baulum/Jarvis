"""Reply from the notification shade: the answer must reach the phone, not only the Mac."""

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.agent.orchestrator import TurnResult
from jarvis.mobile import api as mobile_api
from jarvis.mobile.alerts import Alert, alert_target
from jarvis.security.auth import Principal

PHONE = Principal(id="Pixel", role="mobile_owner", channel="mobile", device_id=uuid.uuid4())


class FakeOrchestrator:
    def __init__(self, reply: str, error: str | None = None) -> None:
        self.reply, self.error = reply, error
        self.sessions: list[uuid.UUID | None] = []
        self.handled: list[tuple[str, str, uuid.UUID | None]] = []

    async def get_or_create_session(self, p: Principal, channel: str, sid: uuid.UUID | None = None) -> Any:
        self.sessions.append(sid)
        return SimpleNamespace(id=sid or uuid.uuid4())

    async def handle(self, p: Principal, channel: str, text: str, *, session_id: uuid.UUID | None = None) -> Any:
        self.handled.append((channel, text, session_id))
        assert session_id is not None
        return TurnResult(session_id, self.reply, [], error=self.error)


class FakeAlerts:
    def __init__(self, row: Any = None) -> None:
        self.row = row
        self.sent: list[Alert] = []

    async def get(self, aid: uuid.UUID) -> Any:
        return self.row

    async def dispatch(self, alert: Alert) -> dict[str, Any]:
        self.sent.append(alert)
        return {"decision": "push"}


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    tasks: list[Any] = []
    monkeypatch.setattr(mobile_api, "spawn", lambda coro, name=None: tasks.append(coro))
    return tasks


async def test_answer_comes_back_as_notification_opening_the_chat() -> None:
    sid = uuid.uuid4()
    st: Any = SimpleNamespace(orchestrator=FakeOrchestrator("Erledigt, Sir."), alerts=FakeAlerts())
    await mobile_api._answer_reply(st, PHONE, "Bitte erledigen", sid)
    assert st.orchestrator.handled == [("mobile", "Bitte erledigen", sid)]
    (alert,) = st.alerts.sent
    assert alert.body == "Erledigt, Sir."
    assert alert_target(alert) == ("chat", str(sid))  # the tap opens this conversation in the app
    assert alert.allow_call is False


async def test_no_notification_for_an_interrupted_answer() -> None:
    st: Any = SimpleNamespace(orchestrator=FakeOrchestrator("Abgebrochen, Sir.", "cancelled"), alerts=FakeAlerts())
    await mobile_api._answer_reply(st, PHONE, "x", uuid.uuid4())
    assert st.alerts.sent == []


async def test_reply_continues_the_conversation_the_notification_came_from(spawned: list[Any]) -> None:
    origin = uuid.uuid4()
    row = SimpleNamespace(title="Antwort von Jarvis", body="Erledigt.", data={"session_id": str(origin)})
    st: Any = SimpleNamespace(orchestrator=FakeOrchestrator("Gern."), alerts=FakeAlerts(row))
    out = await mobile_api.alert_reply(str(uuid.uuid4()), mobile_api.ReplyBody(text="Danke"), st, PHONE)
    assert out["session_id"] == str(origin)
    assert st.orchestrator.sessions == [origin]
    await spawned[0]
    assert st.alerts.sent[0].data == {"session_id": str(origin)}


async def test_reply_to_other_notifications_starts_a_mobile_conversation(spawned: list[Any]) -> None:
    row = SimpleNamespace(title="Job fertig", body="Website steht.", data={"job_id": "j1"})
    st: Any = SimpleNamespace(orchestrator=FakeOrchestrator("Gern."), alerts=FakeAlerts(row))
    await mobile_api.alert_reply(str(uuid.uuid4()), mobile_api.ReplyBody(text="Danke"), st, PHONE)
    assert st.orchestrator.sessions == [None]
    for coro in spawned:
        coro.close()
