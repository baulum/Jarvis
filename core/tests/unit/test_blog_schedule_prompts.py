"""Blogpost-Zeitpläne: Prompts indexierbar + conversion-orientiert (scripts/blog-schedules) und das Werkzeug
schedule_update, mit dem Jarvis bestehende Aufgaben inhaltlich ändern kann."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.coding.runner import split_test_command
from jarvis.connectors.system import SystemConnector
from jarvis.security.auth import Principal
from jarvis.tools.executor import ToolUserError
from jarvis.tools.registry import ToolContext

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "blog-schedules" / "update_blog_prompts.py"
_spec = importlib.util.spec_from_file_location("update_blog_prompts", SCRIPT)
assert _spec and _spec.loader
blog = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = blog  # dataclass löst Annotationen über sys.modules auf
_spec.loader.exec_module(blog)

REQUIRED_TERMS = (
    "index, follow",
    "canonical",
    "sitemap",
    "noindex-Prüfung",
    "Call-to-Action",
    "Meta-Description",
    "coding_start_job",
    "test_command",
)


@pytest.mark.parametrize("target", blog.TARGETS, ids=lambda t: t.name)
def test_prompt_covers_index_and_conversion(target: Any) -> None:
    text = blog.build_prompt(target.site, target.slot).lower()
    for term in REQUIRED_TERMS:
        assert term.lower() in text, f"{target.name}: '{term}' fehlt"
    for term in ("noindex", "follow", "index", "sitemap.xml", "kontakt"):
        assert term in text
    # Wahrheitsgebot und Zielgruppe
    assert "keine erfundenen preise" in text and "kundennamen" in text and "garantien" in text
    assert target.site.domain in text and target.site.path.lower() in text


def test_four_targets_keep_ids_names_and_cron() -> None:
    assert {(t.sid, t.name, t.cron) for t in blog.TARGETS} == {
        ("50ed06fd-2ffa-4103-879c-d4fe05156da6", "Apo HR Blogpost Vormittag", "0 8 * * *"),
        ("91191ba8-5d39-4f19-b5dc-91d614225677", "Apo HR Blogpost Nachmittag", "0 14 * * *"),
        ("0b473f3b-714b-4236-8f85-eb2727b37b66", "PJE Systems Blogpost Vormittag", "0 9 * * *"),
        ("c3492925-9c19-40a5-b1fd-bfa77045a41b", "PJE Systems Blogpost Nachmittag", "0 15 * * *"),
    }


def test_site_specific_targets() -> None:
    apo = blog.build_prompt(blog.APO, "vormittag")
    pje = blog.build_prompt(blog.PJE, "nachmittag")
    assert "Apothekenleitung" in apo and "/app/signup" in apo and "https://apohr-portal.de/blog/<slug>" in apo
    assert "https://pje-systems.de/blog/<slug>/" in pje and "/projekt-check/" in pje and "/kontakt/" in pje
    assert "Apo HR" not in pje and "pje-systems.de" not in apo  # Daten eines Kunden nie für den anderen
    assert "auch den heutigen Vormittagsbeitrag" in pje and "zweiten neuen Blogbeitrag" in pje


@pytest.mark.parametrize("site", [blog.APO, blog.PJE], ids=lambda s: s.domain)
def test_test_command_is_runnable_by_the_runner(site: Any) -> None:
    steps = split_test_command(blog.test_command(site))
    assert [s[:3] for s in steps] == [
        ["npm", "run", "blog:check"],
        ["npm", "run", "build"],
        ["node", "scripts/index-check.mjs", "--host"],
    ]
    assert steps[2][3] == site.domain
    assert blog.build_prompt(site, "vormittag").count(blog.test_command(site)) == 1


def test_embedded_index_check_script_is_in_every_prompt() -> None:
    assert "process.exit(1)" in blog.INDEX_CHECK and "canonical" in blog.INDEX_CHECK
    for t in blog.TARGETS:
        assert blog.INDEX_CHECK in blog.build_prompt(t.site, t.slot)


# ------------------------------------------------------------------ schedule_update tool
class FakeScheduler:
    def __init__(self) -> None:
        self.sched = SimpleNamespace(
            id=uuid.uuid4(),
            name="Test",
            cron="0 8 * * *",
            enabled=True,
            action={"type": "prompt", "prompt": "alt"},
            next_run_at=datetime(2026, 11, 17, 7, 0, tzinfo=UTC),
        )
        self.calls: list[tuple[Any, ...]] = []

    async def list(self) -> list[Any]:
        return [self.sched, SimpleNamespace(**{**vars(self.sched), "id": uuid.uuid4(), "action": {"type": "tool"}})]

    async def update(self, sid: uuid.UUID, actor: str, **fields: Any) -> Any:
        self.calls.append((sid, actor, fields))
        if sid != self.sched.id:
            return None
        if fields.get("prompt") == "":
            raise ValueError("Die Anweisung darf nicht leer sein")
        for k, v in fields.items():
            if k == "prompt":
                self.sched.action = {**self.sched.action, "prompt": v}
            else:
                setattr(self.sched, k, v)
        return self.sched


def _tool(name: str) -> tuple[FakeScheduler, Any, ToolContext]:
    sched = FakeScheduler()
    tool = next(t for t in SystemConnector(SimpleNamespace(scheduler=sched)).tools() if t.name == name)
    return sched, tool, ToolContext(principal=Principal(id="owner", role="owner", channel="test"))


async def test_schedule_update_changes_only_given_fields() -> None:
    sched, tool, ctx = _tool("schedule_update")
    out = await tool.handler(ctx, {"id": str(sched.sched.id), "prompt": "neu"})
    assert sched.sched.action == {"type": "prompt", "prompt": "neu"}
    assert sched.calls[0][2] == {"prompt": "neu"}  # name, cron, enabled bleiben unberührt
    assert out["id"] == str(sched.sched.id) and out["cron"] == "0 8 * * *" and out["name"] == "Test"
    assert tool.risk == "medium" and "schedule_list" in tool.description


async def test_schedule_update_rejects_bad_input() -> None:
    sched, tool, ctx = _tool("schedule_update")
    with pytest.raises(ToolUserError):
        await tool.handler(ctx, {"id": "keine-uuid", "prompt": "x"})
    with pytest.raises(ToolUserError, match="Nichts zu ändern"):
        await tool.handler(ctx, {"id": str(sched.sched.id)})
    with pytest.raises(ToolUserError, match="nicht gefunden"):
        await tool.handler(ctx, {"id": str(uuid.uuid4()), "name": "x"})
    with pytest.raises(ToolUserError, match="leer"):
        await tool.handler(ctx, {"id": str(sched.sched.id), "prompt": ""})
    assert sched.sched.action["prompt"] == "alt"


async def test_schedule_list_shows_prompt_only_for_prompt_schedules() -> None:
    sched, tool, ctx = _tool("schedule_list")
    rows = await tool.handler(ctx, {})
    assert rows[0]["prompt"] == "alt"
    assert "prompt" not in rows[1]
    assert set(rows[0]) == {"id", "name", "cron", "enabled", "next_run", "prompt"}
