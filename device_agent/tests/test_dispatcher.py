"""Dispatcher: every protocol action is handled, capabilities gate with reasons, kill switch, coordinates."""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest
from agent_fakes import FakeBackend

from jarvis_device.dispatcher import CommandError
from jarvis_device.protocol import ACTIONS, READ_ONLY_ACTIONS, capabilities


def test_every_action_has_a_handler_and_every_capability_is_reported(make_dispatcher):
    d = make_dispatcher()
    assert set(d.handlers) == set(ACTIONS)
    caps = d.capabilities()
    assert set(caps) >= set(capabilities())
    for st in caps.values():
        assert st["status"] in ("available", "degraded", "unavailable")


async def test_unavailable_capability_answers_with_the_reason(make_dispatcher):
    backend = FakeBackend(caps={"input.mouse": ("unavailable", "Wayland erlaubt keine Maussteuerung")})
    d = make_dispatcher(backend)
    with pytest.raises(CommandError) as e:
        await d.run("input.click", {"x": 1, "y": 2})
    assert e.value.code == "unavailable"
    assert "Wayland erlaubt keine Maussteuerung" in e.value.message
    assert backend.calls == []


async def test_capabilities_missing_from_the_backend_count_as_unavailable(make_dispatcher):
    d = make_dispatcher(FakeBackend(caps={}))
    caps = d.capabilities()
    assert caps["screen.capture"]["status"] == "unavailable"
    assert caps["system.info"]["status"] == "available"  # platform-neutral, from psutil


async def test_kill_switch_allows_only_read_actions(make_dispatcher, tmp_path, root):
    d = make_dispatcher()
    d.kill.remote = True
    with pytest.raises(CommandError) as e:
        await d.run("input.type", {"text": "hallo"})
    assert e.value.code == "kill_switch"
    (root / "a.txt").write_text("x")
    assert "files.list" in READ_ONLY_ACTIONS
    listing = await d.run("files.list", {"path": str(root)})
    assert [x["name"] for x in listing["entries"]] == ["a.txt"]
    d.kill.remote = False
    d.kill.engage_local()
    with pytest.raises(CommandError):
        await d.run("notify", {"title": "t", "message": "m"})


async def test_screenshot_is_scaled_and_clicks_map_back_to_screen_pixels(make_dispatcher):
    backend = FakeBackend()
    d = make_dispatcher(backend)
    shot = await d.run("screen.capture", {"max_width": 1600})
    assert (shot["width"], shot["height"], shot["scale"]) == (1600, 900, 2.0)
    assert base64.b64decode(shot["png_base64"]).startswith(b"\x89PNG")
    await d.run("input.click", {"x": 100, "y": 40})
    # 100 px in the screenshot = 200 screen px, plus the monitor's origin (100, 50)
    assert backend.calls[-1] == ("click", 300, 130, "left", 1)
    await d.run("input.scroll", {"dy": 3, "x": 10, "y": 10})
    assert backend.calls[-2:] == [("move", 120, 70), ("scroll", 0, 3)]


async def test_input_with_app_activates_and_verifies_the_target(make_dispatcher):
    backend = FakeBackend()
    d = make_dispatcher(backend)
    await d.run("input.type", {"text": "1+1", "app": "Rechner"})
    assert backend.calls == [("open_app", "Rechner"), ("type", "1+1")]

    class Stubborn(FakeBackend):
        async def open_app(self, name):
            self.calls.append(("open_app", name))
            return {}

        async def frontmost(self):
            return {"app": "explorer", "title": "Desktop"}

    stubborn = Stubborn()
    d2 = make_dispatcher(stubborn)
    with pytest.raises(CommandError) as e:
        await d2.run("input.key", {"key": "s", "modifiers": ["ctrl"], "app": "Editor"})
    assert "Vordergrund" in e.value.message
    assert not any(c[0] == "key" for c in stubborn.calls)  # no keystroke into the wrong window


async def test_argument_validation(make_dispatcher):
    d = make_dispatcher()
    for action, args, needle in [
        ("input.click", {"x": "a", "y": 1}, "Zahl"),
        ("input.click", {"x": 1, "y": 1, "button": "kaputt"}, "button"),
        ("apps.quit", {}, "name oder pid"),
        ("audio.volume", {"op": "set", "level": 150}, "0–100"),
        ("browser.open", {"url": "file:///etc/passwd"}, "http"),
        ("input.type", {"text": "x" * 5001}, "zu lang"),
    ]:
        with pytest.raises(CommandError) as e:
            await d.run(action, args)
        assert e.value.code == "invalid", action
        assert needle in e.value.message, action
    with pytest.raises(CommandError) as e:
        await d.run("does.not.exist", {})
    assert e.value.code == "unknown_action"


async def test_files_stay_inside_the_roots(make_dispatcher, root, tmp_path):
    d = make_dispatcher()
    out = await d.run("files.write", {"path": str(root / "sub" / "notiz.txt"), "content": "Hallo Sir"})
    assert Path(out["path"]).read_text() == "Hallo Sir"
    with pytest.raises(CommandError) as e:
        await d.run("files.write", {"path": str(root / "sub" / "notiz.txt"), "content": "x"})
    assert e.value.code == "invalid" and "existiert" in e.value.message
    read = await d.run("files.read", {"path": str(root / "sub" / "notiz.txt")})
    assert read["text"] == "Hallo Sir" and not read["binary"]
    found = await d.run("files.search", {"query": "notiz"})
    assert [Path(r["path"]).name for r in found["results"]] == ["notiz.txt"]
    moved = await d.run("files.move", {"from_path": str(root / "sub" / "notiz.txt"), "to_path": str(root / "n2.txt")})
    assert Path(moved["to"]).exists()
    outside = tmp_path / "outside.txt"
    outside.write_text("geheim")
    for action, args in [
        ("files.read", {"path": str(outside)}),
        ("files.write", {"path": str(outside), "content": "x", "overwrite": True}),
        ("files.move", {"from_path": str(root / "n2.txt"), "to_path": str(outside.with_name("x.txt"))}),
    ]:
        with pytest.raises(CommandError) as e:
            await d.run(action, args)
        assert e.value.code == "denied", action


async def test_shell_runs_without_shell_and_respects_cwd_and_timeout(make_dispatcher, root, tmp_path):
    d = make_dispatcher()
    res = await d.run("shell.run", {"argv": [sys.executable, "-c", "import os; print(os.getcwd())"], "cwd": str(root)})
    assert res["exit_code"] == 0 and Path(res["stdout"].strip()).resolve() == root.resolve()
    with pytest.raises(CommandError) as e:
        await d.run("shell.run", {"argv": [sys.executable, "-c", "print(1)"], "cwd": str(tmp_path)})
    assert e.value.code == "denied"
    slow = await d.run(
        "shell.run", {"argv": [sys.executable, "-c", "import time; time.sleep(30)"], "cwd": str(root), "timeout_s": 1}
    )
    assert slow["timed_out"] is True


async def test_terminal_can_be_switched_off_on_the_device(make_dispatcher, root):
    d = make_dispatcher(terminal="off")
    with pytest.raises(CommandError) as e:
        await d.run("shell.run", {"argv": ["echo", "x"], "cwd": str(root)})
    assert e.value.code == "unavailable" and "abgeschaltet" in e.value.message


async def test_claude_code_is_unavailable_without_the_cli(make_dispatcher, root):
    d = make_dispatcher(claude_binary="definitely-not-installed-claude")
    caps = d.capabilities()
    assert caps["claude_code"]["status"] == "unavailable"
    with pytest.raises(CommandError) as e:
        await d.run("claude.run", {"prompt": "Hallo", "cwd": str(root)})
    assert e.value.code == "unavailable" and "nicht installiert" in e.value.message


async def test_audio_and_notifications_reach_the_backend(make_dispatcher):
    backend = FakeBackend()
    d = make_dispatcher(backend)
    assert await d.run("audio.volume", {"op": "set", "level": 40}) == {"level": 40, "muted": False}
    await d.run("notify", {"title": "Jarvis", "message": "Build fertig"})
    assert ("notify", "Jarvis", "Build fertig") in backend.calls
    assert (await d.run("clipboard.get", {}))["text"] == "aus der Zwischenablage"


async def test_system_info_reports_platform_and_roots(make_dispatcher, root):
    info = await make_dispatcher().run("system.info", {})
    assert info["platform"] == "fake"
    assert info["allowed_roots"] == [str(root.resolve())]
    assert info["memory"]["total"] > 0
