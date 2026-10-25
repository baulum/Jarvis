"""SidecarClient against a real child process that speaks the sidecar's stdio protocol (no Node, no WhatsApp)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from jarvis.whatsapp.sidecar import METHODS, SidecarClient, SidecarError

FAKE = r'''
import json, os, sys
log = sys.argv[1]
def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()
with open(log, "a") as f:
    f.write(json.dumps({"start": True, "argv": sys.argv[1:], "secret_env": [k for k in os.environ if k.startswith("JARVIS_SECRET")]}) + "\n")
out({"type": "event", "event": "ready", "data": {"protocol": 1, "baileys": "6.7.24"}})
for line in sys.stdin:
    req = json.loads(line)
    m, p, rid = req["method"], req["params"], req["id"]
    if m == "init":
        with open(log, "a") as f:
            f.write(json.dumps({"init": True, "key_len": len(p.get("sessionKey", ""))}) + "\n")
        out({"type": "res", "id": rid, "ok": True, "result": {"hasSession": True, "baileys": "6.7.24"}})
        out({"type": "event", "event": "status", "data": {"state": "open", "hasSession": True}})
    elif m == "status":
        out({"type": "res", "id": rid, "ok": True, "result": {"state": "open"}})
    elif m == "send":
        out({"type": "res", "id": rid, "ok": False, "error": {"code": "not_connected", "message": "WhatsApp ist nicht verbunden."}})
    elif m == "checkNumber":
        os._exit(3)  # simulated crash in the middle of a request
    elif m == "shutdown":
        out({"type": "res", "id": rid, "ok": True, "result": {"ok": True}})
        sys.exit(0)
'''


def test_python_and_node_sides_know_the_same_methods() -> None:
    proto = (Path(__file__).resolve().parents[4] / "whatsapp-sidecar" / "src" / "protocol.ts").read_text()
    block = proto.split("export const METHODS = [", 1)[1].split("]", 1)[0]
    ts_methods = [m.strip().strip('"') for m in block.split(",") if m.strip()]
    assert ts_methods == list(METHODS)


def test_message_ids_match_the_sidecar_derivation() -> None:
    from jarvis.whatsapp.sending import message_id_for

    # same vector as whatsapp-sidecar/test/connection.test.ts (messageIdFor): both sides know a send's id in advance
    assert message_id_for("send:abc") == "3EB0DF85D5526DC4905C02"
    assert message_id_for("a") != message_id_for("b")


@pytest.fixture
def fake(tmp_path: Path) -> tuple[Path, Path]:
    script = tmp_path / "fake_sidecar.py"
    script.write_text(FAKE)
    return script, tmp_path / "log.jsonl"


async def test_start_init_events_errors_crash_and_restart(fake: tuple[Path, Path], tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    script, log = fake
    monkeypatch.setenv("JARVIS_SECRET_WHATSAPP_SESSION_KEY", "must-not-reach-the-child")
    events: list[tuple[str, dict[str, Any]]] = []

    async def on_event(name: str, data: dict[str, Any]) -> None:
        events.append((name, data))

    client = SidecarClient(
        command=lambda: [sys.executable, str(script), str(log)],
        init_params=lambda: {"sessionKey": "k" * 43, "sessionDir": str(tmp_path / "s")},
        on_event=on_event,
        log_path=tmp_path / "sidecar.log",
        request_timeout_s=5,
    )
    await client.start()
    try:
        assert await client.wait_ready(10)
        assert client.info["hasSession"] is True
        assert await client.request("status") == {"state": "open"}
        with pytest.raises(SidecarError) as err:
            await client.request("send", {"jid": "x"})
        assert err.value.code == "not_connected"
        await asyncio.sleep(0.05)
        assert ("status", {"state": "open", "hasSession": True}) in events

        # crash: the running request fails, the supervisor restarts and initialises again (session resumes)
        with pytest.raises(SidecarError) as crash:
            await client.request("checkNumber", {"phone": "1"})
        assert crash.value.code == "sidecar_exited"
        assert await client.wait_ready(15)
        assert await client.request("status") == {"state": "open"}
        assert client.restarts == 1
        assert any(n == "sidecar" and d.get("state") == "exited" for n, d in events)
    finally:
        await client.stop()
    lines = [json.loads(x) for x in log.read_text().splitlines()]
    starts = [x for x in lines if x.get("start")]
    assert len(starts) == 2 and len([x for x in lines if x.get("init")]) == 2
    for s in starts:
        assert s["secret_env"] == [] and all("k" * 10 not in a for a in s["argv"])  # key only through the pipe
    assert all(x["key_len"] == 43 for x in lines if x.get("init"))
    assert not client.running


async def test_missing_sidecar_build_is_reported(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, Any]]] = []

    async def on_event(name: str, data: dict[str, Any]) -> None:
        events.append((name, data))

    client = SidecarClient(command=lambda: None, init_params=dict, on_event=on_event, log_path=tmp_path / "x.log")
    client.last_error = "Der WhatsApp-Dienst ist nicht gebaut"
    await client.start()
    await asyncio.sleep(0.05)
    with pytest.raises(SidecarError) as err:
        await client.request("status", timeout=0.1)
    await client.stop()
    assert err.value.code == "unavailable" and "nicht gebaut" in err.value.message
    assert events and events[0][1]["state"] == "unavailable"
