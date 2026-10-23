"""The agent against a stand-in Core: hello with token, commands, kill switch, rejection, pinning, pairing."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest
from agent_fakes import FakeBackend
from websockets.asyncio.server import serve

from jarvis_device.config import AgentConfig
from jarvis_device.connection import (
    AgentConnection,
    PairingFailed,
    Unauthorized,
    check_pin,
    normalize_fingerprint,
    register,
    ws_url,
)
from jarvis_device.protocol import PROTOCOL_VERSION, REGISTER_PATH, WS_PATH


def test_urls_and_fingerprints():
    assert ws_url("https://100.64.0.1:8767/") == f"wss://100.64.0.1:8767{WS_PATH}"
    assert ws_url("http://127.0.0.1:8765") == f"ws://127.0.0.1:8765{WS_PATH}"
    assert normalize_fingerprint("AB:CD ef") == "abcdef"
    der = b"certificate"
    check_pin(der, hashlib.sha256(der).hexdigest().upper())
    check_pin(None, "")  # no pin: CA verification applies instead
    with pytest.raises(PairingFailed, match="passt nicht"):
        check_pin(der, "00" * 32)
    with pytest.raises(PairingFailed):
        check_pin(None, "00" * 32)


async def _core(handler):
    server = await serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"http://127.0.0.1:{port}"


async def test_agent_session_executes_commands_and_follows_the_kill_switch(make_dispatcher):
    backend = FakeBackend()
    dispatcher = make_dispatcher(backend)
    seen: dict[str, object] = {}
    done = asyncio.Event()

    async def core(ws):
        hello = json.loads(await ws.recv())
        seen["hello"] = hello
        await ws.send(json.dumps({"type": "welcome", "device_id": "d1", "kill_switch": False}))
        await ws.send(
            json.dumps(
                {
                    "type": "command",
                    "id": "c1",
                    "action": "notify",
                    "args": {"title": "Jarvis", "message": "Hallo"},
                    "timeout_s": 10,
                }
            )
        )
        seen["r1"] = json.loads(await ws.recv())
        await ws.send(json.dumps({"type": "killswitch", "engaged": True}))
        await ws.send(json.dumps({"type": "command", "id": "c2", "action": "input.type", "args": {"text": "x"}}))
        seen["r2"] = json.loads(await ws.recv())
        await ws.send(json.dumps({"type": "command", "id": "c3", "action": "apps.list", "args": {}}))
        seen["r3"] = json.loads(await ws.recv())
        await ws.send(json.dumps({"type": "command", "id": "c4", "action": "input.click", "args": {"x": "?"}}))
        seen["r4"] = json.loads(await ws.recv())
        done.set()
        await ws.wait_closed()

    server, url = await _core(core)
    cfg = AgentConfig(core_url=url, token="jdv_test", name="Test-PC")
    conn = AgentConnection(cfg, dispatcher)
    task = asyncio.create_task(conn.session())
    await asyncio.wait_for(done.wait(), 10)
    task.cancel()
    server.close()
    hello = seen["hello"]
    assert hello["type"] == "hello" and hello["protocol"] == PROTOCOL_VERSION and hello["token"] == "jdv_test"
    assert hello["device"]["platform"] == "fake" and hello["capabilities"]["notifications"]["status"] == "available"
    assert seen["r1"] == {"type": "result", "id": "c1", "ok": True, "data": {"ok": True}}
    assert ("notify", "Jarvis", "Hallo") in backend.calls
    assert seen["r2"]["ok"] is False and seen["r2"]["error"]["code"] == "kill_switch"
    assert seen["r3"]["ok"] is True  # read-only actions still work during the kill switch
    assert seen["r4"]["error"]["code"] in ("invalid", "kill_switch")


async def test_rejected_token_stops_the_agent(make_dispatcher):
    async def core(ws):
        await ws.recv()
        await ws.send(json.dumps({"type": "error", "message": "Geräte-Token ungültig"}))
        await ws.close(code=4401)

    server, url = await _core(core)
    conn = AgentConnection(AgentConfig(core_url=url, token="jdv_alt"), make_dispatcher())
    with pytest.raises(Unauthorized, match="ungültig"):
        await asyncio.wait_for(conn.run_forever(), 10)
    server.close()


class _RegisterHandler(BaseHTTPRequestHandler):
    seen: ClassVar[list[dict]] = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        type(self).seen.append({"path": self.path, **body})
        ok = body["code"] == "richtig"
        payload = (
            {"device_id": "d-1", "token": "jdv_neu", "protocol": PROTOCOL_VERSION}
            if ok
            else {"detail": "Kopplungscode ungültig"}
        )
        data = json.dumps(payload).encode()
        self.send_response(200 if ok else 403)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def test_pairing_over_loopback_and_refusals():
    server = HTTPServer(("127.0.0.1", 0), _RegisterHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        res = register(url, "richtig", "Büro-PC", "", "windows")
        assert res["token"] == "jdv_neu"
        assert _RegisterHandler.seen[-1]["path"] == REGISTER_PATH and _RegisterHandler.seen[-1]["name"] == "Büro-PC"
        with pytest.raises(PairingFailed, match="ungültig"):
            register(url, "falsch", "PC", "", "windows")
    finally:
        server.shutdown()
    with pytest.raises(PairingFailed, match="https"):
        register("http://192.168.1.20:8767", "code", "PC", "", "linux")  # plain HTTP only on loopback
    with pytest.raises(PairingFailed):
        register("ftp://host", "code", "PC", "", "linux")
