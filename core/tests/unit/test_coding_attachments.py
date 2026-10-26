"""Jarvis gibt Chat-Anhänge an einen Coding-Job weiter: Kopie außerhalb des Repos, Pfade in den Anweisungen,
und nur echte Anhänge (nackter Name) kommen durch."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.agent import attachments as att
from jarvis.connectors.coding import CodingConnector
from jarvis.security.auth import Principal
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 40


class FakeRunner:
    def __init__(self) -> None:
        self.created: dict[str, Any] = {}

    async def create_job(self, title: str, instructions: str, project_path: str, **kw: Any) -> Any:
        self.created = {"title": title, "instructions": instructions, **kw}
        return SimpleNamespace(id="J1", model="sonnet", effort="medium")


def _setup(tmp_path: Path) -> tuple[Any, FakeRunner, Any]:
    state = SimpleNamespace(settings=SimpleNamespace(paths=SimpleNamespace(data_dir=tmp_path)))
    runner = FakeRunner()
    state.coding = runner
    conn = CodingConnector(state)
    tool = next(t for t in conn.tools() if t.name == "coding_start_job")
    ctx = ToolContext(principal=Principal(id="desktop", role="owner", channel="desktop"), session_id=None)
    return state, runner, (tool, ctx)


async def test_job_gets_copies_and_paths(tmp_path: Path) -> None:
    state, runner, (tool, ctx) = _setup(tmp_path)
    info = att.save_attachment(att.attachments_dir(state), "Fehler Screenshot.png", PNG)
    out = await tool.handler(
        ctx,
        {
            "title": "Bug",
            "instructions": "Behebe den Fehler im Screenshot.",
            "project_path": "/p",
            "attachments": [info.file],
        },
    )
    assert out["job_id"] == "J1"
    text = runner.created["instructions"]
    assert text.startswith("Behebe den Fehler im Screenshot.")
    assert "Anhänge von Sir" in text and "Fehler-Screenshot.png" in text
    copies = list((tmp_path / "coding-attachments").glob(f"*/{info.file}"))
    assert len(copies) == 1 and copies[0].read_bytes() == PNG
    assert str(copies[0]) in text
    assert info.path.exists()  # the chat original stays


async def test_without_attachments_nothing_changes(tmp_path: Path) -> None:
    _, runner, (tool, ctx) = _setup(tmp_path)
    await tool.handler(ctx, {"title": "T", "instructions": "Mach etwas Sinnvolles.", "project_path": "/p"})
    assert runner.created["instructions"] == "Mach etwas Sinnvolles."
    assert not (tmp_path / "coding-attachments").exists()


@pytest.mark.parametrize("bad", ["../secret.png", "/etc/passwd", "nicht-da.png", "20261022-141500-1a2b3c4d-x.exe"])
async def test_only_real_attachments_pass(tmp_path: Path, bad: str) -> None:
    _, runner, (tool, ctx) = _setup(tmp_path)
    with pytest.raises(ToolUserError):
        await tool.handler(
            ctx, {"title": "T", "instructions": "Mach etwas Sinnvolles.", "project_path": "/p", "attachments": [bad]}
        )
    assert not runner.created
