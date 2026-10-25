"""The desktop app's REST API: upload → approval → send, file content, connector gate."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from jarvis.api.deps import require_owner
from jarvis.security.auth import Principal
from jarvis.whatsapp.api import router
from jarvis.whatsapp.tools import whatsapp_tools

from .conftest import OWNER, media_message
from .test_wa_service import PNG, deliver, stored_msg

WEBER = "4917000000001@s.whatsapp.net"
DECIDER = Principal(id="desk-1", role="owner", channel="desktop", device_id=uuid.uuid4())


@pytest.fixture
async def api(wa: SimpleNamespace) -> AsyncIterator[SimpleNamespace]:
    for spec in whatsapp_tools(wa.svc):
        wa.registry.register(spec)
    await wa.svc.repo.upsert_contacts([{"jid": WEBER, "name": "Jonathan Weber"}])
    wa.connector = SimpleNamespace(enabled=True)
    wa.state.connectors.connectors["whatsapp"] = wa.connector
    app = FastAPI()
    app.include_router(router)
    app.state.jarvis = wa.state
    app.dependency_overrides[require_owner] = lambda: OWNER
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        wa.client = client
        yield wa


async def upload(api: SimpleNamespace, name: str, data: bytes) -> httpx.Response:
    return await api.client.post("/api/whatsapp/uploads", params={"name": name}, content=data)


def sends(api: SimpleNamespace) -> list[dict[str, Any]]:
    return [p for m, p in api.sidecar.calls if m == "send"]


async def test_upload_then_send_goes_through_an_approval(api: SimpleNamespace) -> None:
    up = await upload(api, "Foto.jpg", PNG)
    assert up.status_code == 201, up.text
    body = up.json()
    assert body["ref"].startswith("staged:") and body["name"] == "Foto.png" and body["category"] == "image"

    res = await api.client.post("/api/whatsapp/send", json={"chat": WEBER, "files": [body["ref"]]})
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["status"] == "approval_required" and "Foto.png" in out["summary"]
    assert sends(api) == []  # nothing leaves before Sir confirms

    decided = await api.executor.decide(uuid.UUID(str(out["approval_id"])), True, DECIDER)
    assert decided["status"] == "executed", decided
    assert len(sends(api)) == 1 and sends(api)[0]["kind"] == "image" and sends(api)[0]["jid"] == WEBER


async def test_uploads_refuse_programs_and_empty_files(api: SimpleNamespace) -> None:
    exe = await upload(api, "rechnung.pdf", b"MZ\x90\x00" + b"\x00" * 200)
    assert exe.status_code == 415
    empty = await upload(api, "leer.txt", b"")
    assert empty.status_code == 400
    uploads = api.svc.staging_dir / "uploads"
    assert not uploads.exists() or not any(uploads.iterdir())  # rejected uploads leave nothing behind


async def test_stale_upload_reference_is_an_honest_error(api: SimpleNamespace) -> None:
    res = await api.client.post("/api/whatsapp/send", json={"chat": WEBER, "files": ["staged:abc123"]})
    assert res.status_code == 400 and "erneut hochladen" in res.json()["detail"]


async def test_file_content_inline_for_media_attachment_on_request(api: SimpleNamespace) -> None:
    await deliver(api, media_message("IMG", "image", mimetype="image/png"))
    api.sidecar.download_data = PNG
    f = await api.svc.download(await stored_msg(api, "IMG"), actor="t")
    inline = await api.client.get(f"/api/whatsapp/files/{f.id}/content")
    assert inline.status_code == 200 and inline.content == PNG
    assert inline.headers["content-type"] == "image/png" and inline.headers["content-disposition"].startswith("inline")
    assert inline.headers["x-content-type-options"] == "nosniff"
    att = await api.client.get(f"/api/whatsapp/files/{f.id}/content", params={"download": "true"})
    assert att.headers["content-type"] == "application/octet-stream"
    assert att.headers["content-disposition"].startswith("attachment")
    listed = await api.client.get("/api/whatsapp/files", params={"type": "image"})
    assert [r["file_id"] for r in listed.json()] == [str(f.id)]
    assert (await api.client.get(f"/api/whatsapp/files/{uuid.uuid4()}/content")).status_code == 404


async def test_disabled_connector_is_refused(api: SimpleNamespace) -> None:
    api.connector.enabled = False
    assert (await api.client.get("/api/whatsapp/files")).status_code == 409
    assert (await upload(api, "a.png", PNG)).status_code == 409
