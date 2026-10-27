from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from agent_fakes import FakeBackend

from jarvis_device.backends.base import DesktopBackend
from jarvis_device.config import AgentConfig
from jarvis_device.dispatcher import Dispatcher
from jarvis_device.security import KillSwitch, PathPolicy


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "root"
    r.mkdir()
    return r


@pytest.fixture
def make_dispatcher(tmp_path: Path, root: Path) -> Callable[..., Dispatcher]:
    def make(backend: DesktopBackend | None = None, automations_dir: Path | None = None, **cfg_kw: Any) -> Dispatcher:
        cfg = AgentConfig(allowed_roots=[str(root)], **cfg_kw)
        home = tmp_path / "agent-home"
        policy = PathPolicy(cfg.roots(), protected=[home])
        return Dispatcher(backend or FakeBackend(), cfg, policy, KillSwitch(home / "KILL"), automations_dir)

    return make
