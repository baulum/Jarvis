from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.connectors.kie_creative import generated_dir, resolve_image_path
from jarvis.tools.executor import ToolUserError


def _state(tmp_path: Path) -> SimpleNamespace:
    paths = SimpleNamespace(
        data_dir=tmp_path / "data", workspaces_dir=tmp_path / "ws", projects_dir=tmp_path / "projects"
    )
    for p in (paths.data_dir, paths.workspaces_dir, paths.projects_dir):
        p.mkdir()
    return SimpleNamespace(settings=SimpleNamespace(paths=paths))


def test_accepts_generated_image(tmp_path: Path) -> None:
    state = _state(tmp_path)
    gen = generated_dir(state)
    gen.mkdir()
    img = gen / "a.png"
    img.write_bytes(b"x")
    assert resolve_image_path(state, str(img)) == img.resolve()


def test_rejects_path_outside_roots(tmp_path: Path) -> None:
    state = _state(tmp_path)
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"x")
    with pytest.raises(ToolUserError):
        resolve_image_path(state, str(outside))


def test_rejects_non_image_and_traversal(tmp_path: Path) -> None:
    state = _state(tmp_path)
    gen = generated_dir(state)
    gen.mkdir()
    (gen / "notes.txt").write_text("x")
    with pytest.raises(ToolUserError):
        resolve_image_path(state, str(gen / "notes.txt"))
    with pytest.raises(ToolUserError):
        resolve_image_path(state, str(gen / ".." / ".." / "secret.png"))


def test_send_roots_can_be_restricted_to_generated_only(tmp_path: Path) -> None:
    state = _state(tmp_path)
    ws_img = state.settings.paths.workspaces_dir / "b.png"
    ws_img.write_bytes(b"x")
    assert resolve_image_path(state, str(ws_img)) == ws_img.resolve()
    with pytest.raises(ToolUserError):
        resolve_image_path(state, str(ws_img), roots=[generated_dir(state).resolve()])
