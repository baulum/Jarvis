"""Erkennt eine Desktop-App, die älter ist als die Oberfläche im Repository (Ursache: Anhänge und Bildvorschau
fehlten im Chat, weil die installierte App vor diesen Commits gebaut worden war)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.api import routes
from jarvis.desktop_build import desktop_build_status
from jarvis.security.auth import Principal

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git fehlt")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip


def _commit(root: Path, path: str, text: str) -> str:
    f = root / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    _git(root, "add", path)
    _git(root, "commit", "-q", "-m", path)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    return tmp_path


async def test_only_ui_changes_since_the_build_make_the_app_stale(repo: Path) -> None:
    built = _commit(repo, "desktop/src/App.tsx", "v1")
    _commit(repo, "core/src/jarvis/x.py", "core only")
    status = await desktop_build_status(repo, built)
    assert status["stale"] is False and status["changes"] == 0  # core-only commits do not matter

    _commit(repo, "desktop/src/components/chat/Attachments.tsx", "paper clip")
    head = _commit(repo, "desktop/src-tauri/tauri.conf.json", "{}")
    status = await desktop_build_status(repo, built)
    assert status == {"build_commit": built[:12], "core_commit": head[:12], "stale": True, "changes": 2}
    assert (await desktop_build_status(repo, head))["stale"] is False


@pytest.mark.parametrize("commit", ["", "HEAD", "--output=/tmp/x", "abc", "g" * 40])
async def test_malformed_commits_are_not_passed_to_git(repo: Path, commit: str) -> None:
    _commit(repo, "desktop/src/App.tsx", "v1")
    status = await desktop_build_status(repo, commit)
    assert status["stale"] is None and status["build_commit"] == "" and status["reason"]


async def test_unknown_commit_or_no_repository_cannot_tell(
    repo: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    _commit(repo, "desktop/src/App.tsx", "v1")
    unknown = await desktop_build_status(repo, "0" * 40)
    assert unknown["stale"] is None and "unbekannt" in unknown["reason"] and unknown["core_commit"]
    elsewhere = await desktop_build_status(tmp_path_factory.mktemp("kein-repo"), "a" * 40)
    assert elsewhere["stale"] is None and elsewhere["core_commit"] is None


def test_endpoint_reports_for_the_repository_of_the_core(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    built = _commit(repo, "desktop/src/App.tsx", "v1")
    _commit(repo, "desktop/src/App.tsx", "v2")
    monkeypatch.setattr(routes, "REPO_ROOT", repo)
    app = FastAPI()
    app.include_router(routes.router)
    app.state.jarvis = SimpleNamespace()
    reader = Principal(id="desktop", role="owner", channel="desktop")
    app.dependency_overrides[routes.Reader.dependency] = lambda: reader  # type: ignore[attr-defined]
    r = TestClient(app).get("/api/system/desktop-build", params={"commit": built.upper()})
    assert r.status_code == 200, r.text
    assert r.json()["stale"] is True and r.json()["changes"] == 1
