"""Selectable personalities (Jarvis / René Redo): prompt, persistence, voice routing, switch without restart."""

from __future__ import annotations

import io
import json
import wave
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agent.persona import (
    ack_phrase,
    call_opening,
    error_phrase,
    never_say,
    render_soul,
    turn_instructions,
)
from jarvis.agent.personas import PersonaStore
from jarvis.api import routes
from jarvis.app_state import AppState
from jarvis.config import CONFIG_DIR, Personality, TtsCfg, load_personalities, load_personality
from jarvis.events import Event, bus
from jarvis.mobile.gateway import mobile_path_allowed
from jarvis.platform import MacAdapter
from jarvis.security.auth import Principal
from jarvis.voice.speech import LocalTTS, MacSayTTS, TTSService

EVENING = datetime(2026, 10, 28, 20, 0)


def _old_render_soul(p: Personality) -> str:
    """render_soul as it was before personas existed – Jarvis must stay word for word the same."""
    from jarvis.agent.persona import OPERATING_RULES, SPOKEN_NAME_RULE

    traits = "\n".join(f"- {t}" for t in p.traits)
    rules = "\n".join(f"- {r}" for r in [*p.style_rules, SPOKEN_NAME_RULE])
    humor = "\n".join(f'- "{h}"' for h in p.humor_examples)
    return f"""# {p.name}

Du bist {p.name}, der persönliche KI-Assistent deines Besitzers, den du mit "{p.owner_title}" ansprichst \
– nach dem Vorbild des Assistenten aus Iron Man. Du läufst lokal auf seinem Mac.

## Persönlichkeit
{traits}

## Stil
{rules}

Beispiele für den gewünschten Ton (als Anregung, nicht wörtlich wiederholen):
{humor}

{OPERATING_RULES}"""


def _personas() -> dict[str, Personality]:
    return {p.id: p for p in load_personalities()}


# ---------------------------------------------------------------- prompt
def test_jarvis_prompt_and_phrases_are_unchanged() -> None:
    jarvis = load_personality()
    assert jarvis.id == "jarvis" and jarvis.voice == "jarvis"
    assert render_soul(jarvis) == _old_render_soul(jarvis)
    assert "z. B. „Sehr wohl, Sir. Ich kümmere mich darum.“" in turn_instructions("voice", now=EVENING)
    assert call_opening(jarvis, now=EVENING) == "Guten Abend, Sir. Ich höre."
    assert error_phrase(jarvis) == "Das hat leider nicht funktioniert, Sir."
    assert ack_phrase(jarvis) == "Einen Moment, Sir." and never_say(jarvis) is None


def test_rene_redo_is_a_complete_persona_of_its_own() -> None:
    personas = _personas()
    assert list(personas)[:2] == ["jarvis", "rene_redo"]  # Jarvis stays the default
    rene, jarvis = personas["rene_redo"], personas["jarvis"]
    soul = render_soul(rene)
    assert soul.startswith("# René Redo\n\nDu bist Jarvis")
    for word in ("Alter", "Softkloß", "Geil", "Wahnsinn", "Brutal", "Junge", "Was ist denn hier los?!"):
        assert word in soul
    assert "Gefixt ist geil!" in soul and "Gebastelt ist geil!" in soul and "Geschafft ist geil!" in soul
    assert "Iron Man" not in soul and "britisch" not in soul and "Sehr wohl" not in soul.replace("„Sehr wohl“", "")
    # GIG only appears in the rule that forbids it
    assert [line for line in soul.splitlines() if "GIG" in line] == [
        next(r for r in [f"- {s}" for s in rene.style_rules] if "GIG" in r)
    ]
    # serious topics, third parties and technical quality are covered by the persona itself
    assert "Sicherheitsfragen" in soul and "sachlich und professionell" in soul and "nie Ersatz für Inhalt" in soul
    # same tools, memory, permissions and operating rules as Jarvis
    assert soul.split("## Arbeitsweise", 1)[1] == render_soul(jarvis).split("## Arbeitsweise", 1)[1]
    assert rene.voice == "rene_redo" and rene.sample and rene.wakeup_say and rene.never_say == ["GIG"]
    assert "Sehr wohl" not in turn_instructions("voice", now=EVENING, confirm_example=rene.confirm_example)


def test_rene_redo_speaks_his_own_lines() -> None:
    rene = _personas()["rene_redo"]
    assert call_opening(rene, now=EVENING) == "Abend, Sir! Was liegt an?"  # no butler "Ich höre."
    assert call_opening(rene, "Dein Build ist rot.", now=EVENING) == "Abend, Sir! Was liegt an? Dein Build ist rot."
    assert error_phrase(rene).startswith("Alter") and ack_phrase(rene) == "Sekunde, Alter!"


def test_the_abbreviation_is_never_spoken() -> None:
    rx = never_say(_personas()["rene_redo"])
    assert rx is not None
    assert rx.sub("", "Gefixt ist geil! GIG").strip() == "Gefixt ist geil!"
    assert rx.sub("", "Läuft. #GIG Weiter geht's.") == "Läuft.  Weiter geht's."
    assert rx.sub("", "Ein Gig, gigantisch, GIGABYTE") == "Ein Gig, gigantisch, GIGABYTE"


def test_a_broken_extra_persona_never_stops_the_core(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import jarvis.config as config

    (tmp_path / "personality.yaml").write_text((CONFIG_DIR / "personality.yaml").read_text(encoding="utf-8"))
    (tmp_path / "personalities").mkdir()
    (tmp_path / "personalities" / "a.yaml").write_text("id: zed\nname: Zed\ntraits: [x: y]\n", encoding="utf-8")  # invalid
    (tmp_path / "personalities" / "b.yaml").write_text("id: jarvis\nname: Doppelt\n", encoding="utf-8")  # duplicate id
    (tmp_path / "personalities" / "c.yaml").write_text("id: calm\nname: Calm\nvoice: calm\n", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    assert [p.id for p in config.load_personalities()] == ["jarvis", "calm"]


# ---------------------------------------------------------------- persistence
def test_choice_survives_a_restart_and_unknown_ids_fall_back(tmp_path: Path) -> None:
    path = tmp_path / "persona.json"
    store = PersonaStore(path, load_personalities())
    assert store.active.id == "jarvis"
    store.set("rene_redo")
    assert PersonaStore(path, load_personalities()).active.id == "rene_redo"
    with pytest.raises(KeyError):
        store.set("tony_stark")
    assert store.active.id == "rene_redo"
    path.write_text(json.dumps({"id": "removed_persona"}), encoding="utf-8")
    assert PersonaStore(path, load_personalities()).active.id == "jarvis"
    path.write_text("{kaputt", encoding="utf-8")
    assert PersonaStore(path, load_personalities()).active.id == "jarvis"
    summary = store.to_dict()
    assert summary["active"] == "rene_redo"
    assert [p["label"] for p in summary["personas"]] == ["J.A.R.V.I.S.", "René Redo"]


# ---------------------------------------------------------------- voice
def _tts(tmp_path: Path, *, local_voices: tuple[str, ...] = ("jarvis", "rene_redo")) -> TTSService:
    svc = TTSService(TtsCfg(provider="local"), tmp_path / "cache", adapter=MacAdapter())
    svc.calls = []  # type: ignore[attr-defined]

    def fake(provider: Any, name: str) -> None:
        async def available() -> bool:
            return name in local_voices if name != "fish" else True

        async def stream(text: str, rate: int):
            svc.calls.append(f"{name}:{text}")  # type: ignore[attr-defined]
            yield f"{name}|{text}".encode()

        async def synthesize(text: str, rate: int) -> bytes:
            svc.calls.append(f"{name}:{text}")  # type: ignore[attr-defined]
            return f"{name}|{text}".encode()

        provider.available, provider.stream, provider.synthesize = available, stream, synthesize

    fake(svc.local, "jarvis")
    fake(svc.local_for("rene_redo"), "rene_redo")
    fake(svc.fish, "fish")
    fake(svc.macos, "say")
    svc.set_voice("rene_redo", "Flo (Deutsch (Deutschland))")
    fake(svc.system_for("rene_redo"), "flo")
    svc.set_voice("jarvis")
    return svc


async def _say(svc: TTSService, text: str, voice: str | None = None) -> bytes:
    return b"".join([c async for c in svc.stream(text, 24000, voice)])


async def test_each_persona_speaks_with_its_own_local_voice_and_cache(tmp_path: Path) -> None:
    svc = _tts(tmp_path)
    svc.configure_fish(True, "dce57717f1ad4540ab6002b4deefa3bf")
    assert await _say(svc, "Hallo.") == b"jarvis|Hallo."
    svc.set_voice("rene_redo")
    assert await _say(svc, "Hallo.") == b"rene_redo|Hallo."  # same text, other voice: not the cached Jarvis audio
    assert await _say(svc, "Hallo.", voice="jarvis") == b"jarvis|Hallo."  # a reply started before keeps its voice
    assert svc.calls == ["jarvis:Hallo.", "rene_redo:Hallo."]  # type: ignore[attr-defined]
    assert (await svc.status())["voice"] == "rene_redo"
    assert await svc.live_session(24000) is None  # Fish Audio never speaks for René


async def test_rene_never_falls_back_to_an_external_provider(tmp_path: Path) -> None:
    svc = _tts(tmp_path, local_voices=("jarvis",))  # a voice server started before the René voice existed
    svc.configure_fish(True, "dce57717f1ad4540ab6002b4deefa3bf")
    svc.set_voice("rene_redo", "Flo (Deutsch (Deutschland))")
    p = await svc.provider()
    assert isinstance(p, MacSayTTS) and p.voice == "Flo (Deutsch (Deutschland))"
    assert await _say(svc, "Gefixt ist geil!") == b"flo|Gefixt ist geil!"
    assert not any(c.startswith("fish:") for c in svc.calls)  # type: ignore[attr-defined]


async def test_local_voice_server_reports_its_voices(monkeypatch: pytest.MonkeyPatch) -> None:
    class Resp:
        status_code = 200

        def __init__(self, body: dict[str, Any]) -> None:
            self.body = body

        def json(self) -> dict[str, Any]:
            return self.body

    old = {"ok": True, "voice": "qwen:jarvis_ref_short:abc"}
    new = {**old, "voices": {"jarvis": "qwen:jarvis_ref_short:abc", "rene_redo": "qwen:rene_redo:def"}}
    for body, rene_ok in ((old, False), (new, True)):
        jarvis, rene = LocalTTS("http://x"), LocalTTS("http://x", "rene_redo")
        for c in (jarvis, rene):

            async def get(path: str, timeout: float = 0, body: dict[str, Any] = body) -> Resp:
                return Resp(body)

            monkeypatch.setattr(c._http, "get", get)
        assert await jarvis.available() is True
        assert jarvis.voice == "local:qwen:jarvis_ref_short:abc"  # the phrase cache of the Jarvis voice stays valid
        assert await rene.available() is rene_ok
    assert rene.voice == "local:qwen:rene_redo:def"


# ---------------------------------------------------------------- switch without restart
class _Agent:
    def __init__(self) -> None:
        self.reloads = 0

    async def reload(self) -> None:
        self.reloads += 1


class _State:
    personality = AppState.personality
    set_persona = AppState.set_persona

    def __init__(self, tmp_path: Path) -> None:
        self.personas = PersonaStore(tmp_path / "persona.json", load_personalities())
        self.tts = _tts(tmp_path)
        self.agent = _Agent()
        self.settings = SimpleNamespace(
            hermes=SimpleNamespace(enabled=False), voice=SimpleNamespace(wakeup=SimpleNamespace(say="Zu Diensten."))
        )
        self.wakeup = SimpleNamespace(to_dict=lambda: {"enabled": True, "supported": True})


async def test_switch_applies_prompt_voice_and_sync_event(tmp_path: Path) -> None:
    st = _State(tmp_path)
    seen: list[Event] = []

    async def on(ev: Event) -> None:
        seen.append(ev)

    bus.subscribe("persona.changed", on)
    try:
        await st.set_persona("rene_redo")  # type: ignore[arg-type]
    finally:
        bus.unsubscribe("persona.changed", on)
    assert st.personality.id == "rene_redo" and st.tts.voice == "rene_redo"
    assert st.agent.reloads == 1  # idle Claude processes get the new system prompt now
    assert seen and seen[0].data["active"] == "rene_redo"
    assert json.loads((tmp_path / "persona.json").read_text()) == {"id": "rene_redo"}


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, _State]:
    async def no_audit(*a: Any, **kw: Any) -> None:
        pass

    monkeypatch.setattr(routes, "audit", no_audit)
    state = _State(tmp_path)
    app = FastAPI()
    app.include_router(routes.router)
    app.state.jarvis = state
    owner = Principal(id="desktop", role="owner", channel="desktop")
    for dep in (routes.Actor, routes.Reader, routes.Owner):
        app.dependency_overrides[dep.dependency] = lambda: owner  # type: ignore[attr-defined]
    return TestClient(app), state


def test_persona_api_switches_and_previews(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c, st = _client(tmp_path, monkeypatch)
    r = c.get("/api/persona").json()
    assert r["active"] == "jarvis" and {p["id"]: p["voice_ready"] for p in r["personas"]} == {
        "jarvis": True,
        "rene_redo": True,
    }
    assert c.get("/api/voice/wakeup").json()["say"] == "Zu Diensten."
    assert c.post("/api/persona", json={"id": "rene_redo"}).json()["active"] == "rene_redo"
    assert st.tts.voice == "rene_redo"
    assert c.get("/api/voice/wakeup").json()["say"].startswith("Alter, ich bin wach!")
    assert c.post("/api/persona", json={"id": "nope"}).status_code == 404

    preview = c.post("/api/persona/preview", json={"id": "jarvis"})  # a Hörprobe never switches anything
    assert preview.status_code == 200 and preview.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(preview.content)) as w:
        assert w.getframerate() == 24000
        assert w.readframes(w.getnframes()).startswith(b"jarvis|Guten")  # Jarvis' own line in the Jarvis voice
    rene = c.post("/api/persona/preview", json={}).content  # the active persona
    assert b"rene_redo|Alter, was ist denn hier los?!" in rene
    assert st.personality.id == "rene_redo"


def test_phone_may_switch_and_listen() -> None:
    assert mobile_path_allowed("GET", "/api/persona") and mobile_path_allowed("POST", "/api/persona")
    assert mobile_path_allowed("POST", "/api/persona/preview")
