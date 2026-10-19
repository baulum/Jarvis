"""Phone location sharing: newest fix only, staleness notes, request-on-demand, endpoints, tool registration."""

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.mobile import api as mobile_api
from jarvis.mobile.location import LocationStore, describe
from jarvis.mobile.push import PushResult
from jarvis.security.auth import Principal

DEV = uuid.uuid4()


class FakePush:
    def __init__(self, ok: bool = True, on_send: Any = None) -> None:
        self.ok = ok
        self.on_send = on_send
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def send_all(self, kind: str, payload: dict[str, Any], **kw: Any) -> list[PushResult]:
        self.sent.append((kind, payload))
        if self.on_send:
            self.on_send()
        return [PushResult(str(DEV), self.ok, None if self.ok else "UNREGISTERED")]


def _store(push: FakePush | None = None) -> LocationStore:
    return LocationStore(push or FakePush())  # type: ignore[arg-type]


def test_keeps_only_newest_fix_and_hides_coordinates_in_repr() -> None:
    st = _store()
    now = datetime.now(UTC)
    assert st.update(DEV, 52.52, 13.405, 12.0, now - timedelta(minutes=1))
    assert not st.update(DEV, 48.1, 11.5, 5.0, now - timedelta(minutes=5))  # older, out of order
    fix = st.latest()
    assert fix is not None and fix.latitude == 52.52
    assert "52.52" not in repr(fix) and "13.405" not in repr(fix)
    with pytest.raises(ValueError):
        st.update(DEV, 1, 1, None, now + timedelta(hours=1))


def test_describe_fresh_stale_and_missing() -> None:
    st = _store()
    now = datetime.now(UTC)
    st.update(DEV, 52.52, 13.405, 8.4, now - timedelta(minutes=3))
    fresh = describe(st.latest(), requested=False, sharing_off=False, now=now)
    assert fresh["available"] and fresh["age_minutes"] == 3.0 and not fresh["stale"] and "note" not in fresh
    assert fresh["accuracy_m"] == 8

    stale = describe(st.latest(), requested=False, sharing_off=False, now=now + timedelta(minutes=20))
    assert stale["stale"] and "älter als 10 Minuten" in stale["note"]

    missing = describe(None, requested=True, sharing_off=False)
    assert not missing["available"] and "Kein Standort" in missing["note"]
    off = describe(None, requested=False, sharing_off=True)
    assert "ausgeschaltet" in off["note"]


async def test_current_requests_fresh_fix_from_phone() -> None:
    holder: dict[str, LocationStore] = {}

    def phone_answers() -> None:  # the phone reacts to the push with a new fix
        asyncio.get_running_loop().call_later(0.05, lambda: holder["s"].update(DEV, 50.0, 8.0, 20.0, datetime.now(UTC)))

    push = FakePush(on_send=phone_answers)
    st = holder["s"] = _store(push)
    st.update(DEV, 49.0, 7.0, 30.0, datetime.now(UTC) - timedelta(minutes=30))
    res = await st.current()
    assert push.sent == [("location_request", {})]  # the push carries no data
    assert res["latitude"] == 50.0 and not res["stale"]

    res2 = await st.current()  # fresh now → no further request
    assert len(push.sent) == 1 and res2["latitude"] == 50.0


async def test_current_without_reachable_phone_returns_stale_note(monkeypatch: pytest.MonkeyPatch) -> None:
    st = _store(FakePush(ok=False))
    st.update(DEV, 49.0, 7.0, None, datetime.now(UTC) - timedelta(minutes=45))
    res = await st.current()
    assert res["stale"] and res["accuracy_m"] is None and "45 Minuten" in res["note"]
    st.clear(DEV)
    res = await st.current()
    assert not res["available"] and "ausgeschaltet" in res["note"]


def _client(st: LocationStore, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def no_audit(*a: Any, **kw: Any) -> None:
        pass

    monkeypatch.setattr(mobile_api, "audit", no_audit)
    app = FastAPI()
    app.include_router(mobile_api.router)
    app.state.jarvis = SimpleNamespace(location=st)
    phone = Principal(id="Pixel", role="mobile_owner", channel="mobile", device_id=DEV)
    app.dependency_overrides[mobile_api.mobile_principal] = lambda: phone
    return TestClient(app)


def test_location_endpoints_store_and_delete_without_logging(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    st = _store()
    c = _client(st, monkeypatch)
    caplog.set_level(logging.DEBUG)
    body = {"latitude": 52.520008, "longitude": 13.404954, "accuracy_m": 9.5, "timestamp_ms": int(time.time() * 1000)}
    r = c.put("/api/mobile/location", json=body)
    assert r.status_code == 200 and r.json()["stored"] is True
    fix = st.latest()
    assert fix is not None and fix.longitude == 13.404954 and fix.device_id == DEV
    assert "52.52" not in caplog.text and "13.40" not in caplog.text

    assert c.put("/api/mobile/location", json={**body, "latitude": 91}).status_code == 422
    assert c.put("/api/mobile/location", json={**body, "timestamp_ms": 10**16}).status_code == 422

    assert c.delete("/api/mobile/location").status_code == 200
    assert st.latest() is None


def test_tool_registered_without_parameters() -> None:
    from jarvis.mobile.tools import mobile_tools

    spec = next(t for t in mobile_tools(SimpleNamespace()) if t.name == "mobile_get_location")  # type: ignore[arg-type]
    assert spec.parameters == {"type": "object", "properties": {}, "additionalProperties": False}
    assert spec.risk == "read" and not spec.writes
