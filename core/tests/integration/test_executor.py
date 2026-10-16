import uuid

import pytest

from jarvis.db import session_scope
from jarvis.db.models import ChatSession
from jarvis.security.audit import verify_chain
from jarvis.security.auth import Principal, authenticate_token, create_device
from jarvis.tools.registry import ToolContext

pytestmark = pytest.mark.integration
OWNER = Principal(id="desk", role="owner", channel="device", device_id=uuid.uuid4())
VOICE = Principal(id="mic", role="voice_owner", channel="voice")
VIEWER = Principal(id="tv", role="viewer", channel="device")


async def test_read_and_low_run_directly(stack) -> None:
    r = await stack.executor.execute("t_read", {"x": 1}, ToolContext(principal=OWNER))
    assert r.status == "ok" and stack.counters["t_read"].calls == [{"x": 1}]
    r = await stack.executor.execute("t_low", {"x": 2}, ToolContext(principal=VOICE))
    assert r.status == "ok"


async def test_schema_validation(stack) -> None:
    r = await stack.executor.execute("t_read", {"x": "nope"}, ToolContext(principal=OWNER))
    assert r.status == "error" and "Ungültige Argumente" in r.message


async def test_medium_requires_approval_and_runs_exactly_once(stack) -> None:
    ctx = ToolContext(principal=VOICE, idempotency_key="k1")
    r1 = await stack.executor.execute("t_medium", {"x": 5}, ctx)
    assert r1.status == "approval_required" and r1.approval_id
    r2 = await stack.executor.execute("t_medium", {"x": 5}, ctx)  # model retries -> same approval
    assert r2.approval_id == r1.approval_id
    assert stack.counters["t_medium"].calls == []
    with pytest.raises(PermissionError):  # voice cannot approve
        await stack.executor.decide(r1.approval_id, True, VOICE)
    res = await stack.executor.decide(r1.approval_id, True, OWNER)
    assert res["status"] == "executed"
    again = await stack.executor.decide(r1.approval_id, True, OWNER)  # double click
    assert again["status"] == "executed"
    r3 = await stack.executor.execute("t_medium", {"x": 5}, ctx)  # resumed workflow
    assert r3.status == "duplicate" and r3.data["n"] == 1
    assert len(stack.counters["t_medium"].calls) == 1


async def test_rejection(stack) -> None:
    r = await stack.executor.execute("t_medium", {"x": 1}, ToolContext(principal=OWNER))
    res = await stack.executor.decide(r.approval_id, False, OWNER, "nein")
    assert res["status"] == "rejected" and stack.counters["t_medium"].calls == []


async def test_critical_needs_paired_desktop_device(stack) -> None:
    tg = Principal(id="chat", role="telegram_owner", channel="telegram")
    r = await stack.executor.execute("t_critical", {}, ToolContext(principal=OWNER))
    with pytest.raises(PermissionError):
        await stack.executor.decide(r.approval_id, True, tg)
    assert (await stack.executor.decide(r.approval_id, True, OWNER))["status"] == "executed"


async def test_role_ceiling_and_killswitch(stack) -> None:
    assert (await stack.executor.execute("t_low", {}, ToolContext(principal=VIEWER))).status == "denied"
    await stack.kill_switch.engage("test", "pytest")
    assert (await stack.executor.execute("t_low", {}, ToolContext(principal=OWNER))).status == "denied"
    assert (await stack.executor.execute("t_read", {}, ToolContext(principal=OWNER))).status == "ok"
    await stack.kill_switch.release("pytest")


async def test_idempotent_low_write(stack) -> None:
    ctx = ToolContext(principal=OWNER, idempotency_key="same")
    a = await stack.executor.execute("t_low", {"x": 1}, ctx)
    b = await stack.executor.execute("t_low", {"x": 1}, ctx)
    assert a.status == "ok" and b.status == "duplicate" and len(stack.counters["t_low"].calls) == 1


async def test_untrusted_output_taints_session_and_forces_approval(stack) -> None:
    async with session_scope() as s:
        sess = ChatSession(channel="desktop", actor_role="owner", actor_id="desk")
        s.add(sess)
        await s.flush()
        sid = sess.id
    ctx = ToolContext(principal=OWNER, session_id=sid)
    assert (await stack.executor.execute("t_low", {"x": 1}, ctx)).status == "ok"
    assert (await stack.executor.execute("t_web", {}, ctx)).status == "ok"
    r = await stack.executor.execute("t_low", {"x": 2}, ctx)
    assert r.status == "approval_required" and "nicht vertrauenswürdige" in (await _reason(r.approval_id))


async def _reason(aid) -> str:
    from jarvis.db.models import Approval

    async with session_scope() as s:
        return (await s.get(Approval, aid)).reason


async def test_audit_chain_detects_tampering(stack) -> None:
    for i in range(3):
        await stack.executor.execute("t_read", {"x": i}, ToolContext(principal=OWNER))
    assert (await verify_chain())["valid"]
    from sqlalchemy import text

    async with session_scope() as s:
        await s.execute(text("UPDATE audit_log SET outcome='forged' WHERE id=2"))
    res = await verify_chain()
    assert not res["valid"] and res["broken_at"] == 2


async def test_device_tokens() -> None:
    device, token = await create_device("phone", "operator")
    p = await authenticate_token(token)
    assert p is not None and p.role == "operator"
    assert await authenticate_token(token + "x") is None
    assert await authenticate_token(None) is None


async def test_tool_events_carry_graph_nodes_and_task_attribution(stack) -> None:
    """Live graph: every real tool call names its tool/connector node, a call id and the task it belongs to."""
    from jarvis.events import activity, bus

    q = bus.open_queue()
    try:
        with activity(task_id="task-42"):
            r = await stack.executor.execute("t_read", {"x": 1}, ToolContext(principal=OWNER))
        assert r.status == "ok"
        evs = []
        while not q.empty():
            ev = q.get_nowait()
            if ev.type.startswith("tool."):
                evs.append(ev)
    finally:
        bus.close_queue(q)
    started, finished = evs[0], evs[-1]
    assert started.type == "tool.started" and finished.type == "tool.finished"
    assert started.nodes[:2] == ["tool:t_read", "connector:test"] and started.source == "tool"
    assert started.task_id == finished.task_id == "task-42"
    assert started.data["call_id"] == finished.data["call_id"] and finished.data["status"] == "ok"
