from __future__ import annotations

from pathlib import Path

from jarvis.config import TtsCfg
from jarvis.voice.speech import TTSService


def _service(tmp_path: Path, provider: str = "local", local_up: bool = True, fish_up: bool = True) -> TTSService:
    svc = TTSService(TtsCfg(provider=provider), tmp_path / "cache")
    calls: list[str] = []
    svc.calls = calls  # type: ignore[attr-defined]

    def fake(name: str, up: bool):
        async def available() -> bool:
            return up

        async def stream(text: str, rate: int):
            calls.append(f"{name}:{text}")
            yield f"{name}|".encode()
            yield f"{text}|".encode()

        return available, stream

    svc.local.available, svc.local.stream = fake("local", local_up)  # type: ignore[method-assign]
    svc.fish.available, svc.fish.stream = fake("fish", fish_up)  # type: ignore[method-assign]
    svc.macos.available, svc.macos.stream = fake("macos", True)  # type: ignore[method-assign]
    return svc


async def _say(svc: TTSService, text: str) -> bytes:
    return b"".join([c async for c in svc.stream(text, 24000)])


async def test_local_wins_over_active_fish_connector(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    svc.configure_fish(True, "ref")
    assert (await svc.provider()) is svc.local
    assert (await svc.status())["fallback"] is False


async def test_local_down_falls_back_to_fish_then_macos(tmp_path: Path) -> None:
    svc = _service(tmp_path, local_up=False)
    svc.configure_fish(True, "ref")
    assert (await svc.provider()) is svc.fish
    assert (await svc.status())["fallback"] is True
    svc.configure_fish(False)
    assert (await svc.provider()) is svc.macos


async def test_short_phrases_are_synthesised_once_and_survive_restart(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    first = await _say(svc, "Sehr wohl, Sir.")
    assert await _say(svc, "Sehr wohl, Sir.") == first
    assert svc.calls == ["local:Sehr wohl, Sir."]  # type: ignore[attr-defined]
    again = _service(tmp_path)
    assert await _say(again, "Sehr wohl, Sir.") == first
    assert again.calls == []  # type: ignore[attr-defined]


async def test_long_text_and_macos_are_not_cached(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    long = "Ein sehr langer Satz, " * 10
    await _say(svc, long)
    await _say(svc, long)
    assert svc.calls.count(f"local:{long}") == 2  # type: ignore[attr-defined]
    off = _service(tmp_path / "off", provider="macos")
    await _say(off, "Sehr wohl, Sir.")
    await _say(off, "Sehr wohl, Sir.")
    assert off.calls == ["macos:Sehr wohl, Sir."] * 2  # type: ignore[attr-defined]


async def test_cache_is_per_voice(tmp_path: Path) -> None:
    svc = _service(tmp_path, provider="fish_audio")
    svc.configure_fish(True, "voice-a")
    await _say(svc, "Guten Morgen, Sir.")
    svc.configure_fish(True, "voice-b")
    await _say(svc, "Guten Morgen, Sir.")
    assert svc.calls == ["fish:Guten Morgen, Sir."] * 2  # type: ignore[attr-defined]


async def test_interrupted_stream_is_not_cached(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    gen = svc.stream("Einen Moment, Sir.", 24000)
    await gen.__anext__()
    await gen.aclose()  # barge-in after the first chunk
    await _say(svc, "Einen Moment, Sir.")
    assert len(svc.calls) == 2  # type: ignore[attr-defined]
