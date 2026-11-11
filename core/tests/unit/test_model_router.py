from pathlib import Path

from jarvis.agent.model_router import ModelRouter, is_simple, parse_command


def test_switch_commands() -> None:
    c = parse_command("Jarvis, nimm Opus.")
    assert c and c.model == "opus" and c.scope == "session" and c.remainder == ""
    c = parse_command("Wechsle ab jetzt dauerhaft auf Haiku")
    assert c and c.model == "haiku" and c.scope == "default"
    c = parse_command("Nimm nur für diese Frage Opus und analysiere die Umsatzentwicklung der letzten drei Monate")
    assert c and c.model == "opus" and c.scope == "turn" and "analysiere" in c.remainder
    c = parse_command("Opus-Modus bitte")
    assert c and c.model == "opus"
    c = parse_command("Zurück zur automatischen Modellwahl")
    assert c and c.model is None
    assert parse_command("Wie spät ist es?") is None
    assert parse_command("Lies mir ein Sonett von Shakespeare vor") is None


def test_simple_vs_complex() -> None:
    assert is_simple("Wie spät ist es?", "voice")
    assert is_simple("Öffne bitte Safari.", "desktop")
    assert is_simple("Guten Morgen Jarvis!", "voice")
    assert not is_simple("Leg den Kunden Müller in Lexware an", "desktop")
    assert not is_simple("Schreib eine E-Mail an Herrn Schmidt", "desktop")
    assert not is_simple("Analysiere bitte die Website von Hagebau", "voice")


def test_router_scopes(tmp_path: Path) -> None:
    r = ModelRouter("sonnet", True, tmp_path / "m.json")
    assert r.choose("Wie spät ist es?", "voice", "s1") == ("haiku", "automatisch: einfache Anfrage")
    assert r.choose("Plane mein Projekt", "desktop", "s1")[0] == "sonnet"
    r.set("opus", "session", "s1")
    assert r.choose("Wie spät ist es?", "voice", "s1")[0] == "opus"
    assert r.choose("Wie spät ist es?", "voice", "s2")[0] == "haiku"
    assert r.choose("x y z", "desktop", "s2", turn_override="opus")[0] == "opus"
    r.set("haiku", "default")
    assert ModelRouter("sonnet", True, tmp_path / "m.json").choose("Plane mein Projekt", "desktop", "s3")[0] == "haiku"
    r.set(None, "default")
    assert r.choose("Plane mein Projekt", "desktop", "s3")[0] == "sonnet"


def test_skill_catalog(tmp_path: Path) -> None:
    import json

    from jarvis.agent.skills import SkillCatalog

    home = tmp_path / "claude"
    (home / "skills" / "web-design").mkdir(parents=True)
    (home / "skills" / "web-design" / "SKILL.md").write_text("---\nname: web-design\ndescription: Websites\n---\nBody")
    plug = tmp_path / "plug"
    (plug / "skills" / "seo").mkdir(parents=True)
    (plug / "skills" / "seo" / "SKILL.md").write_text("---\nname: seo\ndescription: SEO\n---\n")
    (home / "plugins").mkdir()
    (home / "settings.json").write_text(json.dumps({"enabledPlugins": {"tools@mk": True}}))
    (home / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"plugins": {"tools@mk": [{"installPath": str(plug)}]}})
    )
    cat = SkillCatalog(tmp_path / "data", home)
    (tmp_path / "data").mkdir()
    items = {s["id"]: s for s in cat.items()}
    assert items["user/web-design"]["enabled"] and not items["plugin:tools/seo"]["enabled"]
    cat.set_enabled("plugin:tools/seo", True)
    cat.set_enabled("user/web-design", False)
    links = sorted(p.name for p in (cat.plugin_dir / "skills").iterdir())
    assert links == ["seo"]
    assert SkillCatalog(tmp_path / "data", home).enabled_names() == ["seo"]


def test_calls_always_use_the_fixed_call_model(tmp_path: Path) -> None:
    from jarvis.agent.model_router import CALL_EFFORT, CALL_MODEL, MODELS, is_call_channel

    assert (CALL_MODEL, CALL_EFFORT) == ("claude-haiku-5-5", "medium")
    assert MODELS["haiku"]["id"] == CALL_MODEL
    assert is_call_channel("phone") and not any(is_call_channel(c) for c in ("desktop", "voice", "mobile", "api"))
    r = ModelRouter("opus", False, tmp_path / "m.json")
    r.set_effort("max")
    r.set("sonnet", "session", "s1")
    r.next_turn["s1"] = "opus"
    # chat choices (default, session, one-shot, effort) never touch a call, not even a trivial question
    assert r.choose("Wie spät ist es?", "phone", "s1", turn_override="opus")[0] == "haiku"
    # ... and a call does not consume the chat's one-shot choice or change the chat state
    assert r.next_turn == {"s1": "opus"} and r.default == "opus" and r.effort == "max"
    assert r.choose("Wie spät ist es?", "desktop", "s1")[0] == "opus"  # chat/other channels unchanged: next-turn
    assert r.choose("Wie spät ist es?", "desktop", "s1")[0] == "sonnet"  # session override


def test_call_model_ignores_quota_pressure(tmp_path: Path) -> None:
    from types import SimpleNamespace

    r = ModelRouter("sonnet", True, tmp_path / "m.json")
    r.usage = SimpleNamespace(pressure=lambda: 0.99)  # type: ignore[assignment]
    assert r.choose("Plane mein Projekt", "desktop", "s1")[0] == "haiku"  # chat steps down under quota pressure
    assert r.choose("Plane mein Projekt", "phone", "s1")[0] == "haiku"  # calls: fixed, not the "complex" -> sonnet rule


def test_call_after_a_coding_job_ignores_the_job_and_chat_settings(tmp_path: Path) -> None:
    """A coding job (Sonnet 5.5 / high) finished and the alert rings Sir: the call still runs on the call rule."""
    from types import SimpleNamespace

    from jarvis.agent.model_router import CALL_EFFORT, CALL_MODEL, model_id
    from jarvis.coding.runner import CodingRunner

    r = ModelRouter("sonnet", False, tmp_path / "m.json")
    r.set_effort("high")
    runner = CodingRunner.__new__(CodingRunner)
    runner.state = SimpleNamespace(model_router=r)  # type: ignore[attr-defined]
    runner.cfg = SimpleNamespace(default_model="")  # type: ignore[attr-defined]
    assert (runner.resolve_model(None), runner.resolve_effort(None)) == ("claude-sonnet-5-5", "high")  # the job
    alias, reason = r.choose("Der Job ist fertig, Sir. Soll ich den Stand zusammenfassen?", "phone", "call-session")
    assert model_id(alias) == CALL_MODEL == "claude-haiku-5-5" and CALL_EFFORT == "medium", reason
    # ... and neither the call nor the job touched the other's settings
    assert (r.default, r.effort) == ("sonnet", "high")
