"""Installer (installer/jarvis_installer, stdlib only): dependency report, verified model downloads, guided setup."""

from __future__ import annotations

import hashlib
import io
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from jarvis.config import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "installer"))

from jarvis_installer import deps, models
from jarvis_installer.host import Host
from jarvis_installer.wizard import UI, Options, Wizard, find_desktop_bundle, write_client_config


def _host(tmp_path: Path, os_id: str, programs: set[str], files: set[str] = frozenset()) -> Host:  # type: ignore[assignment]
    env = {"LOCALAPPDATA": str(tmp_path / "Local"), "APPDATA": str(tmp_path / "Roaming"), "ProgramFiles": "C:/PF"}
    return Host(
        os=os_id,
        home=tmp_path / "home",
        environ=env,
        arch="x86_64",
        which=lambda n: f"/bin/{n}" if n in programs else None,
        exists=lambda p: str(p).replace("\\", "/") in files,
        globber=lambda pattern: [],
    )


# --------------------------------------------------------------------------- dependencies
def test_missing_obsidian_is_reported_with_an_install_hint(tmp_path: Path) -> None:
    host = _host(tmp_path, "windows", {"uv", "git", "powershell", "claude", "tailscale"})
    statuses = {s.dep.id: s for s in deps.check_all(host, {"core", "client", "device"})}
    assert not statuses["obsidian"].found and statuses["obsidian"].required is False
    lines = "\n".join(deps.report_lines(list(statuses.values()), host))
    assert "Obsidian" in lines and "https://obsidian.md/download" in lines
    assert statuses["postgresql"].required and not statuses["postgresql"].found
    assert "xdotool" not in statuses  # Linux-only dependencies are not checked on Windows
    obsidian_exe = (tmp_path / "Local" / "Programs" / "Obsidian" / "Obsidian.exe").as_posix()  # same form as exists()
    host = _host(tmp_path, "windows", {"uv"}, {obsidian_exe})
    st = deps.check(next(d for d in deps.DEPENDENCIES if d.id == "obsidian"), host, {"client"})
    assert st.found and st.where is not None and st.where.replace("\\", "/") == obsidian_exe


def test_roles_select_the_dependencies(tmp_path: Path) -> None:
    host = _host(tmp_path, "linux", set())
    device_only = {s.dep.id for s in deps.check_all(host, {"device"})}
    assert {"uv", "tailscale", "xdotool", "xclip", "notify-send", "atspi"} <= device_only
    assert "postgresql" not in device_only and "obsidian" not in device_only
    required = {s.dep.id for s in deps.check_all(host, {"core"}) if s.required}
    assert {"uv", "claude", "git", "postgresql"} <= required


def test_winget_install_command() -> None:
    calls: list[list[str]] = []

    def run(argv: Sequence[str]) -> tuple[int, str]:
        calls.append(list(argv))
        return 0, "ok"

    ok, _ = deps.winget_install(next(d for d in deps.DEPENDENCIES if d.id == "obsidian"), run)
    assert ok and calls[0][:4] == ["winget", "install", "--id", "Obsidian.Obsidian"]
    assert "--accept-package-agreements" in calls[0]


# --------------------------------------------------------------------------- models
class _Resp:
    def __init__(self, data: bytes, status: int = 200) -> None:
        self.buf = io.BytesIO(data)
        self.status = status

    def read(self, n: int) -> bytes:
        return self.buf.read(n)


def _model(data: bytes, **kw: Any) -> models.ModelFile:
    return models.ModelFile("t", "tts", "Testmodell", "https://example.invalid/m.onnx", len(data), "piper/m.onnx",
                            sha256=hashlib.sha256(data).hexdigest(), **kw)  # fmt: skip


def test_download_verifies_and_resumes(tmp_path: Path) -> None:
    data = b"onnx" * 50_000
    m = _model(data)
    seen: list[dict[str, str]] = []

    def opener(req: Any, timeout: float) -> _Resp:
        seen.append(dict(req.header_items()))
        rng = req.get_header("Range")
        if rng:
            start = int(rng.split("=")[1].rstrip("-"))
            return _Resp(data[start:], 206)
        return _Resp(data)

    part = tmp_path / "piper" / "m.onnx.part"
    part.parent.mkdir(parents=True)
    part.write_bytes(data[:1000])  # an interrupted earlier download
    path = models.download(m, tmp_path, opener=opener)
    assert path.read_bytes() == data and not part.exists()
    assert seen[0]["Range"] == "bytes=1000-"
    assert models.missing(tmp_path, ["tts"]) == [x for x in models.files_for(["tts"])]  # catalog files absent
    assert models.download(m, tmp_path, opener=lambda *a, **k: pytest.fail("no second download")) == path


def test_corrupt_download_is_discarded(tmp_path: Path) -> None:
    m = _model(b"richtig")
    with pytest.raises(models.DownloadError, match="Prüfsumme"):
        models.download(m, tmp_path, opener=lambda req, timeout: _Resp(b"falsch!"))
    assert not (tmp_path / "piper" / "m.onnx").exists() and not (tmp_path / "piper" / "m.onnx.part").exists()


def test_git_blob_checksum_for_small_config_files(tmp_path: Path) -> None:
    data = b'{"audio": {"sample_rate": 22050}}'
    blob = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
    m = models.ModelFile("c", "tts", "cfg", "https://x", len(data), "c.json", git_sha1=blob)
    (tmp_path / "c.json").write_bytes(data)
    assert models.digest_ok(tmp_path / "c.json", m)
    (tmp_path / "c.json").write_bytes(data[:-1] + b" ")
    assert not models.digest_ok(tmp_path / "c.json", m)


def test_catalog_is_pinned_and_space_is_checked(tmp_path: Path) -> None:
    for m in models.CATALOG:
        assert m.url.startswith("https://") and m.size > 0 and (m.sha256 or m.git_sha1), m.id
    assert {m.group for m in models.CATALOG} == set(models.GROUPS)
    with pytest.raises(ValueError):
        models.files_for(["unbekannt"])
    ok, needed, free = models.space_check(tmp_path / "noch" / "nicht" / "da", models.files_for(["stt"]))
    assert needed > 574_000_000 and free > 0 and ok == (free >= needed)


# --------------------------------------------------------------------------- wizard
class _ScriptedUI(UI):
    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.out: list[str] = []

    def say(self, text: str = "") -> None:
        self.out.append(text)

    def ask(self, question: str, default: str = "") -> str:
        for key, value in self.answers.items():
            if key in question:
                return value
        return default

    def confirm(self, question: str, default: bool = True) -> bool:
        for key, value in self.answers.items():
            if key in question:
                return value == "ja"
        return default

    def progress(self, label: str, done: int, total: int) -> None:
        pass


def test_windows_device_and_client_setup(tmp_path: Path) -> None:
    host = _host(tmp_path, "windows", {"uv", "powershell", "tailscale", "git", "claude"})
    calls: list[list[str]] = []

    def run(argv: Sequence[str]) -> tuple[int, str]:
        calls.append(list(argv))
        if argv[:3] == ["uv", "tool", "dir"]:
            return 0, "C:/Users/Sir/.local/bin"
        if argv[1:] == ["automations"]:
            return 0, "Automationen-Ordner: C:/Users/Sir/AppData/Roaming/JarvisDevice/automations\n  (noch keine)\n"
        return 0, ""

    opts = Options(repo=tmp_path / "repo", roles={"device", "client"}, install_missing=False,
                   browser_automation=True, client_url="https://mac.tail1234.ts.net/", client_token="jdv_client")  # fmt: skip
    ui = _ScriptedUI({"Core-Adresse (z. B.": "https://100.101.102.103:8767", "Kopplungscode": "code-123",
                      "Fingerabdruck": "AB" * 32, "automatisch starten": "ja"})  # fmt: skip
    summary = Wizard(opts, host, ui, run).start()
    agent = str(Path("C:/Users/Sir/.local/bin") / "jarvis-device.exe")
    assert calls[0] == ["uv", "tool", "install", "--force", "--python", "3.12", f"{tmp_path / 'repo' / 'device_agent'}[browser]"]
    assert [agent, "pair", "--core", "https://100.101.102.103:8767", "--code", "code-123", "--fingerprint", "AB" * 32] in calls
    assert [agent, "autostart", "enable"] in calls
    cfg = json.loads((host.data_dir / "client.json").read_text())
    assert cfg == {"core_url": "https://mac.tail1234.ts.net", "token": "jdv_client"}
    assert any("Obsidian" in line for line in summary.open)  # reported, not silently ignored
    report = json.loads((host.data_dir / "install-report.json").read_text())
    assert "Device Agent gekoppelt" in report["done"]
    assert any("JarvisDevice/automations" in line for line in report["done"])


def test_core_setup_downloads_models_and_prepares_the_voice(tmp_path: Path) -> None:
    host = _host(tmp_path, "linux", {"uv"})
    calls: list[list[str]] = []
    fetched: list[str] = []

    def run(argv: Sequence[str]) -> tuple[int, str]:
        calls.append(list(argv))
        return 0, ""

    def download(m: models.ModelFile, d: Path, progress: Any) -> Path:
        fetched.append(m.id)
        progress(m.size, m.size)
        return d / m.dest

    opts = Options(repo=tmp_path / "repo", roles={"core"}, models=["tts", "wakeword", "stt-small"], assume_yes=True)
    summary = Wizard(opts, host, _ScriptedUI({}), run, download).start()
    assert set(fetched) == {m.id for m in models.files_for(["tts", "wakeword", "stt-small"])}
    assert ["uv", "sync", "--directory", str(tmp_path / "repo" / "core"), "--extra", "voice"] in calls
    piper_venv = host.data_dir / "tts-local" / ".venv"
    assert ["uv", "venv", "--python", "3.12", str(piper_venv)] in calls
    assert any("requirements-piper.txt" in " ".join(c) for c in calls)
    assert host.vault_dir.is_dir()
    assert any("PostgreSQL" in line for line in summary.open)


def test_mac_core_role_points_to_setup_sh(tmp_path: Path) -> None:
    host = _host(tmp_path, "mac", {"uv"})
    summary = Wizard(Options(repo=tmp_path, roles={"core"}, check_only=True), host, _ScriptedUI({}),
                     lambda argv: (0, "")).start()  # fmt: skip
    assert "Core auf dem Mac: scripts/setup.sh ausführen" in summary.open


def test_client_config_and_bundle_lookup(tmp_path: Path) -> None:
    path = write_client_config(tmp_path, "https://mac.ts.net/", "jdv_x")
    assert json.loads(path.read_text())["core_url"] == "https://mac.ts.net"
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o077 == 0
    bundle = tmp_path / "desktop" / "src-tauri" / "target" / "release" / "bundle" / "nsis"
    bundle.mkdir(parents=True)
    (bundle / "J.A.R.V.I.S._0.2.0_x64-setup.exe").write_bytes(b"MZ")
    assert find_desktop_bundle(tmp_path, "windows") == bundle / "J.A.R.V.I.S._0.2.0_x64-setup.exe"
    assert find_desktop_bundle(tmp_path, "linux") is None
