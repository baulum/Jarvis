import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from jarvis import platform as platform_mod
from jarvis.config import WakeupCfg
from jarvis.platform import MacAdapter
from jarvis.voice import wakeup
from jarvis.voice.wakeup import (
    WakeupStore,
    app_command,
    check_sound,
    open_apps,
    play_sound,
    summary,
)

MP3_ID3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 64
MP3_FRAME = b"\xff\xfb\x90\x64" + b"\x00" * 64


def mp3(tmp_path: Path, name: str = "wecker.mp3", data: bytes = MP3_ID3) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


# ------------------------------------------------------------------ settings
def test_defaults_are_on_without_sound_and_programs(tmp_path: Path) -> None:
    store = WakeupStore(tmp_path / "wakeup.json")
    s = store.settings
    assert (s.enabled, s.sound, s.volume, s.apps) == (True, "", 1.0, [])
    d = store.to_dict()
    assert d["sound_error"] is None and isinstance(d["supported"], bool)
    assert not (tmp_path / "wakeup.json").exists()  # nothing written until Sir changes something


def test_yaml_values_are_the_start_values(tmp_path: Path) -> None:
    store = WakeupStore(tmp_path / "wakeup.json", WakeupCfg(apps=["Safari"], volume=0.5))
    assert store.settings.apps == ["Safari"] and store.settings.volume == 0.5


def test_update_persists_and_reloads(tmp_path: Path) -> None:
    sound = mp3(tmp_path)
    path = tmp_path / "data" / "wakeup.json"
    store = WakeupStore(path)
    store.update({"sound": str(sound), "volume": 0.4, "apps": [" Safari ", "Mail", "Safari", ""], "enabled": False})
    assert json.loads(path.read_text())["apps"] == ["Safari", "Mail"]  # trimmed, de-duplicated, order kept
    again = WakeupStore(path, WakeupCfg(apps=["from yaml"]))
    assert again.settings.to_dict() == {
        "enabled": False,
        "sound": str(sound),
        "volume": 0.4,
        "apps": ["Safari", "Mail"],
    }


def test_reordering_and_removing_programs(tmp_path: Path) -> None:
    store = WakeupStore(tmp_path / "wakeup.json")
    store.update({"apps": ["A", "B", "C"]})
    store.update({"apps": ["C", "A"]})
    assert WakeupStore(tmp_path / "wakeup.json").settings.apps == ["C", "A"]


def test_partial_update_keeps_the_rest(tmp_path: Path) -> None:
    store = WakeupStore(tmp_path / "wakeup.json")
    store.update({"apps": ["Safari"]})
    store.update({"volume": 0.2})
    assert store.settings.apps == ["Safari"] and store.settings.volume == 0.2


@pytest.mark.parametrize(
    "patch, message",
    [
        ({"volume": 1.5}, "Lautstärke"),
        ({"volume": True}, "Lautstärke"),
        ({"enabled": "ja"}, "enabled"),
        ({"apps": "Safari"}, "apps"),
        ({"apps": [f"App {i}" for i in range(wakeup.MAX_APPS + 1)]}, "Höchstens"),
        ({"sound": "/nirgends/wecker.mp3"}, "nicht gefunden"),
    ],
)
def test_invalid_updates_are_rejected_and_not_saved(tmp_path: Path, patch: dict[str, Any], message: str) -> None:
    store = WakeupStore(tmp_path / "wakeup.json")
    with pytest.raises(ValueError, match=message):
        store.update(patch)
    assert store.settings.to_dict() == WakeupStore(tmp_path / "x.json").settings.to_dict()
    assert not (tmp_path / "wakeup.json").exists()


def test_removing_the_sound(tmp_path: Path) -> None:
    store = WakeupStore(tmp_path / "wakeup.json")
    store.update({"sound": str(mp3(tmp_path))})
    store.update({"sound": ""})
    assert store.settings.sound == "" and store.to_dict()["sound_error"] is None


def test_sound_deleted_later_is_reported_not_hidden(tmp_path: Path) -> None:
    sound = mp3(tmp_path)
    store = WakeupStore(tmp_path / "wakeup.json")
    store.update({"sound": str(sound)})
    sound.unlink()
    again = WakeupStore(tmp_path / "wakeup.json")
    assert again.settings.sound == str(sound)
    assert "nicht gefunden" in again.to_dict()["sound_error"]
    again.update({"volume": 0.3})  # other changes still work while the file is missing
    assert again.settings.volume == 0.3


@pytest.mark.parametrize("content", ["{kaputt", "[1, 2]", '{"volume": 9}'])
def test_broken_file_falls_back_to_defaults(tmp_path: Path, content: str) -> None:
    (tmp_path / "wakeup.json").write_text(content)
    assert WakeupStore(tmp_path / "wakeup.json").settings.to_dict() == {
        "enabled": True,
        "sound": "",
        "volume": 1.0,
        "apps": [],
    }


# ------------------------------------------------------------------ sound
def test_valid_mp3_files(tmp_path: Path) -> None:
    assert check_sound(str(mp3(tmp_path, "a.mp3", MP3_ID3))) is None
    assert check_sound(str(mp3(tmp_path, "b.MP3", MP3_FRAME))) is None


@pytest.mark.parametrize(
    "name, data, message",
    [
        ("leer.mp3", b"", "leer"),
        ("text.mp3", b"hallo welt", "Keine gültige MP3"),
        ("ton.wav", b"RIFF....WAVE", "Keine MP3-Datei"),
    ],
)
def test_invalid_mp3_files(tmp_path: Path, name: str, data: bytes, message: str) -> None:
    assert message in (check_sound(str(mp3(tmp_path, name, data))) or "")


def test_missing_mp3_and_directory(tmp_path: Path) -> None:
    assert "nicht gefunden" in (check_sound(str(tmp_path / "fehlt.mp3")) or "")
    assert "Keine Datei" in (check_sound(str(tmp_path)) or "")
    assert check_sound("") == "Keine MP3-Datei festgelegt."


@pytest.fixture
def mac_player(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wake-up sound is played with afplay on the Mac; elsewhere ``play_sound`` asks the platform adapter. The
    Mac adapter is injected so the same afplay command is checked on every OS the tests run on."""
    monkeypatch.setattr(platform_mod, "_current", MacAdapter(which=lambda name: f"/usr/bin/{name}"))


# Programs are macOS app bundles started with ``open -a``; on Windows the wake-up routine does not exist
# (WindowsAdapter: voice.wakeup_routine unavailable) and paths have no "/" to tell them from app names.
macos_apps = pytest.mark.skipif(sys.platform == "win32", reason="wake-up programs are macOS .app bundles (open -a)")


class FakeProc:
    def __init__(self, code: int | None, stderr: bytes = b"") -> None:
        self.code = code
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()

    async def wait(self) -> int:
        if self.code is None:  # still playing
            await asyncio.sleep(3600)
        return self.code or 0


async def test_missing_mp3_is_reported_without_starting_afplay(tmp_path: Path) -> None:
    calls: list[tuple[Any, ...]] = []

    async def spawn(*args: Any, **kw: Any) -> FakeProc:
        calls.append(args)
        return FakeProc(0)

    error = await play_sound(str(tmp_path / "fehlt.mp3"), 1.0, max_wait_s=1, spawn=spawn)
    assert error is not None and "nicht gefunden" in error
    assert calls == []


@pytest.mark.usefixtures("mac_player")
async def test_play_sound_uses_afplay_with_volume(tmp_path: Path) -> None:
    calls: list[tuple[Any, ...]] = []

    async def spawn(*args: Any, **kw: Any) -> FakeProc:
        calls.append(args)
        return FakeProc(0)

    sound = mp3(tmp_path)
    assert await play_sound(str(sound), 0.35, max_wait_s=1, spawn=spawn) is None
    assert calls == [(wakeup.AFPLAY, "-v", "0.35", str(sound))]


@pytest.mark.usefixtures("mac_player")
async def test_afplay_rejecting_the_file_is_an_error(tmp_path: Path) -> None:
    async def spawn(*args: Any, **kw: Any) -> FakeProc:
        return FakeProc(1, b"AudioFileOpen failed ('dta?')")

    error = await play_sound(str(mp3(tmp_path)), 1.0, max_wait_s=1, spawn=spawn)
    assert error is not None and "konnte nicht abgespielt werden" in error and "AudioFileOpen" in error


@pytest.mark.usefixtures("mac_player")
async def test_long_sound_keeps_playing_after_max_wait(tmp_path: Path) -> None:
    async def spawn(*args: Any, **kw: Any) -> FakeProc:
        return FakeProc(None)

    assert await play_sound(str(mp3(tmp_path)), 1.0, max_wait_s=0.05, spawn=spawn) is None
    for t in list(wakeup._bg):
        t.cancel()


# ------------------------------------------------------------------ programs
@macos_apps
def test_app_commands(tmp_path: Path) -> None:
    app = tmp_path / "Demo.app"
    app.mkdir()
    assert app_command("Safari") == (["/usr/bin/open", "-a", "Safari"], None)
    assert app_command("Visual Studio Code") == (["/usr/bin/open", "-a", "Visual Studio Code"], None)
    assert app_command("Safari.app") == (["/usr/bin/open", "-a", "Safari"], None)
    assert app_command(str(app)) == (["/usr/bin/open", "-a", str(app)], None)
    assert app_command("com.apple.Safari") == (["/usr/bin/open", "-b", "com.apple.Safari"], None)
    assert app_command("https://github.com") == (["/usr/bin/open", "https://github.com"], None)
    assert app_command(str(tmp_path / "Fehlt.app")) == (None, "Programm nicht gefunden")
    assert app_command("relativ/Demo.app")[0] is None
    (tmp_path / "notiz.txt").write_text("x")
    assert app_command(str(tmp_path / "notiz.txt")) == (None, "kein Programm (.app)")


@macos_apps
async def test_missing_programs_are_skipped_and_the_rest_still_start(tmp_path: Path) -> None:
    ran: list[list[str]] = []

    async def runner(cmd: list[str]) -> tuple[int, str]:
        ran.append(cmd)
        if cmd[-1] == "Gibtsnicht":
            return 1, "Unable to find application named 'Gibtsnicht'"
        if cmd[-1] == "Kaputt":
            return 1, "LSOpenURLsWithRole() failed with error -10810"
        return 0, ""

    opened, failed = await open_apps(
        ["Safari", "Gibtsnicht", str(tmp_path / "Fehlt.app"), "Kaputt", "Mail"], runner=runner
    )
    assert opened == ["Safari", "Mail"]
    assert failed == [
        {"app": "Gibtsnicht", "reason": "Programm nicht gefunden"},
        {"app": str(tmp_path / "Fehlt.app"), "reason": "Programm nicht gefunden"},
        {"app": "Kaputt", "reason": "Start fehlgeschlagen (LSOpenURLsWithRole() failed with error -10810)"},
    ]
    assert [c[-1] for c in ran] == ["Safari", "Gibtsnicht", "Kaputt", "Mail"]  # the missing path never ran


async def test_empty_program_list_does_nothing() -> None:
    async def runner(cmd: list[str]) -> tuple[int, str]:
        raise AssertionError("must not run")

    assert await open_apps([], runner=runner) == ([], [])


def test_spoken_summary() -> None:
    assert summary({"failed": [], "sound_error": None}) == ""
    one = summary({"failed": [{"app": "/Applications/Foo.app", "reason": "x"}]})
    assert "Foo konnte ich nicht öffnen" in one
    both = summary({"sound_error": "fehlt", "failed": [{"app": "A"}, {"app": "B"}]})
    assert "Weckmusik" in both and "2 Programme" in both and "A, B" in both
