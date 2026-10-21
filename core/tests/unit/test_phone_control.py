"""Phone control: command serialisation over the push channel, honest errors, result endpoint, tool registration."""

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from jarvis.mobile import api as mobile_api
from jarvis.mobile.phone_control import MAX_COMMAND_BYTES, PhoneControl, PhoneError
from jarvis.mobile.phone_tools import phone_tools
from jarvis.mobile.push import PushResult, decrypt_payload, encrypt_payload
from jarvis.security.auth import Principal
from jarvis.tools.executor import ToolUserError

DEV = uuid.uuid4()


class FakePush:
    def __init__(
        self, *, configured: bool = True, results: list[PushResult] | None = None, on_send: Any = None
    ) -> None:
        self.fcm = SimpleNamespace(configured=lambda: configured)
        self.results = [PushResult(str(DEV), True)] if results is None else results
        self.on_send = on_send
        self.sent: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    async def send_all(self, kind: str, payload: dict[str, Any], **kw: Any) -> list[PushResult]:
        self.sent.append((kind, payload, kw))
        if self.on_send:
            self.on_send(payload)
        return self.results


def _phone(push: FakePush) -> PhoneControl:
    return PhoneControl(push)  # type: ignore[arg-type]


def _answer(pc: PhoneControl, result: dict[str, Any], device: uuid.UUID = DEV):  # type: ignore[no-untyped-def]
    def on_send(payload: dict[str, Any]) -> None:
        asyncio.get_running_loop().call_later(0.01, lambda: pc.complete(device, payload["cmd_id"], result))

    return on_send


async def test_command_is_serialised_as_high_priority_push_and_result_returned() -> None:
    push = FakePush()
    pc = _phone(push)
    push.on_send = _answer(pc, {"ok": True, "data": {"launched": True, "package": "com.whatsapp"}})
    data = await pc.run("open_app", {"name": "WhatsApp"})
    assert data == {"launched": True, "package": "com.whatsapp"}
    kind, payload, kw = push.sent[0]
    assert kind == "phone_command"
    assert payload["action"] == "open_app" and payload["args"] == {"name": "WhatsApp"} and len(payload["cmd_id"]) == 32
    assert kw["high_priority"] is True and kw["ttl_s"] >= 30


def test_command_survives_the_encrypted_push_envelope() -> None:
    key = b"k" * 32
    body = {"id": "a" * 32, "type": "phone_command", "ts": 1, "exp": 2, "cmd_id": "b" * 32, "action": "type_text",
            "args": {"text": "Grüße 👋" * 40, "element_id": "0.1.2", "append": False}}
    env = encrypt_payload(key, str(DEV), body)
    assert decrypt_payload(key, str(DEV), env) == body


async def test_phone_not_connected_without_paired_phone() -> None:
    pc = _phone(FakePush(results=[]))
    with pytest.raises(PhoneError, match="Handy nicht verbunden: kein gekoppeltes Handy"):
        await pc.run("ui_tree")


async def test_phone_not_connected_without_firebase() -> None:
    push = FakePush(configured=False)
    with pytest.raises(PhoneError, match="Handy nicht verbunden: Push"):
        await _phone(push).run("ui_tree")
    assert push.sent == []


async def test_push_not_delivered_names_the_reason() -> None:
    pc = _phone(FakePush(results=[PushResult(str(DEV), False, "UNREGISTERED")]))
    with pytest.raises(PhoneError, match=r"nicht zugestellt.*UNREGISTERED"):
        await pc.run("ui_tree")


async def test_no_answer_is_reported_as_unreachable() -> None:
    with pytest.raises(PhoneError, match="Handy nicht erreichbar"):
        await _phone(FakePush()).run("ui_tree", timeout_s=0.05)


async def test_accessibility_disabled_gives_clear_message() -> None:
    push = FakePush()
    pc = _phone(push)
    push.on_send = _answer(pc, {"ok": False, "error": "accessibility_disabled"})
    with pytest.raises(PhoneError, match="Bedienungshilfe in der Jarvis-App nicht aktiviert"):
        await pc.run("tap", {"x": 1, "y": 2})


async def test_password_field_error_and_phone_detail() -> None:
    push = FakePush()
    pc = _phone(push)
    push.on_send = _answer(pc, {"ok": False, "error": "password_field", "message": "(Feld „PIN“)"})
    with pytest.raises(PhoneError, match=r"Passwortfeld.*PIN"):
        await pc.run("type_text", {"text": "x"})


async def test_only_the_pushed_phone_may_answer_and_only_once() -> None:
    push = FakePush()
    pc = _phone(push)
    stranger = uuid.uuid4()
    outcomes: list[bool] = []

    def on_send(payload: dict[str, Any]) -> None:
        def reply() -> None:
            outcomes.append(pc.complete(stranger, payload["cmd_id"], {"ok": True, "data": {"evil": 1}}))
            outcomes.append(pc.complete(DEV, payload["cmd_id"], {"ok": True, "data": {"fine": 1}}))
            outcomes.append(pc.complete(DEV, payload["cmd_id"], {"ok": True, "data": {"again": 1}}))

        asyncio.get_running_loop().call_later(0.01, reply)

    push.on_send = on_send
    # the push has gone out by the time replies arrive, so the answering phone is checked against the target set
    assert await pc.run("list_apps") == {"fine": 1}
    assert outcomes == [False, True, False]
    assert not pc.complete(DEV, "unknown-command-id", {"ok": True})


async def test_too_long_or_unknown_commands_are_refused_before_sending() -> None:
    push = FakePush()
    pc = _phone(push)
    with pytest.raises(PhoneError, match="zu lang"):
        await pc.run("type_text", {"text": "x" * (MAX_COMMAND_BYTES + 10)})
    with pytest.raises(PhoneError, match="Unbekannter"):
        await pc.run("format_phone")
    assert push.sent == []


def _tools(push: FakePush) -> tuple[PhoneControl, dict[str, Any]]:
    pc = _phone(push)
    specs = {t.name: t for t in phone_tools(SimpleNamespace(phone=pc))}  # type: ignore[arg-type]
    return pc, specs


async def test_tool_returns_user_error_when_phone_not_connected() -> None:
    _, tools = _tools(FakePush(results=[]))
    with pytest.raises(ToolUserError, match="Handy nicht verbunden"):
        await tools["phone_open_app"].handler(SimpleNamespace(), {"name": "WhatsApp"})


async def test_tools_send_expected_actions_and_arguments() -> None:
    push = FakePush()
    pc, tools = _tools(push)
    push.on_send = _answer(pc, {"ok": True, "data": {}})
    ctx = SimpleNamespace()
    await tools["phone_tap"].handler(ctx, {"element_id": "0.3"})
    await tools["phone_tap"].handler(ctx, {"x": 10, "y": 20})
    await tools["phone_type_text"].handler(ctx, {"text": "Hallo", "element_id": "0.1"})
    await tools["phone_scroll"].handler(ctx, {"direction": "down"})
    await tools["phone_key"].handler(ctx, {"key": "home"})
    sent = [(p["action"], p["args"]) for _, p, _ in push.sent]
    assert sent == [
        ("tap", {"element_id": "0.3"}),
        ("tap", {"x": 10, "y": 20}),
        ("type_text", {"text": "Hallo", "append": False, "element_id": "0.1"}),
        ("scroll", {"direction": "down"}),
        ("key", {"key": "home"}),
    ]


async def test_tap_and_open_app_validate_their_arguments() -> None:
    _, tools = _tools(FakePush())
    with pytest.raises(ToolUserError):
        await tools["phone_tap"].handler(SimpleNamespace(), {})
    with pytest.raises(ToolUserError):
        await tools["phone_tap"].handler(SimpleNamespace(), {"element_id": "0", "x": 1, "y": 2})
    with pytest.raises(ToolUserError):
        await tools["phone_open_app"].handler(SimpleNamespace(), {})


def test_tool_names_and_risk_levels_mirror_the_mac_tools() -> None:
    specs = {t.name: t for t in phone_tools(SimpleNamespace())}  # type: ignore[arg-type]
    assert set(specs) == {
        "phone_open_app", "phone_list_apps", "phone_ui_tree", "phone_tap",
        "phone_type_text", "phone_scroll", "phone_key", "phone_screenshot",
    }  # fmt: skip
    risks = {n: s.risk for n, s in specs.items()}
    assert risks["phone_list_apps"] == "read" and risks["phone_ui_tree"] == "read"
    assert risks["phone_open_app"] == "low"
    assert risks["phone_tap"] == risks["phone_type_text"] == risks["phone_key"] == "high"
    assert specs["phone_ui_tree"].untrusted_output and specs["phone_screenshot"].untrusted_output
    assert all("HANDY" in s.description for s in specs.values())


def test_persona_routes_phone_requests_to_phone_tools() -> None:
    from jarvis.agent.persona import OPERATING_RULES

    assert "phone_*" in OPERATING_RULES and "niemals `mac_*`" in OPERATING_RULES


def _client(pc: PhoneControl) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(mobile_api.router)
    app.state.jarvis = SimpleNamespace(phone=pc)
    phone = Principal(id="Pixel", role="mobile_owner", channel="mobile", device_id=DEV)
    app.dependency_overrides[mobile_api.mobile_principal] = lambda: phone
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")  # same loop as the test


async def test_result_endpoint_completes_pending_command() -> None:
    push = FakePush()
    pc = _phone(push)
    c = _client(pc)
    posted: list[Any] = []

    def on_send(payload: dict[str, Any]) -> None:
        async def reply() -> None:
            r = await c.post(
                "/api/mobile/phone/result", json={"cmd_id": payload["cmd_id"], "ok": True, "data": {"nodes": []}}
            )
            posted.append(r.json())

        asyncio.get_running_loop().create_task(reply())

    push.on_send = on_send
    assert await pc.run("ui_tree") == {"nodes": []}
    await asyncio.sleep(0.05)
    assert posted == [{"ok": True, "accepted": True}]
    r = await c.post("/api/mobile/phone/result", json={"cmd_id": "x" * 32, "ok": True})
    assert r.status_code == 200 and r.json()["accepted"] is False
    assert (await c.post("/api/mobile/phone/result", json={"cmd_id": "short", "ok": True})).status_code == 422
    await c.aclose()
