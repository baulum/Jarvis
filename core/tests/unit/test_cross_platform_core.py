"""Former crash points off the Mac: system voice, audio playback, image scaling, Finder reveal, secrets, shell,
process groups. Each runs on any host by injecting the platform."""

from __future__ import annotations

import base64
import io
import os
import signal
import struct
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from keyring.errors import NoKeyringError, PasswordDeleteError

from jarvis.agent import attachments as att
from jarvis.api import routes
from jarvis.config import TtsCfg, load_permissions
from jarvis.connectors import generic
from jarvis.platform import LinuxAdapter, MacAdapter, WindowsAdapter, set_adapter
from jarvis.platform.procs import terminate_group
from jarvis.secrets import SecretStore, SecretStoreUnavailable
from jarvis.security.auth import Principal
from jarvis.voice import audio_io, speech
from jarvis.voice.speech import (
    EspeakTTS,
    MacSayTTS,
    SilentTTS,
    TTSService,
    WindowsSapiTTS,
    sapi_rate,
    system_tts,
    wav_pcm,
)

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture(autouse=True)
def _reset_adapter():
    yield
    set_adapter(None)


def _wav(rate: int, samples: list[int], channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buf.getvalue()


# --------------------------------------------------------------------------- voice
def test_system_voice_per_platform() -> None:
    cfg = TtsCfg()
    assert isinstance(system_tts(cfg, MacAdapter(which=lambda n: None)), MacSayTTS)
    assert isinstance(system_tts(cfg, WindowsAdapter(which=lambda n: None)), WindowsSapiTTS)
    linux = LinuxAdapter(which=lambda n: None, environ={})
    assert isinstance(system_tts(cfg, linux), (EspeakTTS, SilentTTS))


def test_tts_service_on_the_mac_keeps_say_as_last_stage(tmp_path: Path) -> None:
    svc = TTSService(TtsCfg(provider="macos"), tmp_path, adapter=MacAdapter(which=lambda n: None))
    assert svc.system is svc.macos and svc.wanted == "macos"


async def test_tts_service_without_any_voice_answers_silently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(speech.shutil, "which", lambda n: None)
    svc = TTSService(TtsCfg(provider="system"), tmp_path, adapter=LinuxAdapter(which=lambda n: None, environ={}))
    assert isinstance(svc.system, SilentTTS)
    assert await svc.provider() is svc.system
    assert await svc.synthesize("Guten Morgen, Sir.", 24000) == b""  # text-only reply, no FileNotFoundError


def test_wav_parsing_tolerates_streamed_headers() -> None:
    wav = _wav(22050, [1, -2, 3, -4])
    assert wav_pcm(wav) == (22050, 1, struct.pack("<4h", 1, -2, 3, -4))
    streamed = bytearray(wav)
    data_at = streamed.index(b"data")
    streamed[data_at + 4 : data_at + 8] = b"\xff\xff\xff\x7f"  # espeak-ng --stdout writes a bogus size
    streamed[4:8] = b"\xff\xff\xff\x7f"
    assert wav_pcm(bytes(streamed))[2] == struct.pack("<4h", 1, -2, 3, -4)
    with pytest.raises(ValueError):
        wav_pcm(b"not a wav")


def test_sapi_command_keeps_text_out_of_the_script() -> None:
    sapi = WindowsSapiTTS("Anna", 185, powershell="powershell.exe")
    assert sapi.voice == ""  # the macOS default voice name is not a SAPI voice
    argv = sapi.command()
    assert argv[:4] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand"]
    script = base64.b64decode(argv[4]).decode("utf-16-le")
    assert "[Console]::In.ReadToEnd()" in script and "$env:JARVIS_TTS_OUT" in script
    assert [sapi_rate(r) for r in (90, 185, 400)] == [-5, 0, 10]


async def test_espeak_output_is_converted_to_mono_pcm(monkeypatch: pytest.MonkeyPatch) -> None:
    wav = _wav(22050, [100, 100, 200, 200], channels=2)

    class Proc:
        returncode = 0

        async def communicate(self, data: bytes) -> tuple[bytes, bytes]:
            assert data == b"Hallo"
            return wav, b""

    seen: list[tuple[str, ...]] = []

    async def fake_exec(*argv: str, **kw: Any) -> Proc:
        seen.append(argv)
        return Proc()

    monkeypatch.setattr(speech.shutil, "which", lambda n: "/usr/bin/espeak-ng" if n == "espeak-ng" else None)
    monkeypatch.setattr(speech.asyncio, "create_subprocess_exec", fake_exec)
    pcm = await EspeakTTS("Anna", 185).synthesize("Hallo", 22050)
    assert seen[0] == ("/usr/bin/espeak-ng", "-v", "de", "-s", "185", "--stdout", "--stdin")
    assert struct.unpack("<2h", pcm) == (100, 200)


async def test_audio_fallback_off_the_mac_uses_sounddevice(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    started: list[str] = []

    async def start(self: Any, on_mic: Any) -> None:
        started.append(type(self).__name__)

    monkeypatch.setattr(audio_io.FallbackAudio, "start", start)
    monkeypatch.setattr(audio_io.sys, "platform", "win32")
    a = await audio_io.open_audio(tmp_path, lambda pcm: None, native=False)
    assert isinstance(a, audio_io.SounddeviceAudio)
    monkeypatch.setattr(audio_io.sys, "platform", "darwin")
    b = await audio_io.open_audio(tmp_path, lambda pcm: None, native=False)
    assert type(b) is audio_io.FallbackAudio  # the Mac keeps afplay
    assert started == ["SounddeviceAudio", "FallbackAudio"]


# --------------------------------------------------------------------------- images, reveal
async def test_large_images_are_scaled_with_pillow_without_sips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image

    path = tmp_path / "gross.png"
    Image.new("RGB", (8200, 1000), (200, 10, 10)).save(path)  # longer than the API allows
    monkeypatch.setattr(att.shutil, "which", lambda n: None)
    info = SimpleNamespace(path=path, type=SimpleNamespace(mime="image/png"))
    mime, data = await att.image_for_model(info)  # type: ignore[arg-type,misc]
    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(data)) as img:
        assert max(img.size) == att.SCALED_IMAGE_EDGE


def test_reveal_uses_the_adapter_off_the_mac(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, ...]] = []

    class Proc:
        async def wait(self) -> int:
            return 1  # explorer.exe answers 1 although it worked

    async def fake_exec(*argv: str, **kw: Any) -> Proc:
        seen.append(argv)
        return Proc()

    async def no_audit(*a: Any, **kw: Any) -> None:
        pass

    monkeypatch.setattr(routes, "audit", no_audit)
    monkeypatch.setattr(routes.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(routes.sys, "platform", "win32")
    set_adapter(WindowsAdapter(which=lambda n: "C:\\Windows\\explorer.exe" if n == "explorer.exe" else None))
    app = FastAPI()
    app.include_router(routes.router)
    app.state.jarvis = SimpleNamespace(settings=SimpleNamespace(paths=SimpleNamespace(data_dir=tmp_path)))
    owner = Principal(id="desktop", role="owner", channel="desktop")
    for dep in (routes.Actor, routes.Reader, routes.Owner):
        app.dependency_overrides[dep.dependency] = lambda: owner  # type: ignore[attr-defined]
    c = TestClient(app)
    stored = c.post("/api/attachments", params={"name": "bericht.pdf"}, content=PDF).json()["file"]
    f = tmp_path / "chat-attachments" / stored
    assert c.post(f"/api/media/attachments/{f.name}/reveal").status_code == 200
    assert seen == [("C:\\Windows\\explorer.exe", f"/select,{f.resolve()}")]
    set_adapter(LinuxAdapter(which=lambda n: None, environ={}, headless=True))
    monkeypatch.setattr(routes.sys, "platform", "linux")
    r = c.post(f"/api/media/attachments/{f.name}/reveal")
    assert r.status_code == 501 and "Dateimanager" in r.json()["detail"] and "headless" in r.json()["detail"]


# --------------------------------------------------------------------------- secrets
class _FakeKeyring:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, user: str) -> str | None:
        return self.data.get((service, user))

    def set_password(self, service: str, user: str, value: str) -> None:
        if len(value) > 1280:
            raise AssertionError("Credential Manager limit exceeded")
        self.data[(service, user)] = value

    def delete_password(self, service: str, user: str) -> None:
        if (service, user) not in self.data:
            raise PasswordDeleteError(user)
        del self.data[(service, user)]


def test_long_secrets_are_chunked_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    kr = _FakeKeyring()
    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(f"jarvis.secrets.keyring.{name}", getattr(kr, name))
    store = SecretStore("jarvis-test", chunked=True)
    big = '{"type": "service_account", "private_key": "' + "k" * 3000 + '"}'
    store.set("fcm-service-account", big)
    assert store.get("fcm-service-account") == big
    assert len([k for k in kr.data if k[1].startswith("fcm-service-account::")]) == 3
    store.set("fcm-service-account", "kurz")  # shrinking removes the old parts
    assert store.get("fcm-service-account") == "kurz"
    assert not any("::" in k[1] for k in kr.data)
    store.set("fcm-service-account", big)
    assert store.delete("fcm-service-account") and kr.data == {}


def test_mac_store_keeps_values_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    kr = _FakeKeyring()
    kr.set_password = lambda s, u, v: kr.data.__setitem__((s, u), v)  # type: ignore[method-assign]
    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(f"jarvis.secrets.keyring.{name}", getattr(kr, name))
    store = SecretStore("jarvis-test", chunked=False)
    store.set("x", "v" * 3000)
    assert list(kr.data) == [("jarvis-test", "x")]


def test_missing_keyring_gives_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*a: Any) -> None:
        raise NoKeyringError("no backend")

    monkeypatch.setattr("jarvis.secrets.keyring.set_password", fail)
    with pytest.raises(SecretStoreUnavailable, match="JARVIS_SECRET_TOKEN_X"):
        SecretStore("jarvis-test", chunked=False).set("token-x", "v")


# --------------------------------------------------------------------------- shell, processes
def test_shell_allowlist_per_platform() -> None:
    perms = load_permissions()
    set_adapter(MacAdapter(which=lambda n: None))
    assert generic.shell_allowlist(perms) == perms.shell_allowlist  # the Mac list, unchanged
    set_adapter(WindowsAdapter(which=lambda n: None))
    win = generic.shell_allowlist(perms)
    assert "find" not in win and "git" in win  # find.exe is a different program on Windows
    set_adapter(LinuxAdapter(which=lambda n: None, environ={}))
    linux = generic.shell_allowlist(perms)
    assert "sw_vers" not in linux and "git" in linux


async def test_batch_files_refuse_cmd_metacharacters(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    set_adapter(WindowsAdapter(which=lambda n: None))
    state = SimpleNamespace(
        permissions=load_permissions(), settings=SimpleNamespace(coding=SimpleNamespace(extra_path=[]))
    )
    conn = generic.ShellConnector(state)  # type: ignore[arg-type]
    run = next(t for t in conn.tools() if t.name == "shell_run").handler
    monkeypatch.setattr(generic.shutil, "which", lambda exe, path=None: f"C:\\nodejs\\{exe}.cmd")
    with pytest.raises(generic.ToolUserError, match=r"cmd\.exe"):
        await run(None, {"argv": ["npm", "install", "%PATH%"]})  # type: ignore[arg-type]
    with pytest.raises(generic.ToolUserError, match="Allowlist"):
        await run(None, {"argv": ["C:\\evil\\npm"]})  # type: ignore[arg-type]


def test_terminate_group_with_and_without_killpg(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[int, int]] = []
    if hasattr(os, "killpg"):
        monkeypatch.setattr(os, "killpg", lambda pid, sig: sent.append((pid, sig)))
        terminate_group(4242, signal.SIGTERM)
        assert sent == [(4242, signal.SIGTERM)]
        monkeypatch.delattr(os, "killpg")
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        terminate_group(child.pid, signal.SIGTERM)  # Windows path: psutil terminates the tree
        assert child.wait(10) is not None
    finally:
        if child.poll() is None:
            child.kill()
    with pytest.raises(ProcessLookupError):
        terminate_group(child.pid, signal.SIGTERM)
