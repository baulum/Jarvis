from pathlib import Path

from jarvis.agent.model_router import (
    CALL_EFFORT,
    CALL_MODEL,
    MODELS,
    ModelRouter,
    call_effort,
    is_simple,
    parse_command,
)


def test_call_rule_constants() -> None:
    assert CALL_MODEL == "claude-haiku-5-5"
    assert CALL_EFFORT == "medium"
    assert call_effort("phone") == "medium"
    assert call_effort("desktop") is None and call_effort("mobile") is None and call_effort("voice") is None


def test_calls_are_fixed_to_haiku_whatever_the_chat_choice(tmp_path: Path) -> None:
    r = ModelRouter("sonnet", True, tmp_path / "m.json")
    r.set("opus", "default")
    r.set("opus", "session", "s1")
    r.next_turn["s1"] = "opus"
    model, _ = r.choose("Plane mein Projekt und analysiere die Zahlen ausführlich", "phone", "s1", turn_override="opus")
    assert MODELS[model]["id"] == CALL_MODEL
    assert r.next_turn == {"s1": "opus"}  # a one-shot chat choice is not consumed by a call
    # chat and unrelated channels are unchanged
    assert r.choose("Plane mein Projekt", "desktop", "s1")[0] == "opus"
    assert r.choose("Plane mein Projekt", "desktop", "s2")[0] == "opus"
    r.set("sonnet", "default")
    assert r.choose("Plane mein Projekt", "desktop", "s9")[0] == "sonnet"


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
