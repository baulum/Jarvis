import asyncio

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocket

from jarvis.api.exposure import PublicExposureGuard
from jarvis.telephony import tunnel


async def ok(request: object) -> PlainTextResponse:
    return PlainTextResponse("ok")


async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_text("hi")
    await websocket.close()


app = Starlette(
    routes=[
        Route("/api/health", ok),
        Route("/api/chat", ok, methods=["POST"]),
        Route("/api/telephony/twilio/voice", ok, methods=["POST"]),
        WebSocketRoute("/api/telephony/stream", ws),
        WebSocketRoute("/api/events", ws),
    ]
)
app.add_middleware(PublicExposureGuard)
local = TestClient(app, base_url="http://127.0.0.1:8765")
public = TestClient(app, base_url="https://quiet-words.trycloudflare.com")


def test_local_requests_reach_everything() -> None:
    assert local.get("/api/health").status_code == 200
    assert local.post("/api/chat").status_code == 200


def test_tunnel_requests_only_reach_telephony() -> None:
    assert public.get("/api/health").status_code == 404
    assert public.post("/api/chat").status_code == 404
    assert public.post("/api/telephony/twilio/voice").status_code == 200
    # Cloudflare headers mark tunnel traffic even with a loopback Host header
    assert local.post("/api/chat", headers={"cf-connecting-ip": "1.2.3.4"}).status_code == 404


def test_tunnel_websockets_only_media_stream() -> None:
    with public.websocket_connect("/api/telephony/stream") as s:
        assert s.receive_text() == "hi"
    with pytest.raises(Exception):
        with public.websocket_connect("/api/events") as s:
            s.receive_text()


def test_quick_tunnel_parses_url(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fake cloudflared prints the banner on stderr like the real one; the URL is picked up."""
    fake = __import__("pathlib").Path(str(tmp_path)) / "cloudflared"
    fake.write_text(
        "#!/bin/sh\n"
        "echo 'INF Requesting new quick Tunnel on trycloudflare.com...' >&2\n"
        "echo 'INF |  https://brave-lion-sample.trycloudflare.com  |' >&2\n"
        "sleep 30\n"
    )
    fake.chmod(0o755)
    monkeypatch.setattr(tunnel, "CANDIDATES", (str(fake),))
    seen: list[str] = []

    async def run() -> None:
        async def on_url(u: str) -> None:
            seen.append(u)

        t = tunnel.QuickTunnel(8765, on_url)
        t.start()
        for _ in range(50):
            if seen:
                break
            await asyncio.sleep(0.1)
        await t.stop()
        assert not t.running

    asyncio.run(run())
    assert seen == ["https://brave-lion-sample.trycloudflare.com"]
