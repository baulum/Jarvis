"""Distributed part of the Core: device hub (remote-command routing), agent API, device_* tools, gateway paths and
the wire contract with the Jarvis Device Agent (device_agent/)."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis.config import REPO_ROOT
from jarvis.devices import api as devices_api
from jarvis.devices import protocol
from jarvis.devices.connector import DevicesConnector
from jarvis.devices.hub import DeviceConnection, DeviceError, DeviceHub, parse_hello
from jarvis.mobile.gateway import mobile_path_allowed, remote_path_allowed
from jarvis.mobile.pairing import PairingService
from jarvis.security.auth import Principal
from jarvis.tools.executor import ToolUserError

ALL_AVAILABLE = {cap: {"status": "available", "reason": ""} for cap in protocol.capabilities()}


class FakeAgent:
    """The device end of a DeviceConnection: answers commands through the hub like the real agent does."""

    def __init__(self, hub: DeviceHub, name: str, caps: dict[str, dict[str, str]] | None = None, **reply: Any) -> None:
        self.hub = hub
        self.frames: list[dict[str, Any]] = []
        self.reply = reply
        self.silent = False
        self.conn = DeviceConnection(
            device_id=uuid.uuid4(),
            name=name,
            info={"platform": "windows", "hostname": name.lower() + "-host"},
            capabilities=caps if caps is not None else dict(ALL_AVAILABLE),
            send=self.send,
            close=self.close,
        )
        self.closed = False

    async def send(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)
        if frame["type"] != "command" or self.silent:
            return
        data = self.reply.get(frame["action"], {"ok": True, "args": frame["args"]})

        async def answer() -> None:
            await asyncio.sleep(0)
            if isinstance(data, DeviceError):
                msg = {"type": "result", "id": frame["id"], "ok": False,
                       "error": {"code": data.code, "message": data.message}}  # fmt: skip
            else:
                msg = {"type": "result", "id": frame["id"], "ok": True, "data": data}
            self.hub.handle_message(self.conn, msg)

        asyncio.get_running_loop().create_task(answer())

    async def close(self) -> None:
        self.closed = True


async def _no_publish(*a: Any, **kw: Any) -> None:
    pass


@pytest.fixture(autouse=True)
def _quiet_bus(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jarvis.devices.hub.bus.publish", _no_publish)


# --------------------------------------------------------------------------- contract with the agent
def _agent_protocol() -> Any:
    path = REPO_ROOT / "device_agent" / "src" / "jarvis_device" / "protocol.py"
    spec = importlib.util.spec_from_file_location("agent_protocol", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_core_and_agent_speak_the_same_protocol() -> None:
    agent = _agent_protocol()
    assert agent.PROTOCOL_VERSION == protocol.PROTOCOL_VERSION
    assert agent.ACTIONS == protocol.ACTIONS
    assert agent.CAPABILITY_LABELS == protocol.CAPABILITY_LABELS
    assert agent.READ_ONLY_ACTIONS == protocol.READ_ONLY_ACTIONS
    assert agent.ERROR_CODES == protocol.ERROR_CODES
    assert agent.MAX_FRAME_BYTES == protocol.MAX_FRAME_BYTES
    assert set(protocol.CAPABILITY_LABELS) == set(protocol.capabilities())


def test_hello_validation() -> None:
    info, caps = parse_hello({
        "type": "hello", "protocol": 1, "token": "x",
        "device": {"platform": "linux", "hostname": "pi", "version": "0.1.0"},
        "capabilities": {"input.mouse": {"status": "unavailable", "reason": "Wayland"}, "x": "kaputt",
                         "screen.capture": {"status": "erfunden"}},
    })  # fmt: skip
    assert info["platform"] == "linux" and info["hostname"] == "pi"
    assert caps == {
        "input.mouse": {"status": "unavailable", "reason": "Wayland"},
        "screen.capture": {"status": "unavailable", "reason": ""},  # unknown states fail closed
    }
    for bad in ({"type": "command"}, {"type": "hello", "protocol": 99, "device": {}}, {"type": "hello", "protocol": 1}):
        with pytest.raises(DeviceError):
            parse_hello(bad)


# --------------------------------------------------------------------------- hub routing
async def test_call_routes_to_the_named_device_and_returns_its_result() -> None:
    hub = DeviceHub(default_timeout_s=5)
    pc = FakeAgent(hub, "Büro-PC", **{"apps.list": [{"name": "Rechner"}]})
    laptop = FakeAgent(hub, "Laptop")
    await hub.attach(pc.conn)
    await hub.attach(laptop.conn)
    assert await hub.call("büro-pc", "apps.list") == [{"name": "Rechner"}]
    assert await hub.call("laptop-host", "notify", {"title": "t", "message": "m"}) == {
        "ok": True, "args": {"title": "t", "message": "m"},
    }  # fmt: skip
    assert pc.frames[0]["action"] == "apps.list" and laptop.frames[0]["action"] == "notify"
    with pytest.raises(DeviceError, match="Mehrere Geräte"):
        await hub.call(None, "apps.list")
    with pytest.raises(DeviceError, match="Unbekanntes Gerät"):
        await hub.call("Toaster", "apps.list")
    with pytest.raises(DeviceError) as e:
        await hub.call("Laptop", "does.not.exist")
    assert e.value.code == "unknown_action"


async def test_unavailable_capability_is_reported_with_the_devices_reason() -> None:
    hub = DeviceHub(default_timeout_s=5)
    caps = dict(ALL_AVAILABLE) | {"input.mouse": {"status": "unavailable", "reason": "Wayland erlaubt keine Maus"}}
    agent = FakeAgent(hub, "Linux-PC", caps)
    await hub.attach(agent.conn)
    with pytest.raises(DeviceError) as e:
        await hub.call(None, "input.click", {"x": 1, "y": 1})
    assert e.value.code == "unavailable"
    assert (
        "Maus (klicken, bewegen, scrollen) ist auf Linux-PC nicht verfügbar: Wayland erlaubt keine Maus"
        == e.value.message
    )
    assert agent.frames == []  # nothing was sent


async def test_kill_switch_blocks_everything_but_reading() -> None:
    engaged = {"on": True}
    hub = DeviceHub(default_timeout_s=5, kill_switch_engaged=lambda: engaged["on"])
    agent = FakeAgent(hub, "PC")
    await hub.attach(agent.conn)
    with pytest.raises(DeviceError) as e:
        await hub.call(None, "input.type", {"text": "x"})
    assert e.value.code == "kill_switch"
    assert await hub.call(None, "screen.capture") == {"ok": True, "args": {}}
    await hub.broadcast_killswitch(True)
    assert agent.frames[-1] == {"type": "killswitch", "engaged": True}


async def test_errors_timeouts_and_disconnects() -> None:
    hub = DeviceHub(default_timeout_s=5)
    agent = FakeAgent(hub, "PC", **{"files.read": DeviceError("denied", "liegt außerhalb der freigegebenen Ordner")})
    await hub.attach(agent.conn)
    with pytest.raises(DeviceError) as e:
        await hub.call(None, "files.read", {"path": "C:/Windows/x"})
    assert e.value.code == "denied" and "außerhalb" in e.value.message
    agent.silent = True
    with pytest.raises(DeviceError) as e:
        await hub.call(None, "apps.list", timeout_s=0.05)
    assert e.value.code == "timeout"
    pending = asyncio.create_task(hub.call(None, "apps.list", timeout_s=5))
    await asyncio.sleep(0.01)
    await hub.detach(agent.conn)
    with pytest.raises(DeviceError) as e:
        await pending
    assert e.value.code == "offline"
    with pytest.raises(DeviceError, match="Kein Jarvis Device Agent verbunden"):
        await hub.call(None, "apps.list")


async def test_reconnect_replaces_the_stale_socket_and_paired_offline_devices_are_listed() -> None:
    hub = DeviceHub()
    first = FakeAgent(hub, "PC")
    await hub.attach(first.conn)
    second = FakeAgent(hub, "PC")
    second.conn.device_id = first.conn.device_id
    await hub.attach(second.conn)
    assert first.closed and hub.online() == [second.conn]
    hub.paired = {first.conn.device_id: "PC", uuid.uuid4(): "Laptop"}
    listed = {d["name"]: d["online"] for d in hub.describe()}
    assert listed == {"PC": True, "Laptop": False}
    with pytest.raises(DeviceError, match="gekoppelt, aber gerade nicht verbunden"):
        hub.resolve("Laptop")


async def test_pairing_changes_notify_the_app_state() -> None:
    hub = DeviceHub()
    calls: list[bool] = []

    async def changed() -> None:
        calls.append(hub.has_paired)

    hub.on_paired_change = changed
    did = uuid.uuid4()
    await hub.set_paired(did, "PC")
    await hub.set_paired(uuid.uuid4(), "Laptop")  # still paired → no second notification
    await hub.set_paired(did, None)
    assert calls == [True]


# --------------------------------------------------------------------------- tools
def _connector(hub: DeviceHub, tmp_path: Path) -> DevicesConnector:
    async def oneshot(prompt: str, read_dir: Path) -> str:
        return "Ein Editor-Fenster mit dem Text 'Hallo'."

    state = SimpleNamespace(
        device_hub=hub,
        settings=SimpleNamespace(paths=SimpleNamespace(data_dir=tmp_path)),
        agent=object(),
        oneshot=SimpleNamespace(run=oneshot),
    )
    return DevicesConnector(state)  # type: ignore[arg-type]


async def test_device_tools_exist_only_with_a_paired_agent(tmp_path: Path) -> None:
    hub = DeviceHub()
    conn = _connector(hub, tmp_path)
    conn.enabled = True
    assert not conn.active and conn.missing_settings() == ["gekoppelter Jarvis Device Agent"]
    hub.paired[uuid.uuid4()] = "PC"
    assert conn.active
    tools = {t.name: t for t in conn.tools()}
    assert all(name.startswith("device_") and t.connector == "devices" for name, t in tools.items())
    for name in ("device_click", "device_type_text", "device_key", "device_shell_run", "device_claude_code"):
        assert tools[name].risk == "high", name
    assert tools["device_screenshot"].untrusted_output and tools["device_file_read"].untrusted_output
    assert tools["device_file_write"].writes


async def test_device_tools_map_errors_and_analyse_screenshots(tmp_path: Path) -> None:
    import base64

    hub = DeviceHub(default_timeout_s=5)
    png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
    caps = dict(ALL_AVAILABLE) | {"terminal": {"status": "unavailable", "reason": "am Gerät abgeschaltet"}}
    agent = FakeAgent(
        hub, "PC", caps, **{"screen.capture": {"png_base64": png, "width": 1600, "height": 900, "scale": 2.0}}
    )
    await hub.attach(agent.conn)
    tools = {t.name: t for t in _connector(hub, tmp_path).tools()}
    ctx = None
    out = await tools["device_screenshot"].handler(ctx, {"question": "Was ist offen?"})  # type: ignore[arg-type]
    assert Path(out["path"]).read_bytes().startswith(b"\x89PNG") and out["scale"] == 2.0
    assert "Editor-Fenster" in json.dumps(out["analysis"], ensure_ascii=False)
    assert agent.frames[-1]["args"] == {"max_width": 1600}
    with pytest.raises(ToolUserError, match="Terminal-Befehle ist auf PC nicht verfügbar: am Gerät abgeschaltet"):
        await tools["device_shell_run"].handler(ctx, {"argv": ["dir"]})  # type: ignore[arg-type]
    await tools["device_audio"].handler(ctx, {"action": "volume_set", "level": 30})  # type: ignore[arg-type]
    assert agent.frames[-1]["action"] == "audio.volume" and agent.frames[-1]["args"] == {"op": "set", "level": 30}
    await tools["device_click"].handler(ctx, {"device": "PC", "x": 10, "y": 20, "app": "Editor"})  # type: ignore[arg-type]
    assert agent.frames[-1]["args"] == {"x": 10, "y": 20, "app": "Editor"}  # "device" is routing, not an argument


async def test_controls_automations_and_content_search_tools(tmp_path: Path) -> None:
    hub = DeviceHub(default_timeout_s=5)
    tree = {
        "id": "0", "role": "Window", "title": "Rechner", "value": "", "app": {"name": "Rechner"},
        "children": [{"id": "0/0", "role": "Button", "title": "Gleich", "enabled": True, "description": None,
                      "bounds": {"x": 1, "y": 2, "width": 3, "height": 4}}],
    }  # fmt: skip
    caps = dict(ALL_AVAILABLE) | {"automation.applescript": {"status": "unavailable", "reason": "nur macOS"}}
    agent = FakeAgent(
        hub,
        "PC",
        caps,
        **{
            "ui.tree": tree,
            "automation.run": {"name": "backup", "exit_code": 0, "output": "fertig", "stderr": ""},
        },
    )
    await hub.attach(agent.conn)
    tools = {t.name: t for t in _connector(hub, tmp_path).tools()}
    ctx: Any = None
    assert tools["device_ui_press"].risk == "high" and tools["device_applescript"].risk == "high"
    assert tools["device_ui_tree"].risk == "read" and tools["device_ui_tree"].untrusted_output
    assert tools["device_automation_run"].risk == "medium" and tools["device_automations_list"].risk == "read"

    out = await tools["device_ui_tree"].handler(ctx, {"app": "Rechner", "max_depth": 20})
    assert agent.frames[-1]["action"] == "ui.tree"
    assert agent.frames[-1]["args"] == {"app": "Rechner", "max_depth": 8, "max_nodes": 250}
    assert "value" not in out and out["app"] == {"name": "Rechner"}
    assert out["children"] == [
        {
            "id": "0/0",
            "role": "Button",
            "title": "Gleich",
            "enabled": True,
            "bounds": {"x": 1, "y": 2, "width": 3, "height": 4},
        }
    ]

    await tools["device_ui_press"].handler(ctx, {"device": "PC", "window_id": 7, "role": "Button", "title": ""})
    assert agent.frames[-1]["action"] == "ui.press" and agent.frames[-1]["args"] == {"window_id": 7, "role": "Button"}

    res = await tools["device_automation_run"].handler(ctx, {"name": "backup", "timeout_s": 60})
    assert agent.frames[-1]["timeout_s"] == 70 and agent.frames[-1]["args"] == {"name": "backup", "timeout_s": 60}
    assert "fertig" in json.dumps(res["output"], ensure_ascii=False) and res["output"] != "fertig"  # marked untrusted

    await tools["device_files_search"].handler(ctx, {"query": "Rechnung", "mode": "content"})
    assert agent.frames[-1]["args"] == {"query": "Rechnung", "mode": "content"}
    await tools["device_frontmost"].handler(ctx, {})
    assert agent.frames[-1]["action"] == "windows.frontmost"
    with pytest.raises(ToolUserError, match="AppleScript \\(nur macOS\\) ist auf PC nicht verfügbar"):
        await tools["device_applescript"].handler(ctx, {"source": "beep"})


# --------------------------------------------------------------------------- gateway exposure
def test_gateway_paths_for_agents_phones_and_remote_clients() -> None:
    assert mobile_path_allowed("POST", "/api/devices/agents/register")
    assert mobile_path_allowed("GET", "/api/devices/agent/ws")
    assert not mobile_path_allowed("GET", "/api/tools")  # default: phone paths only
    assert not mobile_path_allowed("POST", "/api/devices/agents/pairing")  # pairing codes only from the owner
    assert remote_path_allowed("GET", "/api/tools")
    assert remote_path_allowed("GET", "/app/index.html")
    assert remote_path_allowed("GET", "/ws")
    for path in ("/mcp", "/api/webhooks/x", "/api/telephony/twilio/voice", "/api/connectors/google/oauth/callback"):
        assert not remote_path_allowed("POST", path), path


# --------------------------------------------------------------------------- agent API
def _api_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, SimpleNamespace]:
    async def no_audit(*a: Any, **kw: Any) -> None:
        pass

    device_id = uuid.uuid4()
    tokens = {"jdv_good": Principal(id="Büro-PC", role="device_agent", channel="device", device_id=device_id),
              "jdv_owner": Principal(id="desktop", role="owner", channel="device", device_id=uuid.uuid4())}  # fmt: skip

    async def authenticate(token: str | None) -> Principal | None:
        return tokens.get(token or "")

    async def create(name: str, role: str) -> tuple[Any, str]:
        return SimpleNamespace(id=device_id), "jdv_good"

    monkeypatch.setattr(devices_api, "audit", no_audit)
    monkeypatch.setattr(devices_api, "authenticate_token", authenticate)
    monkeypatch.setattr(devices_api, "create_device", create)
    monkeypatch.setattr(devices_api, "advertised_urls", lambda s: ["https://100.64.0.7:8767"])
    state = SimpleNamespace(
        device_hub=DeviceHub(),
        device_pairing=PairingService(600),
        settings=SimpleNamespace(),
        gateway=SimpleNamespace(fingerprint=lambda: "ab" * 32),
        kill_switch=SimpleNamespace(engaged=False),
    )
    app = FastAPI()
    app.include_router(devices_api.router)
    app.state.jarvis = state
    owner = Principal(id="desktop", role="owner", channel="desktop")
    app.dependency_overrides[devices_api.Owner.dependency] = lambda: owner  # type: ignore[attr-defined]
    app.dependency_overrides[devices_api.Reader.dependency] = lambda: owner  # type: ignore[attr-defined]
    return TestClient(app), state


def test_pairing_and_registration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    c, state = _api_client(monkeypatch, tmp_path)
    info = c.post("/api/devices/agents/pairing", json={"name": "Büro-PC"}).json()
    assert info["urls"] == ["https://100.64.0.7:8767"] and info["fingerprint"] == "ab" * 32
    assert f"--code {info['code']}" in info["command"] and "--fingerprint" in info["command"]
    body = {"code": info["code"], "name": "Büro-PC", "platform": "windows", "hostname": "BUERO"}
    r = c.post("/api/devices/agents/register", json=body)
    assert r.status_code == 200 and r.json()["token"] == "jdv_good"
    assert state.device_hub.has_paired
    again = c.post("/api/devices/agents/register", json=body)
    assert again.status_code == 403  # one-time code


def test_agent_socket_authenticates_in_the_hello_frame(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    c, state = _api_client(monkeypatch, tmp_path)
    hello = {"type": "hello", "protocol": 1, "device": {"platform": "windows", "hostname": "BUERO"},
             "capabilities": ALL_AVAILABLE}  # fmt: skip
    for token in ("jdv_bad", "jdv_owner"):  # unknown token and a non-agent role are both refused
        with c.websocket_connect("/api/devices/agent/ws") as ws:
            ws.send_text(json.dumps({**hello, "token": token}))
            assert ws.receive_json()["type"] == "error"
            with pytest.raises(WebSocketDisconnect) as e:
                ws.receive_text()
            assert e.value.code == 4401
    with c.websocket_connect("/api/devices/agent/ws") as ws:
        ws.send_text(json.dumps({**hello, "token": "jdv_good"}))
        welcome = ws.receive_json()
        assert welcome["type"] == "welcome" and welcome["kill_switch"] is False
        online = state.device_hub.online()
        assert [d.name for d in online] == ["Büro-PC"] and online[0].info["hostname"] == "BUERO"
        ws.send_text(json.dumps({"type": "ping"}))
        assert ws.receive_json() == {"type": "pong"}
    assert state.device_hub.online() == []
