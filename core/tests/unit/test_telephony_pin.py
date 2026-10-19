"""A spoken PIN must never be stored or broadcast, and an outbound call's message waits for the PIN."""

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

import jarvis.telephony.service as svc_mod
from jarvis.events import bus
from jarvis.telephony.service import CallState, TelephonyService


async def _noop(*_a: Any, **_k: Any) -> None:
    return None


@pytest.fixture
def svc(monkeypatch: pytest.MonkeyPatch) -> tuple[TelephonyService, list[str]]:
    said: list[str] = []
    stt_lines = iter(["eins zwei drei vier", "tschüss"])

    class STT:
        async def transcribe_pcm(self, *_a: Any) -> str:
            return next(stt_lines)

    s = TelephonyService.__new__(TelephonyService)
    s.cfg = SimpleNamespace(pin_secret="phone-pin", provider="local_test")  # type: ignore[assignment]
    s.state = SimpleNamespace(stt=STT(), orchestrator=None)  # type: ignore[assignment]
    s.active = {}

    async def say(_ws: Any, _st: CallState, text: str) -> None:
        said.append(text)

    async def hangup(*_a: Any) -> None:
        return None

    monkeypatch.setattr(s, "_say", say)
    monkeypatch.setattr(s, "_hangup", hangup)
    monkeypatch.setattr(svc_mod, "audit", _noop)
    monkeypatch.setattr(svc_mod.secret_store, "get", lambda name: "1234" if name == "phone-pin" else None)
    return s, said


async def test_pin_is_not_in_transcript_or_event_bus_and_message_follows_pin(
    svc: tuple[TelephonyService, list[str]],
) -> None:
    s, said = svc
    st = CallState(uuid.uuid4(), "outbound", "+4900", "+4911", purpose="Die Rechnung wurde bezahlt")
    q: asyncio.Queue[np.ndarray] = asyncio.Queue()
    seen: list[Any] = []
    bus.subscribe("telephony.transcript", lambda ev: _collect(seen, ev))
    await q.put(np.ones(1600, dtype=np.int16))  # the PIN, spoken
    await q.put(np.ones(1600, dtype=np.int16))  # "tschüss"
    await asyncio.wait_for(s._conversation(None, st, q), 5)  # type: ignore[arg-type]

    assert st.authenticated
    everything = " ".join(str(t["text"]) for t in st.transcript) + " " + " ".join(str(e) for e in seen)
    assert "eins zwei drei vier" not in everything
    prompt = next(i for i, line in enumerate(said) if "PIN" in line)
    assert not any("Rechnung" in line for line in said[: prompt + 1])  # only "Hier ist Jarvis" before the PIN
    assert said.index("Die Rechnung wurde bezahlt") > prompt  # delivered after verification


async def _collect(seen: list[Any], ev: Any) -> None:
    seen.append(ev.data)
