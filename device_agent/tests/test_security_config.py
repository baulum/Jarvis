"""PathPolicy, local kill switch and the config file."""

from __future__ import annotations

import os
import stat

import pytest

from jarvis_device.config import AgentConfig, config_dir
from jarvis_device.security import KillSwitch, PathPolicy, PolicyError


def test_policy_allows_only_paths_below_the_roots(tmp_path):
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    policy = PathPolicy([root])
    assert policy.check(str(root / "a" / "neu.txt")) == (root / "a" / "neu.txt").resolve()
    for bad in [str(tmp_path / "x"), str(root / ".." / "x"), "", "a\x00b"]:
        with pytest.raises(PolicyError):
            policy.check(bad)


@pytest.mark.skipif(os.name != "posix", reason="symlinks need privileges on Windows")
def test_symlinks_out_of_the_roots_do_not_count_as_inside(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("x")
    (root / "link.txt").symlink_to(secret)
    with pytest.raises(PolicyError):
        PathPolicy([root]).check(str(root / "link.txt"))


def test_key_folders_and_the_agent_config_are_protected(tmp_path):
    home = tmp_path / "home"
    agent = home / "agent"
    (home / ".ssh").mkdir(parents=True)
    agent.mkdir()
    policy = PathPolicy([home], protected=[agent])
    with pytest.raises(PolicyError, match="Schlüsselordner"):
        policy.check(str(home / ".ssh" / "id_ed25519"))
    with pytest.raises(PolicyError, match="geschützt"):
        policy.check(str(agent / "config.json"))


def test_kill_switch_local_and_remote(tmp_path):
    ks = KillSwitch(tmp_path / "KILL")
    assert not ks.engaged
    ks.remote = True
    assert ks.engaged
    ks.remote = False
    ks.engage_local("test")
    assert ks.engaged and (tmp_path / "KILL").exists()
    ks.release_local()
    assert not ks.engaged


def test_config_roundtrip_hides_token_and_is_private(tmp_path):
    path = tmp_path / "cfg" / "config.json"
    cfg = AgentConfig(core_url="https://100.64.0.1:8767", fingerprint="ab" * 32, token="jdv_secret", name="PC")
    cfg.save(path)
    loaded = AgentConfig.load(path)
    assert loaded.token == "jdv_secret" and loaded.paired
    assert loaded.public()["token"] == "gesetzt"
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.write_text('{"core_url": "x", "terminal": "kaputt", "unknown": 1}')
    odd = AgentConfig.load(path)
    assert odd.terminal == "on" and odd.core_url == "x"


def test_config_dir_per_platform(tmp_path):
    assert config_dir({"APPDATA": r"C:\Users\Sir\AppData\Roaming"}, "win32").name == "JarvisDevice"
    assert config_dir({"XDG_CONFIG_HOME": str(tmp_path)}, "linux") == tmp_path / "jarvis-device"
    assert config_dir({}, "darwin").parts[-2:] == ("Application Support", "JarvisDevice")
    assert config_dir({"JARVIS_DEVICE_HOME": str(tmp_path / "x")}, "linux") == tmp_path / "x"
